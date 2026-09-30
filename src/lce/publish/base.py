"""Provider-agnostic publishing contract.

Adapters: `lce.publish.linkedin` (official LinkedIn Posts API) is the only
provider. See docs/PUBLISHING.md.

Contract every adapter must honour:
- `publish` returns PUBLISHED, REJECTED (definitely not created) or AMBIGUOUS
  (the request may have reached the provider; outcome unknown).
- An AMBIGUOUS result is never retried by code. The caller moves the post to
  NEEDS_RECONCILE and a human decides.
- `capabilities` must be truthful. If the provider cannot look up existing
  posts (`can_find_existing = False`), `find_existing`/`get_status` raise
  UnsupportedCapability instead of guessing.
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
class ImageAttachment:
    """The approved image: bytes as approved (hash-checked), alt text for accessibility."""
    data: bytes
    alt_text: str
    sha256: str
    file_name: str


@dataclass(frozen=True)
class PostPayload:
    post_id: str
    idempotency_key: str
    text: str
    content_hash: str
    language: str
    scheduled_at_utc: str | None = None
    media_urls: tuple[str, ...] = ()
    image: ImageAttachment | None = None


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


class UnsupportedCapability(NotImplementedError):
    pass


@dataclass(frozen=True)
class ProviderCapabilities:
    can_publish: bool
    can_find_existing: bool
    can_get_status: bool
    can_schedule: bool
    supports_media: bool
    max_chars: int | None
    notes: tuple[str, ...] = ()


# Truthful, static description of each provider (read by the dashboard without
# importing the sending path).
PROVIDER_CAPABILITIES: dict[str, ProviderCapabilities] = {
    "linkedin_api": ProviderCapabilities(
        can_publish=True, can_find_existing=False, can_get_status=False, can_schedule=False,
        supports_media=True, max_chars=3000,
        notes=("reading member posts needs r_member_social (restricted by LinkedIn)",
               "no provider-side idempotency key; ambiguous results need a human decision",
               "one image per post (Images API upload, Phase 6B); no video/documents/polls")),
}


@runtime_checkable
class Publisher(Protocol):
    name: str
    supports_native_scheduling: bool
    capabilities: ProviderCapabilities

    def validate(self, post: PostPayload) -> list[Issue]: ...

    def publish(self, post: PostPayload) -> PublishResult: ...

    def find_existing(self, idempotency_key: str, content_hash: str) -> RemotePost | None: ...

    def get_status(self, remote_id: str) -> RemoteStatus: ...
