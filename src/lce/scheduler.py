"""Scheduler: one deterministic pass (`run_once`), with a side-effect-free dry run.

What one pass does, in order:
1. Take the scheduler lock (a concurrent pass exits; nothing runs twice).
2. Load schedule + automation config from the private data (invalid → stop).
3. Create missing jobs for slots in [now - lookback, now + horizon] (exclusive
   file creation → idempotent).
4. Advance jobs: open prepare windows, reconcile expired leases, schedule due
   retries, mark missed slots, follow post changes.
5. Execute READY jobs: only deterministic, local steps (QA, duplicate check,
   approval artifact). Steps needing an LLM (research, selection, draft,
   humanize) are handed to Claude Code: the job becomes BLOCKED/awaiting_agent.

It never approves, never marks a post ready, never publishes.
There is no daemon; something outside the engine must call `run_once`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from lce import clock
from lce.clock import iso_utc, parse_iso
from lce.jobs import (
    J,
    acquire_lease,
    acquire_lock,
    automation_config,
    create_job,
    job_id_for,
    lease_expired,
    list_jobs,
    load_job,
    new_invocation_id,
    new_job,
    record_error,
    record_step,
    release_lock,
    save_job,
    transition,
)
from lce.schedule import ScheduleError, load_schedule, slots_between
from lce.state import PostState
from lce.store import DataStore, StoreError
from lce.textutil import content_hash

P = PostState
AGENT_STATES = {None, P.RESEARCHED, P.SELECTED, P.DRAFTED, P.NEEDS_REVISION}
# At or past the human approval boundary: the job's part is done. The scheduler
# never moves a post beyond this point (publishing is human-triggered only).
DONE_STATES = {P.AWAITING_APPROVAL, P.APPROVED, P.READY_TO_PUBLISH, P.PUBLISHING, P.PUBLISHED,
               P.PUBLISH_FAILED, P.NEEDS_RECONCILE}


@dataclass
class Action:
    job_id: str
    action: str       # create | ready | run | reconcile | retry | skip_missed | follow_post
    detail: str = ""
    slot_local: str = ""


@dataclass
class Report:
    invocation_id: str
    now: str
    dry_run: bool
    status: str = "ok"           # ok | locked | config_invalid
    message: str = ""
    actions: list[Action] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)


# ── post inspection (read-only) ───────────────────────────────────────
def _post(store: DataStore, job: dict) -> dict | None:
    pid = job.get("post_id")
    if not pid:
        return None
    try:
        return store.load_post(pid)
    except StoreError:
        return {"_missing": pid}


def post_consistency(store: DataStore, post: dict) -> str | None:
    """None when the post's files and recorded state agree; otherwise the reason."""
    if post.get("_missing"):
        return f"linked post {post['_missing']} does not exist"
    text = store.post_text(post["post_id"], "post.md")
    state = P(post["state"])
    if text is not None and post.get("content_hash") and content_hash(text) != post["content_hash"]:
        return "post.md does not match the recorded content hash"
    if state in {P.QA_PASSED, P.DUPLICATE_CHECKED} | DONE_STATES:
        if (post.get("qa") or {}).get("content_hash") != post.get("content_hash"):
            return "QA result does not belong to the current text"
    if state in {P.DUPLICATE_CHECKED} | DONE_STATES:
        if (post.get("duplicate") or {}).get("content_hash") != post.get("content_hash"):
            return "duplicate result does not belong to the current text"
    return None


# ── planning (pure: no writes) ────────────────────────────────────────
def plan(store: DataStore, now=None) -> tuple[list[Action], list[dict], dict]:
    """Decide what a pass would do. Returns (actions, new_job_docs, config)."""
    now = now or clock.now()
    cfg = automation_config(store)
    schedule = load_schedule(store.settings())
    start = now - timedelta(days=cfg["lookback_days"])
    end = now + timedelta(days=cfg["horizon_days"])
    existing = {j["job_id"]: j for j in list_jobs(store)}
    actions: list[Action] = []
    new_docs: list[dict] = []
    for slot in slots_between(schedule, start, end):
        jid = job_id_for(slot.slot_id)
        if jid in existing:
            continue
        prepare_from = iso_utc(slot.utc - timedelta(hours=cfg["lead_hours"]))
        doc = new_job(slot.to_dict(), prepare_from, "(pending)", cfg["max_attempts"])
        new_docs.append(doc)
        existing[jid] = doc
        actions.append(Action(jid, "create", f"prepare from {prepare_from}", slot.local.isoformat()))

    for jid, job in sorted(existing.items()):
        state = J(job["state"])
        slot_utc = parse_iso(job["slot"]["utc"])
        post = _post(store, job)
        pstate = None if post is None or post.get("_missing") else P(post["state"])
        local = job["slot"]["local"]
        if state == J.RUNNING:
            if lease_expired(job):
                actions.append(Action(jid, "reconcile", "lease expired", local))
            continue
        if state in {J.SKIPPED, J.NEEDS_RECONCILE}:
            continue
        if state == J.SUCCEEDED:
            if pstate is not None and pstate not in DONE_STATES and pstate != P.REJECTED \
                    and slot_utc > now:
                actions.append(Action(jid, "follow_post", f"post back in {pstate.value}", local))
                actions.append(Action(jid, "run", describe_run(pstate), local))
            continue
        if slot_utc <= now and pstate not in DONE_STATES:
            actions.append(Action(jid, "skip_missed", "slot passed before approval", local))
            continue
        if state == J.SCHEDULED:
            if parse_iso(job["prepare_from"]) <= now:
                actions.append(Action(jid, "ready", "prepare window open", local))
                actions.append(Action(jid, "run", describe_run(pstate), local))
            continue
        if state == J.FAILED:
            last = job.get("last_error") or {}
            due = parse_iso(job["next_attempt_at"]) <= now if job.get("next_attempt_at") else False
            if last.get("retryable") and job["attempts"] < job["max_attempts"] and due:
                actions.append(Action(jid, "retry", f"attempt {job['attempts'] + 1}", local))
                actions.append(Action(jid, "run", describe_run(pstate), local))
            continue
        if state == J.BLOCKED:
            if job.get("blocked_reason") in {"awaiting_agent", "awaiting_revision"} \
                    and pstate in {P.HUMANIZED, P.QA_PASSED, P.DUPLICATE_CHECKED} | DONE_STATES:
                actions.append(Action(jid, "ready", "agent work found", local))
                actions.append(Action(jid, "run", describe_run(pstate), local))
            elif job.get("blocked_reason") == "needs_input" and pstate not in {P.NEEDS_INPUT}:
                actions.append(Action(jid, "ready", "input no longer missing", local))
                actions.append(Action(jid, "run", describe_run(pstate), local))
            continue
        if state == J.READY:
            actions.append(Action(jid, "run", describe_run(pstate), local))
    return actions, new_docs, cfg


def describe_run(pstate: PostState | None) -> str:
    if pstate in AGENT_STATES:
        return "would hand off to Claude Code (research/select/draft/humanize) → BLOCKED"
    if pstate == P.NEEDS_INPUT:
        return "would block: owner input needed"
    if pstate in {P.HUMANIZED, P.QA_PASSED, P.DUPLICATE_CHECKED}:
        return "would run QA → duplicate check → approval artifact; stops at human approval"
    if pstate in DONE_STATES:
        return "post already at/after human approval → job done; nothing published"
    if pstate == P.REJECTED:
        return "post rejected by human → skip"
    return "would inspect post"


# ── execution ─────────────────────────────────────────────────────────
def _classify(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, OSError):
        return "filesystem", True
    if isinstance(exc, (StoreError, ScheduleError)):
        return "validation", False
    return f"internal:{type(exc).__name__}", True


def execute_job(store: DataStore, job: dict, inv: str, cfg: dict) -> dict:
    """Run the deterministic steps for one READY job. Never approves or publishes."""
    from lce import images
    from lce.approval import prepare
    from lce.dupcheck import run_dupcheck
    from lce.qa import run_qa

    acquire_lease(job, "scheduler", inv, cfg["lease_minutes"])
    transition(store, job, J.RUNNING, "execution started", inv)
    try:
        post = _post(store, job)
        if post is None:
            return transition(store, job, J.BLOCKED, "no post yet: research/select/draft needed",
                              inv, blocked_reason="awaiting_agent")
        problem = post_consistency(store, post)
        if problem:
            record_error(store, job, "inconsistent_post", problem, False, inv)
            return transition(store, job, J.NEEDS_RECONCILE, problem, inv)
        pid, state = post["post_id"], P(post["state"])
        if state == P.REJECTED:
            return transition(store, job, J.SKIPPED, "rejected_by_human", inv)
        if state == P.NEEDS_INPUT:
            return transition(store, job, J.BLOCKED, "post needs owner input", inv,
                              blocked_reason="needs_input")
        if state in AGENT_STATES:
            reason = "awaiting_revision" if state == P.NEEDS_REVISION else "awaiting_agent"
            return transition(store, job, J.BLOCKED, f"post is {state.value}", inv,
                              blocked_reason=reason)
        if state == P.HUMANIZED:
            record_step(store, job, "qa", "started", inv)
            report = run_qa(store, pid)
            record_step(store, job, "qa", "done", inv, report["status"])
            if report["status"] != "passed":
                return _revision(store, job, "qa_failed", inv, cfg)
            state = P.QA_PASSED
        if state == P.QA_PASSED:
            record_step(store, job, "duplicate_check", "started", inv)
            report = run_dupcheck(store, pid)
            record_step(store, job, "duplicate_check", "done", inv, report["status"])
            if report["status"] != "passed":
                return _revision(store, job, "duplicate_failed", inv, cfg)
            state = P.DUPLICATE_CHECKED
        if state == P.DUPLICATE_CHECKED:
            if images.load(store, pid) is None:
                return transition(store, job, J.BLOCKED, "image decision needed", inv,
                                  blocked_reason="awaiting_agent")
            img_errors, _ = images.check(store, pid)
            if img_errors:
                return transition(store, job, J.BLOCKED, "image: " + "; ".join(img_errors), inv,
                                  blocked_reason="needs_input")
            record_step(store, job, "approval_artifact", "started", inv)
            prepare(store, pid)
            record_step(store, job, "approval_artifact", "done", inv, "AWAITING_APPROVAL")
            state = P.AWAITING_APPROVAL
        if state in DONE_STATES:
            job["outcome"] = ("awaiting human approval" if state == P.AWAITING_APPROVAL
                              else f"post {state.value}; publication not implemented")
            return transition(store, job, J.SUCCEEDED, job["outcome"], inv)
        record_error(store, job, "unexpected_post_state", state.value, False, inv)
        return transition(store, job, J.NEEDS_RECONCILE, f"unexpected post state {state.value}",
                           inv)
    except Exception as exc:  # recorded and surfaced, never swallowed silently
        kind, retryable = _classify(exc)
        job = load_job(store, job["job_id"]) | {"lease": job.get("lease")}
        if job["state"] != J.RUNNING.value:
            raise
        job["attempts"] += 1
        record_error(store, job, kind, f"{type(exc).__name__}: {exc}", retryable, inv)
        if retryable and job["attempts"] < job["max_attempts"]:
            job["next_attempt_at"] = iso_utc(clock.now() + timedelta(
                minutes=cfg["retry_delay_minutes"]))
        else:
            job["last_error"]["retryable"] = False
        return transition(store, job, J.FAILED, kind, inv)


def _revision(store: DataStore, job: dict, kind: str, inv: str, cfg: dict) -> dict:
    job["revisions"] += 1
    if job["revisions"] > cfg["max_revisions"]:
        record_error(store, job, kind, f"{kind} after {job['revisions']} revisions", False, inv)
        return transition(store, job, J.FAILED, kind, inv)
    return transition(store, job, J.BLOCKED, kind, inv, blocked_reason="awaiting_revision")


def reconcile_auto(store: DataStore, job: dict, inv: str) -> dict:
    """Decide an expired RUNNING lease from recorded state; ambiguous → NEEDS_RECONCILE."""
    post = _post(store, job)
    holder = (job.get("lease") or {}).get("holder", "?")
    if post is not None:
        problem = post_consistency(store, post)
        if problem:
            record_error(store, job, "ambiguous", problem, False, inv)
            return transition(store, job, J.NEEDS_RECONCILE, f"lease expired; {problem}", inv)
    open_steps = [s["name"] for s in job["steps"] if s["status"] == "started"
                  and not any(d["name"] == s["name"] and d["status"] == "done"
                              and d["at"] >= s["at"] for d in job["steps"])]
    note = f"lease of {holder} expired"
    if open_steps:
        note += f"; interrupted step {open_steps[-1]} re-runs (post state is consistent)"
    return transition(store, job, J.READY, note, inv)


# ── one pass ──────────────────────────────────────────────────────────
def run_once(store: DataStore, *, dry_run: bool = False, now=None) -> Report:
    if now is not None and not dry_run:
        raise ValueError("a simulated time is only allowed with dry_run")
    at = now or clock.now()
    inv = new_invocation_id() if not dry_run else "dry-run"
    report = Report(inv, iso_utc(at), dry_run)
    try:
        actions, new_docs, cfg = plan(store, at)
    except (ScheduleError, StoreError) as exc:
        report.status, report.message = "config_invalid", str(exc)
        if not dry_run:
            store.log_event("scheduler.config_invalid", invocation_id=inv, message=str(exc)[:300])
        return report
    report.actions = actions
    if dry_run:
        return report

    ok, holder = acquire_lock(store, inv, cfg["lock_minutes"])
    if not ok:
        report.status = "locked"
        report.message = f"another scheduler run holds the lock ({holder.get('invocation_id')})"
        store.log_event("scheduler.locked", invocation_id=inv, holder=holder.get("invocation_id"))
        return report
    store.log_event("scheduler.start", invocation_id=inv, planned=len(actions))
    try:
        # Re-plan under the lock: state may have changed since the unlocked plan.
        actions, new_docs, cfg = plan(store, at)
        report.actions = actions
        for doc in new_docs:
            doc["created_by"] = inv
            doc["history"][0]["invocation_id"] = inv
            if create_job(store, doc):
                store.log_event("job.created", job_id=doc["job_id"], slot_id=doc["slot"]["slot_id"],
                                slot_utc=doc["slot"]["utc"], prepare_from=doc["prepare_from"],
                                invocation_id=inv)
        for a in actions:
            if a.action == "create":
                continue
            job = load_job(store, a.job_id)
            if a.action == "reconcile":
                job = reconcile_auto(store, job, inv)
            elif a.action == "ready":
                job = transition(store, job, J.READY, a.detail, inv)
            elif a.action == "retry":
                job.pop("next_attempt_at", None)
                job = transition(store, job, J.READY, f"automatic retry ({a.detail})", inv)
            elif a.action == "follow_post":
                job = transition(store, job, J.READY, a.detail, inv)
            elif a.action == "skip_missed":
                job = transition(store, job, J.SKIPPED, "missed_slot", inv)
            elif a.action == "run" and job["state"] == J.READY.value:
                job = execute_job(store, job, inv, cfg)
            report.results.append({"job_id": a.job_id, "action": a.action,
                                   "state": job["state"],
                                   "blocked_reason": job.get("blocked_reason")})
    finally:
        release_lock(store, inv)
        store.log_event("scheduler.finish", invocation_id=inv, actions=len(report.actions),
                        results=len(report.results))
    return report


# ── manual operations ─────────────────────────────────────────────────
def claim(store: DataStore, job_id: str, holder: str) -> dict:
    """Lease a job for agent work (Claude Code). Only READY or BLOCKED jobs."""
    cfg = automation_config(store)
    job = load_job(store, job_id)
    if job["state"] not in {J.READY.value, J.BLOCKED.value}:
        raise StoreError(f"only READY or BLOCKED jobs can be claimed; {job_id} is {job['state']}")
    inv = new_invocation_id()
    acquire_lease(job, f"agent:{holder}", inv, cfg["lease_minutes"])
    job = transition(store, job, J.RUNNING, f"claimed by agent:{holder}", inv)
    record_step(store, job, "agent_work", "started", inv, holder)
    return job


def release(store: DataStore, job_id: str, note: str = "") -> dict:
    """End agent work; the next scheduler pass continues deterministically."""
    job = load_job(store, job_id)
    lease = job.get("lease") or {}
    if job["state"] != J.RUNNING.value or not str(lease.get("holder", "")).startswith("agent:"):
        raise StoreError(f"{job_id} is not claimed by an agent")
    inv = lease["invocation_id"]
    record_step(store, job, "agent_work", "done", inv, note)
    return transition(store, job, J.READY, f"released by {lease['holder']}", inv)


def link_post(store: DataStore, job_id: str, post_id: str) -> dict:
    job = load_job(store, job_id)
    store.load_post(post_id)  # must exist
    if job.get("post_id") and job["post_id"] != post_id:
        raise StoreError(f"{job_id} already drives post {job['post_id']}")
    for other in list_jobs(store):
        if other["job_id"] != job_id and other.get("post_id") == post_id:
            raise StoreError(f"post {post_id} is already linked to {other['job_id']}")
    job["post_id"] = post_id
    save_job(store, job)
    store.log_event("job.linked", job_id=job_id, post_id=post_id)
    return job


def retry(store: DataStore, job_id: str) -> dict:
    """Manual retry of a FAILED job (owner decision; any failure kind)."""
    job = load_job(store, job_id)
    if job["state"] != J.FAILED.value:
        raise StoreError(f"only FAILED jobs can be retried; {job_id} is {job['state']}")
    job.pop("next_attempt_at", None)
    return transition(store, job, J.READY, "manual retry", new_invocation_id())


def skip(store: DataStore, job_id: str, reason: str) -> dict:
    job = load_job(store, job_id)
    if job["state"] in {J.SKIPPED.value, J.SUCCEEDED.value}:
        raise StoreError(f"{job_id} is already {job['state']}")
    if job["state"] == J.RUNNING.value and not lease_expired(job):
        raise StoreError(f"{job_id} is running; wait for it or its lease to expire")
    return transition(store, job, J.SKIPPED, f"manual: {reason}", new_invocation_id())


def reconcile(store: DataStore, job_id: str, decision: str | None = None) -> dict:
    """Resolve NEEDS_RECONCILE: automatic when the data proves the answer, else a decision."""
    job = load_job(store, job_id)
    inv = new_invocation_id()
    if job["state"] == J.RUNNING.value and lease_expired(job):
        return reconcile_auto(store, job, inv)
    if job["state"] != J.NEEDS_RECONCILE.value:
        raise StoreError(f"{job_id} does not need reconciliation ({job['state']})")
    post = _post(store, job)
    problem = post_consistency(store, post) if post is not None else None
    if decision is None:
        if problem is None:
            return transition(store, job, J.READY, "reconciled: recorded state is consistent", inv)
        raise StoreError(f"still ambiguous ({problem}); pass --decision retry|skip|fail")
    if decision == "retry":
        if problem:
            raise StoreError(f"cannot retry while inconsistent: {problem}")
        return transition(store, job, J.READY, "reconciled by owner: retry", inv)
    if decision == "skip":
        return transition(store, job, J.SKIPPED, "reconciled by owner: skip", inv)
    if decision == "fail":
        record_error(store, job, "reconciled_as_failed", problem or "owner decision", False, inv)
        return transition(store, job, J.FAILED, "reconciled by owner: fail", inv)
    raise StoreError("decision must be retry, skip or fail")


def pending_agent_tasks(store: DataStore) -> list[dict]:
    return [j for j in list_jobs(store)
            if j["state"] == J.BLOCKED.value and j.get("blocked_reason") in
            {"awaiting_agent", "awaiting_revision"}]
