"""Scheduled jobs: model, lifecycle, persistence, scheduler lock and job leases.

A job prepares the post for exactly one schedule slot. Its lifecycle is separate
from the post lifecycle; the job only records which post it drives
(`post_id`). A job's goal is to bring that post to AWAITING_APPROVAL before the
slot. Approval is human-only; jobs never publish (publishing is `lce publish` or a
consented cloud slot, both after approval).

Files (private data directory):
- automation/jobs/<job_id>.yaml   one file per job (created exclusively)
- automation/scheduler.lock       exists only while a scheduler run holds it
- config/automation.yaml          optional tuning; defaults below
All timestamps are UTC ISO 8601 with +00:00 (see lce.clock).
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

from lce import clock
from lce.clock import iso_utc, parse_iso
from lce.store import DataStore, StoreError, dump_yaml

JOB_ID_RE = re.compile(r"^job-\d{4}-\d{2}-\d{2}-(mon|tue|wed|thu|fri|sat|sun)-\d{4}$")

DEFAULT_AUTOMATION = {
    "lead_hours": 48,           # the job becomes READY this long before its slot
    "horizon_days": 14,         # jobs are created for slots up to this far ahead
    "lookback_days": 7,         # past slots considered (to mark them missed)
    "max_attempts": 3,          # automatic retries of retryable failures
    "retry_delay_minutes": 15,  # fixed delay before an automatic retry
    "lease_minutes": 30,        # job lease for a running execution or agent claim
    "lock_minutes": 10,         # scheduler lock lifetime
    "max_revisions": 2,         # QA/duplicate failures before the job fails
    # LCE-049: rolling calendar and schedule-derived freshness
    "plan_horizon_days": 28,       # cadence slots reserved (and filled with candidates) this far ahead
    "freshness_lead_hours": 36,    # the freshness window opens this long before the slot
    "freshness_recheck_hours": 12, # re-check inside the window when the last check is older
    "freshness_escalate": True,    # stale -> automatic same-slot replacement request
}


class JobState(StrEnum):
    SCHEDULED = "SCHEDULED"
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    NEEDS_RECONCILE = "NEEDS_RECONCILE"


J = JobState
JOB_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    J.SCHEDULED: frozenset({J.READY, J.SKIPPED}),
    J.READY: frozenset({J.RUNNING, J.SKIPPED}),
    J.RUNNING: frozenset({J.SUCCEEDED, J.BLOCKED, J.FAILED, J.NEEDS_RECONCILE, J.READY,
                          J.SKIPPED}),
    J.BLOCKED: frozenset({J.READY, J.RUNNING, J.SKIPPED, J.FAILED}),
    J.FAILED: frozenset({J.READY, J.SKIPPED}),
    J.NEEDS_RECONCILE: frozenset({J.READY, J.SUCCEEDED, J.FAILED, J.SKIPPED}),
    # A succeeded job re-opens only if its post was sent back for editing.
    J.SUCCEEDED: frozenset({J.READY}),
    J.SKIPPED: frozenset(),
}
TERMINAL = frozenset({J.SKIPPED})
BLOCK_REASONS = ("awaiting_agent", "needs_input", "awaiting_revision")


class InvalidJobTransition(ValueError):
    pass


def new_invocation_id() -> str:
    return f"{clock.now().strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


# ── configuration ─────────────────────────────────────────────────────
def automation_config(store: DataStore) -> dict:
    from lce.validate import validate_doc

    path = store.root / "config" / "automation.yaml"
    doc = store.read_doc(path)
    errors = validate_doc("automation", doc) if doc else []
    if errors:
        raise StoreError("config/automation.yaml is invalid: " + "; ".join(errors))
    return {**DEFAULT_AUTOMATION, **doc}


# ── paths & persistence ───────────────────────────────────────────────
def jobs_dir(store: DataStore) -> Path:
    return store.root / "automation" / "jobs"


def job_path(store: DataStore, job_id: str) -> Path:
    if not JOB_ID_RE.match(job_id):
        raise StoreError(f"invalid job id {job_id!r}")
    return jobs_dir(store) / f"{job_id}.yaml"


def job_id_for(slot_id: str) -> str:
    return f"job-{slot_id}"


def load_job(store: DataStore, job_id: str) -> dict:
    path = job_path(store, job_id)
    if not path.exists():
        raise StoreError(f"job {job_id} does not exist")
    return store.read_doc(path)


def list_jobs(store: DataStore) -> list[dict]:
    return [store.read_doc(p) for p in sorted(jobs_dir(store).glob("job-*.yaml"))]


def save_job(store: DataStore, job: dict) -> None:
    job["updated_at"] = iso_utc(clock.now())
    store.write_doc(job_path(store, job["job_id"]), "job", job)


def create_job(store: DataStore, job: dict) -> bool:
    """Create the job file exclusively. Returns False if it already exists (idempotent)."""
    from lce.validate import validate_doc

    errors = validate_doc("job", job)
    if errors:
        raise StoreError("job is invalid: " + "; ".join(errors))
    path = job_path(store, job["job_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as fh:
            fh.write(dump_yaml(job))
    except FileExistsError:
        return False
    return True


def new_job(slot: dict, prepare_from: str, invocation_id: str, max_attempts: int) -> dict:
    now = iso_utc(clock.now())
    return {
        "job_id": job_id_for(slot["slot_id"]),
        "kind": "prepare_post",
        "slot": slot,
        "prepare_from": prepare_from,
        "state": J.SCHEDULED.value,
        "attempts": 0,
        "max_attempts": max_attempts,
        "revisions": 0,
        "created_at": now,
        "created_by": invocation_id,
        "updated_at": now,
        "steps": [],
        "history": [{"at": now, "from": None, "to": J.SCHEDULED.value, "reason": "created",
                     "invocation_id": invocation_id}],
    }


# ── lifecycle ─────────────────────────────────────────────────────────
def transition(store: DataStore, job: dict, to: JobState, reason: str, invocation_id: str,
               *, blocked_reason: str | None = None, save: bool = True) -> dict:
    current = JobState(job["state"])
    if to not in JOB_TRANSITIONS[current]:
        raise InvalidJobTransition(f"{job['job_id']}: {current.value} -> {to.value} is not allowed")
    if to == J.BLOCKED and blocked_reason not in BLOCK_REASONS:
        raise InvalidJobTransition("BLOCKED needs a known blocked_reason")
    job["state"] = to.value
    if to == J.BLOCKED:
        job["blocked_reason"] = blocked_reason
    else:
        job.pop("blocked_reason", None)
    if to != J.RUNNING:
        job.pop("lease", None)
    job["history"].append({"at": iso_utc(clock.now()), "from": current.value, "to": to.value,
                           "reason": reason, "invocation_id": invocation_id})
    if save:
        save_job(store, job)
    store.log_event("job.transition", job_id=job["job_id"], slot_id=job["slot"]["slot_id"],
                    **{"from": current.value}, to=to.value, reason=reason,
                    invocation_id=invocation_id, post_id=job.get("post_id"))
    return job


def record_step(store: DataStore, job: dict, name: str, status: str, invocation_id: str,
                detail: str = "") -> None:
    entry = {"at": iso_utc(clock.now()), "name": name, "status": status,
             "invocation_id": invocation_id}
    if detail:
        entry["detail"] = detail
    job["steps"].append(entry)
    save_job(store, job)  # checkpoint: survives a crash right after this step
    store.log_event("job.step", job_id=job["job_id"], step=name, status=status,
                    invocation_id=invocation_id, post_id=job.get("post_id"), detail=detail)


def record_error(store: DataStore, job: dict, kind: str, message: str, retryable: bool,
                 invocation_id: str) -> None:
    job["last_error"] = {"at": iso_utc(clock.now()), "kind": kind, "message": message[:300],
                         "retryable": retryable}
    store.log_event("job.error", job_id=job["job_id"], kind=kind, retryable=retryable,
                    message=message[:300], invocation_id=invocation_id,
                    post_id=job.get("post_id"))


# ── leases ────────────────────────────────────────────────────────────
def lease_expired(job: dict) -> bool:
    lease = job.get("lease")
    return bool(lease) and parse_iso(lease["expires_at"]) <= clock.now()


def acquire_lease(job: dict, holder: str, invocation_id: str, minutes: int) -> None:
    now = clock.now()
    job["lease"] = {"holder": holder, "invocation_id": invocation_id,
                    "acquired_at": iso_utc(now),
                    "expires_at": iso_utc(now + timedelta(minutes=minutes))}


# ── scheduler lock ────────────────────────────────────────────────────
def lock_path(store: DataStore) -> Path:
    return store.root / "automation" / "scheduler.lock"


def acquire_lock(store: DataStore, invocation_id: str, minutes: int) -> tuple[bool, dict | None]:
    """Exclusive scheduler lock. Returns (acquired, holder_if_not_acquired).

    An expired lock is taken over: it is atomically renamed away first, so only
    one contender can win the takeover.
    """
    path = lock_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = clock.now()
    body = json.dumps({"invocation_id": invocation_id, "pid": os.getpid(),
                       "acquired_at": iso_utc(now),
                       "expires_at": iso_utc(now + timedelta(minutes=minutes))})
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                holder = json.loads(path.read_text("utf-8"))
                expired = parse_iso(holder["expires_at"]) <= now
            except (OSError, ValueError, KeyError):
                holder, expired = {"invocation_id": "unreadable"}, True
            if not expired:
                return False, holder
            stale = path.with_name(f"scheduler.lock.stale-{invocation_id}")
            try:
                os.rename(path, stale)
            except FileNotFoundError:
                continue  # someone else took it over first; try again
            store.log_event("scheduler.lock_takeover", invocation_id=invocation_id,
                            previous=holder.get("invocation_id"))
            stale.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        return True, None
    return False, {"invocation_id": "contended"}


def release_lock(store: DataStore, invocation_id: str) -> None:
    path = lock_path(store)
    try:
        holder = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return
    if holder.get("invocation_id") == invocation_id:
        path.unlink(missing_ok=True)


def snapshot_job(job: dict) -> dict:
    return copy.deepcopy(job)
