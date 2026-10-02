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
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
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


def _user_agent() -> str:
    from lce import __version__

    # An explicit agent: Cloudflare's Browser Integrity Check refuses the default
    # "Python-urllib/x.y" with 403 (error 1010) before Access or the Worker see it.
    return f"lce-cli/{__version__} (linkedin-content-engine)"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


def _access_app(location: str) -> str:
    """Which Access application answered: team host and the AUD tag (`kid`) from the
    login redirect. Cloudflare sends both to every unauthenticated visitor, so they
    are public identifiers, not secrets; the Worker needs exactly these two values."""
    import re
    from urllib.parse import parse_qs, urlparse

    u = urlparse(location)
    kid = (parse_qs(u.query).get("kid") or [""])[0]
    team = u.hostname or "?"
    if kid and re.fullmatch(r"[0-9a-f]{64}", kid):
        return f" [application: {team}, AUD {kid}]"
    return f" [application: {team}]"


def classify_refusal(status: int, body: bytes, location: str = "") -> str:
    """Short, credential-free reason for a refused request (Cloudflare edge vs Worker)."""
    import re

    if "cloudflareaccess.com" in location:
        return "Cloudflare Access login redirect (credential not accepted)" + _access_app(location)
    text = body[:2048].decode("utf-8", "replace")
    m = re.search(r"error code:\s*(\d+)", text)
    if m:
        code = m.group(1)
        return {"1010": "Cloudflare Browser Integrity Check refused the client (error 1010)",
                "1020": "Cloudflare WAF rule blocked the request (error 1020)"}.get(
            code, f"Cloudflare error {code}")
    try:  # the Worker answers JSON; check it before any text marker
        err = json.loads(text).get("error")
        if err:
            return f"Worker: {err}"
    except (ValueError, AttributeError):
        pass
    if "cloudflareaccess" in text or "Cloudflare Access" in text:
        return "Cloudflare Access refused the request"
    snippet = " ".join(re.sub(r"<[^>]+>", " ", text).split())[:80]
    return f"HTTP {status}" + (f": {snippet}" if snippet else "")


class UrllibCloudTransport:
    def __init__(self):
        self.opener = urllib.request.build_opener(_NoRedirect())

    def request(self, method, url, headers, body):
        if not url.startswith("https://"):
            raise CloudError("the cloud API must use https")
        req = urllib.request.Request(url, data=body, method=method,
                                     headers={"User-Agent": _user_agent(), **headers})
        try:
            with self.opener.open(req, timeout=30) as resp:  # noqa: S310
                raw = resp.read()
                try:
                    return CloudResponse(resp.status, json.loads(raw or b"{}"))
                except ValueError:
                    return CloudResponse(resp.status, {"_raw": classify_refusal(resp.status, raw)})
        except urllib.error.HTTPError as exc:
            raw = exc.read() or b""
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            if exc.code >= 300 and "error" not in data:
                data["_raw"] = classify_refusal(exc.code, raw, exc.headers.get("Location", ""))
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


_CLIENT_ID_RE = r"[0-9a-f]{32}\.access"
# Legacy secrets are 64 hex characters; secrets issued since 2026-08-26 are
# "cfast_" + 40 alphanumeric characters + an 8-character checksum (54 in total).
_CLIENT_SECRET_RE = r"(?:[0-9a-f]{64}|cfast_[A-Za-z0-9]{48})"


def _token_value(raw: str, pattern: str) -> tuple[str, bool]:
    """The raw secret, or the value after a pasted label such as 'CF-Access-Client-Id: …'
    or 'Client Secret: …' when that remainder has Cloudflare's exact format.
    Returns (value, label_removed)."""
    import re

    value = raw.strip().strip("'\"").strip()
    if re.fullmatch(pattern, value):
        return value, value != raw.strip()
    # Exactly one run with Cloudflare's format inside a pasted label or line.
    found = re.findall(rf"(?<![0-9A-Za-z_.]){pattern}(?![0-9A-Za-z_])", value)
    if len(found) == 1:
        return found[0], True
    return raw.strip(), False


def _composition(value: str) -> str:
    """What a value is made of, never the value itself."""
    import string

    v = value.strip()
    hexdig = sum(c in "0123456789abcdef" for c in v)
    upper = sum(c in string.ascii_uppercase for c in v)
    other_alpha = sum(c in string.ascii_lowercase and c not in "abcdef" for c in v)
    space = sum(c.isspace() for c in v)
    punct = sorted({c for c in v if c in string.punctuation})
    return (f"{len(v)} chars: {hexdig} lowercase hex, {upper} uppercase, {other_alpha} other letters, "
            f"{space} whitespace, punctuation {''.join(punct) or 'none'}")


def _service_token() -> tuple[str, str]:
    cid, _ = _token_value(os.environ.get("LCE_CF_ACCESS_CLIENT_ID", ""), _CLIENT_ID_RE)
    secret, _ = _token_value(os.environ.get("LCE_CF_ACCESS_CLIENT_SECRET", ""), _CLIENT_SECRET_RE)
    return cid, secret


def default_access(api_base: str) -> str | dict[str, str]:
    """Credentials for Cloudflare Access, never stored by the engine.

    A service token (LCE_CF_ACCESS_CLIENT_ID / LCE_CF_ACCESS_CLIENT_SECRET, e.g.
    GitHub Actions secrets) is sent as the two Access headers; Access validates
    it at the edge and forwards a signed JWT to the Worker. Otherwise the
    owner's cloudflared login token is used.
    """
    cid, secret = _service_token()
    if cid and secret:
        return {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": secret}
    if cid or secret:
        raise CloudError("set both LCE_CF_ACCESS_CLIENT_ID and LCE_CF_ACCESS_CLIENT_SECRET, or neither")
    return access_token_from_cloudflared(api_base)


def service_token_format() -> str | None:
    """Shape check of the service token from the environment, never its value.

    Cloudflare issues client IDs as `<32 hex>.access` and secrets as
    `cfast_<48 alphanumerics>` (since 2026-08-26) or 64 hex characters (older);
    anything else is usually a paste error (quotes, a header name, whitespace).
    Returns None when no service token is configured.
    """
    import re

    raw_id = os.environ.get("LCE_CF_ACCESS_CLIENT_ID", "")
    raw_secret = os.environ.get("LCE_CF_ACCESS_CLIENT_SECRET", "")
    if not raw_id.strip() and not raw_secret.strip():
        return None
    cid, id_label = _token_value(raw_id, _CLIENT_ID_RE)
    secret, secret_label = _token_value(raw_secret, _CLIENT_SECRET_RE)
    problems = []
    if not re.fullmatch(_CLIENT_ID_RE, cid):
        problems.append(f"client id is not '<32 hex>.access' ({_composition(raw_id)})")
    if not re.fullmatch(_CLIENT_SECRET_RE, secret):
        problems.append("client secret is neither 'cfast_' + 48 alphanumerics nor 64 hex "
                        f"({_composition(raw_secret)})")
    if problems:
        return "; ".join(problems)
    if id_label or secret_label:
        return "ok (a pasted label or quotes were removed before sending)"
    return "ok"


def access_headers(credential: str | dict[str, str]) -> dict[str, str]:
    return dict(credential) if isinstance(credential, dict) else {"cf-access-token": credential}


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
    def __init__(self, api_base: str, token: Callable[[], str | dict[str, str]],
                 transport: CloudTransport):
        self.api_base, self._token, self.transport = api_base.rstrip("/"), token, transport

    def call(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {**access_headers(self._token()), "content-type": "application/json",
                   "x-lce-client": "cli"}
        body = json.dumps(payload).encode() if payload is not None else None
        resp = self.transport.request(method, f"{self.api_base}/api{path}", headers, body)
        if resp.status >= 400:
            raise CloudError(f"cloud API {method} {path}: HTTP {resp.status} "
                             f"{resp.body.get('error', '')}".strip())
        return resp.body


def make_client(store: DataStore, transport: CloudTransport | None = None,
                token: Callable[[], str | dict[str, str]] | None = None) -> CloudClient:
    cfg = load_cloud_config(store)
    return CloudClient(cfg["api_base"], token or (lambda: default_access(cfg["api_base"])),
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


def push(store: DataStore, post_id: str, client: CloudClient, *, confirm=input, is_tty=None,
         decision_id: str | None = None) -> dict:
    """Delegate a READY_TO_PUBLISH post to the cloud. Interactive (typed phrase), or
    applying an owner's Control Center approval (`decision_id`, LCE-036), where
    the person already confirmed in the dashboard."""
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
    pub_file = store.post_dir(post_id) / "publication.json"
    if pub_file.exists():
        prior = json.loads(pub_file.read_text("utf-8"))
        # Only a cloud record that ended without a post may be re-delegated (after a withdrawal).
        if not (prior.get("runtime") == "cloud"
                and prior.get("state") in {"publish_failed", "not_published_confirmed"}):
            raise CloudError("a publish attempt exists for this post; it cannot be delegated")
    image_payload = _image_payload(store, post_id, approval)
    if decision_id is None:
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
    if image_payload:
        record["image_sha256"] = image_payload["sha256"]
    _atomic_write(delegation_path(store, post_id), json.dumps(record, indent=2) + "\n")
    store.log_event("cloud.delegated", post_id=post_id, approved_hash=h,
                    **({"decision_id": decision_id} if decision_id else {}))
    return result


def consent(store: DataStore, post_id: str, slot_id: str, client: CloudClient, *, confirm=input,
            is_tty=None) -> dict:
    if not load_delegation(store, post_id):
        raise CloudError("push the post to the cloud first (`lce cloud push`)")
    _confirm(confirm, is_tty, f"SCHEDULE {post_id}",
             f"The cloud will publish {post_id} automatically at slot {slot_id} "
             "(only if the kill switch is on).")
    result = client.call("POST", "/consents", {"post_id": post_id, "slot_id": slot_id,
                                               "confirm": f"SCHEDULE {post_id}"})
    store.log_event("cloud.consent", post_id=post_id, slot_id=slot_id,
                    consent_id=result.get("consent_id"))
    return result


ATTEMPT_KEYS = ("attempt", "intent_at", "finished_at", "outcome", "http_status", "reason",
                "message", "retryable", "sent")
CLOUD_PUB_STATES = {"publishing", "published", "publish_failed", "needs_reconcile",
                    "not_published_confirmed"}


def _mirror_publication(store: DataStore, post_id: str, pub: dict, delegation: dict) -> None:
    """Write the cloud's publication record locally (read-only copy) so verification,
    reconciliation and analytics see cloud publications like local ones."""
    if pub.get("state") not in CLOUD_PUB_STATES:
        return
    resolution = pub.get("resolution")
    if isinstance(resolution, str):
        try:
            resolution = json.loads(resolution)
        except ValueError:
            resolution = None
    record = {"post_id": post_id, "provider": "linkedin_api", "runtime": "cloud",
              "idempotency_key": pub["idempotency_key"], "approved_hash": pub["approved_hash"],
              "commentary_hash": pub["commentary_hash"], "state": pub["state"],
              "attempts": [{k: a[k] for k in ATTEMPT_KEYS if a.get(k) is not None}
                           for a in pub.get("attempts") or []]}
    for key in ("api_version", "author", "remote_id", "url", "published_at", "verified_by"):
        if pub.get(key):
            record[key] = pub[key]
    if isinstance(resolution, dict) and resolution.get("decision") in {"published", "not_published"}:
        record["resolution"] = {k: resolution[k] for k in ("at", "decision", "by") if k in resolution}
    image_hash = delegation.get("image_sha256")
    if pub.get("image_urn") and image_hash:
        record["image"] = {"urn": pub["image_urn"], "sha256": image_hash}
    from lce.validate import validate_doc

    errors = validate_doc("publication", record)
    if errors:
        raise CloudError(f"cloud publication record for {post_id} is invalid: " + "; ".join(errors))
    _atomic_write(store.post_dir(post_id) / "publication.json",
                  json.dumps(record, indent=2, ensure_ascii=False) + "\n")


def pull(store: DataStore, client: CloudClient) -> list[dict]:
    """Mirror cloud state into the local lifecycle (read-only on the cloud).

    - PUBLISHED / PUBLISH_FAILED / NEEDS_RECONCILE: local state follows; the cloud's
      publication record is copied to posts/<id>/publication.json.
    - READY_TO_PUBLISH after a rearm or a "not published" reconciliation: a local
      PUBLISH_FAILED / NEEDS_RECONCILE post becomes READY_TO_PUBLISH again.
    - WITHDRAWN: the delegation ends, so the owner may publish locally or re-delegate.
    - The cloud's approved hash must equal the delegated one; otherwise nothing is
      mirrored for that post and a problem is reported.
    """
    snap = client.call("GET", "/snapshot")
    cloud_posts = {p["post_id"]: p for p in snap.get("posts", [])}
    pubs = {p["post_id"]: p for p in snap.get("publications", [])}
    changes = []
    for post_id in store.post_ids():
        delegation = load_delegation(store, post_id)
        if not delegation or post_id not in cloud_posts:
            continue
        cp = cloud_posts[post_id]
        if cp.get("approved_hash") != delegation.get("approved_hash"):
            changes.append({"post_id": post_id, "problem": "cloud approved hash differs from the "
                                                            "delegated one; nothing mirrored"})
            continue
        cloud_state = cp["state"]
        post = store.load_post(post_id)
        _atomic_write(store.post_dir(post_id) / "cloud.json",
                      json.dumps({"state": cloud_state, "publication": pubs.get(post_id),
                                  "pulled_at": iso_utc(now())}, indent=2, ensure_ascii=False) + "\n")
        if pubs.get(post_id):
            _mirror_publication(store, post_id, pubs[post_id], delegation)
        local = PostState(post["state"])
        if cloud_state == "WITHDRAWN":
            delegation_path(store, post_id).unlink()
            if local == S.PUBLISH_FAILED:  # approved and never published: ready again locally
                set_state(store, post, S.READY_TO_PUBLISH, "withdrawn from the cloud")
                local = S.READY_TO_PUBLISH
            store.log_event("cloud.withdrawn", post_id=post_id)
            changes.append({"post_id": post_id, "state": local.value, "delegation": "ended"})
            continue
        if cloud_state == "READY_TO_PUBLISH" and local in {S.PUBLISH_FAILED, S.NEEDS_RECONCILE}:
            set_state(store, post, S.READY_TO_PUBLISH, "mirrored from cloud (ready again)")
            changes.append({"post_id": post_id, "state": S.READY_TO_PUBLISH.value})
            continue
        target = {"PUBLISHED": S.PUBLISHED, "PUBLISH_FAILED": S.PUBLISH_FAILED,
                  "NEEDS_RECONCILE": S.NEEDS_RECONCILE}.get(cloud_state)
        if target and local != target:
            if local in {S.READY_TO_PUBLISH, S.PUBLISH_FAILED}:
                if local == S.PUBLISH_FAILED:
                    post = set_state(store, post, S.READY_TO_PUBLISH, "mirrored from cloud")
                post = set_state(store, post, S.PUBLISHING, "mirrored from cloud")
                local = S.PUBLISHING
            if local in {S.PUBLISHING, S.NEEDS_RECONCILE}:
                set_state(store, post, target, f"mirrored from cloud ({cloud_state})")
                changes.append({"post_id": post_id, "state": target.value})
    return changes


def configure_payload(store: DataStore) -> dict:
    """Settings the Worker needs, from the private config. Never the kill switch, never a token."""
    settings = store.settings()
    body = {k: settings[k] for k in ("timezone", "cadence") if settings.get(k)}
    li = store.read_doc(store.root / "config" / "linkedin.yaml")
    for key in ("api_version", "person_urn", "visibility", "token_expires_at", "display_name",
                "profile_url"):
        if li.get(key):
            body[key] = str(li[key])
    if li.get("api_version") and li.get("person_urn"):
        body["provider"] = "linkedin_api"
    return body


def configure(store: DataStore, client: CloudClient) -> dict:
    body = configure_payload(store)
    if not body:
        raise CloudError("nothing to configure: set timezone/cadence (and config/linkedin.yaml)")
    out = client.call("PUT", "/settings", body)
    store.log_event("cloud.configured", keys=sorted(body))
    return out


def slot_for_post(store: DataStore, post_id: str) -> str:
    """The slot of the local scheduled job this post fulfils (for `lce cloud consent`)."""
    from lce.jobs import list_jobs

    slots = [j["slot"]["slot_id"] for j in list_jobs(store) if j.get("post_id") == post_id]
    if len(slots) != 1:
        raise CloudError("give --slot: the post is not linked to exactly one scheduled job")
    return slots[0]


# ── doctor: production preflight, one line per owner gate ─────────────
OK, ACTION, FAIL = "ok", "action", "fail"


def _check(name: str, status: str, detail: str, action: str = "") -> dict:
    return {"check": name, "status": status, "detail": detail, "action": action}


def _migrations_check(transport: CloudTransport, base: str, credential) -> dict:
    """D1 schema by migration name, read from the Worker (GET /api/migrations)."""
    r = transport.request("GET", f"{base}/api/migrations",
                          {**access_headers(credential), "x-lce-client": "cli"}, None)
    if r.status != 200:
        return _check("migrations", FAIL, f"/api/migrations HTTP {r.status} "
                      f"{r.body.get('error') or r.body.get('_raw') or ''}".strip())
    applied, pending = r.body.get("applied", []), r.body.get("pending", [])
    detail = f"applied: {', '.join(applied) or 'none'}; pending: {', '.join(pending) or 'none'}"
    return _check("migrations", ACTION if pending else OK, detail,
                  "lce cloud migrate --apply (or wrangler d1 migrations apply lce --remote)"
                  if pending else "")


IDENTITY_FIX = {
    "token_rejected": "LinkedIn rejected LINKEDIN_TOKEN (invalid, expired or revoked): create a new "
                      "token and replace the Worker secret",
    "forbidden": "the token lacks the openid scope: create it with openid, profile, w_member_social",
    "token_missing": "add LINKEDIN_TOKEN to the production Worker as type Secret",
}


def linkedin_identity(transport: CloudTransport, base: str, credential) -> CloudResponse:
    """GET /api/linkedin/identity: the Worker's read-only userinfo check (LCE-035)."""
    return transport.request("GET", f"{base}/api/linkedin/identity",
                             {**access_headers(credential), "x-lce-client": "cli"}, None)


def _identity_check(transport: CloudTransport, base: str, credential, settings: dict) -> dict:
    """LinkedIn authentication with the Worker's runtime token, and person_urn validation."""
    r = linkedin_identity(transport, base, credential)
    b = r.body
    if r.status == 404:
        return _check("linkedin identity", ACTION, "the deployed Worker has no /api/linkedin/identity",
                      "deploy the engine version with LCE-035 (Workers Builds on main)")
    status = str(b.get("status", ""))
    if r.status == 200 and b.get("ok") is True:
        urn, configured = b.get("person_urn"), settings.get("person_urn")
        if not configured:
            return _check("linkedin identity", ACTION,
                          f"token verified with LinkedIn; member {urn}; person_urn not set",
                          f"set person_urn: {urn} in config/linkedin.yaml (lce cloud identity --write), "
                          "then lce cloud configure")
        if b.get("person_urn_matches") is not True:
            return _check("linkedin identity", FAIL,
                          f"settings person_urn {configured} is not the token's member {urn}",
                          f"set person_urn: {urn} in config/linkedin.yaml (lce cloud identity --write), "
                          "then lce cloud configure; or use the token of the intended account")
        return _check("linkedin identity", OK, f"token verified with LinkedIn; {urn} matches settings")
    why = b.get("reason") or b.get("error") or b.get("_raw") or ""
    detail = f"{status or 'HTTP ' + str(r.status)}: {why}"
    if b.get("http_status"):
        detail += f" (LinkedIn HTTP {b['http_status']})"
    return _check("linkedin identity", FAIL, detail.strip(),
                  IDENTITY_FIX.get(status, "retry later; check LinkedIn status if it persists"))


# Same rules as lce.publish.linkedin (not imported: only the CLI may reach the publishing path).
PERSON_URN_RE = re.compile(r"^urn:li:person:[A-Za-z0-9_-]+$")
VERSION_RE = re.compile(r"^\d{6}$")


def write_person_urn(store: DataStore, person_urn: str, api_version: str | None = None) -> dict:
    """Record the verified person URN in config/linkedin.yaml (private data repo).
    Keeps every other key; a new file needs an explicit api_version."""
    if not PERSON_URN_RE.match(person_urn or ""):
        raise CloudError(f"not a person URN: {person_urn!r}")
    path = store.root / "config" / "linkedin.yaml"
    doc = store.read_doc(path)
    if api_version:
        doc["api_version"] = api_version
    if not VERSION_RE.match(str(doc.get("api_version", ""))):
        raise CloudError("config/linkedin.yaml needs api_version (YYYYMM): pass --api-version")
    doc["api_version"] = str(doc["api_version"])
    doc["person_urn"] = person_urn
    doc.setdefault("visibility", "PUBLIC")
    store.write_doc(path, "linkedin", doc)
    store.log_event("linkedin.person_urn_recorded", source="cloud identity")
    return doc


def migrate(client: CloudClient, apply: bool = False) -> dict:
    if not apply:
        return client.call("GET", "/migrations")
    return client.call("POST", "/migrations", {"confirm": "APPLY MIGRATIONS"})


def doctor(store: DataStore, transport: CloudTransport | None = None,
           token: Callable[[], str | dict[str, str]] | None = None,
           today: date | None = None, probe: StatusTransport | None = None) -> list[dict]:
    """Read-only checks of the deployed Worker, in dependency order. Stops at the
    first gate that hides everything behind it. Never sends a mutation."""
    out: list[dict] = []
    try:
        cfg = load_cloud_config(store)
    except CloudError as exc:
        return [_check("config", ACTION, str(exc),
                       "add config/cloud.yaml with api_base (templates/private-data)")]
    base = cfg["api_base"].rstrip("/")
    out.append(_check("config", OK, base))
    transport = transport or UrllibCloudTransport()
    try:
        health = transport.request("GET", f"{base}/api/health", {}, None)
    except CloudError as exc:
        return out + [_check("worker", FAIL, str(exc), "check api_base and the Workers Builds deploy")]
    edge = health.status in (302, 401, 403)   # Access protects the whole hostname at the edge
    if not edge and (health.status != 200 or health.body.get("ok") is not True):
        return out + [_check("worker", FAIL, f"/api/health answered HTTP {health.status}",
                             "check api_base and the Workers Builds deploy")]
    try:
        credential = (token or (lambda: default_access(base)))()
    except CloudError as exc:
        out.append(_check("worker", OK, f"/api/health behind Cloudflare Access ({health.status})"
                          if edge else "/api/health 200"))
        return out + [_check("access login", ACTION, str(exc),
                             f"cloudflared access login {base} (or set an Access service token)")]
    if edge:
        health = transport.request("GET", f"{base}/api/health", access_headers(credential), None)
        if health.status != 200 or health.body.get("ok") is not True:
            why = health.body.get("_raw") or f"HTTP {health.status}"
            shape = service_token_format()
            if shape is not None and not shape.startswith("ok"):
                return out + [_check("worker", FAIL, f"/api/health with credentials: {why}"),
                              _check("service token format", FAIL, shape,
                                     "re-enter both GitHub secrets: the raw Client ID and Client "
                                     "Secret values only, no quotes or header names")]
            hint = ("service token format ok, so Access does not accept it for this application: "
                    "check that the Service Auth policy includes this service token and is attached "
                    "to the application that covers this hostname" if shape and shape.startswith("ok") else
                    "Access did not let the credential through: for a service token check the "
                    "Service Auth policy on the Worker's Access application; for cloudflared run "
                    f"cloudflared access login {base} again")
            return out + [_check("worker", FAIL, f"/api/health with credentials: {why}", hint)]
        out.append(_check("worker", OK, "/api/health 200 through Cloudflare Access"))
    else:
        out.append(_check("worker", OK, "/api/health 200"))
    snap = transport.request("GET", f"{base}/api/snapshot",
                             {**access_headers(credential), "x-lce-client": "cli"}, None)
    err = str(snap.body.get("error", ""))
    if snap.status == 503 and "Access" in err:
        return out + [_check("access", ACTION, f"Worker: {err}",
                             "the Worker has no ACCESS_TEAM_DOMAIN / ACCESS_AUD at runtime: add both "
                             "on the production Worker (Settings → Variables and Secrets) as type "
                             "Secret, or `npx wrangler secret put …` in cloud/; values are the Access "
                             "team domain and the application's AUD tag")]
    if snap.status in (401, 403):
        return out + [_check("access", FAIL, f"HTTP {snap.status} {err}".strip(),
                             "the Access token does not match ACCESS_AUD / team; check both secrets")]
    if snap.status == 503 and "schema" in err:
        return out + [_check("access", OK, "authenticated"),
                      _migrations_check(transport, base, credential)]
    if snap.status != 200:
        return out + [_check("snapshot", FAIL, f"HTTP {snap.status} {err}".strip())]
    out.append(_check("access", OK, "authenticated"))
    out.append(_migrations_check(transport, base, credential))
    pipe = transport.request("GET", f"{base}/api/pipeline",
                             {**access_headers(credential), "x-lce-client": "cli"}, None)
    perr = str(pipe.body.get("error", ""))
    if pipe.status == 503 and "schema" in perr:
        out.append(_check("database", ACTION,
                          "migrations 0001–0002 applied; 0003 (pipeline_snapshot) missing",
                          "cd cloud && npx wrangler d1 migrations apply lce --remote"))
    elif pipe.status in (200, 404):
        synced = pipe.status == 200
        received = pipe.body.get("meta", {}).get("mirror", {}).get("received_at")
        out.append(_check("database", OK, "schema present"))
        out.append(_check("pipeline mirror", OK if synced else ACTION,
                          f"synced {received}" if synced else "no snapshot yet",
                          "" if synced else "lce cloud sync (or the private cloud-sync workflow)"))
    else:
        out.append(_check("database", FAIL, f"/api/pipeline HTTP {pipe.status} {perr}".strip()))
    probe = probe or StatusTransport()
    pages = {"/": b"LCE Cloud Control Center", "/pipeline/": b"LCE Control Center",
             "/pipeline/config.js": b'"snapshotUrl": "/api/pipeline"'}
    bad = []
    for path, marker in pages.items():
        try:
            code, body = probe.status("GET", f"{base}{path}", headers=access_headers(credential))
        except CloudError as exc:
            bad.append(f"{path}: {exc}")
            continue
        if code != 200 or marker not in body:
            bad.append(f"{path}: HTTP {code}")
    out.append(_check("dashboard", FAIL if bad else OK,
                      "; ".join(bad) if bad else "/, /pipeline/ and its config load through Access"))
    s = snap.body.get("settings", {})
    missing = [k for k in ("timezone", "cadence", "api_version", "person_urn") if not s.get(k)]
    out.append(_check("settings", ACTION if missing else OK,
                      "missing: " + ", ".join(missing) if missing
                      else "timezone, cadence, LinkedIn config set",
                      "lce cloud configure" if missing else ""))
    enabled = s.get("provider") == "linkedin_api"
    out.append(_check("provider", OK if enabled else ACTION, f"provider {s.get('provider')}",
                      "" if enabled else "fill config/linkedin.yaml (api_version, person_urn), "
                                         "then lce cloud configure"))
    if not s.get("token_present"):
        out.append(_check("linkedin token", ACTION, "no LINKEDIN_TOKEN secret",
                          "npx wrangler secret put LINKEDIN_TOKEN (owner credential)"))
    else:
        exp = s.get("token_expires_at")
        days = None
        if exp:
            days = (datetime.fromisoformat(exp.replace("Z", "+00:00")).date() - (today or now().date())).days
        if days is not None and days < 0:
            out.append(_check("linkedin token", ACTION, f"expired {-days} day(s) ago",
                              "create a new token and replace the secret"))
        else:
            out.append(_check("linkedin token", OK if days is None or days > 7 else ACTION,
                              "present" + (f", {days} day(s) left" if days is not None else
                                           ", expiry unknown (lce cloud configure sends it)"),
                              "" if days is None or days > 7 else "renew the token soon"))
    if s.get("token_present"):
        out.append(_identity_check(transport, base, credential, s))
    if snap.body.get("schedule_error"):
        # Without timezone/cadence the schedule cannot load: an open setup step, not a fault.
        unset = not s.get("timezone") or not s.get("cadence")
        out.append(_check("schedule", ACTION if unset else FAIL, snap.body["schedule_error"],
                          "lce cloud configure"))
    out.append(_check("kill switch", OK, "auto-publish ON" if s.get("auto_publish") else
                      "auto-publish OFF (nothing publishes until you enable it with its phrase)"))
    return out



# ── sync: private-pipeline mirror for the cloud Web Control Center (LCE-013) ──
def sync_payload(store: DataStore) -> dict:
    """The dashboard snapshot, without local paths or the private repository's remote."""
    from lce.dashboard.snapshot import build_snapshot

    snap = build_snapshot(store, mode="real", data_label="private data (cloud mirror)")
    git = snap["meta"]["data"].get("git") or {}
    snap["meta"]["data"]["git"] = {k: git.get(k) for k in ("available", "head", "branch", "dirty_files")}
    problems = leak_problems(store, json.dumps(snap, default=str), git.get("remote") or "")
    if problems:
        raise CloudError(f"{problems[0]}; refusing to upload")
    return snap


def leak_problems(store: DataStore, text: str, remote: str = "") -> list[str]:
    """What must never be in a cloud snapshot: local paths, the private remote, credentials."""
    from lce.dashboard.snapshot import SECRET_RE

    out = []
    if str(store.root) in text:
        out.append("the snapshot contains a local path")
    remote = remote.strip().removesuffix(".git")
    if remote and remote in text:
        out.append("the snapshot contains the private repository URL")
    for name in ("LCE_CF_ACCESS_CLIENT_ID", "LCE_CF_ACCESS_CLIENT_SECRET", "LCE_CF_ACCESS_TOKEN",
                 "GITHUB_TOKEN", "LINKEDIN_TOKEN"):
        value = os.environ.get(name, "").strip()
        if len(value) >= 8 and value in text:
            out.append(f"the snapshot contains the value of {name}")
    cid, secret = _service_token()
    for label, value in (("service token id", cid), ("service token secret", secret)):
        if len(value) >= 8 and value in text:
            out.append(f"the snapshot contains the {label}")
    if SECRET_RE.search(text):
        out.append("the snapshot contains a secret-shaped string")
    return out


def sync(store: DataStore, client: CloudClient, verify: bool = False) -> dict:
    snap = sync_payload(store)
    out = client.call("PUT", "/pipeline", snap)
    store.log_event("cloud.synced", bytes=out.get("bytes"), sha256=out.get("sha256"))
    if verify:
        out["checks"] = verify_sync(store, client, snap, out)
    return out


def verify_sync(store: DataStore, client: CloudClient, sent: dict, stored: dict) -> list[dict]:
    """Read the mirror back from production and prove it is what was sent, and clean."""
    got = client.call("GET", "/pipeline")
    mirror = got.get("meta", {}).get("mirror", {})
    checks = []
    same_sha = bool(stored.get("sha256")) and mirror.get("sha256") == stored.get("sha256")
    read_sha, sent_sha = str(mirror.get("sha256"))[:12], str(stored.get("sha256"))[:12]
    checks.append(_check("mirror stored", OK if same_sha else FAIL,
                         f"D1 row sha256 {read_sha}… received {mirror.get('received_at')}"
                         if same_sha else f"stored {sent_sha} ≠ read back {read_sha}"))
    ids = lambda snap: sorted(p.get("post_id") for p in snap.get("posts", []))  # noqa: E731
    same_posts = ids(got) == ids(sent)
    checks.append(_check("mirror content", OK if same_posts else FAIL,
                         f"{len(ids(got))} post(s), {len(got.get('research', []))} research item(s), "
                         f"generated {got.get('meta', {}).get('generated_at')}" if same_posts
                         else "posts read back differ from those sent"))
    body = {k: v for k, v in got.items() if k != "meta"}
    body["meta"] = {k: v for k, v in got.get("meta", {}).items() if k != "mirror"}
    leaks = leak_problems(store, json.dumps(body, default=str))
    checks.append(_check("mirror privacy", FAIL if leaks else OK,
                         "; ".join(leaks) if leaks else "no local path, private remote or credential"))
    return checks



# ── smoke: unauthenticated production checks (LCE-018) ────────────────
# Anything 2xx here without credentials is a security failure. Redirects are
# not followed: Cloudflare Access answers a browser without a session with a
# 302 to its login page, which must count as "refused", not as the login HTML.
PROTECTED = [("GET", "/", None), ("GET", "/pipeline/", None), ("GET", "/api/snapshot", None),
             ("GET", "/api/pipeline", None), ("PUT", "/api/settings", {"auto_publish": True}),
             ("GET", "/api/linkedin/identity", None), ("GET", "/api/decisions", None),
             ("POST", "/api/decisions", {"action": "approve"}),
             ("POST", "/api/posts/20260101-smoke/publish-now", {}), ("POST", "/api/consents", {}),
             ("PUT", "/api/pipeline", {"schema": 1, "meta": {"mode": "real"}})]


class StatusTransport:
    """HTTPS status probe: no redirects, no credentials, body ignored."""

    def __init__(self, timeout: float = 15):
        self.opener = urllib.request.build_opener(_NoRedirect())
        self.timeout = timeout

    def status(self, method: str, url: str, body: dict | None = None,
               headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        if not url.startswith("https://"):
            raise CloudError("the cloud API must use https")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "User-Agent": _user_agent(), "content-type": "application/json", "x-lce-client": "cli",
            **(headers or {})})
        self.last_location = ""
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:  # noqa: S310
                return resp.status, resp.read(4096)
        except urllib.error.HTTPError as exc:
            self.last_location = exc.headers.get("Location", "")
            return exc.code, exc.read(4096) or b""
        except OSError as exc:
            raise CloudError(f"cloud API unreachable: {type(exc).__name__}") from exc


def smoke(api_base: str, transport: StatusTransport | None = None, *, wait_seconds: int = 0,
          sleep: Callable[[float], None] | None = None) -> list[dict]:
    """Reachability, then fail-closed checks on every protected path. Sends no credential."""
    import time

    base = api_base.rstrip("/")
    t = transport or StatusTransport()
    sleep = sleep or time.sleep
    deadline_tries = max(1, wait_seconds // 15 + 1)
    out: list[dict] = []
    code, body, last_err = 0, b"", ""
    for i in range(deadline_tries):
        try:
            code, body = t.status("GET", f"{base}/api/health")
            if code == 200 or code in (302, 401, 403):
                break
        except CloudError as exc:
            last_err = str(exc)
        if i + 1 < deadline_tries:
            sleep(15)
    if code == 200 and b'"ok":true' in body.replace(b" ", b""):
        out.append(_check("worker", OK, "/api/health 200"))
    elif code in (302, 401, 403):
        out.append(_check("worker", OK, f"/api/health behind Cloudflare Access at the edge ({code})"))
    else:
        return [_check("worker", FAIL, last_err or f"/api/health answered HTTP {code}",
                       "check the Workers Builds deploy and the URL")]
    try:  # the one public page (LinkedIn Developer Portal must open it without a login)
        code, page = t.status("GET", f"{base}/privacy")
    except CloudError as exc:
        code, page = 0, str(exc).encode()
    if code == 200 and b"<h1>Privacy Policy" in page:
        out.append(_check("public privacy policy", OK, f"{base}/privacy 200, policy served"))
    elif code in (302, 401, 403):
        out.append(_check("public privacy policy", ACTION,
                          f"{base}/privacy is behind Cloudflare Access ({code}; "
                          f"{classify_refusal(code, page, getattr(t, 'last_location', ''))})",
                          "Zero Trust → Access → Applications: add an application for the path "
                          "/privacy on this hostname with a Bypass policy (Include: Everyone)"))
    else:
        out.append(_check("public privacy policy", FAIL, f"{base}/privacy answered HTTP {code}"))
    for method, path, payload in PROTECTED:
        try:
            status, refused_body = t.status(method, f"{base}{path}", payload)
        except CloudError as exc:
            out.append(_check(f"{method} {path}", FAIL, str(exc)))
            continue
        if 200 <= status < 300:
            out.append(_check(f"{method} {path}", FAIL, f"HTTP {status} without credentials",
                              "the Worker must refuse unauthenticated requests: check Access"))
        else:
            reason = classify_refusal(status, refused_body, getattr(t, "last_location", ""))
            out.append(_check(f"{method} {path}", OK, f"refused without credentials ({status}; {reason})"))
    return out
