"""Manual post refresh (LCE-041): regenerate the whole post package.

The owner clicks Refresh in the Control Center → a `refresh` decision → the
decisions workflow records `refresh_request` on the post. A Claude Code
session (the `lce-refresh` skill, run by the Routine or by hand) then writes
the new package and calls `lce refresh package`, which here, atomically:

1. keeps the current version in `versions/vN` (never overwritten);
2. replaces text (hook included), sources and claims;
3. records the humanization/voice check (save_humanized);
4. decides the media for THIS text: a new conceptual visual (relevance-checked),
   an explicit text-only decision, or keeping the old image only when its
   relevance is re-accepted against the new text AND a reason is given;
5. QA → duplicate check against the archive → media checks → fresh approval
   artifact bound to the new text and image hashes (AWAITING_APPROVAL);
6. records the refresh (post.refresh, freshness record with old/new hashes).

Any failure rolls the post back to exactly the version it was. Nothing is
approved or published here, and a scheduled publication still needs the
owner's approval and the same-day freshness check (LCE-040) of the new text.
"""

from __future__ import annotations

from datetime import date

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
    post = _refreshable(store, post_id)
    post["refresh_request"] = {
        "requested_at": now_iso(),
        "requested_by": by,
        **({"note": note.strip()} if note.strip() else {}),
        **({"decision_id": decision_id} if decision_id else {}),
    }
    store.save_post(post)
    store.log_event("post.refresh_requested", post_id=post_id, by=by)
    return post["refresh_request"]


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
    if sum(k in media for k in ("spec", "keep", "text_only")) != 1:
        raise StoreError(
            "media must be exactly one of: spec (new visual), keep (reason the current image "
            "still fits), text_only {reason, rationale}"
        )


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
    kept = versions.snapshot(store, post_id, reason="before refresh: " + pkg["reason"].strip(), by=by)
    try:
        if S(post["state"]) in {S.AWAITING_APPROVAL, S.APPROVED, S.READY_TO_PUBLISH}:
            reopen(store, post_id, "manual refresh")
        post = store.load_post(post_id)
        post["sources"] = [
            {k: v for k, v in s.items() if k in ("url", "title", "publisher", "accessed_at")}
            | {"accessed_at": s.get("accessed_at") or now_iso()}
            for s in pkg["sources"]
        ]
        post["claims"] = [{"text": c["text"], "source_url": c["source_url"]} for c in pkg.get("claims") or []]
        post.pop("regeneration", None)
        store.save_post(post)
        if S(post["state"]) == S.SELECTED:
            from lce.posts import save_draft

            save_draft(store, post_id, pkg["text"])
        post = save_humanized(store, post_id, pkg["text"], source="session", by=by)
        media = pkg["media"]
        if "spec" in media:
            concept(store, post_id, media["spec"])
            media_note = "new conceptual visual"
        elif "text_only" in media:
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
        else:
            rel = images.relevance_now(store, post_id)
            if not before_doc or before_doc.get("kind") == images.NO_IMAGE:
                raise StoreError("keep: there is no current image to keep")
            if rel is None or rel["media_decision"] != "accepted":
                raise StoreError(
                    "keep refused: the current image is not relevant to the new text ("
                    + "; ".join((rel or {}).get("problems") or ["no relevance record"])
                    + ")"
                )
            media_note = "image kept: " + str(media["keep"]).strip()
        qa = run_qa(store, post_id)
        if qa["status"] != "passed":
            raise StoreError("QA failed: " + "; ".join(f"{f['code']}: {f['message']}" for f in qa["errors"]))
        dup = run_dupcheck(store, post_id)
        if dup["status"] != "passed":
            raise StoreError(f"duplicate check failed ({len(dup['exact'])} exact, {len(dup['near'])} near)")
        errors, _ = images.check(store, post_id)
        if errors:
            raise StoreError("media: " + "; ".join(errors))
        prepare(store, post_id)
    except Exception:
        versions.rollback(store, post_id, kept["version"])
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


def keep(store: DataStore, post_id: str, *, reason: str, by: str = "session") -> dict:
    """Close a refresh request without a new version: allowed only when the current
    package is complete and valid (relevant media, approval artifact for the current
    hashes). Nothing changes; the approval state is untouched."""
    from lce.approval import _require_consistent, image_unchanged

    post = _refreshable(store, post_id)
    if not reason.strip():
        raise StoreError("say why the current version is kept")
    if S(post["state"]) not in {S.AWAITING_APPROVAL, S.APPROVED, S.READY_TO_PUBLISH}:
        raise StoreError(f"the current version is not complete ({post['state']}); refresh it instead")
    errors, _ = images.check(store, post_id)
    if errors:
        raise StoreError("the current version cannot be kept: " + "; ".join(errors))
    _require_consistent(store, post)
    if not image_unchanged(store, post):
        raise StoreError("the image changed since the approval artifact; refresh it instead")
    art = store.post_dir(post_id) / "APPROVAL.md"
    want = (post.get("approval") or {}).get("artifact_hash")
    if not art.exists() or (want and content_hash(art.read_text(encoding="utf-8")) != want):
        raise StoreError("the approval artifact does not match the current version; refresh it instead")
    req = post.pop("refresh_request", None) or {}
    post["refresh"] = {
        "completed_at": now_iso(),
        "by": by,
        "reason": reason.strip(),
        "outcome": "kept",
        "previous_version": None,
        "requested_at": req.get("requested_at"),
        "decision_id": req.get("decision_id"),
    }
    store.save_post(post)
    store.log_event("post.refresh_kept", post_id=post_id, reason=reason.strip())
    return post["refresh"]
