from datetime import UTC, datetime, timedelta, timezone

import pytest

from lce import clock
from lce.clock import FixedClock, NaiveDatetimeError, iso_utc, parse_iso, use_clock
from lce.store import now_iso


def test_fixed_clock_drives_now_and_store_timestamps():
    with use_clock(FixedClock("2026-09-29T08:30:00+02:00")):
        assert clock.now() == datetime(2026, 9, 29, 6, 30, tzinfo=UTC)
        assert now_iso() == "2026-09-29T06:30:00+00:00"
    assert clock.now() != datetime(2026, 9, 29, 6, 30, tzinfo=UTC)  # restored


def test_naive_datetimes_are_rejected():
    with pytest.raises(NaiveDatetimeError):
        parse_iso("2026-09-29T08:30:00")
    with pytest.raises(NaiveDatetimeError):
        FixedClock(datetime(2026, 9, 29, 8, 30))
    with pytest.raises(NaiveDatetimeError):
        iso_utc(datetime(2026, 1, 1))


def test_iso_utc_always_has_offset():
    dt = datetime(2026, 3, 29, 3, 0, tzinfo=timezone(timedelta(hours=2)))
    assert iso_utc(dt) == "2026-03-29T01:00:00+00:00"
    assert parse_iso("2026-01-01T00:00:00Z").utcoffset() == timedelta(0)


def test_advance():
    c = FixedClock("2026-12-31T23:59:00+00:00")
    c.advance(minutes=2)
    assert c.now().year == 2027


def test_no_direct_system_time_outside_clock():
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "lce"
    offenders = [p.name for p in src.rglob("*.py") if p.name != "clock.py"
                 and ("datetime.now(" in p.read_text() or "date.today(" in p.read_text())]
    assert offenders == []
