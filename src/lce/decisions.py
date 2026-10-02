"""Apply owner decisions recorded in the cloud Control Center (LCE-036).

The Worker only records a decision (person signed in through Cloudflare Access,
typed phrase for approve/reject, approve bound to the full hash of the text the
owner reviewed). This module applies each pending decision to the git-tracked
pipeline in the private repository, re-checking every hash against the files,
and resolves it in the cloud as `applied` or `refused`. The private workflow
then commits the change: GitHub stays the source of truth.

Nothing here publishes. An approval ends in the cloud publisher's queue
(READY_TO_PUBLISH, delegated); scheduling and publishing stay separate,
explicit owner actions.
"""

from __future__ import annotations

import json
from datetime import date

from lce import cloud
from lce.approval import approve_recorded, mark_ready, prepare, reject
from lce.dupcheck import run_dupcheck
from lce.planning import _new_post_id
from lce.posts import current_text, reopen, save_draft, save_humanized, set_state
from lce.qa import run_qa
from lce.state import InvalidTransition, PostState
from lce.store import DataStore, StoreError, now_iso
from lce.textutil import content_hash

S = PostState
APPLIED_EVENT = "cloud.decision"


class Refused(Exception):
    """The decision cannot be applied to the current files; nothing changed."""


class Retry(Exception):
    """Applied locally but the cloud step failed (network); left pending for the next run."""


def _applied_ids(store: DataStore) -> dict[str, str]:
    out: dict[str, str] = {}
    runs = store.root / "runs"
    for path in sorted(runs.glob("*.jsonl")) if runs.exists() else []:
        for line in path.read_text("utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("event") == APPLIED_EVENT and rec.get("decision_id"):
                out[rec["decision_id"]] = rec.get("result", "")
    return out


def _delegated(store: DataStore, post_id: str) -> bool:
    return cloud.load_delegation(store, post_id) is not None


def _require_hash(store: DataStore, post_id: str, expected: str | None) -> str:
    h = content_hash(current_text(store, post_id))
    if expected and h != expected:
        raise Refused("the text in git changed since the decision; review it again")
    return h


def _checks(store: DataStore, post_id: str) -> str:
    """Deterministic QA → duplicate check → approval artifact. Never approves."""
    qa = run_qa(store, post_id)
    if qa["status"] != "passed":
        return f"QA failed ({len(qa['errors'])} errors): NEEDS_REVISION"
    dup = run_dupcheck(store, post_id)
    if dup["status"] != "passed":
        return "duplicate check failed: NEEDS_REVISION"
    try:
        prepare(store, post_id)
    except StoreError as exc:
        return f"QA and duplicate check passed; approval not prepared: {exc}"
    return "QA and duplicate check passed: AWAITING_APPROVAL (approve it in the Control Center)"


def _approve(store, client, d) -> str:
    pid = d["post_id"]
    post = store.load_post(pid)
    resumed = (post.get("approval") or {}).get("decision_id") == d["decision_id"]
    if not resumed:
        approve_recorded(store, pid, d["content_hash"], d["payload"].get("image_sha256"),
                         approver=d["created_by"], decision_id=d["decision_id"])
    if PostState(store.load_post(pid)["state"]) == S.APPROVED:
        mark_ready(store, pid)
    try:
        cloud.push(store, pid, client, decision_id=d["decision_id"])
    except cloud.CloudError as exc:
        raise Retry(f"approved locally; push to the cloud failed ({exc}); retried next run") from exc
    return "approved, READY_TO_PUBLISH and in the cloud publisher (schedule or test-publish it there)"


def _reject(store, client, d) -> str:
    pid = d["post_id"]
    if _delegated(store, pid):
        raise Refused("the post is in the cloud publisher; withdraw it there first")
    reject(store, pid, d["payload"]["reason"])
    return "rejected"


def _edit(store, client, d) -> str:
    pid = d["post_id"]
    if _delegated(store, pid):
        raise Refused("the post is in the cloud publisher; withdraw it there first")
    _require_hash(store, pid, d["content_hash"])
    post = store.load_post(pid)
    state = PostState(post["state"])
    if state in {S.AWAITING_APPROVAL, S.APPROVED, S.READY_TO_PUBLISH}:
        reopen(store, pid, "edited in the Control Center")
    elif state == S.SELECTED:
        save_draft(store, pid, d["payload"]["text"])
    save_humanized(store, pid, d["payload"]["text"], source="owner_edit",
                   by=f"cloud-access:{d['created_by']}")
    return "text replaced; " + _checks(store, pid)


def _regenerate(store, client, d) -> str:
    pid = d["post_id"]
    if _delegated(store, pid):
        raise Refused("the post is in the cloud publisher; withdraw it there first")
    post = store.load_post(pid)
    state = PostState(post["state"])
    if state in {S.AWAITING_APPROVAL, S.APPROVED, S.READY_TO_PUBLISH}:
        post = reopen(store, pid, "regeneration requested")
        state = S.HUMANIZED
    if state == S.DUPLICATE_CHECKED:
        post = set_state(store, post, S.HUMANIZED, "regeneration requested")
        state = S.HUMANIZED
    post = store.load_post(pid)
    post["regeneration"] = {"requested_at": now_iso(), "requested_by": d["created_by"],
                            "reason": d["payload"]["reason"], "decision_id": d["decision_id"]}
    store.save_post(post)
    if state in {S.HUMANIZED, S.QA_PASSED}:
        set_state(store, post, S.NEEDS_REVISION, f"regeneration requested: {d['payload']['reason']}")
    return ("flagged for rewriting (the next drafting session handles it; "
            "nothing is regenerated automatically)")


def _plan_entry(store: DataStore, post_id: str) -> tuple[dict, dict | None]:
    plan = store.plan()
    entry = next((e for e in plan.get("entries", []) if e.get("draft_ref") == post_id), None)
    return plan, entry


def _reschedule(store, client, d) -> str:
    pid, new = d["post_id"], d["plan_date"]
    if _delegated(store, pid):
        raise Refused("the post is in the cloud publisher; reschedule its slot there (revoke + schedule)")
    date.fromisoformat(new)
    post = store.load_post(pid)
    old = post.get("plan_date")
    post["plan_date"] = new
    store.save_post(post)
    plan, entry = _plan_entry(store, pid)
    if entry is not None:
        entry["date"] = new
        plan["entries"].sort(key=lambda e: str(e.get("date", "")))
        store.write_doc(store.plan_path, "plan", plan)
    store.log_event("post.rescheduled", post_id=pid, from_date=old, to_date=new)
    return f"planned date {old} → {new}"


def _skip(store, client, d) -> str:
    pid = d.get("post_id")
    reason = d["payload"].get("reason") or "skipped in the Control Center"
    if pid:
        if _delegated(store, pid):
            raise Refused("the post is in the cloud publisher; withdraw it there first")
        reject(store, pid, f"skipped: {reason}")
        plan, entry = _plan_entry(store, pid)
    else:
        plan = store.plan()
        entry = next((e for e in plan.get("entries", [])
                      if e.get("date") == d["plan_date"] and not e.get("draft_ref")), None)
        if entry is None:
            raise Refused(f"no planned entry without a post on {d['plan_date']}")
    if entry is not None:
        entry["status"] = "skipped"
        store.write_doc(store.plan_path, "plan", plan)
    return "skipped"


def _duplicate(store, client, d) -> str:
    src = store.load_post(d["post_id"])
    text = current_text(store, src["post_id"])
    when = date.fromisoformat(d["plan_date"])
    keep = ("language", "topic", "pillar", "format", "angle", "candidate_id", "sources", "claims",
            "stories_used", "brand", "objective")
    post = {k: src[k] for k in keep if k in src}
    post.update({"post_id": _new_post_id(store, when, src.get("topic") or src["post_id"]),
                 "created_at": now_iso(), "plan_date": when.isoformat(), "state": S.SELECTED.value,
                 "duplicated_from": src["post_id"],
                 "history": [{"at": now_iso(), "state": S.SELECTED.value,
                              "note": f"duplicated from {src['post_id']}"}]})
    store.save_post(post)
    plan = store.plan()
    entry = {"date": when.isoformat(), "topic": post.get("topic") or post["post_id"],
             "pillar": post.get("pillar", ""), "status": "selected", "draft_ref": post["post_id"]}
    for key in ("format", "angle", "candidate_id"):
        if post.get(key):
            entry[key] = post[key]
    plan.setdefault("entries", []).append(entry)
    plan["entries"].sort(key=lambda e: str(e.get("date", "")))
    store.write_doc(store.plan_path, "plan", plan)
    save_draft(store, post["post_id"], text)
    save_humanized(store, post["post_id"], text, source="owner_edit", by=f"cloud-access:{d['created_by']}")
    return f"copied to {post['post_id']} (HUMANIZED; edit it, then the checks run)"


HANDLERS = {"approve": _approve, "reject": _reject, "edit": _edit, "regenerate": _regenerate,
            "reschedule": _reschedule, "skip": _skip, "duplicate": _duplicate}


def pending(client: cloud.CloudClient) -> list[dict]:
    return client.call("GET", "/decisions?status=pending").get("decisions", [])


def apply_all(store: DataStore, client: cloud.CloudClient) -> list[dict]:
    """Apply every pending decision, oldest first; resolve each in the cloud."""
    done = _applied_ids(store)
    out = []
    for d in pending(client):
        did = d["decision_id"]
        if did in done:                      # applied before, resolve failed: only resolve now
            status, result = "applied", done[did] or "applied earlier"
        else:
            handler = HANDLERS.get(d.get("action"))
            try:
                if handler is None:
                    raise Refused(f"unknown action {d.get('action')!r}")
                result, status = handler(store, client, d), "applied"
                store.log_event(APPLIED_EVENT, decision_id=did, action=d["action"],
                                post_id=d.get("post_id"), result=result)
            except Retry as exc:
                store.log_event("cloud.decision_retry", decision_id=did, reason=str(exc))
                out.append({"decision_id": did, "action": d.get("action"), "post_id": d.get("post_id"),
                            "status": "pending", "result": str(exc)})
                continue
            except (Refused, StoreError, InvalidTransition, cloud.CloudError, ValueError, KeyError) as exc:
                status, result = "refused", str(exc) or type(exc).__name__
                store.log_event("cloud.decision_refused", decision_id=did, action=d.get("action"),
                                post_id=d.get("post_id"), reason=result)
        client.call("POST", f"/decisions/{did}/resolve", {"status": status, "result": result[:2000]})
        out.append({"decision_id": did, "action": d.get("action"), "post_id": d.get("post_id"),
                    "status": status, "result": result})
    return out

