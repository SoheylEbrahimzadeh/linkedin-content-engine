"""Read-only snapshot of a data directory for the Web Control Center.

The snapshot is built from the files on disk every time it is requested. It
never writes, never reads `.env` or credential files, uses an allowlist for
settings, and runs a final secret-redaction pass. Values that are not present
stay `None`; nothing is inferred or invented.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from lce import __version__
from lce.config.paths import find_engine_root
from lce.posts import PLAN_STATUS
from lce.state import PostState
from lce.store import DataStore, now_iso
from lce.textutil import content_hash

SCHEMA_VERSION = 1

# Stages of the target architecture and what Phase 1 actually implements.
PIPELINE = [
    ("scheduling", "Scheduling", "available"),
    ("research", "Research", "available"),
    ("planning", "Planning", "available"),
    ("draft", "Draft", "available"),
    ("humanize", "Humanize", "available"),
    ("qa", "QA", "available"),
    ("duplicate", "Duplicate check", "available"),
    ("approval", "Approval", "available"),
    ("publishing", "Publishing", "manual"),  # after human approval: `lce publish` or a consented cloud slot
    # API 201 + post URN, or the owner's reconcile; LinkedIn grants no read-back
    ("verification", "Verification", "available"),
    ("analytics", "Analytics", "manual"),  # owner-supplied metrics (`lce analytics`)
]
STAGE_OF_STATE = {
    "RESEARCHED": "research", "SELECTED": "planning", "NEEDS_INPUT": "planning",
    "DRAFTED": "draft", "HUMANIZED": "humanize", "NEEDS_REVISION": "humanize",
    "QA_PASSED": "qa", "DUPLICATE_CHECKED": "duplicate", "AWAITING_APPROVAL": "approval",
    "APPROVED": "approval", "READY_TO_PUBLISH": "approval", "PUBLISHING": "publishing",
    "PUBLISHED": "publishing", "PUBLISH_FAILED": "publishing", "NEEDS_RECONCILE": "publishing",
}
PUBLICATION_STATUS = {"READY_TO_PUBLISH": "ready_to_publish", "PUBLISHING": "publishing",
                      "PUBLISHED": "published", "PUBLISH_FAILED": "not_published",
                      "NEEDS_RECONCILE": "unknown"}
PUBLISH_STATES = {"PUBLISHING", "PUBLISHED", "PUBLISH_FAILED", "NEEDS_RECONCILE"}
# Where posts are now: one bucket per state group of the real state model.
# Publishing/Published have no state yet; they are listed as not implemented.
STATE_BUCKETS = [
    ("research", "Research", ("RESEARCHED",)),
    ("planning", "Planning", ("SELECTED",)),
    ("needs_input", "Needs input", ("NEEDS_INPUT",)),
    ("draft", "Draft", ("DRAFTED",)),
    ("humanize", "Humanize", ("HUMANIZED",)),
    ("needs_revision", "Needs revision", ("NEEDS_REVISION",)),
    ("qa", "QA passed", ("QA_PASSED",)),
    ("duplicate", "Duplicate-checked", ("DUPLICATE_CHECKED",)),
    ("awaiting_approval", "Awaiting approval", ("AWAITING_APPROVAL",)),
    ("approved", "Approved", ("APPROVED",)),
    ("ready", "Ready to publish", ("READY_TO_PUBLISH",)),
    ("rejected", "Rejected", ("REJECTED",)),
    ("publishing", "Publishing", ("PUBLISHING",)),
    ("published", "Published", ("PUBLISHED",)),
    ("publish_failed", "Publish failed", ("PUBLISH_FAILED",)),
    ("needs_reconcile", "Needs reconcile", ("NEEDS_RECONCILE",)),
]
# Stages a post passes through, and the state that proves each one was reached.
RUN_STAGES = [
    ("research", "Research", "RESEARCHED"),
    ("planning", "Planning", "SELECTED"),
    ("draft", "Draft", "DRAFTED"),
    ("humanize", "Humanize", "HUMANIZED"),
    ("qa", "QA", "QA_PASSED"),
    ("duplicate", "Duplicate check", "DUPLICATE_CHECKED"),
    ("approval", "Approval", "APPROVED"),
]
EDIT_STATES = {"DRAFTED", "HUMANIZED"}

# Credential-like strings that must never reach the browser, even if they
# somehow ended up in a data file.
SECRET_RE = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{10,}|\bsk_(?:live|test)?_?[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{30,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{30,}|\bAKIA[0-9A-Z]{16}\b|\bpf_live_[A-Za-z0-9]{16,}"
    r"|\bapify_api_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b(?:AQ|AQV)[A-Za-z0-9_-]{60,}|\bBearer\s+[A-Za-z0-9._-]{20,}"
)
CRED_URL_RE = re.compile(r"(https?://)[^/\s:@]+(?::[^/\s@]*)?@")


def _git(cwd: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                             timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def git_info(path: Path) -> dict:
    head = _git(path, "rev-parse", "HEAD")
    if head is None:
        return {"available": False, "head": None, "branch": None, "remote": None,
                "dirty_files": None}
    porcelain = _git(path, "status", "--porcelain") or ""
    remote = _git(path, "remote", "get-url", "origin")
    return {
        "available": True,
        "head": head,
        "branch": _git(path, "branch", "--show-current") or None,
        "remote": CRED_URL_RE.sub(r"\1", remote) if remote else None,
        "dirty_files": len([x for x in porcelain.splitlines() if x.strip()]),
    }


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_runs(store: DataStore) -> tuple[list[dict], list[dict]]:
    """All run events (oldest first) and unreadable lines."""
    events, bad = [], []
    for path in sorted((store.root / "runs").glob("*.jsonl")):
        for no, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                bad.append({"file": path.name, "line": no})
                continue
            if isinstance(ev, dict):
                events.append(ev)
    events.sort(key=lambda e: str(e.get("at", "")))
    return events, bad


def _image_view(store: DataStore, pid: str) -> dict | None:
    from lce import images

    doc = images.load(store, pid)
    if doc is None:
        return None
    errors, warnings = images.check(store, pid)
    return {"kind": doc["kind"], "rationale": doc.get("rationale"), "relation": doc.get("relation"),
            "alt_text": doc.get("alt_text"), "file": doc.get("file"), "sha256": doc.get("sha256"),
            "provenance": doc.get("provenance"), "errors": errors, "warnings": warnings}


def _post_view(store: DataStore, pid: str, calendar_by_ref: dict, events: list[dict],
               pillar_names: dict) -> dict:
    meta = store.load_post(pid)
    folder = store.post_dir(pid)
    text = store.post_text(pid, "post.md")
    draft = store.post_text(pid, "draft.md")
    entry = calendar_by_ref.get(pid)
    approval = meta.get("approval") or {}
    actual_hash = content_hash(text) if text is not None else None
    return {
        "post_id": pid,
        "topic": meta.get("topic"),
        "plan_date": meta.get("plan_date"),
        "created_at": meta.get("created_at"),
        "pillar": meta.get("pillar"),
        "pillar_name": pillar_names.get(meta.get("pillar")),
        "format": meta.get("format"),
        "angle": meta.get("angle"),
        "language": meta.get("language"),
        "state": meta["state"],
        "stage": STAGE_OF_STATE.get(meta["state"]),
        "approval": {
            "state": approval.get("state"),
            "approved_hash": approval.get("approved_hash"),
            "approved_at": approval.get("approved_at"),
            "approved_by": approval.get("approved_by"),
            "reason": approval.get("reason"),
        },
        "approval_events": [e for e in events
                            if e.get("event") == "approval" and e.get("post_id") == pid],
        # Phase 1 has no publisher: nothing can be published.
        "publication_status": PUBLICATION_STATUS.get(
            meta["state"], (entry or {}).get("publication_status", "not_published")),
        "publication": _read_json(folder / "publication.json"),
        "content_hash": meta.get("content_hash"),
        "actual_hash": actual_hash,
        "draft_hash": meta.get("draft_hash"),
        "qa": meta.get("qa"),
        "qa_report": _read_json(folder / "qa.json"),
        "duplicate": meta.get("duplicate"),
        "duplicate_report": _read_json(folder / "duplicate.json"),
        "sources": meta.get("sources", []),
        "claims": meta.get("claims", []),
        "stories_used": meta.get("stories_used", []),
        "brand": meta.get("brand"),
        "image": _image_view(store, pid),
        "has_metrics": (folder / "metrics.yaml").exists(),
        "candidate_id": meta.get("candidate_id"),
        "history": meta.get("history", []),
        "text": text,
        "draft": draft,
        "has_approval_artifact": (folder / "APPROVAL.md").exists(),
        "calendar_entry": entry,
    }


def state_distribution(posts: list[dict]) -> list[dict]:
    """How many posts are in each state right now (real counts only)."""
    counts: dict[str, int] = {}
    for p in posts:
        counts[p["state"]] = counts.get(p["state"], 0) + 1
    known = {s for _, _, states in STATE_BUCKETS for s in states}
    out = [{"id": bid, "label": label, "states": list(states), "implemented": bool(states),
            "count": sum(counts.get(s, 0) for s in states)} for bid, label, states in STATE_BUCKETS]
    for state in sorted(set(counts) - known):  # never hide a state we do not know
        out.append({"id": state.lower(), "label": state, "states": [state], "implemented": True,
                    "count": counts[state]})
    return out


def latest_run(posts: list[dict]) -> dict | None:
    """Stages the most recently active post actually passed through, from its history.

    Stages after the last text edit (QA, duplicate check, approval) only count if
    they happened for the current version of the text.
    """
    def last_at(p: dict) -> str:
        return max((str(h.get("at", "")) for h in p.get("history", [])), default="")

    candidates = [p for p in posts if p.get("history")]
    if not candidates:
        return None
    post = max(candidates, key=lambda p: (last_at(p), p["post_id"]))
    history = post["history"]
    states = [h.get("state") for h in history]
    last_edit = max((i for i, s in enumerate(states) if s in EDIT_STATES), default=-1)
    current = post["state"]
    stages = []
    for sid, label, proof in RUN_STAGES:
        after_edit = sid in {"qa", "duplicate", "approval"}
        start = last_edit + 1 if after_edit else 0
        hits = [h for h in history[start:] if h.get("state") == proof]
        if sid == "approval" and not hits and current == "READY_TO_PUBLISH":
            hits = [h for h in history[start:] if h.get("state") == "READY_TO_PUBLISH"]
        if hits:
            stage = {"id": sid, "label": label, "status": "done", "at": hits[-1].get("at")}
        else:
            stage = {"id": sid, "label": label, "status": "not_reached", "at": None}
        stages.append(stage)
    by_id = {s["id"]: s for s in stages}
    if current == "NEEDS_REVISION":
        failed = "qa" if (post.get("qa") or {}).get("status") == "failed" else "duplicate"
        by_id[failed]["status"] = "failed"
    elif current == "NEEDS_INPUT":
        by_id["planning"]["status"] = "needs_input"
    elif current == "AWAITING_APPROVAL":
        by_id["approval"]["status"] = "waiting"
    elif current == "REJECTED":
        by_id["approval"]["status"] = "rejected"
    pub = [h for h in history if h.get("state") in PUBLISH_STATES]
    pub_status = {"PUBLISHED": "done", "PUBLISH_FAILED": "failed",
                  "NEEDS_RECONCILE": "needs_reconcile", "PUBLISHING": "running"}.get(current)
    stages.append({"id": "publishing", "label": "Publishing",
                   "status": pub_status or "not_reached", "at": pub[-1].get("at") if pub else None})
    record = post.get("publication") or {}
    verified = current == "PUBLISHED" and record.get("verified_by")
    stages.append({"id": "verification", "label": "Verification",
                   "status": "done" if verified else "needs_reconcile" if current == "NEEDS_RECONCILE"
                   else "not_reached",
                   "at": record.get("published_at") if verified else None})
    stages.append({"id": "analytics", "label": "Analytics",
                   "status": "done" if post.get("has_metrics") else "not_reached", "at": None})
    return {
        "post_id": post["post_id"],
        "topic": post.get("topic"),
        "state": current,
        "last_activity": last_at(post),
        "text_versions": sum(1 for s in states if s == "HUMANIZED"),
        "stages": stages,
    }


def research_view(store: DataStore, posts: list[dict]) -> list[dict]:
    """Candidates with selection and claim facts taken from the data (no inferred reasons)."""
    used_by: dict[str, list[str]] = {}
    for p in posts:
        if p.get("candidate_id"):
            used_by.setdefault(p["candidate_id"], []).append(p["post_id"])
    out = []
    for c in sorted(store.candidates().values(), key=lambda c: str(c.get("created_at", ""))):
        claims = c.get("claims") or []
        out.append({**c, "selected": c.get("status") == "selected",
                    "claims_count": len(claims), "used_by_posts": used_by.get(c["candidate_id"], [])})
    return out


def automation_view(store: DataStore, posts: list[dict], events: list[dict]) -> dict:
    """Scheduler and job state straight from automation/ and config (nothing inferred)."""
    from datetime import timedelta

    from lce import clock
    from lce.jobs import automation_config, job_id_for, list_jobs, lock_path
    from lce.schedule import ScheduleError, load_schedule, slots_between

    now = clock.now()
    jobs = list_jobs(store)
    by_id = {j["job_id"]: j for j in jobs}
    posts_by_id = {p["post_id"]: p for p in posts}
    out: dict = {"now": clock.iso_utc(now), "schedule_ok": False, "schedule_error": None,
                 "config": None, "config_error": None, "upcoming": [], "next_slot": None,
                 "trigger": "none configured by the engine (manual: lce automation run-once)"}
    try:
        out["config"] = automation_config(store)
    except Exception as exc:  # shown to the owner, never hidden
        out["config_error"] = str(exc)[:300]
    try:
        schedule = load_schedule(store.settings())
        out["schedule_ok"] = True
        horizon = (out["config"] or {}).get("horizon_days", 14)
        for slot in slots_between(schedule, now, now + timedelta(days=horizon)):
            job = by_id.get(job_id_for(slot.slot_id))
            post = posts_by_id.get((job or {}).get("post_id"))
            out["upcoming"].append({
                **slot.to_dict(), "job_id": job_id_for(slot.slot_id),
                "job_state": job["state"] if job else None,
                "blocked_reason": (job or {}).get("blocked_reason"),
                "post_id": (job or {}).get("post_id"),
                "post_state": post["state"] if post else None,
                "approval_state": post["approval"]["state"] if post else None,
                "publication_status": post["publication_status"] if post else None,
            })
        out["next_slot"] = out["upcoming"][0] if out["upcoming"] else None
    except ScheduleError as exc:
        out["schedule_error"] = str(exc)
    counts: dict[str, int] = {}
    for j in jobs:
        counts[j["state"]] = counts.get(j["state"], 0) + 1
    out["counts"] = counts
    out["due"] = sum(1 for j in jobs if j["state"] == "READY" or (
        j["state"] == "SCHEDULED" and clock.parse_iso(j["prepare_from"]) <= now))
    runs = [e for e in events if e.get("event") in {"scheduler.finish", "scheduler.locked",
                                                     "scheduler.config_invalid"}]
    out["last_run"] = runs[-1] if runs else None
    lock = lock_path(store)
    out["lock"] = None
    if lock.exists():
        try:
            holder = json.loads(lock.read_text("utf-8"))
            out["lock"] = {"invocation_id": holder.get("invocation_id"),
                           "expires_at": holder.get("expires_at"),
                           "expired": clock.parse_iso(holder["expires_at"]) <= now}
        except (OSError, ValueError, KeyError):
            out["lock"] = {"invocation_id": None, "expires_at": None, "expired": True}
    out["jobs"] = [{k: j.get(k) for k in (
        "job_id", "state", "blocked_reason", "slot", "prepare_from", "post_id", "attempts",
        "max_attempts", "revisions", "next_attempt_at", "last_error", "lease", "outcome",
        "created_at", "created_by", "updated_at", "steps", "history")}
        | {"lease_expired": bool(j.get("lease")) and clock.parse_iso(
            j["lease"]["expires_at"]) <= now} for j in jobs]
    return out


def publishing_view(store: DataStore, posts: list[dict], mode: str) -> dict:
    """Publishing configuration and records. Never touches the token or the Keychain."""
    from lce import clock
    from lce.publish.base import PROVIDER_CAPABILITIES
    from lce.validate import validate_doc

    provider = store.settings().get("publisher", {}).get("provider")
    cfg_path = store.root / "config" / "linkedin.yaml"
    cfg = store.read_doc(cfg_path) if cfg_path.exists() else None
    cfg_errors = validate_doc("linkedin", cfg) if cfg else []
    view: dict = {
        "provider": provider,
        "provider_enabled": provider == "linkedin_api",
        "linkedin_config": None,
        "config_errors": cfg_errors,
        "token": {"stored_in": "macOS Keychain", "checked": False,
                  "how_to_check": "lce linkedin status", "expires_at": None, "days_left": None},
        "capabilities": None,
        "trigger": "manual only: `lce publish <post>` in an interactive terminal",
        "records": [],
    }
    caps = PROVIDER_CAPABILITIES.get("linkedin_api")
    view["capabilities"] = {k: getattr(caps, k) for k in (
        "can_publish", "can_find_existing", "can_get_status", "can_schedule", "supports_media",
        "max_chars")} | {"notes": list(caps.notes)}
    if cfg and not cfg_errors:
        view["linkedin_config"] = {k: cfg.get(k) for k in ("api_version", "visibility")}
        view["linkedin_config"]["person_urn"] = cfg.get("person_urn") if mode == "real" else None
        now = clock.now()
        if cfg.get("token_expires_at"):
            exp = clock.parse_iso(cfg["token_expires_at"])
            view["token"].update(expires_at=cfg["token_expires_at"], days_left=(exp - now).days)
        v = cfg.get("api_version", "")
        age = (now.year - int(v[:4])) * 12 + now.month - int(v[4:]) if len(v) == 6 else None
        view["linkedin_config"]["api_version_age_months"] = age
    for p in posts:
        rec = p.get("publication")
        if rec:
            view["records"].append({k: rec.get(k) for k in (
                "post_id", "state", "remote_id", "url", "published_at", "verified_by",
                "api_version")} | {"attempts": len(rec.get("attempts", [])),
                                   "last_attempt": (rec.get("attempts") or [None])[-1]})
    view["posts"] = [{"post_id": p["post_id"], "state": p["state"],
                      "publication_status": p["publication_status"]} for p in posts]
    return view


def detect_issues(store: DataStore, posts: list[dict], calendar: list[dict],
                  events: list[dict], bad_lines: list[dict], validation: dict,
                  automation: dict | None = None) -> list[dict]:
    """Deterministic consistency checks. Returns [] when everything agrees."""
    issues: list[dict] = []

    def add(kind: str, severity: str, message: str, post_id: str | None = None) -> None:
        issues.append({"kind": kind, "severity": severity, "message": message,
                       "post_id": post_id})

    for err in validation.get("errors", []):
        add("INCONSISTENT", "error", f"validation: {err}")
    for bad in bad_lines:
        add("INCONSISTENT", "warning", f"unreadable run log line {bad['file']}:{bad['line']}")
    for p in posts:
        pid, state = p["post_id"], p["state"]
        if state == "FAILED":
            add("FAILED", "error", "post is in FAILED state", pid)
        if state == "PUBLISH_FAILED":
            add("FAILED", "warning", "publish attempt failed (not created on LinkedIn); "
                "run `lce publish` again or reject", pid)
        if state == "PUBLISHING":
            add("NEEDS_RECONCILE", "error", "publish attempt was interrupted; "
                "run `lce publish reconcile`", pid)
        if state == "NEEDS_RECONCILE":
            add("NEEDS_RECONCILE", "error", "post needs reconciliation", pid)
        if p["content_hash"] and p["actual_hash"] and p["content_hash"] != p["actual_hash"]:
            add("NEEDS_RECONCILE", "error", "post.md does not match the recorded content hash",
                pid)
        if state in {"QA_PASSED", "DUPLICATE_CHECKED", "AWAITING_APPROVAL", "APPROVED",
                     "READY_TO_PUBLISH"}:
            if (p["qa"] or {}).get("content_hash") != p["content_hash"]:
                add("INCONSISTENT", "error", "QA result does not belong to the current text", pid)
        if state in {"DUPLICATE_CHECKED", "AWAITING_APPROVAL", "APPROVED", "READY_TO_PUBLISH"}:
            if (p["duplicate"] or {}).get("content_hash") != p["content_hash"]:
                add("INCONSISTENT", "error",
                    "duplicate check does not belong to the current text", pid)
        if state in {"APPROVED", "READY_TO_PUBLISH"} | PUBLISH_STATES:
            if p["approval"]["approved_hash"] != p["actual_hash"]:
                add("NEEDS_RECONCILE", "error", "approved hash differs from the current text", pid)
            if not p["approval_events"]:
                add("INCONSISTENT", "warning", "no approval event in the run log", pid)
        if state == "AWAITING_APPROVAL" and not p["has_approval_artifact"]:
            add("INCONSISTENT", "error", "awaiting approval but APPROVAL.md is missing", pid)
        entry = p["calendar_entry"]
        if entry is not None:
            expected = PLAN_STATUS.get(PostState(state)) if state in PostState.__members__ else None
            if expected and entry.get("status") != expected:
                add("INCONSISTENT", "warning",
                    f"calendar status {entry.get('status')!r} but post is {state}", pid)
        last_state = next((e.get("state") for e in reversed(events)
                           if e.get("event") == "state" and e.get("post_id") == pid), None)
        if last_state and last_state != state:
            add("INCONSISTENT", "warning",
                f"last logged state {last_state} differs from post state {state}", pid)
    known = {p["post_id"] for p in posts}
    for entry in calendar:
        ref = entry.get("draft_ref")
        if ref and ref not in known:
            add("INCONSISTENT", "warning", f"calendar references missing post {ref}")
    for j in (automation or {}).get("jobs", []):
        jid = j["job_id"]
        if j["state"] == "FAILED":
            err = j.get("last_error") or {}
            add("FAILED", "error", f"job {jid} failed ({err.get('kind', 'unknown')}"
                f"{', retry scheduled' if err.get('retryable') else ''})")
        elif j["state"] == "NEEDS_RECONCILE":
            add("NEEDS_RECONCILE", "error", f"job {jid} needs reconciliation", j.get("post_id"))
        elif j["state"] == "RUNNING" and j["lease_expired"]:
            add("NEEDS_RECONCILE", "warning",
                f"job {jid} lease expired while RUNNING; the next scheduler pass reconciles it")
        if j.get("post_id") and j["post_id"] not in known:
            add("NEEDS_RECONCILE", "error", f"job {jid} links to missing post {j['post_id']}")
    lock = (automation or {}).get("lock")
    if lock and lock.get("expired"):
        add("INCONSISTENT", "warning", "stale scheduler lock (a run was interrupted); "
            "the next pass takes it over")
    for e in events:
        if e.get("event") == "research.fetch" and e.get("errors"):
            add("FAILED", "warning", f"feed fetch had {e['errors']} error(s) at {e.get('at')}")
    return issues


def safe_settings(store: DataStore) -> dict:
    """Allowlisted configuration only. Unknown values stay None."""
    s, v, prof = store.settings(), store.voice(), store.profile()
    tuning = store.tuning()
    feeds = s.get("research", {}).get("feeds", [])
    return {
        "timezone": s.get("timezone"),
        "cadence": s.get("cadence"),
        "approval_mode": s.get("approval", {}).get("mode"),
        "publisher_provider": s.get("publisher", {}).get("provider"),
        "llm_runtime": s.get("llm", {}).get("runtime"),
        "research": {
            "feeds": [{"name": f.get("name"), "url": f.get("url")} for f in feeds],
            "method": ("configured RSS/Atom feeds + manual/web_search candidates via Claude Code"
                       if feeds else "manual/web_search candidates via Claude Code (no feeds)"),
        },
        "duplicates": tuning.get("duplicates"),
        "voice": {
            "language": v.get("language"),
            "formality": v.get("formality"),
            "tone": v.get("tone"),
            "emoji_max": v.get("emoji_policy", {}).get("max_per_post"),
            "hashtag_max": v.get("hashtag_policy", {}).get("max"),
            "hashtag_placement": v.get("hashtag_policy", {}).get("placement"),
            "max_chars": v.get("formatting", {}).get("max_chars"),
            "bullets_allowed": v.get("formatting", {}).get("bullets_allowed"),
            "avoid_phrases": v.get("avoid_phrases"),
            "cta_allowed": v.get("cta", {}).get("allowed"),
        },
        "topics_public": prof.get("topics", {}).get("public"),
        "pillars": [{"id": p.get("id"), "name": p.get("name")} for p in prof.get("pillars", [])],
        "languages": prof.get("languages"),
    }


def redact(obj: object) -> object:
    if isinstance(obj, str):
        return SECRET_RE.sub("[redacted]", obj)
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def analytics_view(store: DataStore) -> dict:
    from lce import analytics
    from lce.clock import now

    ins = analytics.insights(store, now().date())
    return {**ins, "suggestion": analytics.suggest_mix(store)}


def brand_view(store: DataStore, posts: list[dict], mode: str) -> dict:
    """Brand strategy status. Demo data is dated, so the demo uses its latest plan date."""
    from datetime import date
    from zoneinfo import ZoneInfo

    from lce import brand
    from lce.clock import now

    if mode == "demo" and posts:
        today = max(date.fromisoformat(p["plan_date"]) for p in posts if p.get("plan_date"))
    else:
        today = now().astimezone(ZoneInfo(store.settings().get("timezone") or "UTC")).date()
    doc = store.brand()
    return {**brand.status(store, today), "as_of": today.isoformat(),
            "objective": doc.get("objective"),
            "throughline": (doc.get("narrative") or {}).get("throughline"),
            "next": brand.recommend(store, today)}


def build_snapshot(store: DataStore, *, mode: str, data_label: str | None = None) -> dict:
    """Collect everything the dashboard shows. `mode` is 'real' or 'demo'."""
    if mode not in {"real", "demo"}:
        raise ValueError("mode must be 'real' or 'demo'")
    from lce import interview
    from lce.validate import validate_dir

    engine_root = find_engine_root(Path(__file__).resolve().parent)
    engine = {"version": __version__, "commit": None, "dirty_files": None}
    if engine_root is not None:
        gi = git_info(engine_root)
        engine.update(commit=gi["head"], dirty_files=gi["dirty_files"], branch=gi["branch"])

    checked, errors = validate_dir(store.root)
    validation = {"status": "passed" if not errors else "failed", "files": checked,
                  "errors": [e.render().strip() for e in errors]}
    events, bad = read_runs(store)
    plan = store.plan().get("entries", [])
    calendar_by_ref = {e["draft_ref"]: e for e in plan if e.get("draft_ref")}
    pillar_names = {p.get("id"): p.get("name") for p in store.profile().get("pillars", [])}
    posts = [_post_view(store, pid, calendar_by_ref, events, pillar_names)
             for pid in store.post_ids()]
    posts.sort(key=lambda p: (p.get("plan_date") or "", p["post_id"]))
    states = {p["post_id"]: p["state"] for p in posts}
    calendar = [{**e, "post_state": states.get(e.get("draft_ref"))} for e in plan]
    calendar.sort(key=lambda e: str(e.get("date", "")))
    research = research_view(store, posts)
    status = interview.status(store)
    if mode == "real":
        repo = git_info(store.root)
        label = data_label or str(store.root)
    else:
        # Never expose local filesystem paths or git remotes in a demo build.
        repo = {"available": False, "head": None, "branch": None, "remote": None,
                "dirty_files": None}
        label = data_label or "fictional demo data"
    automation = automation_view(store, posts, events)
    issues = detect_issues(store, posts, calendar, events, bad, validation, automation)
    snapshot = {
        "schema": SCHEMA_VERSION,
        "meta": {"mode": mode, "generated_at": now_iso(), "engine": engine,
                 "data": {"label": label, "git": repo}},
        "health": {
            "validation": validation,
            "last_run": events[-1] if events else None,
            "interview": {
                "answered": sum(1 for _, done in status if done),
                "total": len(status),
                "required_missing": [q.id for q in interview.ready_for_drafting(store)],
            },
            "stories": {"total": len(store.stories()),
                        "public": sum(1 for s in store.stories().values()
                                      if s.get("publication_status") == "PUBLIC")},
        },
        # Capabilities of the system (not post counts).
        "pipeline": [{"id": sid, "label": label_, "status": st} for sid, label_, st in PIPELINE],
        "state_distribution": state_distribution(posts),
        "latest_run": latest_run(posts),
        "posts": posts,
        "research": research,
        "calendar": calendar,
        "runs": events,
        "issues": issues,
        "settings": safe_settings(store),
        "publishing": publishing_view(store, posts, mode),
        "automation": automation,
        "brand": brand_view(store, posts, mode),
        "analytics": analytics_view(store),
    }
    return redact(snapshot)


def to_json(snapshot: dict) -> str:
    return json.dumps(snapshot, ensure_ascii=False, indent=1, default=str)
