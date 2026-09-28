"""Provider-agnostic publishing contract.

No provider adapters exist yet. Candidate providers (official LinkedIn API,
a third-party scheduler, manual fallback) are evaluated in a later phase, and
every provider-specific claim (scopes, token lifetime, limits, pricing) must be
verified against that provider's official documentation before an adapter is
written. See docs/PUBLISHING.md.

Idempotency contract every adapter must honour:
- `publish` is called at most once per idempotency key without a prior
  `find_existing` check. An ambiguous failure (timeout, 5xx after the request
  was sent) must NOT be retried blindly: the caller moves the post to
  NEEDS_RECONCILE and asks `find_existing` whether the post already exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: Severity = Severity.ERROR


@dataclass(frozen=True)
class PostPayload:
    post_id: str
    idempotency_key: str
    text: str
    content_hash: str
    language: str
    scheduled_at_utc: str | None = None
    media_urls: tuple[str, ...] = ()


class Outcome(StrEnum):
    PUBLISHED = "published"
    SCHEDULED = "scheduled"
    REJECTED = "rejected"  # definitively not created; safe to fix and retry
    AMBIGUOUS = "ambiguous"  # may or may not exist remotely; reconcile first


@dataclass(frozen=True)
class PublishResult:
    outcome: Outcome
    remote_id: str | None = None
    url: str | None = None
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RemotePost:
    remote_id: str
    url: str | None
    content_hash: str | None


class RemoteStatus(StrEnum):
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"
    NOT_FOUND = "not_found"


@runtime_checkable
class Publisher(Protocol):
    name: str
    supports_native_scheduling: bool

    def validate(self, post: PostPayload) -> list[Issue]: ...

    def publish(self, post: PostPayload) -> PublishResult: ...

    def find_existing(self, idempotency_key: str, content_hash: str) -> RemotePost | None: ...

    def get_status(self, remote_id: str) -> RemoteStatus: ...
