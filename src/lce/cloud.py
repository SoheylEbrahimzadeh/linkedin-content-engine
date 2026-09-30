"""Client for the LCE cloud Worker (Phase 4B).

- `push` delegates a LOCALLY approved, READY_TO_PUBLISH post to the cloud. From
  then on the cloud is its only publisher: a `delegation.json` record makes
  local `lce publish` refuse it (one publisher per post).
- `consent` asks the cloud to publish that post at one configured slot.
- Authentication: a short-lived Cloudflare Access token obtained at run time
  (`cloudflared access token -app=<api_base>`, i.e. your browser login) or
  $LCE_CF_ACCESS_TOKEN. Nothing is stored; no long-lived credential exists.
The LinkedIn token lives only in the Worker's encrypted secret.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from lce.clock import iso_utc, now
from lce.posts import current_text, set_state
from lce.state import PostState
from lce.store import DataStore, StoreError, _atomic_write
from lce.textutil import content_hash

S = PostState
CLOUD_CONFIG = "config/cloud.yaml"


class CloudError(StoreError):
    pass


@dataclass(frozen=True)
class CloudResponse:
    status: int
    body: dict


class CloudTransport(Protocol):
    def request(self, method: str, url: str, headers: dict[str, str],
                body: bytes | None) -> CloudResponse: ...


class UrllibCloudTransport:
    def request(self, method, url, headers, body):
        if not url.startswith("https://"):
            raise CloudError("the cloud API must use https")
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return CloudResponse(resp.status, json.loads(resp.read() or b"{}"))
        except urllib.error.HTTPError as exc:
            try:
                data = json.loads(exc.read() or b"{}")
            except ValueError:
                data = {}
            return CloudResponse(exc.code, data)
        except OSError as exc:
            raise CloudError(f"cloud API unreachable: {type(exc).__name__}") from exc


def access_token_from_cloudflared(api_base: str, runner=subprocess.run) -> str:
    env_token = os.environ.get("LCE_CF_ACCESS_TOKEN", "").strip()
    if env_token:
        return env_token
    try:
        r = runner(["cloudflared", "access", "token", f"-app={api_base}"], capture_output=True,
                   text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CloudError("cloudflared is not available; install it or set LCE_CF_ACCESS_TOKEN") from exc
    token = (r.stdout or "").strip()
    if r.returncode != 0 or not token:
        raise CloudError("no Cloudflare Access token; run `cloudflared access login "
                         f"{api_base}` first")
    return token


def load_cloud_config(store: DataStore) -> dict:
    from lce.validate import validate_doc

    doc = store.read_doc(store.root / CLOUD_CONFIG)
    if not doc:
        raise CloudError(f"{CLOUD_CONFIG} is missing (see templates/private-data)")
    errors = validate_doc("cloud", doc)
    if errors:
        raise CloudError(f"{CLOUD_CONFIG} is invalid: " + "; ".join(errors))
    return doc


class CloudClient:
    def __init__(self, api_base: str, token: Callable[[], str], transport: CloudTransport):
        self.api_base, self._token, self.transport = api_base.rstrip("/"), token, transport

    def call(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {"cf-access-token": self._token(), "content-type": "application/json"}
        body = json.dumps(payload).encode() if payload is not None else None
        resp = self.transport.request(method, f"{self.api_base}/api{path}", headers, body)
        if resp.status >= 400:
            raise CloudError(f"cloud API {method} {path}: HTTP {resp.status} "
                             f"{resp.body.get('error', '')}".strip())
        return resp.body


def make_client(store: DataStore, transport: CloudTransport | None = None,
                token: Callable[[], str] | None = None) -> CloudClient:
    cfg = load_cloud_config(store)
    return CloudClient(cfg["api_base"], token or (lambda: access_token_from_cloudflared(cfg["api_base"])),
                       transport or UrllibCloudTransport())


CLOUD_MAX_IMAGE_BYTES = 1_500_000  # the Worker stores images in D1 (rows ≤ ~2 MB)


def _image_payload(store: DataStore, post_id: str, approval: dict) -> dict | None:
    """The approved image for the Worker, re-hashed against the approval; None for no image."""
    import base64
    import hashlib

    doc = store.read_doc(store.post_dir(post_id) / "image.yaml")
    if not doc or doc.get("kind", "none") == "none":
        return None
    data = (store.post_dir(post_id) / doc["file"]).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if sha != approval.get("image_hash"):
        raise CloudError("the image does not match the approved image; nothing is sent")
    if len(data) > CLOUD_MAX_IMAGE_BYTES:
        raise CloudError(f"the image is {len(data)} bytes; the cloud publisher accepts up to "
                         f"{CLOUD_MAX_IMAGE_BYTES}. Publish this post locally (`lce publish`).")
    return {"data_base64": base64.b64encode(data).decode("ascii"), "sha256": sha,
            "alt_text": doc.get("alt_text", "")}


# ── delegation (one publisher per post) ───────────────────────────────
def delegation_path(store: DataStore, post_id: str):
    return store.post_dir(post_id) / "delegation.json"


def load_delegation(store: DataStore, post_id: str) -> dict | None:
    p = delegation_path(store, post_id)
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def _confirm(confirm, is_tty, phrase: str, prompt: str) -> None:
    import sys

    tty = is_tty or (lambda: sys.stdin.isatty() and sys.stdout.isatty())
    if not tty():
        raise CloudError("this action requires an interactive terminal")
    if confirm(f"{prompt}\nType '{phrase}': ").strip() != phrase:
        raise CloudError("confirmation phrase did not match; nothing changed")


def push(store: DataStore, post_id: str, client: CloudClient, *, confirm=input, is_tty=None) -> dict:
    post = store.load_post(post_id)
    if PostState(post["state"]) != S.READY_TO_PUBLISH:
        raise CloudError(f"only READY_TO_PUBLISH posts can be delegated; this one is {post['state']}")
    text = current_text(store, post_id)
    h = content_hash(text)
    approval = post.get("approval") or {}
    if approval.get("state") != "approved" or approval.get("approved_hash") != h:
        raise CloudError("the text does not match the approved hash; nothing is sent")
    existing = load_delegation(store, post_id)
    if existing and existing.get("approved_hash") != h:
        raise CloudError("this post was already delegated with a different text")
    if (store.post_dir(post_id) / "publication.json").exists():
        raise CloudError("a local publish attempt exists for this post; it cannot be delegated")
    image_payload = _image_payload(store, post_id, approval)
    _confirm(confirm, is_tty, f"DELEGATE {post_id}",
             f"From now on only the cloud may publish {post_id} (hash {h[:12]}); local "
             "`lce publish` will refuse it.")
    body = {"text": text, "approved_hash": h, "approved_at": approval["approved_at"],
            "language": post["language"], "plan_date": post.get("plan_date")}
    if image_payload:
        body["image"] = image_payload
    result = client.call("PUT", f"/posts/{post_id}", body)
    record = {"post_id": post_id, "runtime": "cloud", "api_base": client.api_base,
              "approved_hash": h, "delegated_at": existing["delegated_at"] if existing else iso_utc(now())}
    _atomic_write(delegation_path(store, post_id), json.dumps(record, indent=2) + "\n")
    store.log_event("cloud.delegated", post_id=post_id, approved_hash=h)
    return result


def consent(store: DataStore, post_id: str, slot_id: str, client: CloudClient, *, confirm=input,
            is_tty=None) -> dict:
    if not load_delegation(store, post_id):
        raise CloudError("push the post to the cloud first (`lce cloud push`)")
    _confirm(confirm, is_tty, f"SCHEDULE {post_id}",
             f"The cloud will publish {post_id} automatically at slot {slot_id} "
             "(only if the kill switch is on).")
    result = client.call("POST", "/consents", {"post_id": post_id, "slot_id": slot_id})
    store.log_event("cloud.consent", post_id=post_id, slot_id=slot_id,
                    consent_id=result.get("consent_id"))
    return result


def pull(store: DataStore, client: CloudClient) -> list[dict]:
    """Mirror cloud publication outcomes into the local lifecycle (read-only on the cloud)."""
    snap = client.call("GET", "/snapshot")
    cloud_posts = {p["post_id"]: p for p in snap.get("posts", [])}
    pubs = {p["post_id"]: p for p in snap.get("publications", [])}
    changes = []
    for post_id in store.post_ids():
        if not load_delegation(store, post_id) or post_id not in cloud_posts:
            continue
        cloud_state = cloud_posts[post_id]["state"]
        post = store.load_post(post_id)
        _atomic_write(store.post_dir(post_id) / "cloud.json",
                      json.dumps({"state": cloud_state, "publication": pubs.get(post_id),
                                  "pulled_at": iso_utc(now())}, indent=2, ensure_ascii=False) + "\n")
        target = {"PUBLISHED": S.PUBLISHED, "PUBLISH_FAILED": S.PUBLISH_FAILED,
                  "NEEDS_RECONCILE": S.NEEDS_RECONCILE}.get(cloud_state)
        local = PostState(post["state"])
        if target and local != target:
            if local == S.READY_TO_PUBLISH:
                post = set_state(store, post, S.PUBLISHING, "mirrored from cloud")
                local = S.PUBLISHING
            if local in {S.PUBLISHING, S.NEEDS_RECONCILE}:
                set_state(store, post, target, f"mirrored from cloud ({cloud_state})")
                changes.append({"post_id": post_id, "state": target.value})
    return changes
