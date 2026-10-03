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


def request(
    store: DataStore,
    post_id: str,
    *,
    by: str,
    note: str = "",
    decision_id: str = "",
    origin: str = "owner",
    stale: dict | None = None,
) -> dict:
    """The current version must be replaced: archive it and deactivate it now (its
    approval is discarded); a writing session produces the replacement for the same slot.

    origin `owner`: the owner rejected it (Refresh; archived as `rejected`).
    origin `freshness` (LCE-049): the freshness check before the slot found it stale
    (archived as `stale`, with what was found)."""
    from lce.posts import reopen, set_state

    post = _refreshable(store, post_id)
    why = (
        "rejected by the owner (Refresh)"
        if origin == "owner"
        else "stale before publication: " + ((stale or {}).get("reason") or note or "sources changed")[:300]
    )
    already = bool(post.get("refresh_request"))
    archived = None
    if not already and (store.post_dir(post_id) / "post.md").exists():
        archived = versions.snapshot(
            store, post_id, reason=why, by=by, status="rejected" if origin == "owner" else "stale"
        )["version"]
    state = S(post["state"])
    if state in {S.AWAITING_APPROVAL, S.APPROVED, S.READY_TO_PUBLISH}:
        reopen(store, post_id, why)
        state = S.HUMANIZED
    if state == S.DUPLICATE_CHECKED:
        set_state(store, store.load_post(post_id), S.HUMANIZED, why)
        state = S.HUMANIZED
    post = store.load_post(post_id)
    prev = post.get("refresh_request") or {}
    post["refresh_request"] = {
        "requested_at": now_iso(),
        "requested_by": by,
        **({"note": note.strip()} if note.strip() else {}),
        **({"decision_id": decision_id} if decision_id else {}),
        "rejected_version": archived if archived is not None else prev.get("rejected_version"),
        "origin": origin,
        **({"stale": stale} if stale else {}),
    }
    store.save_post(post)
    if state in {S.HUMANIZED, S.QA_PASSED}:
        set_state(store, post, S.NEEDS_REVISION, "Refresh: replacement requested")
    store.log_event(
        "post.refresh_requested", post_id=post_id, by=by, rejected_version=archived, origin=origin
    )
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
        raise StoreError("Refresh replaces the media too: a new real image (commons) or text_only")
    if sum(k in media for k in ("reviewed", "commons", "spec", "text_only")) != 1:
        raise StoreError(
            "media must be exactly one of: reviewed (a real, licensed image chosen after looking at it), "
            "commons (a real, licensed image found by subject), "
            "text_only {reason, rationale}, spec (a drawn visual, only when the owner asked for one)"
        )
    # LCE-046: a real image or "no suitable licensed image" starts from the post's own source.
    from lce import source_visuals

    if (
        "reviewed" in media
        or "commons" in media
        or (media.get("text_only") or {}).get("reason") == ("no_suitable_licensed_image")
    ):
        source_visuals.validate_check(media.get("source_check") or {}, has_sources=True)
    # LCE-043: a generated diagram is never the automatic choice; only the owner can ask for one.
    if "spec" in media and media.get("owner_requested") is not True:
        raise StoreError(
            "a generated diagram is used only when the owner asked for one (media.owner_requested: true); "
            "use a real licensed image (media.commons) or text-only"
        )


def _commons_media(store, post_id, spec, text, history, used, by) -> str:
    """LCE-043: a real image chosen by subject from Wikimedia Commons, rights verified;
    nothing suitable → text-only `no_suitable_licensed_image` (never a generated diagram)."""
    from lce import commons

    titles = {((v.get("media") or {}).get("source_title")) for v in history} - {None}
    doc, record = commons.select(
        store, post_id, spec, transport=_TRANSPORT, exclude_sha256=used - {None}, exclude_titles=titles
    )
    if doc is None:
        tried = len(record["tried"])
        images.decide(
            store,
            post_id,
            kind="none",
            decided_by="agent",
            text_only_reason="no_suitable_licensed_image",
            rationale=(
                f"No suitable licensed image: {tried} Wikimedia Commons file(s) checked for "
                f"'{record['subject']}'; none both allowed reuse and showed the subject."
            ),
        )
        doc = images.load(store, post_id)
        doc["selection"] = record
        store.write_doc(images.path(store, post_id), "image", doc)
        return f"text-only (no suitable licensed image; {tried} candidates checked)"
    _credit(store, post_id, doc, text, by)
    return f"real image from Wikimedia Commons: {record['selected']} ({doc['provenance']['license']})"


def _reviewed_media(store, post_id, sel, text, history, used, by) -> str:
    """LCE-044: the candidate a reviewer chose after looking at it (any supported source)."""
    from lce import media_search

    ids = {((v.get("media") or {}).get("source_title")) for v in history} - {None}
    doc = media_search.attach_reviewed(
        store,
        post_id,
        sel,
        transport=_TRANSPORT,
        exclude_sha256=used - {None},
        exclude_ids=ids,
        source_check=_SOURCE_CHECK.get("current"),
    )
    _credit(store, post_id, doc, text, by)
    prov = doc["provenance"]
    return f"real image, reviewed: {prov['source_id']} ({prov['license']}, {prov['source_name']})"


def _credit(store, post_id, doc, text, by) -> None:
    from lce import relevance as relv
    from lce.posts import save_humanized

    prov = doc["provenance"]
    if prov.get("attribution_required"):
        # The licence asks for credit: it goes into the post itself, before QA and approval.
        save_humanized(store, post_id, with_credit(text, prov["attribution"]), source="session", by=by)
        from lce.posts import current_text as ct

        sem = doc["media_relevance"].get("semantic")
        doc["media_relevance"] = relv.declared(
            concept=doc["media_relevance"]["concept"],
            visual_type=doc["media_relevance"]["visual_type"],
            reason=doc["media_relevance"]["relevance_reason"],
            alt_text=doc["alt_text"],
            post=store.load_post(post_id),
            text=ct(store, post_id),
        )
        doc["media_relevance"]["semantic"] = sem
        store.write_doc(images.path(store, post_id), "image", doc)


def _progress(store, post_id, stage, req, note=""):
    """LCE-048: a real stage reached (sent only where cloud credentials exist)."""
    from lce import cloud

    if not req:  # a new slot candidate (LCE-049) has no Refresh to report on
        return
    cloud.report_progress(store, post_id, stage, note=note, decision_id=(req or {}).get("decision_id") or "")


_TRANSPORT = None  # tests replace the Commons transport
_SOURCE_CHECK: dict = {}


def with_credit(text: str, credit: str) -> str:
    """The licence's credit line, above a closing hashtag paragraph (hashtags stay last)."""
    paras = text.rstrip().split("\n\n")
    if len(paras) > 1 and all(w.startswith("#") for w in paras[-1].split()):
        return "\n\n".join(paras[:-1] + [credit, paras[-1]]) + "\n"
    return text.rstrip() + "\n\n" + credit + "\n"


def without_credit(text: str, credit: str) -> str:
    paras = [p for p in text.rstrip().split("\n\n") if p.strip() != credit]
    return "\n\n".join(paras) + "\n"


def package(
    store: DataStore,
    post_id: str,
    pkg: dict,
    *,
    by: str = "session",
    as_of: date | None = None,
    media_only: bool = False,
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
    # LCE-049: a rolling-calendar candidate is a post without text yet (SELECTED); it is
    # written through exactly the same checks, it just has no earlier version to keep.
    is_new = not (store.post_dir(post_id) / "post.md").exists()
    if is_new and S(post["state"]) != S.SELECTED:
        raise StoreError(f"a post without text must be SELECTED to be written (it is {post['state']})")
    before_text = "" if is_new else current_text(store, post_id)
    before_doc = images.load(store, post_id) or {}
    before = {
        "content_hash": content_hash(before_text),
        "image_sha256": before_doc.get("sha256"),
        "approval": (post.get("approval") or {}).get("state") or "none",
        "state": post["state"],
    }
    history = versions.listing(store, post_id)
    last_before = history[-1]["version"] if history else 0
    if not media_only and not is_new:
        novelty_check_text(store, post_id, pkg["text"], before_text)
    with tempfile.TemporaryDirectory() as tmp:
        versions.backup(store, post_id, Path(tmp))
        try:
            # The version being replaced is in history once: the Refresh decision archived it
            # already (status rejected); a session-started refresh archives it here.
            if is_new:
                pass
            elif not history or history[-1]["content_hash"] != before["content_hash"]:
                history.append(
                    versions.snapshot(
                        store,
                        post_id,
                        reason="replaced by a refresh: " + pkg["reason"].strip(),
                        by=by,
                        status="replaced",
                    )
                )
            kept = history[-1] if history else None
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
            used = {v.get("image_sha256") for v in history} | {before["image_sha256"]}
            _SOURCE_CHECK["current"] = media.get("source_check")
            if "reviewed" in media:
                media_note = _reviewed_media(
                    store, post_id, media["reviewed"], pkg["text"], history, used, by
                )
            elif "commons" in media:
                media_note = _commons_media(store, post_id, media["commons"], pkg["text"], history, used, by)
            elif "spec" in media:
                doc = concept(store, post_id, media["spec"])
                if doc["sha256"] in used:
                    raise StoreError(
                        "the new visual is identical to an earlier version's image; draw a new one"
                    )
                media_note = "new conceptual visual (requested by the owner)"
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
            if media.get("source_check"):  # LCE-046: the source-first decision stays with the media
                d = images.load(store, post_id)
                d["source_visual"] = media["source_check"]
                store.write_doc(images.path(store, post_id), "image", d)
            _progress(store, post_id, "humanization", req)
            qa = run_qa(store, post_id)
            if qa["status"] != "passed":
                raise StoreError(
                    "QA failed: " + "; ".join(f"{f['code']}: {f['message']}" for f in qa["errors"])
                )
            _progress(store, post_id, "qa", req, "QA passed")
            dup = run_dupcheck(store, post_id)
            if dup["status"] != "passed":
                raise StoreError(
                    f"duplicate check failed ({len(dup['exact'])} exact, {len(dup['near'])} near)"
                )
            _progress(store, post_id, "duplicate_check", req,
                      f"passed against {dup['compared_against']} archived post(s)")
            errors, _ = images.check(store, post_id)
            if errors:
                raise StoreError("media: " + "; ".join(errors))
            prepare(store, post_id)
            _progress(store, post_id, "approval_prepared", req)
        except Exception:
            versions.restore_backup(store, post_id, Path(tmp))
            versions.drop_after(store, post_id, last_before)  # only what THIS call archived
            raise
    post = store.load_post(post_id)
    text = current_text(store, post_id)
    doc = images.load(store, post_id) or {}
    rel = images.relevance_now(store, post_id, doc)
    post.pop("refresh_request", None)
    if not is_new:
        post["refresh"] = {
            "completed_at": now_iso(),
            "by": by,
            "reason": pkg["reason"].strip(),
            "outcome": "refreshed",
            "previous_version": kept["version"],
            "requested_at": req.get("requested_at"),
            "decision_id": req.get("decision_id"),
            "origin": req.get("origin") or "owner",
        }
    store.save_post(post)
    record = {
        "checked_at": now_iso(),
        "check_date": as_of.isoformat(),
        "mode": "new" if is_new else "manual",
        "by": by,
        "decision": "created" if is_new else "updated",
        "status": "current" if is_new else "update_awaiting_approval",
        "material_change": True,
        "reason": ("new candidate for the slot: " if is_new else "manual refresh: ") + pkg["reason"].strip(),
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
        "version_before": kept["version"] if kept else None,
    }
    refresh._append(store, post_id, record)
    store.log_event(
        "post.refreshed",
        post_id=post_id,
        previous_version=kept["version"] if kept else None,
        content_hash=record["content_hash"],
        image_sha256=record["image_sha256"],
    )
    return record


def replace_media(store: DataStore, post_id: str, media: dict, *, reason: str, by: str) -> dict:
    """LCE-043b: the current candidate's image turned out to be wrong before the owner
    reviewed it. Keep the text, archive the current package (status `replaced`, with
    the reason), choose new media under the same rules as a refresh (real licensed
    image by subject, or text-only; never an earlier version's file) and prepare a
    fresh approval artifact. Not for an approved post: that is the owner's to change."""
    post = _refreshable(store, post_id)
    if S(post["state"]) in {S.APPROVED, S.READY_TO_PUBLISH}:
        raise StoreError("the owner approved this version; only the owner can change it (Refresh or Edit)")
    if not (reason or "").strip():
        raise StoreError("say why the media is replaced")
    text = current_text(store, post_id)
    attr = ((images.load(store, post_id) or {}).get("provenance") or {}).get("attribution")
    if attr:
        text = without_credit(text, attr)  # the old image's credit line goes with it
    pkg = {
        "text": text,
        "reason": "media replaced: " + reason.strip(),
        "sources": [
            {k: v for k, v in s.items() if k in ("url", "title", "publisher")}
            for s in post.get("sources") or []
        ],
        "claims": [{"text": c["text"], "source_url": c["source_url"]} for c in post.get("claims") or []],
        "media": media,
    }
    return package(store, post_id, pkg, by=by, media_only=True)


def unskip_as_refresh(store: DataStore, post_id: str, *, by: str, note: str = "") -> dict:
    """The owner skipped a post but meant Refresh (LCE-042): reopen the slot and request a
    replacement. Only for posts rejected by Skip and never published; the skipped version is
    archived (status `rejected`) and the post waits in NEEDS_REVISION for the replacement.
    This is the one sanctioned way out of REJECTED, recorded in the post history."""
    from lce import cloud

    post = store.load_post(post_id)
    reason = (post.get("approval") or {}).get("reason") or ""
    if post["state"] != S.REJECTED.value or not reason.startswith("skipped"):
        raise StoreError("only a post rejected by Skip can be reopened as a Refresh")
    if (store.post_dir(post_id) / "publication.json").exists() or cloud.load_delegation(store, post_id):
        raise StoreError("the post was published or handed to the cloud publisher; it is not reopened")
    archived = versions.snapshot(
        store,
        post_id,
        reason=f"skipped by the owner ({reason}); reopened as Refresh",
        by=by,
        status="rejected",
    )["version"]
    post = store.load_post(post_id)
    post.pop("approval", None)
    for k in ("qa", "duplicate"):
        post.pop(k, None)
    post["state"] = S.NEEDS_REVISION.value
    post.setdefault("history", []).append(
        {
            "at": now_iso(),
            "state": S.NEEDS_REVISION.value,
            "note": f"skip undone: reopened as Refresh by {by}",
        }
    )
    post["refresh_request"] = {
        "requested_at": now_iso(),
        "requested_by": by,
        **({"note": note.strip()} if note.strip() else {}),
        "rejected_version": archived,
    }
    store.save_post(post)
    plan = store.plan()
    for e in plan.get("entries", []):
        if e.get("draft_ref") == post_id and e.get("status") == "skipped":
            e["status"] = "in_progress"
    store.write_doc(store.plan_path, "plan", plan)
    from lce.posts import sync_plan

    sync_plan(store, store.load_post(post_id))
    store.log_event("post.unskipped_as_refresh", post_id=post_id, by=by, rejected_version=archived)
    return post["refresh_request"]
