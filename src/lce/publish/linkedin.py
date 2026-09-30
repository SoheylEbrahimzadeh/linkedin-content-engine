"""Official LinkedIn Posts API adapter (member posts, text only).

Facts this adapter relies on (docs/PUBLISHING.md lists the sources):
- POST https://api.linkedin.com/rest/posts with `Linkedin-Version: YYYYMM` and
  `X-Restli-Protocol-Version: 2.0.0`; permission `w_member_social`.
- Success is `201` with the post URN in the `x-restli-id` response header.
- `lifecycleState` must be `PUBLISHED` on create: the API cannot schedule.
- Reading a member's posts needs `r_member_social`, which is restricted, so this
  adapter cannot look up whether a post exists (`can_find_existing = False`).
- The API offers no idempotency key. An ambiguous outcome is reported as
  AMBIGUOUS and never retried here.

HTTP goes through an injectable transport; tests use a fake one.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from lce.publish.base import (
    PROVIDER_CAPABILITIES,
    Issue,
    Outcome,
    PostPayload,
    ProviderCapabilities,
    PublishResult,
    RemotePost,
    RemoteStatus,
    UnsupportedCapability,
)
from lce.publish.credentials import CredentialError, TokenStore
from lce.publish.little import to_little

POSTS_URL = "https://api.linkedin.com/rest/posts"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
PERSON_URN_RE = re.compile(r"^urn:li:person:[A-Za-z0-9_-]+$")
VERSION_RE = re.compile(r"^\d{6}$")
POST_URN_RE = re.compile(r"^urn:li:(share|ugcPost):\d+$")
DEFAULT_MAX_CHARS = 3000  # LinkedIn's post limit; the API answers 400 FIELD_LENGTH_TOO_LONG
REJECTED_STATUSES = {400, 401, 403, 404, 422}


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes = b""


class TransportError(Exception):
    """`sent` is False only when the request certainly never reached the server."""

    def __init__(self, message: str, *, sent: bool):
        super().__init__(message)
        self.sent = sent


class Transport(Protocol):
    def request(self, method: str, url: str, headers: dict[str, str], body: bytes | None,
                timeout: float) -> HttpResponse: ...


class UrllibTransport:
    """Real HTTPS transport (stdlib). Only used by `lce publish` / `lce linkedin whoami`."""

    def request(self, method, url, headers, body, timeout):
        if not url.startswith("https://api.linkedin.com/"):
            raise TransportError("refusing a non-LinkedIn URL", sent=False)
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return HttpResponse(resp.status, {k.lower(): v for k, v in resp.headers.items()},
                                    resp.read(65536))
        except urllib.error.HTTPError as exc:
            return HttpResponse(exc.code, {k.lower(): v for k, v in exc.headers.items()},
                                exc.read(65536))
        except urllib.error.URLError as exc:
            reason = exc.reason
            not_sent = isinstance(reason, (socket.gaierror, ConnectionRefusedError))
            raise TransportError(f"network error: {type(reason).__name__}", sent=not not_sent) \
                from exc
        except (TimeoutError, ConnectionError, OSError) as exc:
            raise TransportError(f"network error after sending: {type(exc).__name__}",
                                 sent=True) from exc


@dataclass(frozen=True)
class LinkedInConfig:
    api_version: str
    person_urn: str
    visibility: str = "PUBLIC"
    max_chars: int = DEFAULT_MAX_CHARS
    timeout_seconds: float = 30.0

    def problems(self) -> list[str]:
        out = []
        if not VERSION_RE.match(self.api_version or ""):
            out.append("api_version must be YYYYMM")
        if not PERSON_URN_RE.match(self.person_urn or ""):
            out.append("person_urn must look like urn:li:person:<id> (see `lce linkedin whoami`)")
        if self.visibility not in {"PUBLIC", "CONNECTIONS", "LOGGED_IN"}:
            out.append("visibility must be PUBLIC, CONNECTIONS or LOGGED_IN")
        return out


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


def _error_text(resp: HttpResponse) -> str:
    try:
        data = json.loads(resp.body.decode("utf-8", "replace") or "{}")
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    parts = [str(data.get(k)) for k in ("code", "serviceErrorCode", "message") if data.get(k)]
    return " ".join(parts)[:300]


@dataclass
class LinkedInPublisher:
    config: LinkedInConfig
    tokens: TokenStore
    transport: Transport
    name: str = "linkedin_api"
    supports_native_scheduling: bool = False
    capabilities: ProviderCapabilities = field(
        default_factory=lambda: PROVIDER_CAPABILITIES["linkedin_api"])

    # ── building the request (no token, no network) ─────────────────
    def validate(self, post: PostPayload) -> list[Issue]:
        issues = [Issue("config", p) for p in self.config.problems()]
        text = post.text.strip()
        if not text:
            issues.append(Issue("text.missing", "post text is empty"))
        if len(text) > self.config.max_chars:
            issues.append(Issue("text.too_long", f"{len(text)} characters; the limit is "
                                f"{self.config.max_chars}"))
        if post.scheduled_at_utc:
            issues.append(Issue("unsupported.schedule", "LinkedIn's API cannot schedule posts"))
        if post.media_urls:
            issues.append(Issue("unsupported.media", "media is not supported in Phase 3"))
        return issues

    def build_request(self, post: PostPayload) -> tuple[str, dict[str, str], dict]:
        headers = {"Linkedin-Version": self.config.api_version,
                   "X-Restli-Protocol-Version": "2.0.0",
                   "Content-Type": "application/json"}
        body = {
            "author": self.config.person_urn,
            "commentary": to_little(post.text.strip()),
            "visibility": self.config.visibility,
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                             "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        return POSTS_URL, headers, body

    # ── sending ─────────────────────────────────────────────────────
    def publish(self, post: PostPayload) -> PublishResult:
        issues = self.validate(post)
        if issues:
            return PublishResult(Outcome.REJECTED, detail={
                "reason": "validation", "sent": False, "retryable": False,
                "issues": [f"{i.code}: {i.message}" for i in issues]})
        try:
            token = self.tokens.get()
        except CredentialError as exc:
            return PublishResult(Outcome.REJECTED, detail={
                "reason": "credentials", "sent": False, "retryable": False,
                "message": str(exc)})
        url, headers, body = self.build_request(post)
        headers = {**headers, "Authorization": f"Bearer {token.reveal()}"}
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            resp = self.transport.request("POST", url, headers, payload,
                                          self.config.timeout_seconds)
        except TransportError as exc:
            if exc.sent:
                return PublishResult(Outcome.AMBIGUOUS, detail={
                    "reason": "transport", "sent": True, "message": str(exc)})
            return PublishResult(Outcome.REJECTED, detail={
                "reason": "transport", "sent": False, "retryable": True, "message": str(exc)})
        return self._map(resp)

    def _map(self, resp: HttpResponse) -> PublishResult:
        status = resp.status
        detail = {"http_status": status, "sent": True, "message": _error_text(resp)}
        if status == 201:
            urn = resp.headers.get("x-restli-id", "").strip()
            if POST_URN_RE.match(urn):
                return PublishResult(Outcome.PUBLISHED, remote_id=urn, url=post_url(urn),
                                     detail=detail)
            return PublishResult(Outcome.AMBIGUOUS, detail={
                **detail, "reason": "created without a recognizable post URN"})
        if status in REJECTED_STATUSES:
            return PublishResult(Outcome.REJECTED, detail={
                **detail, "reason": f"http_{status}", "retryable": False})
        if status == 429:
            return PublishResult(Outcome.REJECTED, detail={
                **detail, "reason": "rate_limited", "retryable": True})
        # 409 (conflict), 5xx and any other status: the post may or may not exist.
        return PublishResult(Outcome.AMBIGUOUS, detail={**detail, "reason": f"http_{status}"})

    # ── capabilities LinkedIn does not grant to member apps ──────────
    def find_existing(self, idempotency_key: str, content_hash: str) -> RemotePost | None:
        raise UnsupportedCapability("LinkedIn member posts cannot be looked up without "
                                    "r_member_social; reconcile manually")

    def get_status(self, remote_id: str) -> RemoteStatus:
        raise UnsupportedCapability("post status lookup needs r_member_social (restricted)")

    # ── identity ────────────────────────────────────────────────────
    def whoami(self) -> dict:
        """GET /v2/userinfo (OpenID Connect). Returns sub, name and the derived person URN."""
        token = self.tokens.get()
        resp = self.transport.request("GET", USERINFO_URL,
                                      {"Authorization": f"Bearer {token.reveal()}"}, None,
                                      self.config.timeout_seconds)
        if resp.status != 200:
            raise CredentialError(f"userinfo failed with HTTP {resp.status} {_error_text(resp)}")
        data = json.loads(resp.body.decode("utf-8"))
        sub = str(data.get("sub", ""))
        return {"sub": sub, "name": data.get("name"), "person_urn": f"urn:li:person:{sub}"}
