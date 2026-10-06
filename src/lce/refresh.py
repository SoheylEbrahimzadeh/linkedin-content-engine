"""Same-day content refresh (LCE-040).

On the publication day, before the publishing time, every planned/scheduled post
gets a final freshness check. The engine does the deterministic part (no LLM):

- re-fetch each recorded source and verify that every recorded claim is still
  stated there (normalized text match, numbers included);
- re-evaluate the media: file hash, rights metadata, and for diagrams that the
  diagram's verbatim strings are still in the post's text;
- decide: `unchanged` (sources still support every claim; approval preserved),
  `update_required` (a source no longer supports a claim), or `unverifiable`
  (a source could not be read: needs a session's review).

A Claude Code session does the judgment part with the existing commands: fresh
research (`lce refresh research`), and, only when the post must change,
`lce refresh apply` (new text → humanization record → QA → duplicate check
against the archive → media re-evaluation → new approval artifact; the old
approval is discarded because it was bound to the old hashes).

Every check is appended to `posts/<id>/freshness.yaml`. Nothing here approves or
publishes; the Worker refuses a scheduled publication without a same-day
`current` check for the approved text.
"""

from __future__ import annotations

import hashlib
import html
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from lce import clock, images
from lce.posts import current_text
from lce.state import PostState
from lce.store import DataStore, StoreError, now_iso
from lce.textutil import claim_numbers, content_hash

S = PostState
UA = "lce-refresh/0.1 (linkedin-content-engine; freshness check)"
MAX_BYTES = 3_000_000
CLOSED = {S.PUBLISHED, S.REJECTED, S.PUBLISHING, S.NEEDS_RECONCILE}

Fetcher = Callable[[str], tuple[int, str, bytes]]  # url -> (status, final_url, body)


class FetchError(Exception):
    pass


def http_fetch(url: str, timeout: int = 20) -> tuple[int, str, bytes]:
    if urlparse(url).scheme != "https":
        raise FetchError("only https sources are checked")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https enforced)
            return resp.status, resp.geturl(), resp.read(MAX_BYTES)
    except urllib.error.HTTPError as exc:
        return exc.code, url, b""
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        raise FetchError(type(getattr(exc, "reason", exc)).__name__) from exc


# ── text normalization ───────────────────────────────────────────────
_TAGS = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def page_text(body: bytes) -> str:
    raw = body.decode("utf-8", "replace")
    return norm(html.unescape(_TAGS.sub(" ", raw)))


def norm(s: str) -> str:
    return " ".join(s.translate(_QUOTES).lower().split())


def claim_status(claim: str, text: str) -> str:
    """found: the claim is stated; partial: its numbers and most words are there; missing."""
    c = norm(claim).strip(" .")
    if c and c in text:
        return "found"
    nums = claim_numbers(claim)
    words = [w for w in re.findall(r"[a-z0-9%]+", c) if len(w) > 3]
    if nums and all(n.lower() in text for n in nums) and words:
        share = sum(1 for w in words if w in text) / len(words)
        if share >= 0.6:
            return "partial"
    return "missing"


# ── media re-evaluation ──────────────────────────────────────────────
def media_check(store: DataStore, post_id: str, text: str) -> dict:
    """Is the media still right for this text? Rights/file integrity, then the LCE-041
    semantic relevance record re-evaluated against the current text (a visual that
    restates the post, or states an unsourced figure, is stale and must be redone)."""
    doc = images.load(store, post_id)
    if doc is None:
        return {"status": "needs_decision", "note": "no media decision recorded"}
    out = {"kind": doc["kind"], "sha256": doc.get("sha256")}
    if doc["kind"] == images.NO_IMAGE:
        _, warnings = images.check(store, post_id)
        return {**out, "status": "text_only", "note": doc.get("text_only_reason") or "text-only",
                "warnings": warnings}
    rel = images.relevance_now(store, post_id, doc)
    if rel is not None:
        out["relevance"] = {k: rel.get(k) for k in ("concept", "visual_type", "copied_post_text_ratio",
                                                    "media_decision", "problems")}
    if rel is not None and rel["media_decision"] != "accepted":
        return {**out, "status": "stale", "note": "media relevance rejected: " + "; ".join(rel["problems"])}
    errors, _ = images.check(store, post_id)
    if errors:
        return {**out, "status": "invalid", "note": "; ".join(errors)}
    prov = doc.get("provenance") or {}
    notes = []
    if prov.get("origin") == "licensed_stock":
        from lce.commons import license_usage

        if license_usage(prov.get("license", "")) is None:
            note = f"licence {prov.get('license')!r} is no longer accepted"
            return {**out, "status": "invalid", "note": note}
        notes.append(f"licence {prov.get('license')} still accepted")
    if rel is not None:
        ratio = rel.get("copied_post_text_ratio")
        notes.append(f"relevance accepted ({rel.get('visual_type')}"
                     + (f", {ratio:.0%} copied post text" if ratio is not None else ", declared") + ")")
    notes.append("file hash and rights metadata valid")
    return {**out, "status": "still_relevant", "note": "; ".join(notes)}


# ── records ──────────────────────────────────────────────────────────
def path(store: DataStore, post_id: str):
    return store.post_dir(post_id) / "freshness.yaml"


def history(store: DataStore, post_id: str) -> list[dict]:
    p = path(store, post_id)
    return (store.read_doc(p).get("checks") or []) if p.exists() else []


def _append(store: DataStore, post_id: str, record: dict) -> dict:
    # A run "as of" another day is a test: recorded, shown as such, never sent to the
    # Worker and never counted as that day's check (the Worker also refuses it).
    record["test_mode"] = record["check_date"] != today_local(store).isoformat()
    checks = history(store, post_id) + [record]
    store.write_doc(path(store, post_id), "freshness", {"checks": checks[-30:]})
    store.log_event("refresh", post_id=post_id, decision=record["decision"], status=record["status"])
    return record


def latest(store: DataStore, post_id: str) -> dict | None:
    h = history(store, post_id)
    return h[-1] if h else None


def localdate(store: DataStore, iso: str) -> date:
    from datetime import datetime

    tz = store.settings().get("timezone") or "UTC"
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(tz)).date()


def today_local(store: DataStore) -> date:
    tz = store.settings().get("timezone") or "UTC"
    return clock.now().astimezone(ZoneInfo(tz)).date()


def due_posts(store: DataStore, day: date, extra: set[str] | None = None) -> list[str]:
    out = []
    for pid in store.post_ids():
        post = store.load_post(pid)
        if S(post["state"]) in CLOSED or post["state"] in {"RESEARCHED", "SELECTED", "NEEDS_INPUT"}:
            continue
        if post.get("plan_date") == day.isoformat() or pid in (extra or set()):
            out.append(pid)
    return out


def _approval(post: dict) -> str:
    return (post.get("approval") or {}).get("state") or "none"


def check(
    store: DataStore,
    post_id: str,
    *,
    as_of: date,
    fetch: Fetcher = http_fetch,
    by: str = "workflow",
    dry_run: bool = False,
    context: dict | None = None,
) -> dict:
    """Deterministic check (same day, or inside the freshness window before the slot).
    Never changes the post text, image or approval."""
    post = store.load_post(post_id)
    text = current_text(store, post_id)
    sources = []
    pages: dict[str, str | None] = {}
    for src in post.get("sources") or []:
        url = src["url"]
        item = {"url": url}
        try:
            status, final, body = fetch(url)
            item.update(http=status, final_url=final if final != url else None)
            if status == 200 and body:
                pages[url] = page_text(body)
                item.update(status="ok", content_sha256=hashlib.sha256(body).hexdigest())
            else:
                item["status"] = "blocked" if status in (401, 403, 429) else "unreachable"
                pages[url] = None
                if item["status"] == "blocked":
                    # LCE-049: the publisher refuses automated clients (Gartner answers 403 to
                    # GitHub Actions). Read the Internet Archive's latest capture instead, and say so.
                    arch = _archived(url, fetch, as_of)
                    if arch:
                        pages[url] = page_text(arch["body"])
                        item.update(
                            status="ok_archive",
                            via="Internet Archive capture (the publisher refused the request)",
                            archive_url=arch["url"],
                            capture=arch.get("capture"),
                            content_sha256=hashlib.sha256(arch["body"]).hexdigest(),
                        )
        except FetchError as exc:
            item.update(status="unreachable", error=str(exc))
            pages[url] = None
        sources.append({k: v for k, v in item.items() if v is not None})
    claims = []
    for c in post.get("claims") or []:
        page = pages.get(c.get("source_url"))
        claims.append(
            {
                "text": c["text"],
                "source_url": c.get("source_url"),
                "status": claim_status(c["text"], page) if page else "unverified",
            }
        )
    missing = [c for c in claims if c["status"] == "missing"]
    unverified = [c for c in claims if c["status"] == "unverified"]
    if not sources:
        decision, status, material = "unverifiable", "needs_review", None
        reason = "no recorded sources to re-check; a session must confirm the content is current"
    elif missing:
        decision, status, material = "update_required", "update_required", True
        reason = f"{len(missing)} recorded claim(s) no longer found in their source"
    elif unverified:
        decision, status, material = "unverifiable", "needs_review", None
        reason = (
            f"{len(unverified)} claim(s) could not be verified (source "
            f"{', '.join(sorted({s['status'] for s in sources if s.get('status') != 'ok'}))}); "
            "a session must confirm with fresh research"
        )
    else:
        decision, status, material = "unchanged", "current", False
        reason = "every recorded claim is still stated in its source"
        archived = [x for x in sources if x.get("status") == "ok_archive"]
        if archived:
            reason += " (" + ", ".join(
                f"{x['url'].split('/')[2]} via its archive capture {x.get('capture') or ''}".strip()
                for x in archived
            ) + ")"
    media = media_check(store, post_id, text)
    if media["status"] in {"stale", "invalid"}:
        decision, status = "update_required", "update_required"
        reason += f"; media {media['status']}: {media['note']}"
    elif media["status"] in {"needs_review", "needs_decision"} and status == "current":
        status = "needs_review"
        reason += f"; media {media['status']}"
    h = content_hash(text)
    record = {
        "checked_at": now_iso(),
        "check_date": as_of.isoformat(),
        "mode": "check",
        "by": by,
        "decision": decision,
        "status": status,
        "material_change": material,
        "reason": reason,
        "sources": sources,
        "claims": claims,
        "media": media,
        "content_hash": h,
        "image_sha256": media.get("sha256"),
        "approval": _approval(post),
        "approval_effect": "preserved",
        "new_developments": "not machine-checked: fresh research is a session step (lce refresh research)",
        **(context or {}),
    }
    return record if dry_run else _append(store, post_id, record)


ARCHIVE = "https://web.archive.org/web/"


def _archived(url: str, fetch: Fetcher, as_of: date) -> dict | None:
    """The Internet Archive's capture closest to `as_of` (its newest, in practice)."""
    try:
        status, final, body = fetch(f"{ARCHIVE}{as_of:%Y%m%d}id_/{url}")
    except (FetchError, OSError, KeyError):
        return None
    if status != 200 or not body:
        return None
    m = re.search(r"/web/(\d{8})\d*id_/", final or "")
    capture = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else None
    return {"url": final, "body": body, "capture": capture}


def research(
    store: DataStore,
    post_id: str,
    *,
    as_of: date,
    sources: list[str],
    note: str,
    material: bool,
    by: str = "session",
) -> dict:
    """A session's fresh-research result (web research with recorded sources)."""
    if not sources:
        raise StoreError("record the sources you checked (--source, repeatable)")
    if not note.strip():
        raise StoreError("describe what the research found (--note)")
    post = store.load_post(post_id)
    text = current_text(store, post_id)
    media = media_check(store, post_id, text)
    status = (
        "update_required"
        if material
        else ("current" if media["status"] in {"still_relevant", "text_only"} else "needs_review")
    )
    record = {
        "checked_at": now_iso(),
        "check_date": as_of.isoformat(),
        "mode": "research",
        "by": by,
        "decision": "update_required" if material else "confirmed",
        "status": status,
        "material_change": material,
        "reason": note.strip(),
        "sources": [{"url": u, "status": "checked_by_session"} for u in sources],
        "claims": [],
        "media": media,
        "content_hash": content_hash(text),
        "image_sha256": media.get("sha256"),
        "approval": _approval(post),
        "approval_effect": "preserved",
    }
    return _append(store, post_id, record)


def apply_update(
    store: DataStore,
    post_id: str,
    *,
    as_of: date,
    text: str,
    reason: str,
    sources: list[str],
    by: str = "session",
) -> dict:
    """Material change: new text through the full pipeline; the old approval is discarded."""
    from lce import cloud
    from lce.approval import prepare
    from lce.dupcheck import run_dupcheck
    from lce.posts import reopen, save_humanized
    from lce.qa import run_qa

    if cloud.load_delegation(store, post_id):
        raise StoreError("the post is in the cloud publisher; withdraw it there first, then refresh")
    if not reason.strip() or not sources:
        raise StoreError("a refresh update needs --reason and the --source(s) that justify it")
    from lce.revise import autofix

    text = autofix(text)[0]          # writing gate step 1: safe contractions before anything is stored
    post = store.load_post(post_id)
    from lce.archive import require_active

    require_active(post, "refresh it")
    before_text = current_text(store, post_id)
    # contractions alone are no update; removing a visible image label is one (LCE-052)
    if content_hash(text) == content_hash(autofix(before_text, labels=False)[0]):
        raise StoreError("the text is unchanged; record a check or research result instead")
    before = {
        "content_hash": content_hash(before_text),
        "image_sha256": (images.load(store, post_id) or {}).get("sha256"),
        "approval": _approval(post),
        "state": post["state"],
    }
    if S(post["state"]) in {S.AWAITING_APPROVAL, S.APPROVED, S.READY_TO_PUBLISH}:
        reopen(store, post_id, "same-day refresh: content updated")
    post = save_humanized(store, post_id, text, source="session", by=by)
    qa = run_qa(store, post_id)
    steps = {"humanization": post.get("humanization", {}).get("checklist"), "qa": qa["status"]}
    gate_ok = True
    if qa["status"] == "passed":         # writing gate: a weak draft never reaches approval
        from lce.posts import set_state
        from lce.repackage import writing_gate

        try:
            writing_gate(store, post_id)
            steps["writing_gate"] = "passed"
        except StoreError as exc:
            gate_ok = False
            steps["writing_gate"] = str(exc)
            set_state(store, store.load_post(post_id), S.NEEDS_REVISION, "writing gate: revise the text")
    if qa["status"] == "passed" and gate_ok:
        dup = run_dupcheck(store, post_id)
        steps["duplicate"] = {
            "status": dup["status"],
            "compared_against": dup["compared_against"],
            "exact": len(dup["exact"]),
            "near": len(dup["near"]),
        }
    media = media_check(store, post_id, text)
    steps["media"] = media
    state = store.load_post(post_id)["state"]
    if state == S.DUPLICATE_CHECKED.value and media["status"] in {"still_relevant", "text_only"}:
        prepare(store, post_id)
        state = S.AWAITING_APPROVAL.value
    after_post = store.load_post(post_id)
    record = {
        "checked_at": now_iso(),
        "check_date": as_of.isoformat(),
        "mode": "update",
        "by": by,
        "decision": "updated",
        "status": "update_awaiting_approval" if state == S.AWAITING_APPROVAL.value else "update_in_progress",
        "material_change": True,
        "reason": reason.strip(),
        "sources": [{"url": u, "status": "checked_by_session"} for u in sources],
        "claims": [],
        "media": media,
        "steps": steps,
        "content_hash": content_hash(text),
        "content_hash_before": before["content_hash"],
        "image_sha256": media.get("sha256"),
        "image_sha256_before": before["image_sha256"],
        "approval_before": before["approval"],
        "approval": _approval(after_post),
        "approval_effect": "invalidated" if before["approval"] in {"pending", "approved"} else "none",
        "state_before": before["state"],
        "state": state,
    }
    if media["status"] not in {"still_relevant", "text_only"}:
        record["next"] = (
            "re-decide the image (lce image diagram|chart|commons|decide), then lce refresh finish " + post_id
        )
    return _append(store, post_id, record)


def finish(store: DataStore, post_id: str, *, as_of: date, by: str = "session") -> dict:
    """After an update needed a new media decision: re-check and prepare approval."""
    from lce.approval import prepare
    from lce.dupcheck import run_dupcheck
    from lce.qa import run_qa

    post = store.load_post(post_id)
    text = current_text(store, post_id)
    # The update this finishes: its "before" hashes are the version that was replaced.
    prev = latest(store, post_id) or {}
    qa = (
        run_qa(store, post_id)["status"]
        if S(post["state"]) == S.HUMANIZED
        else (post.get("qa") or {}).get("status")
    )
    dup = None
    if S(store.load_post(post_id)["state"]) == S.QA_PASSED:
        d = run_dupcheck(store, post_id)
        dup = {
            "status": d["status"],
            "compared_against": d["compared_against"],
            "exact": len(d["exact"]),
            "near": len(d["near"]),
        }
    media = media_check(store, post_id, text)
    if S(store.load_post(post_id)["state"]) == S.DUPLICATE_CHECKED and media["status"] in {
        "still_relevant",
        "text_only",
    }:
        prepare(store, post_id)
    post = store.load_post(post_id)
    record = {
        "checked_at": now_iso(),
        "check_date": as_of.isoformat(),
        "mode": "update",
        "by": by,
        "decision": "updated",
        "status": "update_awaiting_approval"
        if post["state"] == "AWAITING_APPROVAL"
        else "update_in_progress",
        "material_change": True,
        "reason": "update finished after media decision",
        "sources": [],
        "claims": [],
        "media": media,
        "steps": {"qa": qa, "duplicate": dup, "media": media["status"]},
        "content_hash": content_hash(text),
        "content_hash_before": prev.get("content_hash_before", prev.get("content_hash")),
        "image_sha256": media.get("sha256"),
        "image_sha256_before": prev.get("image_sha256_before", prev.get("image_sha256")),
        "approval": _approval(post),
        "approval_effect": "none",
        "state": post["state"],
    }
    return _append(store, post_id, record)


# ── schedule-derived freshness window (LCE-049) ───────────────────────
# The same-day check alone comes too late to replace a stale post (research,
# writing, QA and the owner's approval all need time). So every planned post is
# also checked inside a window that opens `freshness_lead_hours` before its real
# slot (plan date at the cadence time of that weekday, in the configured time
# zone), and again every `freshness_recheck_hours` until the slot. A post found
# stale gets a same-slot replacement request at once (its version is archived as
# `stale`, its approval discarded); the refresh worker writes the replacement.
# Nothing here approves or publishes.
def slot_of(store: DataStore, post: dict) -> dict | None:
    from lce.schedule import DAYS, ScheduleError, load_schedule, slot_for

    if not post.get("plan_date"):
        return None
    try:
        sched = load_schedule(store.settings())
    except ScheduleError:
        return None
    if not sched.slots:
        return None
    day = date.fromisoformat(post["plan_date"])
    spec = next((x for x in sched.slots if x.day == DAYS[day.weekday()]), None)
    source = "plan date at the cadence time for that weekday"
    if spec is None:
        spec = sched.slots[0]
        source = "plan date (no cadence slot on that weekday; the first cadence time)"
    slot = slot_for(sched, spec, day)
    return {"utc": slot.utc, "local": slot.local.isoformat(), "source": source}


WRITTEN_MODES = {"research", "new", "manual", "update"}


def window(store: DataStore, now=None) -> list[dict]:
    """Every unpublished post whose freshness window is open now, and what is due for it."""
    from lce.clock import iso_utc, parse_iso
    from lce.jobs import automation_config

    now = now or clock.now()
    cfg = automation_config(store)
    lead = timedelta(hours=cfg["freshness_lead_hours"])
    recheck = timedelta(hours=cfg["freshness_recheck_hours"])
    out = []
    for pid in store.post_ids():
        post = store.load_post(pid)
        st = S(post["state"])
        if st in CLOSED or st in {S.RESEARCHED, S.SELECTED, S.NEEDS_INPUT}:
            continue
        slot = slot_of(store, post)
        if slot is None:
            continue
        opens = slot["utc"] - lead
        if not (opens <= now < slot["utc"]):
            continue
        recs = history(store, pid)
        checks = [r for r in recs if r.get("mode") == "check" and not r.get("test_mode")]
        last = checks[-1] if checks else None
        last_at = parse_iso(last["checked_at"]) if last else None
        has_text = (store.post_dir(pid) / "post.md").exists()
        item = {
            "post_id": pid,
            "state": post["state"],
            "slot_utc": iso_utc(slot["utc"]),
            "slot_local": slot["local"],
            "slot_source": slot["source"],
            "window_opens_at": iso_utc(opens),
            "last_check_at": last["checked_at"] if last else None,
            "last_check_status": last["status"] if last else None,
        }
        if post.get("refresh_request"):
            item.update(action="writing", why="a replacement for this slot is already requested")
        elif not has_text:
            item.update(action="writing", why="no candidate text yet")
        elif last_at is None or last_at < opens:
            item.update(action="check", why="first check inside the window")
        elif last.get("content_hash") != content_hash(current_text(store, pid)):
            item.update(action="check", why="the text changed since the last check")
        elif now - last_at >= recheck:
            item.update(action="check", why=f"last check older than {cfg['freshness_recheck_hours']} h")
        else:
            item.update(action="wait", why=f"checked at {last['checked_at']}")
        written = [r for r in recs if r.get("mode") in WRITTEN_MODES]
        researched = any(parse_iso(r["checked_at"]) >= opens for r in written)
        # LCE-050: radar items about this topic published after the text was last written/researched
        since = written[-1]["checked_at"] if written else post.get("created_at") or iso_utc(opens)
        from lce import radar

        news = radar.new_developments(store, pid, since=since) if has_text else []
        item["new_developments"] = news
        item["research_since"] = since
        if item["action"] == "writing":
            item["needs_research"] = False
        elif news:
            item["needs_research"] = True
            item["research_why"] = f"{len(news)} new radar item(s) on this topic since {since}"
        else:
            item["needs_research"] = not researched
            if not researched:
                item["research_why"] = "no fresh research inside this window yet"
        out.append(item)
    return sorted(out, key=lambda x: x["slot_utc"])


def escalate(store: DataStore, post_id: str, record: dict, *, by: str = "freshness check") -> dict:
    """A stale post: request its same-slot replacement now (approval discarded)."""
    from lce import cloud, repackage

    post = store.load_post(post_id)
    if post.get("refresh_request"):
        return {"escalated": False, "why": "a replacement is already requested"}
    if cloud.load_delegation(store, post_id):
        return {
            "escalated": False,
            "why": "the post is in the cloud publisher; the publishing gate holds it until the owner "
            "withdraws it there (nothing is withdrawn automatically)",
        }
    stale = {
        "reason": record["reason"][:500],
        "checked_at": record["checked_at"],
        "mode": record.get("mode"),
        "missing_claims": [c["text"] for c in record.get("claims") or [] if c.get("status") == "missing"],
        "media": (record.get("media") or {}).get("status"),
        **({"slot_utc": record["window"]["slot_utc"]} if record.get("window") else {}),
    }
    req = repackage.request(
        store, post_id, by=by, note="found stale before publication", origin="freshness", stale=stale
    )
    return {
        "escalated": True,
        "rejected_version": req.get("rejected_version"),
        "requested_at": req["requested_at"],
    }


def run_window(
    store: DataStore,
    *,
    now=None,
    fetch: Fetcher = http_fetch,
    by: str = "freshness window",
    dry_run: bool = False,
) -> list[dict]:
    """Check what is due in the window; escalate what is stale. Returns one row per post."""
    from lce.jobs import automation_config

    cfg = automation_config(store)
    rows = []
    for item in window(store, now):
        row = dict(item)
        if item["action"] == "check":
            approval_before = _approval(store.load_post(item["post_id"]))
            win = {k: item[k] for k in ("slot_utc", "slot_local", "window_opens_at")}
            rec = check(
                store,
                item["post_id"],
                as_of=today_local(store),
                fetch=fetch,
                by=by,
                dry_run=True,
                context={"trigger": "window", "window": win},
            )
            news = item.get("new_developments") or []
            rec["new_developments"] = (
                {"count": len(news), "items": news, "note": "radar items newer than the text; a writing "
                 "session judges whether they change the post (lce refresh research)"}
                if news else {"count": 0, "note": "no radar item on this topic newer than the text"}
            )
            row["result"] = {k: rec[k] for k in ("decision", "status", "reason")}
            if not dry_run:
                if rec["decision"] == "update_required" and cfg["freshness_escalate"]:
                    esc = escalate(store, item["post_id"], rec)
                    rec["escalation"] = esc
                    if esc["escalated"]:
                        rec["approval_effect"] = (
                            "invalidated" if approval_before in {"pending", "approved"} else "none"
                        )
                        rec["approval"] = _approval(store.load_post(item["post_id"]))
                    row["escalation"] = esc
                _append(store, item["post_id"], rec)
        rows.append(row)
    return rows


def cloud_rows(store: DataStore, post_ids: list[str]) -> list[dict]:
    """What the Worker needs for its same-day gate (latest record per post)."""
    rows = []
    for pid in post_ids:
        rec = latest(store, pid)
        if rec and not rec.get("test_mode"):
            rows.append(
                {
                    "post_id": pid,
                    "check_date": rec["check_date"],
                    "checked_at": rec["checked_at"],
                    "status": rec["status"],
                    "decision": rec["decision"],
                    "content_hash": rec["content_hash"],
                    "image_sha256": rec.get("image_sha256"),
                    "reason": rec["reason"][:500],
                }
            )
    return rows
