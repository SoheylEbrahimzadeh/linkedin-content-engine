"""Post lifecycle operations: create, save text, change state, keep the plan in sync."""

from __future__ import annotations

from lce.state import EDITABLE, PostState, transition
from lce.store import DataStore, StoreError, now_iso
from lce.textutil import content_hash, normalize_text

S = PostState

PLAN_STATUS = {
    S.RESEARCHED: "selected",
    S.SELECTED: "selected",
    S.NEEDS_INPUT: "needs_input",
    S.DRAFTED: "in_progress",
    S.HUMANIZED: "in_progress",
    S.NEEDS_REVISION: "in_progress",
    S.QA_PASSED: "in_progress",
    S.DUPLICATE_CHECKED: "in_progress",
    S.AWAITING_APPROVAL: "awaiting_approval",
    S.APPROVED: "approved",
    S.READY_TO_PUBLISH: "ready_to_publish",
    S.PUBLISHING: "publishing",
    S.PUBLISHED: "published",
    S.PUBLISH_FAILED: "publish_failed",
    S.NEEDS_RECONCILE: "needs_reconcile",
    S.REJECTED: "rejected",
}


PUBLICATION_STATUS = {
    S.READY_TO_PUBLISH: "ready_to_publish",
    S.PUBLISHING: "publishing",
    S.PUBLISHED: "published",
    S.PUBLISH_FAILED: "not_published",
    S.NEEDS_RECONCILE: "unknown",
}


def set_state(store: DataStore, post: dict, new: PostState, note: str = "") -> dict:
    current = PostState(post["state"])
    transition(current, new)
    post["state"] = new.value
    entry = {"at": now_iso(), "state": new.value}
    if note:
        entry["note"] = note
    post.setdefault("history", []).append(entry)
    store.save_post(post)
    sync_plan(store, post)
    store.log_event("state", post_id=post["post_id"], state=new.value, note=note)
    return post


def _invalidate_checks(post: dict) -> None:
    for key in ("qa", "duplicate", "approval"):
        post.pop(key, None)


def save_draft(store: DataStore, post_id: str, text: str) -> dict:
    """Store the first draft (DRAFTED)."""
    post = store.load_post(post_id)
    state = PostState(post["state"])
    if state not in {S.SELECTED, S.DRAFTED, S.NEEDS_REVISION}:
        raise StoreError(f"cannot save a draft while the post is {state.value}")
    if not text.strip():
        raise StoreError("draft text is empty")
    store.write_text(store.post_dir(post_id) / "draft.md", normalize_text(text))
    post["draft_hash"] = content_hash(text)
    _invalidate_checks(post)
    return set_state(store, post, S.DRAFTED, "draft saved")


def save_humanized(store: DataStore, post_id: str, text: str, *, source: str = "session",
                   by: str = "pipeline session") -> dict:
    """Store the humanized candidate text (HUMANIZED). Clears QA, duplicate and approval.

    LCE-037: records what the text was written against (voice/profile/brand file
    hashes, objective, machine-checkable voice rules). A drafting session must
    set the post's objective first when voice.yaml defines objectives; an owner
    edit from the Control Center is recorded as such."""
    post = store.load_post(post_id)
    if (source == "session" and store.voice().get("objectives") and not post.get("objective")):
        raise StoreError("set the post's content objective first (voice.yaml objectives): "
                         "lce post objective <post> <objective>")
    state = PostState(post["state"])
    if state not in EDITABLE or state == S.SELECTED:
        raise StoreError(
            f"cannot edit the text while the post is {state.value}"
            + ("; run `lce post reopen` first" if state in {S.APPROVED, S.READY_TO_PUBLISH} else "")
        )
    if not text.strip():
        raise StoreError("text is empty")
    store.write_text(store.post_dir(post_id) / "post.md", normalize_text(text))
    post["content_hash"] = content_hash(text)
    _invalidate_checks(post)
    post["humanization"] = _humanization_record(store, post, text, source=source, by=by)
    return set_state(store, post, S.HUMANIZED,
                     "candidate text saved" + (" (owner edit)" if source == "owner_edit" else ""))


def _humanization_record(store: DataStore, post: dict, text: str, *, source: str, by: str) -> dict:
    from lce import voice
    from lce.privacy.scan import load_denylist
    from lce.qa import run_checks
    from lce.rules import RulesetNotReady, ready_ruleset

    try:
        findings = run_checks(text, rules=ready_ruleset(post["language"]), voice=store.voice(),
                              profile=store.profile(), post=post, stories=store.stories(),
                              denylist=load_denylist(), brand=store.brand())
    except RulesetNotReady:
        findings = []
    return voice.record(store, post, findings, source=source, by=by)


def set_objective(store: DataStore, post_id: str, objective: str) -> dict:
    """Record the content objective (an id from voice.yaml objectives) before drafting."""
    from lce import voice

    ids = voice.objectives(store)
    if objective not in ids:
        raise StoreError(f"unknown objective {objective!r}; voice.yaml defines: {', '.join(ids) or 'none'}")
    post = store.load_post(post_id)
    post["objective"] = objective
    store.save_post(post)
    plan = store.plan()
    entry = next((e for e in plan.get("entries", []) if e.get("draft_ref") == post_id), None)
    if entry is not None:
        entry["objective"] = objective
        store.write_doc(store.plan_path, "plan", plan)
    store.log_event("post.objective", post_id=post_id, objective=objective)
    return post


def reopen(store: DataStore, post_id: str, reason: str) -> dict:
    """Send an approved or ready post back for editing; the approval is discarded."""
    post = store.load_post(post_id)
    if PostState(post["state"]) not in {S.APPROVED, S.READY_TO_PUBLISH, S.AWAITING_APPROVAL}:
        raise StoreError("only posts awaiting approval, approved or ready can be reopened")
    _invalidate_checks(post)
    return set_state(store, post, S.HUMANIZED, f"reopened: {reason}")


def current_text(store: DataStore, post_id: str) -> str:
    text = store.post_text(post_id, "post.md")
    if text is None:
        raise StoreError(f"post {post_id} has no candidate text yet")
    return text


def sync_plan(store: DataStore, post: dict) -> None:
    plan = store.plan()
    entries = plan.setdefault("entries", [])
    entry = next((e for e in entries if e.get("draft_ref") == post["post_id"]), None)
    if entry is None:
        return
    state = PostState(post["state"])
    entry["status"] = PLAN_STATUS[state]
    dup = post.get("duplicate", {}).get("status")
    entry["duplicate_check"] = dup or "pending"
    appr = post.get("approval", {}).get("state")
    entry["approval_status"] = appr or "pending"
    entry["publication_status"] = PUBLICATION_STATUS.get(state, "not_published")
    store.write_doc(store.plan_path, "plan", plan)
