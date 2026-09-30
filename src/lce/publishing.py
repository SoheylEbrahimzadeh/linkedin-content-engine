"""Publishing orchestration: human-triggered only.

Rules enforced here:
- Only `lce publish` calls `publish()`, and it requires an interactive terminal
  and the typed phrase `PUBLISH <post_id>`. Nothing else (scheduler, jobs,
  dashboard, CI) imports this module.
- Only READY_TO_PUBLISH posts whose current text hash equals the approved hash
  are sent, and exactly that text is sent (escaped for LinkedIn's format).
- Before sending, a publication intent is written (posts/<id>/publication.json)
  and the post moves to PUBLISHING. A crash after that point leaves evidence;
  the post is then reconciled by a human, never re-sent automatically.
- AMBIGUOUS outcomes become NEEDS_RECONCILE. Nothing is retried by code.
- The idempotency key is a local identifier only. LinkedIn offers no
  idempotency; duplicate protection comes from refusing to re-send.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass

from lce.clock import iso_utc, now, parse_iso
from lce.posts import current_text, set_state
from lce.publish.base import ImageAttachment, Outcome, PostPayload, PublishResult
from lce.publish.linkedin import LinkedInConfig, LinkedInPublisher, Transport
from lce.publish.little import to_little
from lce.state import PostState
from lce.store import DataStore, StoreError, _atomic_write
from lce.textutil import content_hash

S = PostState
LINKEDIN_CONFIG = "config/linkedin.yaml"
RECONCILE_URL_RE = re.compile(
    r"^https://www\.linkedin\.com/(?:feed/update/(urn:li:(?:share|ugcPost|activity):\d+)/?"
    r"|posts/[A-Za-z0-9_%.-]+)(?:\?.*)?$")


# ── configuration ─────────────────────────────────────────────────────
def load_linkedin_config(store: DataStore) -> dict:
    from lce.validate import validate_doc

    doc = store.read_doc(store.root / LINKEDIN_CONFIG)
    if not doc:
        raise StoreError(f"{LINKEDIN_CONFIG} is missing (see templates/private-data)")
    errors = validate_doc("linkedin", doc)
    if errors:
        raise StoreError(f"{LINKEDIN_CONFIG} is invalid: " + "; ".join(errors))
    return doc


def linkedin_config(doc: dict) -> LinkedInConfig:
    return LinkedInConfig(api_version=doc["api_version"], person_urn=doc["person_urn"],
                          visibility=doc.get("visibility", "PUBLIC"),
                          max_chars=doc.get("max_chars", 3000),
                          timeout_seconds=float(doc.get("timeout_seconds", 30)))


def make_publisher(store: DataStore, transport: Transport | None = None,
                   tokens=None) -> LinkedInPublisher:
    from lce.publish.credentials import DEFAULT_ACCOUNT, DEFAULT_SERVICE, KeychainTokenStore
    from lce.publish.linkedin import UrllibTransport

    doc = load_linkedin_config(store)
    tokens = tokens or KeychainTokenStore(doc.get("keychain_service", DEFAULT_SERVICE),
                                          doc.get("keychain_account", DEFAULT_ACCOUNT))
    return LinkedInPublisher(linkedin_config(doc), tokens, transport or UrllibTransport())


# ── helpers ───────────────────────────────────────────────────────────
def publication_path(store: DataStore, post_id: str):
    return store.post_dir(post_id) / "publication.json"


def load_publication(store: DataStore, post_id: str) -> dict | None:
    path = publication_path(store, post_id)
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def _save_publication(store: DataStore, doc: dict) -> None:
    from lce.validate import validate_doc

    errors = validate_doc("publication", doc)
    if errors:
        raise StoreError("publication record is invalid: " + "; ".join(errors))
    _atomic_write(publication_path(store, doc["post_id"]),
                  json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prep_image_sha(store: DataStore, post_id: str) -> str | None:
    return ((store.load_post(post_id).get("approval") or {}).get("image_hash") or None)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


@dataclass
class Prepared:
    post: dict
    text: str
    payload: PostPayload
    commentary: str


def prepare(store: DataStore, post_id: str, publisher: LinkedInPublisher) -> Prepared:
    """All gates that do not need the network or the token."""
    if store.settings().get("publisher", {}).get("provider") != "linkedin_api":
        raise StoreError("publisher.provider is not linkedin_api in config/settings.yaml")
    post = store.load_post(post_id)
    state = PostState(post["state"])
    if state not in {S.READY_TO_PUBLISH, S.PUBLISH_FAILED}:
        hint = (" — run `lce publish reconcile`" if state in {S.PUBLISHING, S.NEEDS_RECONCILE}
                else "")
        raise StoreError(f"only READY_TO_PUBLISH posts can be published; this one is "
                         f"{state.value}{hint}")
    text = current_text(store, post_id)
    h = content_hash(text)
    approval = post.get("approval") or {}
    if approval.get("state") != "approved" or approval.get("approved_hash") != h \
            or post.get("content_hash") != h:
        raise StoreError("the text does not match the approved hash; nothing is sent")
    if (store.post_dir(post_id) / "delegation.json").exists():
        raise StoreError("this post is delegated to the cloud publisher; only the cloud may "
                         "publish it (one publisher per post)")
    from lce.approval import image_unchanged
    from lce.images import NO_IMAGE
    from lce.images import load as load_image

    if not image_unchanged(store, post):
        raise StoreError("the image does not match the approved image; nothing is sent")
    img = load_image(store, post_id) or {}
    attachment = None
    if img.get("kind", NO_IMAGE) != NO_IMAGE:
        data = (store.post_dir(post_id) / img["file"]).read_bytes()
        if _sha_bytes(data) != approval.get("image_hash"):
            raise StoreError("the image does not match the approved image; nothing is sent")
        attachment = ImageAttachment(data=data, alt_text=img.get("alt_text", ""),
                                     sha256=approval["image_hash"], file_name=img["file"])
    existing = load_publication(store, post_id)
    if existing and existing["state"] in {"publishing", "needs_reconcile", "published"}:
        raise StoreError(f"a publish attempt is already recorded ({existing['state']}); "
                         "run `lce publish reconcile`")
    payload = PostPayload(post_id=post_id, idempotency_key=_sha(f"{post_id}:{h}"), text=text,
                          content_hash=h, language=post["language"], image=attachment)
    issues = publisher.validate(payload)
    if issues:
        raise StoreError("cannot publish: " + "; ".join(f"{i.code}: {i.message}" for i in issues))
    _, _, body = publisher.build_request(payload)
    return Prepared(post, text, payload, body["commentary"])


def dry_run(store: DataStore, post_id: str, publisher: LinkedInPublisher) -> dict:
    """Exactly what would be sent. No token is read, nothing is written or sent."""
    prep = prepare(store, post_id, publisher)
    url, headers, body = publisher.build_request(prep.payload)
    image = prep.payload.image
    return {"url": url, "headers": {**headers, "Authorization": "Bearer <from Keychain>"},
            "body": body, "approved_hash": prep.payload.content_hash,
            "characters": len(prep.text.strip()),
            "image": ({"file": image.file_name, "sha256": image.sha256, "bytes": len(image.data),
                       "steps": ["POST /rest/images?action=initializeUpload",
                                 "PUT <uploadUrl> (the image bytes)", "POST /rest/posts"]}
                      if image else None),
            "capabilities": publisher.capabilities}


# ── publish ───────────────────────────────────────────────────────────
def publish(store: DataStore, post_id: str, publisher: LinkedInPublisher, *,
            confirm: Callable[[str], str] = input, is_tty: Callable[[], bool] | None = None
            ) -> dict:
    if not (is_tty or _tty)():
        raise StoreError("publishing requires an interactive terminal; run it yourself, not "
                         "via a script, scheduler or agent")
    prep = prepare(store, post_id, publisher)
    phrase = f"PUBLISH {post_id}"
    typed = confirm(f"This posts the approved text (hash {prep.payload.content_hash[:12]}, "
                    f"{len(prep.text.strip())} chars) to LinkedIn as "
                    f"{publisher.config.person_urn}.\nType '{phrase}' to publish: ")
    if typed.strip() != phrase:
        raise StoreError("confirmation phrase did not match; nothing was sent")

    post = prep.post
    record = load_publication(store, post_id) or {
        "post_id": post_id, "provider": publisher.name,
        "idempotency_key": prep.payload.idempotency_key, "attempts": []}
    attempt = {"attempt": len(record["attempts"]) + 1, "intent_at": iso_utc(now()),
               "outcome": "pending"}
    record.update({"approved_hash": prep.payload.content_hash,
                   "commentary_hash": _sha(prep.commentary),
                   "api_version": publisher.config.api_version,
                   "author": publisher.config.person_urn, "state": "publishing"})
    record["attempts"].append(attempt)
    if PostState(post["state"]) == S.PUBLISH_FAILED:
        post = set_state(store, post, S.READY_TO_PUBLISH, "owner retries after a failed attempt")
    _save_publication(store, record)                        # intent first …
    post = set_state(store, post, S.PUBLISHING, f"publish attempt {attempt['attempt']}")
    store.log_event("publish.intent", post_id=post_id, attempt=attempt["attempt"],
                    idempotency_key=record["idempotency_key"])
    try:
        result = publisher.publish(prep.payload)            # … then the single request
    except Exception as exc:  # the request may have been sent: never guess
        result = PublishResult(Outcome.AMBIGUOUS, detail={
            "reason": f"internal:{type(exc).__name__}", "sent": True})
    return _record_result(store, post, record, attempt, result)


def _record_result(store: DataStore, post: dict, record: dict, attempt: dict,
                   result: PublishResult) -> dict:
    d = result.detail or {}
    attempt["finished_at"] = iso_utc(now())
    for key in ("http_status", "reason", "message", "retryable", "sent"):
        if d.get(key) not in (None, ""):
            attempt[key] = d[key]
    pid = post["post_id"]
    if d.get("image_urn") and prep_image_sha(store, pid):
        record["image"] = {"urn": d["image_urn"], "sha256": prep_image_sha(store, pid)}
    if result.outcome == Outcome.PUBLISHED:
        attempt["outcome"] = "published"
        record.update({"state": "published", "remote_id": result.remote_id, "url": result.url,
                       "published_at": attempt["finished_at"], "verified_by": "api_response"})
        _save_publication(store, record)
        post = set_state(store, post, S.PUBLISHED, f"LinkedIn {result.remote_id}")
        store.log_event("publish.published", post_id=pid, remote_id=result.remote_id,
                        http_status=d.get("http_status"))
    elif result.outcome == Outcome.REJECTED:
        attempt["outcome"] = "rejected"
        record["state"] = "publish_failed"
        _save_publication(store, record)
        post = set_state(store, post, S.PUBLISH_FAILED, f"not created: {d.get('reason')}")
        store.log_event("publish.failed", post_id=pid, reason=d.get("reason"),
                        http_status=d.get("http_status"), retryable=d.get("retryable"))
    else:
        attempt["outcome"] = "ambiguous"
        record["state"] = "needs_reconcile"
        _save_publication(store, record)
        post = set_state(store, post, S.NEEDS_RECONCILE, f"outcome unknown: {d.get('reason')}")
        store.log_event("publish.ambiguous", post_id=pid, reason=d.get("reason"),
                        http_status=d.get("http_status"))
    return {"post": post, "publication": record, "result": result}


# ── manual publication (owner posted it themselves) ───────────────────
def record_manual(store: DataStore, post_id: str, *, url: str, published_at: str | None = None,
                  confirm: Callable[[str], str] = input,
                  is_tty: Callable[[], bool] | None = None) -> dict:
    """The owner declares they posted the approved text on LinkedIn by hand.

    Human-only (terminal + typed phrase); bound to the approved text and image,
    so only exactly what was approved can be recorded as published."""
    from lce.approval import image_unchanged

    if not (is_tty or _tty)():
        raise StoreError("recording a manual publication requires an interactive terminal")
    post = store.load_post(post_id)
    if PostState(post["state"]) != S.READY_TO_PUBLISH:
        raise StoreError(f"only READY_TO_PUBLISH posts can be recorded (run `lce ready` after "
                         f"approval); this one is {post['state']}")
    if (store.post_dir(post_id) / "delegation.json").exists():
        raise StoreError("this post is delegated to the cloud publisher")
    if load_publication(store, post_id):
        raise StoreError("a publication record already exists; use `lce publish reconcile`")
    text = current_text(store, post_id)
    h = content_hash(text)
    if (post.get("approval") or {}).get("approved_hash") != h or not image_unchanged(store, post):
        raise StoreError("the post no longer matches its approval; nothing is recorded")
    m = RECONCILE_URL_RE.match(url.strip())
    if not m:
        raise StoreError("expected a LinkedIn post URL "
                         "(https://www.linkedin.com/feed/update/urn:li:... or /posts/...)")
    at = iso_utc(parse_iso(published_at)) if published_at else iso_utc(now())
    phrase = f"PUBLISHED {post_id}"
    typed = confirm(f"You confirm you posted the approved text (hash {h[:12]}) yourself at {url}. "
                    f"Type '{phrase}': ")
    if typed.strip() != phrase:
        raise StoreError("confirmation phrase did not match; nothing changed")
    record = {"post_id": post_id, "provider": "manual", "idempotency_key": _sha(f"{post_id}:{h}"),
              "approved_hash": h, "commentary_hash": _sha(text), "state": "published",
              "url": url.strip(), "published_at": at, "verified_by": "owner", "attempts": [],
              "resolution": {"at": iso_utc(now()), "decision": "published", "by": "owner",
                             "note": "posted manually by the owner"}}
    if m.group(1):
        record["remote_id"] = m.group(1)
    _save_publication(store, record)
    post = set_state(store, post, S.PUBLISHING, "owner posted it manually")
    post = set_state(store, post, S.PUBLISHED, "manual publication recorded by the owner")
    store.log_event("publish.manual", post_id=post_id)
    return {"post": post, "publication": record}


# ── reconcile ─────────────────────────────────────────────────────────
def reconcile(store: DataStore, post_id: str, *, published_url: str | None = None,
              not_published: bool = False, confirm: Callable[[str], str] = input,
              is_tty: Callable[[], bool] | None = None) -> dict:
    """Owner's decision after an ambiguous or interrupted publish attempt."""
    if bool(published_url) == bool(not_published):
        raise StoreError("give exactly one of --published-url or --not-published")
    if not (is_tty or _tty)():
        raise StoreError("reconciliation requires an interactive terminal")
    post = store.load_post(post_id)
    state = PostState(post["state"])
    if state not in {S.NEEDS_RECONCILE, S.PUBLISHING}:
        raise StoreError(f"only NEEDS_RECONCILE or interrupted PUBLISHING posts are reconciled; "
                         f"this one is {state.value}")
    record = load_publication(store, post_id)
    if record is None:
        raise StoreError("no publication record exists for this post")
    urn = None
    if published_url:
        m = RECONCILE_URL_RE.match(published_url.strip())
        if not m:
            raise StoreError("expected a LinkedIn post URL "
                             "(https://www.linkedin.com/feed/update/urn:li:... or /posts/...)")
        urn = m.group(1)
    decision = "published" if published_url else "not_published"
    phrase = f"RECONCILE {post_id}"
    typed = confirm(f"You confirm the post was {decision.replace('_', ' ')} on LinkedIn. "
                    f"Type '{phrase}': ")
    if typed.strip() != phrase:
        raise StoreError("confirmation phrase did not match; nothing changed")
    if state == S.PUBLISHING:  # interrupted run: record the ambiguity before resolving it
        for a in record["attempts"]:
            if a["outcome"] == "pending":
                a["outcome"] = "interrupted"
        post = set_state(store, post, S.NEEDS_RECONCILE, "interrupted publish attempt")
    at = iso_utc(now())
    record["resolution"] = {"at": at, "decision": decision, "by": "owner"}
    if published_url:
        record.update({"state": "published", "url": published_url.strip(),
                       "published_at": record.get("published_at", at), "verified_by": "owner"})
        if urn:
            record["remote_id"] = urn
        _save_publication(store, record)
        post = set_state(store, post, S.PUBLISHED, "owner confirmed it is on LinkedIn")
    else:
        record["state"] = "not_published_confirmed"
        _save_publication(store, record)
        post = set_state(store, post, S.READY_TO_PUBLISH, "owner confirmed it is not on LinkedIn")
    store.log_event("publish.reconciled", post_id=post_id, decision=decision)
    return {"post": post, "publication": record}


def commentary_preview(text: str) -> str:
    return to_little(text.strip())
