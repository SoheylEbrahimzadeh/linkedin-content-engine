"""Manual post Refresh (LCE-041/042): reject this version, write a new replacement.

Refresh in the Control Center means "I reject this version — write another
publishable candidate for the same slot". So:

1. The `refresh` decision (applied by the private workflow) archives the current
   package in `versions/vN` with status `rejected` and takes it out of the active
   role at once: its approval is discarded and the post waits in NEEDS_REVISION
   with a `refresh_request` (it can no longer be approved or published).
2. A Claude Code session (the `lce-refresh` skill, run by the Routine or by hand)
   writes a genuinely new package and calls `lce refresh package`, which here,
   atomically: replaces text (new hook), sources and claims; records the
   humanization/voice check; draws a NEW relevant visual (or decides text-only
   with a reason; the old image is never kept); runs QA, the duplicate check
   against the archive, the media relevance check; writes a fresh approval
   artifact bound to the new hashes (AWAITING_APPROVAL).
3. Novelty is enforced against every earlier version: a different hook, a text
   that is not a rewording of an earlier one, an image that is not an earlier one.

Each Refresh adds a version (v1 → v2 → v3 …); none is ever overwritten. Any
failure restores the post exactly as it was (from a temporary backup).
Skip is different: it releases the slot and generates nothing (decisions._skip).
Nothing here approves or publishes.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from lce import images, refresh, versions
from lce.posts import current_text
from lce.state import PostState as S
from lce.store import DataStore, StoreError, now_iso
from lce.textutil import content_hash

NOT_REFRESHABLE = {S.PUBLISHING, S.PUBLISHED, S.PUBLISH_FAILED, S.NEEDS_RECONCILE, S.REJECTED}


def _refreshable(store: DataStore, post_id: str) -> dict:
    from lce import cloud

    post = store.load_post(post_id)
    if S(post["state"]) in NOT_REFRESHABLE:
        raise StoreError(f"a {post['state']} post is not refreshed (history is never rewritten)")
    if cloud.load_delegation(store, post_id):
        raise StoreError("the post is in the cloud publisher; unschedule and withdraw it there first")
    return post


def request(store: DataStore, post_id: str, *, by: str, note: str = "", decision_id: str = "") -> dict:
    """The owner rejected the current version: archive it (status `rejected`) and
    deactivate it now; a writing session produces the replacement."""
    from lce.posts import reopen, set_state

    post = _refreshable(store, post_id)
    already = bool(post.get("refresh_request"))
    archived = None
    if not already and (store.post_dir(post_id) / "post.md").exists():
        archived = versions.snapshot(
            store, post_id, reason="rejected by the owner (Refresh)", by=by, status="rejected"
        )["version"]
    state = S(post["state"])
    if state in {S.AWAITING_APPROVAL, S.APPROVED, S.READY_TO_PUBLISH}:
        reopen(store, post_id, "rejected by the owner (Refresh)")
        state = S.HUMANIZED
    if state == S.DUPLICATE_CHECKED:
        set_state(store, store.load_post(post_id), S.HUMANIZED, "rejected by the owner (Refresh)")
        state = S.HUMANIZED
    post = store.load_post(post_id)
    prev = post.get("refresh_request") or {}
    post["refresh_request"] = {
        "requested_at": now_iso(),
        "requested_by": by,
        **({"note": note.strip()} if note.strip() else {}),
        **({"decision_id": decision_id} if decision_id else {}),
        "rejected_version": archived if archived is not None else prev.get("rejected_version"),
    }
    store.save_post(post)
    if state in {S.HUMANIZED, S.QA_PASSED}:
        set_state(store, post, S.NEEDS_REVISION, "Refresh: replacement requested")
    store.log_event("post.refresh_requested", post_id=post_id, by=by, rejected_version=archived)
    return store.load_post(post_id)["refresh_request"]


def pending(store: DataStore) -> list[dict]:
    out = []
    for pid in store.post_ids():
        post = store.load_post(pid)
        if post.get("refresh_request"):
            out.append(
                {
                    "post_id": pid,
                    "state": post["state"],
                    "plan_date": post.get("plan_date"),
                    "topic": post.get("topic"),
                    **post["refresh_request"],
                }
            )
    return out


MAX_SIMILARITY = 0.5  # share of the new text's words inside 4-word runs of an earlier version


def _hook_of(text: str) -> str:
    from lce.relevance import words

    line = next((x for x in text.splitlines() if x.strip()), "")
    return " ".join(words(line))


def novelty_check_text(store: DataStore, post_id: str, text: str, current: str) -> None:
    """A replacement must be a different post, not a rewording: new hook, and no earlier
    version (or the current text) may cover half of its words in 4-word runs."""
    from lce.relevance import copied_ratio

    earlier = [current]
    root = versions.folder(store, post_id)
    for v in versions.listing(store, post_id):
        f = root / f"v{v['version']}" / "post.md"
        if f.exists():
            earlier.append(f.read_text(encoding="utf-8"))
    hook = _hook_of(text)
    for old in earlier:
        if hook and hook == _hook_of(old):
            raise StoreError("the hook is the same as an earlier version's; a replacement needs a new hook")
        body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))
        ratio = copied_ratio([body], old)
        if ratio > MAX_SIMILARITY:
            raise StoreError(
                f"the text repeats an earlier version ({ratio:.0%} of its words in shared 4-word runs, "
                f"max {MAX_SIMILARITY:.0%}); write a genuinely new post"
            )


def _validate_package(pkg: dict) -> None:
    if not (pkg.get("text") or "").strip():
        raise StoreError("the package needs the new text")
    if not (pkg.get("reason") or "").strip():
        raise StoreError("the package needs a reason (what changed and why)")
    sources = pkg.get("sources")
    if not sources:
        raise StoreError("the package needs its sources (re-checked for this version)")
    urls = {s["url"] for s in sources}
    for c in pkg.get("claims") or []:
        if c.get("source_url") not in urls:
            raise StoreError(f"claim without one of the package's sources: {c.get('text')!r}")
    media = pkg.get("media") or {}
    if "keep" in media:
        raise StoreError("Refresh replaces the media too: draw a new visual (spec) or decide text_only")
    if sum(k in media for k in ("spec", "text_only")) != 1:
        raise StoreError("media must be exactly one of: spec (a new visual), text_only {reason, rationale}")


def package(
    store: DataStore, post_id: str, pkg: dict, *, by: str = "session", as_of: date | None = None
) -> dict:
    from lce.approval import prepare
    from lce.dupcheck import run_dupcheck
    from lce.posts import reopen, save_humanized
    from lce.qa import run_qa
    from lce.visuals import concept

    post = _refreshable(store, post_id)
    _validate_package(pkg)
    as_of = as_of or refresh.today_local(store)
    req = post.get("refresh_request") or {}
    before_text = current_text(store, post_id)
    before_doc = images.load(store, post_id) or {}
    before = {
        "content_hash": content_hash(before_text),
        "image_sha256": before_doc.get("sha256"),
        "approval": (post.get("approval") or {}).get("state") or "none",
        "state": post["state"],
    }
    history = versions.listing(store, post_id)
    last_before = history[-1]["version"] if history else 0
    novelty_check_text(store, post_id, pkg["text"], before_text)
    with tempfile.TemporaryDirectory() as tmp:
        versions.backup(store, post_id, Path(tmp))
        try:
            # The version being replaced is in history once: the Refresh decision archived it
            # already (status rejected); a session-started refresh archives it here.
            if not history or history[-1]["content_hash"] != before["content_hash"]:
                history.append(
                    versions.snapshot(
                        store,
                        post_id,
                        reason="replaced by a refresh: " + pkg["reason"].strip(),
                        by=by,
                        status="replaced",
                    )
                )
            kept = history[-1]
            if S(post["state"]) in {S.AWAITING_APPROVAL, S.APPROVED, S.READY_TO_PUBLISH}:
                reopen(store, post_id, "manual refresh")
            post = store.load_post(post_id)
            post["sources"] = [
                {k: v for k, v in s.items() if k in ("url", "title", "publisher")}
                | {"accessed_at": now_iso()}
                for s in pkg["sources"]
            ]
            post["claims"] = [
                {"text": c["text"], "source_url": c["source_url"]} for c in pkg.get("claims") or []
            ]
            post.pop("regeneration", None)
            store.save_post(post)
            if S(post["state"]) == S.SELECTED:
                from lce.posts import save_draft

                save_draft(store, post_id, pkg["text"])
            post = save_humanized(store, post_id, pkg["text"], source="session", by=by)
            media = pkg["media"]
            if "spec" in media:
                doc = concept(store, post_id, media["spec"])
                used = {v.get("image_sha256") for v in history} | {before["image_sha256"]}
                if doc["sha256"] in used:
                    raise StoreError(
                        "the new visual is identical to an earlier version's image; draw a new one"
                    )
                media_note = "new conceptual visual"
            else:
                t = media["text_only"]
                images.decide(
                    store,
                    post_id,
                    kind="none",
                    rationale=t["rationale"],
                    decided_by="agent",
                    text_only_reason=t["reason"],
                )
                media_note = f"text-only ({t['reason']})"
            qa = run_qa(store, post_id)
            if qa["status"] != "passed":
                raise StoreError(
                    "QA failed: " + "; ".join(f"{f['code']}: {f['message']}" for f in qa["errors"])
                )
            dup = run_dupcheck(store, post_id)
            if dup["status"] != "passed":
                raise StoreError(
                    f"duplicate check failed ({len(dup['exact'])} exact, {len(dup['near'])} near)"
                )
            errors, _ = images.check(store, post_id)
            if errors:
                raise StoreError("media: " + "; ".join(errors))
            prepare(store, post_id)
        except Exception:
            versions.restore_backup(store, post_id, Path(tmp))
            versions.drop_after(store, post_id, last_before)  # only what THIS call archived
            raise
    post = store.load_post(post_id)
    text = current_text(store, post_id)
    doc = images.load(store, post_id) or {}
    rel = images.relevance_now(store, post_id, doc)
    post.pop("refresh_request", None)
    post["refresh"] = {
        "completed_at": now_iso(),
        "by": by,
        "reason": pkg["reason"].strip(),
        "outcome": "refreshed",
        "previous_version": kept["version"],
        "requested_at": req.get("requested_at"),
        "decision_id": req.get("decision_id"),
    }
    store.save_post(post)
    record = {
        "checked_at": now_iso(),
        "check_date": as_of.isoformat(),
        "mode": "manual",
        "by": by,
        "decision": "updated",
        "status": "update_awaiting_approval",
        "material_change": True,
        "reason": "manual refresh: " + pkg["reason"].strip(),
        "sources": [{"url": s["url"], "status": "checked_by_session"} for s in pkg["sources"]],
        "claims": [
            {"text": c["text"], "source_url": c["source_url"], "status": "recorded"}
            for c in post.get("claims") or []
        ],
        "media": {
            "kind": doc.get("kind"),
            "sha256": doc.get("sha256"),
            "status": "still_relevant" if doc.get("kind") != images.NO_IMAGE else "text_only",
            "note": media_note,
            **(
                {
                    "relevance": {
                        k: rel.get(k)
                        for k in ("concept", "visual_type", "copied_post_text_ratio", "media_decision")
                    }
                }
                if rel
                else {}
            ),
        },
        "steps": {
            "humanization": (post.get("humanization") or {}).get("checklist"),
            "qa": "passed",
            "duplicate": {
                "status": dup["status"],
                "compared_against": dup["compared_against"],
                "exact": len(dup["exact"]),
                "near": len(dup["near"]),
            },
            "media": "accepted",
        },
        "content_hash": content_hash(text),
        "content_hash_before": before["content_hash"],
        "image_sha256": doc.get("sha256"),
        "image_sha256_before": before["image_sha256"],
        "approval_before": before["approval"],
        "approval": "pending",
        "approval_effect": "invalidated" if before["approval"] in {"pending", "approved"} else "none",
        "state_before": before["state"],
        "state": post["state"],
        "version_before": kept["version"],
    }
    refresh._append(store, post_id, record)
    store.log_event(
        "post.refreshed",
        post_id=post_id,
        previous_version=kept["version"],
        content_hash=record["content_hash"],
        image_sha256=record["image_sha256"],
    )
    return record
