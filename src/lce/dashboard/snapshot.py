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
    ("research", "Research", "available"),
    ("planning", "Planning", "available"),
    ("draft", "Draft", "available"),
    ("humanize", "Humanize", "available"),
    ("qa", "QA", "available"),
    ("duplicate", "Duplicate check", "available"),
    ("approval", "Approval", "available"),
    ("publishing", "Publishing", "not_implemented"),
    ("verification", "Verification", "not_implemented"),
    ("analytics", "Analytics", "not_implemented"),
]
STAGE_OF_STATE = {
    "RESEARCHED": "research", "SELECTED": "planning", "NEEDS_INPUT": "planning",
    "DRAFTED": "draft", "HUMANIZED": "humanize", "NEEDS_REVISION": "humanize",
    "QA_PASSED": "qa", "DUPLICATE_CHECKED": "duplicate", "AWAITING_APPROVAL": "approval",
    "APPROVED": "approval", "READY_TO_PUBLISH": "approval",
}
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
        "publication_status": (entry or {}).get("publication_status", "not_published"),
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
        "candidate_id": meta.get("candidate_id"),
        "history": meta.get("history", []),
        "text": text,
        "draft": draft,
        "has_approval_artifact": (folder / "APPROVAL.md").exists(),
        "calendar_entry": entry,
    }


def detect_issues(store: DataStore, posts: list[dict], calendar: list[dict],
                  events: list[dict], bad_lines: list[dict], validation: dict) -> list[dict]:
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
        if state in {"APPROVED", "READY_TO_PUBLISH"}:
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
    research = sorted(store.candidates().values(), key=lambda c: str(c.get("created_at", "")))
    status = interview.status(store)
    stage_counts: dict[str, int] = {}
    for p in posts:
        if p["stage"]:
            stage_counts[p["stage"]] = stage_counts.get(p["stage"], 0) + 1
    if mode == "real":
        repo = git_info(store.root)
        label = data_label or str(store.root)
    else:
        # Never expose local filesystem paths or git remotes in a demo build.
        repo = {"available": False, "head": None, "branch": None, "remote": None,
                "dirty_files": None}
        label = data_label or "fictional demo data"
    issues = detect_issues(store, posts, calendar, events, bad, validation)
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
        "pipeline": [{"id": sid, "label": label_, "status": st,
                      "posts": stage_counts.get(sid, 0)} for sid, label_, st in PIPELINE],
        "posts": posts,
        "research": research,
        "calendar": calendar,
        "runs": events,
        "issues": issues,
        "settings": safe_settings(store),
        "publishing": {
            "provider": store.settings().get("publisher", {}).get("provider"),
            "provider_configured": False,
            "linkedin_access": "not_configured",
            "capability": "not_implemented",
            "posts": [{"post_id": p["post_id"], "state": p["state"],
                       "publication_status": p["publication_status"]} for p in posts],
        },
        "analytics": {"available": False,
                      "reason": "No publication data exists; publishing is not implemented."},
    }
    return redact(snapshot)


def to_json(snapshot: dict) -> str:
    return json.dumps(snapshot, ensure_ascii=False, indent=1, default=str)
