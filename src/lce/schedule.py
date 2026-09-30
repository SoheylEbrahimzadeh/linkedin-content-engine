"""Timezone-aware schedule evaluation.

The schedule (timezone + cadence slots) is read from the private settings; the
engine never hard-codes anyone's schedule.

Slot identity is `<local date>-<day>-<HHMM>` in the configured timezone, e.g.
`2026-10-01-thu-0830`. It does not depend on the machine's timezone or on the
UTC offset, so it is stable across DST changes and machines.

DST rules (zoneinfo):
- A slot inside a spring-forward gap does not exist; it is moved forward by the
  length of the gap (02:30 → 03:30) and marked `dst_adjusted`.
- A slot inside a fall-back overlap uses the first occurrence (fold=0).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lce.clock import iso_utc, to_utc

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class SlotSpec:
    day: str
    hour: int
    minute: int

    @property
    def hhmm(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


@dataclass(frozen=True)
class Schedule:
    timezone: str
    posts_per_week: int
    slots: tuple[SlotSpec, ...]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True)
class Slot:
    slot_id: str
    day: str
    time: str
    local: datetime
    utc: datetime
    timezone: str
    dst_adjusted: bool

    def to_dict(self) -> dict:
        return {"slot_id": self.slot_id, "day": self.day, "time": self.time,
                "local": self.local.isoformat(), "utc": iso_utc(self.utc),
                "timezone": self.timezone, "dst_adjusted": self.dst_adjusted}


def load_schedule(settings: dict) -> Schedule:
    """Validate timezone and cadence; raise ScheduleError with a precise reason."""
    tz_name = settings.get("timezone")
    if not tz_name or not isinstance(tz_name, str):
        raise ScheduleError("timezone is not configured")
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError(f"timezone {tz_name!r} is not a valid IANA timezone") from exc
    cadence = settings.get("cadence")
    if not isinstance(cadence, dict):
        raise ScheduleError("cadence is not configured")
    raw_slots = cadence.get("slots")
    if not isinstance(raw_slots, list) or not raw_slots:
        raise ScheduleError("cadence.slots must be a non-empty list")
    specs: list[SlotSpec] = []
    for i, s in enumerate(raw_slots):
        if not isinstance(s, dict):
            raise ScheduleError(f"slot {i}: expected a mapping with day and time")
        day, t = s.get("day"), s.get("time")
        if day not in DAYS:
            raise ScheduleError(f"slot {i}: day must be one of {', '.join(DAYS)}")
        if not isinstance(t, str):
            raise ScheduleError(
                f"slot {i}: time must be a quoted 'HH:MM' string (got {t!r}; unquoted "
                "YAML times like 10:00 are read as numbers)")
        m = TIME_RE.match(t)
        if not m:
            raise ScheduleError(f"slot {i}: time {t!r} is not HH:MM (24h)")
        specs.append(SlotSpec(day, int(m.group(1)), int(m.group(2))))
    if len({(s.day, s.hour, s.minute) for s in specs}) != len(specs):
        raise ScheduleError("cadence.slots contains duplicates")
    per_week = cadence.get("posts_per_week")
    if not isinstance(per_week, int) or per_week != len(specs):
        raise ScheduleError("cadence.posts_per_week must equal the number of slots")
    specs.sort(key=lambda s: (DAYS.index(s.day), s.hour, s.minute))
    return Schedule(tz_name, per_week, tuple(specs))


def resolve_local(day: date, hour: int, minute: int, tz: ZoneInfo) -> tuple[datetime, bool]:
    """Aware local datetime for a wall-clock time, applying the DST rules above."""
    wall = datetime.combine(day, time(hour, minute))
    aware = wall.replace(tzinfo=tz, fold=0)
    actual = aware.astimezone(UTC).astimezone(tz)
    if actual.replace(tzinfo=None) != wall:  # nonexistent (spring-forward gap)
        return actual, True
    return aware, False


def slot_for(schedule: Schedule, spec: SlotSpec, day: date) -> Slot:
    local, adjusted = resolve_local(day, spec.hour, spec.minute, schedule.tz)
    slot_id = f"{day.isoformat()}-{spec.day}-{spec.hour:02d}{spec.minute:02d}"
    return Slot(slot_id, spec.day, spec.hhmm, local, to_utc(local), schedule.timezone, adjusted)


def slots_between(schedule: Schedule, start: datetime, end: datetime) -> list[Slot]:
    """All slots with start <= utc < end (both bounds aware)."""
    start_u, end_u = to_utc(start), to_utc(end)
    tz = schedule.tz
    first = start_u.astimezone(tz).date() - timedelta(days=1)
    last = end_u.astimezone(tz).date() + timedelta(days=1)
    out: list[Slot] = []
    day = first
    while day <= last:
        for spec in schedule.slots:
            if DAYS[day.weekday()] == spec.day:
                slot = slot_for(schedule, spec, day)
                if start_u <= slot.utc < end_u:
                    out.append(slot)
        day += timedelta(days=1)
    return sorted(out, key=lambda s: s.utc)


def parse_slot_id(slot_id: str) -> tuple[date, str, int, int]:
    m = re.match(r"^(\d{4}-\d{2}-\d{2})-(mon|tue|wed|thu|fri|sat|sun)-(\d{2})(\d{2})$", slot_id)
    if not m:
        raise ScheduleError(f"invalid slot id {slot_id!r}")
    return date.fromisoformat(m.group(1)), m.group(2), int(m.group(3)), int(m.group(4))
