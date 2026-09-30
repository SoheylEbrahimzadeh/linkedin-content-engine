from datetime import UTC, date, datetime, timedelta

import pytest

from lce.schedule import (
    ScheduleError,
    load_schedule,
    parse_slot_id,
    resolve_local,
    slots_between,
)

# Synthetic schedules only; never the owner's configuration.
BASE = {"timezone": "Europe/Berlin",
        "cadence": {"posts_per_week": 2, "slots": [{"day": "mon", "time": "09:15"},
                                                   {"day": "fri", "time": "17:45"}]}}


def sched(**over):
    s = {**BASE, **over}
    return load_schedule(s)


def utc(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def test_valid_schedule():
    s = sched()
    assert s.timezone == "Europe/Berlin" and s.posts_per_week == 2
    assert [(x.day, x.hhmm) for x in s.slots] == [("mon", "09:15"), ("fri", "17:45")]


@pytest.mark.parametrize("settings, msg", [
    ({"cadence": BASE["cadence"]}, "timezone is not configured"),
    ({**BASE, "timezone": "Mars/Olympus"}, "not a valid IANA"),
    ({"timezone": "UTC"}, "cadence is not configured"),
    ({**BASE, "cadence": {"posts_per_week": 1, "slots": []}}, "non-empty"),
    ({**BASE, "cadence": {"posts_per_week": 1, "slots": [{"day": "xyz", "time": "09:00"}]}}, "day"),
    ({**BASE, "cadence": {"posts_per_week": 1, "slots": [{"day": "mon", "time": 600}]}}, "quoted"),
    ({**BASE, "cadence": {"posts_per_week": 1, "slots": [{"day": "mon", "time": "25:00"}]}}, "HH:MM"),
    ({**BASE, "cadence": {"posts_per_week": 3, "slots": BASE["cadence"]["slots"]}}, "posts_per_week"),
    ({**BASE, "cadence": {"posts_per_week": 2,
                          "slots": [{"day": "mon", "time": "09:00"}] * 2}}, "duplicates"),
])
def test_invalid_schedules(settings, msg):
    with pytest.raises(ScheduleError, match=msg):
        load_schedule(settings)


def test_unquoted_yaml_time_is_rejected_not_misread():
    import yaml

    doc = yaml.safe_load("timezone: UTC\ncadence:\n  posts_per_week: 1\n  slots:\n"
                         "  - {day: mon, time: 10:00}\n")
    assert doc["cadence"]["slots"][0]["time"] == 600  # YAML 1.1 sexagesimal
    with pytest.raises(ScheduleError, match="quoted"):
        load_schedule(doc)


def test_slots_in_a_week_have_local_and_utc():
    slots = slots_between(sched(), utc(2026, 9, 28), utc(2026, 10, 5))
    assert [s.slot_id for s in slots] == ["2026-09-28-mon-0915", "2026-10-02-fri-1745"]
    s = slots[0]
    assert s.local.isoformat() == "2026-09-28T09:15:00+02:00"
    assert s.utc == utc(2026, 9, 28, 7, 15)


def test_boundaries_before_at_after():
    s = sched()
    at = utc(2026, 9, 28, 7, 15)
    assert slots_between(s, at - timedelta(minutes=1), at) == []           # just before: excluded end
    assert [x.slot_id for x in slots_between(s, at, at + timedelta(minutes=1))] == [
        "2026-09-28-mon-0915"]                                             # exactly at
    assert slots_between(s, at + timedelta(minutes=1), at + timedelta(hours=1)) == []  # after


def test_dst_spring_forward_gap_is_shifted_and_marked():
    s = load_schedule({"timezone": "Europe/Berlin",
                       "cadence": {"posts_per_week": 1, "slots": [{"day": "sun", "time": "02:30"}]}})
    slot = slots_between(s, utc(2026, 3, 28), utc(2026, 3, 30))[0]  # 29 Mar 2026: 02:00→03:00
    assert slot.slot_id == "2026-03-29-sun-0230"
    assert slot.dst_adjusted is True
    assert slot.local.isoformat() == "2026-03-29T03:30:00+02:00"
    assert slot.utc == utc(2026, 3, 29, 1, 30)


def test_dst_fall_back_overlap_uses_first_occurrence():
    s = load_schedule({"timezone": "Europe/Berlin",
                       "cadence": {"posts_per_week": 1, "slots": [{"day": "sun", "time": "02:30"}]}})
    slot = slots_between(s, utc(2026, 10, 24), utc(2026, 10, 26))[0]  # 25 Oct 2026: 03:00→02:00
    assert slot.dst_adjusted is False
    assert slot.local.utcoffset() == timedelta(hours=2)
    assert slot.utc == utc(2026, 10, 25, 0, 30)


def test_offset_changes_across_dst_but_identity_is_stable():
    s = sched()
    before = slots_between(s, utc(2026, 10, 19), utc(2026, 10, 20))[0]
    after = slots_between(s, utc(2026, 10, 26), utc(2026, 10, 27))[0]
    assert before.local.utcoffset() == timedelta(hours=2)
    assert after.local.utcoffset() == timedelta(hours=1)
    assert before.slot_id.endswith("-mon-0915") and after.slot_id.endswith("-mon-0915")
    assert (before.utc.hour, after.utc.hour) == (7, 8)


def test_month_boundary():
    ids = [s.slot_id for s in slots_between(sched(), utc(2026, 10, 29), utc(2026, 11, 3))]
    assert ids == ["2026-10-30-fri-1745", "2026-11-02-mon-0915"]


def test_year_boundary():
    ids = [s.slot_id for s in slots_between(sched(), utc(2026, 12, 28), utc(2027, 1, 6))]
    assert ids == ["2026-12-28-mon-0915", "2027-01-01-fri-1745", "2027-01-04-mon-0915"]


def test_local_date_not_utc_date_defines_identity():
    s = load_schedule({"timezone": "Pacific/Auckland",
                       "cadence": {"posts_per_week": 1, "slots": [{"day": "mon", "time": "06:00"}]}})
    slot = slots_between(s, utc(2026, 9, 26), utc(2026, 9, 29))[0]
    assert slot.slot_id == "2026-09-28-mon-0600"
    assert slot.utc.date() == date(2026, 9, 27)  # previous day in UTC


def test_resolve_and_parse_slot_id():
    from zoneinfo import ZoneInfo

    local, adjusted = resolve_local(date(2026, 7, 1), 8, 0, ZoneInfo("UTC"))
    assert not adjusted and local.hour == 8
    assert parse_slot_id("2026-10-01-thu-0915") == (date(2026, 10, 1), "thu", 9, 15)
    with pytest.raises(ScheduleError):
        parse_slot_id("2026-10-01-xxx-0915")
