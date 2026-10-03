"""LCE-049: rolling content calendar — cadence slots reserved through a moving horizon,
never twice, never over an existing plan; an open slot is filled through every check
and waits for the owner's approval."""

import pytest
from conftest import awaiting_post
from test_repackage import NEW_TEXT

from lce import rolling
from lce.clock import FixedClock, use_clock
from lce.store import StoreError

NOW = "2026-10-03T18:00:00Z"  # a Saturday; demo cadence tue/wed/thu 09:15 America/Chicago
SRC = "https://example.org/fictional-survey-2026"
CLAIM = "Manual triage dropped from about 40 minutes a day to about 10"


def dates(store, status=None):
    return [str(e["date"]) for e in store.plan()["entries"] if status is None or e["status"] == status]


def test_roll_reserves_every_cadence_slot_in_the_horizon_once(store):
    before = store.plan()["entries"][:]
    with use_clock(FixedClock(NOW)):
        out = rolling.roll(store)
        again = rolling.roll(store)
    added = [e["date"] for e in out["added"]]
    assert added[:3] == ["2026-10-06", "2026-10-07", "2026-10-08"]
    assert len(added) == 12 and out["through"].startswith("2026-10-29")  # 4 weeks x 3 slots
    assert again["added"] == []  # no duplicate slots
    entries = store.plan()["entries"]
    assert [e for e in entries if e in before] == before  # existing plan untouched
    for e in entries:
        if e.get("origin") == "rolling":
            assert e["status"] == "open" and e["topic"] == rolling.ROLLING_TOPIC
    assert len({e["pillar"] for e in entries if e.get("origin") == "rolling"}) > 1  # mix balanced


def test_horizon_moves_with_the_clock_past_month_end(store):
    clock = FixedClock(NOW)
    with use_clock(clock):
        rolling.roll(store)
        clock.advance(days=7)
        out = rolling.roll(store)
    assert [e["date"] for e in out["added"]] == ["2026-11-03", "2026-11-04", "2026-11-05"]
    assert len(dates(store)) == len(set(dates(store)))


def test_existing_and_skipped_slots_are_never_overwritten(store):
    plan = store.plan()
    plan["entries"] += [
        {"date": "2026-10-06", "topic": "Owner's own plan", "pillar": "automation", "status": "planned"},
        {"date": "2026-10-07", "topic": "Skipped by the owner", "pillar": "automation", "status": "skipped"},
    ]
    store.write_doc(store.plan_path, "plan", plan)
    with use_clock(FixedClock(NOW)):
        out = rolling.roll(store)
    assert "2026-10-06" not in [e["date"] for e in out["added"]]
    assert "2026-10-07" not in [e["date"] for e in out["added"]]
    by_date = {str(e["date"]): e for e in store.plan()["entries"]}
    assert by_date["2026-10-06"]["topic"] == "Owner's own plan"
    assert by_date["2026-10-07"]["status"] == "skipped"


def test_configurable_horizon(store):
    (store.root / "config" / "automation.yaml").write_text("plan_horizon_days: 7\n")
    with use_clock(FixedClock(NOW)):
        out = rolling.roll(store)
    assert [e["date"] for e in out["added"]] == ["2026-10-06", "2026-10-07", "2026-10-08"]


def slot_pkg(day="2026-10-08"):
    return {
        "plan_date": day,
        "topic": "Rules before models in ticket triage",
        "angle": "keyword rules first; a model when misroutes rise",
        "candidate": {
            "title": "Fictional 2026 service desk survey",
            "summary": "survey of small service teams",
            "sources": [{"url": SRC, "title": "Fictional survey", "publisher": "Example Org"}],
            "claims": [{"text": CLAIM, "source_url": SRC}],
        },
        "text": NEW_TEXT,
        "sources": [{"url": SRC, "title": "Fictional survey"}],
        "claims": [{"text": CLAIM, "source_url": SRC}],
        "media": {"text_only": {"reason": "text_carries_point", "rationale": "the argument is the text"}},
        "reason": "fresh research for the open slot",
    }


def test_fill_writes_one_open_slot_through_every_check(store):
    with use_clock(FixedClock(NOW)):
        rolling.roll(store)
        out = rolling.fill(store, slot_pkg(), by="test session")
        assert out["state"] == "AWAITING_APPROVAL" and out["plan_date"] == "2026-10-08"
        post = store.load_post(out["post_id"])
        assert post["approval"]["state"] == "pending" and post["plan_date"] == "2026-10-08"
        assert post["qa"]["status"] == "passed" and post["duplicate"]["status"] == "passed"
        entry = next(e for e in store.plan()["entries"] if str(e["date"]) == "2026-10-08")
        assert entry["draft_ref"] == out["post_id"] and entry["status"] == "awaiting_approval"
        assert entry["topic"] == "Rules before models in ticket triage"
        assert "2026-10-08" not in [e["date"] for e in rolling.open_slots(store)]
        with pytest.raises(StoreError, match="not an open slot"):
            rolling.fill(store, slot_pkg(), by="test session")  # never overwrites


def test_fill_refuses_past_or_unreserved_slots_and_restores_on_failure(store):
    with use_clock(FixedClock(NOW)):
        rolling.roll(store)
        with pytest.raises(StoreError, match="has passed"):
            rolling.fill(store, slot_pkg("2026-10-01"))
        with pytest.raises(StoreError, match="not an open slot"):
            rolling.fill(store, slot_pkg("2026-10-10"))  # a Saturday: no cadence slot
        posts_before, plan_before = set(store.post_ids()), store.plan()
        bad = slot_pkg()
        bad["media"] = {"spec": {"title": "x"}}  # a drawn visual nobody asked for (LCE-043)
        with pytest.raises(StoreError, match="owner"):
            rolling.fill(store, bad)
        assert set(store.post_ids()) == posts_before and store.plan() == plan_before
        assert not any(c.get("title") == "Fictional 2026 service desk survey"
                       for c in store.candidates().values())


def test_existing_posts_keep_their_slot(store):
    pid = awaiting_post(store)
    post = store.load_post(pid)
    post["plan_date"] = "2026-10-06"
    store.save_post(post)
    plan = store.plan()
    entry = next(e for e in plan["entries"] if e.get("draft_ref") == pid)
    entry["date"] = "2026-10-06"
    store.write_doc(store.plan_path, "plan", plan)
    with use_clock(FixedClock(NOW)):
        out = rolling.roll(store)
    assert "2026-10-06" not in [e["date"] for e in out["added"]]
