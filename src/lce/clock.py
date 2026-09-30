"""Single source of "now" for the engine.

Convention: every timestamp the engine persists is an aware datetime in UTC,
serialized as ISO 8601 with an explicit `+00:00` offset. Local wall-clock
times (schedule slots) are always stored together with their IANA timezone
and offset. Naive datetimes are rejected, never interpreted as machine time.

Tests and simulations inject a `FixedClock` with `use_clock(...)`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Protocol


class NaiveDatetimeError(ValueError):
    pass


def require_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise NaiveDatetimeError("naive datetime is not allowed; include a timezone offset")
    return dt


def to_utc(dt: datetime) -> datetime:
    return require_aware(dt).astimezone(UTC)


def iso_utc(dt: datetime) -> str:
    return to_utc(dt).replace(microsecond=0).isoformat()


def parse_iso(text: str) -> datetime:
    """Parse an ISO 8601 timestamp that carries an explicit offset."""
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp: {text!r}") from exc
    return require_aware(dt)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Deterministic clock for tests and dry-run simulations."""

    def __init__(self, at: datetime | str):
        self._now = to_utc(parse_iso(at) if isinstance(at, str) else at)

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)


_current: Clock = SystemClock()


def current() -> Clock:
    return _current


def now() -> datetime:
    """Current time as an aware UTC datetime."""
    return to_utc(_current.now())


@contextmanager
def use_clock(clock: Clock) -> Iterator[Clock]:
    global _current
    previous, _current = _current, clock
    try:
        yield clock
    finally:
        _current = previous
