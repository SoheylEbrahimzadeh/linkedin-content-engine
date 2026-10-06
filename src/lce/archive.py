"""Operational archive (owner request 2026-10-06): take a finished post out of the working views.

Overview and Upcoming show work that still needs something: approval, a time, a rewrite. A post
the owner rejected, or one already published, has nothing left to do, yet it kept appearing
there (and a refresh that failed before the rejection kept showing as a failure). Archiving
moves such a post out of those views. It is display state only:

- the post's lifecycle state is unchanged (REJECTED stays REJECTED, PUBLISHED stays PUBLISHED),
  and both are terminal, so nothing can be approved, scheduled or published from an archive;
- history, versions, the rejection reason, refresh records and the run log stay as they were;
  the archive and every restore are appended to `archive_history` (never rewritten);
- nothing is deleted. Restore brings the post back to the views in the same state; it never
  re-enters the writer, refresh, approval or publishing pipeline (a new attempt for a rejected
  topic is an explicit Duplicate, which creates a new post that needs approval like any other).

Only REJECTED and PUBLISHED posts can be archived. A post that still has an actionable step
(awaiting approval, approved, in the cloud publisher, failed or unknown publication) cannot:
that work must be finished, rejected or reconciled through its own lifecycle first.
"""

from __future__ import annotations

from lce import cloud
from lce.state import PostState
from lce.store import DataStore, StoreError, now_iso

S = PostState
ARCHIVABLE = frozenset({S.REJECTED, S.PUBLISHED})
# cloud publisher states in which the post still has work there (a published post may be archived)
CLOUD_ACTIVE = frozenset({"READY_TO_PUBLISH", "PUBLISHING", "PUBLISH_FAILED", "NEEDS_RECONCILE"})


def is_archived(post: dict) -> bool:
    return bool(post.get("archived"))


def _cloud_state(store: DataStore, post_id: str) -> str | None:
    import json

    if not cloud.load_delegation(store, post_id):
        return None
    f = store.post_dir(post_id) / "cloud.json"
    return json.loads(f.read_text("utf-8")).get("state") if f.exists() else "READY_TO_PUBLISH"


def archive(store: DataStore, post_id: str, *, by: str, reason: str = "", decision_id: str = "") -> dict:
    post = store.load_post(post_id)
    if is_archived(post):
        raise StoreError(f"{post_id} is already archived")
    state = S(post["state"])
    if state not in ARCHIVABLE:
        raise StoreError(f"a {state.value} post is not archived; only rejected or published posts are "
                         "(finish, reject or reconcile it first)")
    cs = _cloud_state(store, post_id)
    if cs in CLOUD_ACTIVE:
        raise StoreError(f"the post is in the cloud publisher ({cs}); withdraw or reconcile it there first")
    at = now_iso()
    rec = {"at": at, "by": by, "state": state.value}
    if reason.strip():
        rec["reason"] = reason.strip()[:500]
    if decision_id:
        rec["decision_id"] = decision_id
    post["archived"] = rec
    post.setdefault("archive_history", []).append(
        {k: v for k, v in {"action": "archived", "at": at, "by": by, "reason": rec.get("reason"),
                           "decision_id": decision_id or None}.items() if v})
    store.save_post(post)
    store.log_event("post.archived", post_id=post_id, state=state.value, by=by,
                    decision_id=decision_id or None)
    return post


def restore(store: DataStore, post_id: str, *, by: str, decision_id: str = "") -> dict:
    """Back into the views, in the same (terminal) state; nothing re-enters the pipeline."""
    post = store.load_post(post_id)
    if not is_archived(post):
        raise StoreError(f"{post_id} is not archived")
    post.pop("archived")
    post.setdefault("archive_history", []).append(
        {k: v for k, v in {"action": "restored", "at": now_iso(), "by": by,
                           "decision_id": decision_id or None}.items() if v})
    store.save_post(post)
    store.log_event("post.restored", post_id=post_id, state=post["state"], by=by,
                    decision_id=decision_id or None)
    return post


def require_active(post: dict, what: str) -> None:
    """Defence in depth for every pipeline step: an archived post is never worked on."""
    if is_archived(post):
        raise StoreError(f"{post['post_id']} is archived; restore it before you {what}")
