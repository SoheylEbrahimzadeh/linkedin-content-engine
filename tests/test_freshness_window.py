"""LCE-049: schedule-derived freshness window; a stale post gets a same-slot replacement
automatically (version kept, approval discarded); nothing is approved or published."""

from datetime import date

import pytest
from conftest import awaiting_post
from test_repackage import NEW_TEXT

from lce import approval, cloud, refresh, repackage, versions
from lce.clock import FixedClock, use_clock
from lce.posts import current_text
from lce.textutil import content_hash

URL = "https://example.com/report"
CLAIM = "Manual triage dropped from about 40 minutes a day to about 10"
PLAN_DAY = date(2026, 10, 8)  # a Thursday: demo cadence thu 09:15 America/Chicago = 14:15 UTC
SLOT_UTC = "2026-10-08T14:15:00+00:00"
OPENS_UTC = "2026-10-07T02:15:00+00:00"  # 36 h before the slot (default freshness_lead_hours)
SUPPORTING = "In the study, manual triage dropped from about 40 minutes a day to about 10."


def planned(store):
    pid = awaiting_post(store)
    post = store.load_post(pid)
    post["sources"] = [{"url": URL, "title": "Fictional report"}]
    post["claims"] = [{"text": CLAIM, "source_url": URL}]
    post["plan_date"] = PLAN_DAY.isoformat()
    store.save_post(post)
    return pid


def page(*sentences):
    body = "<html><body><p>" + "</p><p>".join(sentences) + "</p></body></html>"
    return lambda url: (200, url, body.encode())


def test_window_is_derived_from_the_real_slot(store):
    pid = planned(store)
    with use_clock(FixedClock("2026-10-07T02:00:00Z")):
        assert refresh.window(store) == []  # 15 min before the window opens
    with use_clock(FixedClock("2026-10-07T03:00:00Z")):
        [row] = refresh.window(store)
    assert row["post_id"] == pid
    assert row["slot_utc"] == SLOT_UTC and row["window_opens_at"] == OPENS_UTC
    assert row["slot_local"].startswith("2026-10-08T09:15") and "cadence time" in row["slot_source"]
    assert row["action"] == "check" and row["needs_research"] is True
    with use_clock(FixedClock("2026-10-08T14:15:00Z")):
        assert refresh.window(store) == []  # the slot itself: the window has closed


def test_current_post_is_kept_and_rechecked_on_schedule(store):
    pid = planned(store)
    before = (current_text(store, pid), store.load_post(pid)["approval"])
    clock = FixedClock("2026-10-07T03:00:00Z")
    with use_clock(clock):
        [row] = refresh.run_window(store, fetch=page(SUPPORTING))
        assert row["result"]["decision"] == "unchanged" and "escalation" not in row
        rec = refresh.latest(store, pid)
        assert rec["trigger"] == "window" and rec["window"]["slot_utc"] == SLOT_UTC
        assert rec["approval_effect"] == "preserved" and not rec["test_mode"]
        [row] = refresh.window(store)
        assert row["action"] == "wait"
        clock.advance(hours=12)
        [row] = refresh.window(store)
        assert row["action"] == "check" and "older than 12 h" in row["why"]
    assert (current_text(store, pid), store.load_post(pid)["approval"]) == before
    assert versions.listing(store, pid) == []


def test_stale_post_gets_a_same_slot_replacement_request(store):
    pid = planned(store)
    old_hash = content_hash(current_text(store, pid))
    with use_clock(FixedClock("2026-10-07T03:00:00Z")):
        [row] = refresh.run_window(store, fetch=page("The report was withdrawn."))
    assert row["result"]["decision"] == "update_required" and row["escalation"]["escalated"] is True
    post = store.load_post(pid)
    req = post["refresh_request"]
    assert req["origin"] == "freshness" and req["stale"]["missing_claims"] == [CLAIM]
    assert req["stale"]["slot_utc"] == SLOT_UTC and req["rejected_version"] == 1
    assert post["state"] == "NEEDS_REVISION" and post["plan_date"] == PLAN_DAY.isoformat()
    assert post.get("approval", {}).get("state") != "pending"  # old approval artifact discarded
    [v1] = versions.listing(store, pid)
    assert v1["status"] == "stale" and v1["content_hash"] == old_hash and "stale" in v1["reason"]
    rec = refresh.latest(store, pid)
    assert rec["approval_effect"] == "invalidated" and rec["escalation"]["escalated"] is True
    assert [p["post_id"] for p in repackage.pending(store)] == [pid]
    # the writing session's replacement: same slot, new version, waits for the owner
    with use_clock(FixedClock("2026-10-07T05:00:00Z")):
        pkg = {
            "text": NEW_TEXT,
            "reason": "the report was withdrawn; rewritten on current sources",
            "sources": [{"url": "https://example.org/new-study", "title": "Fictional new study"}],
            "claims": [],
            "media": {"text_only": {"reason": "text_carries_point", "rationale": "the argument is the text"}},
        }
        out = repackage.package(store, pid, pkg, by="test session")
        assert out["state"] == "AWAITING_APPROVAL" and out["version_before"] == 1
        post = store.load_post(pid)
        assert "refresh_request" not in post and post["plan_date"] == PLAN_DAY.isoformat()
        assert post["approval"]["state"] == "pending"  # a fresh artifact; never approved here
        [row] = refresh.window(store)
        assert row["action"] == "check" and row["needs_research"] is False  # rewritten inside the window


def test_approved_post_that_goes_stale_loses_its_approval(store):
    pid = planned(store)
    h = content_hash(current_text(store, pid))
    approval.approve(store, pid, h[:12], confirm=lambda _: f"APPROVE {pid}", is_tty=lambda: True)
    assert store.load_post(pid)["state"] == "APPROVED"
    with use_clock(FixedClock("2026-10-07T03:00:00Z")):
        refresh.run_window(store, fetch=page("Nothing about triage here."))
    post = store.load_post(pid)
    assert post["state"] == "NEEDS_REVISION" and post["refresh_request"]["origin"] == "freshness"
    rec = refresh.latest(store, pid)
    assert rec["approval_effect"] == "invalidated"


def test_publisher_refusing_bots_is_read_from_the_archive(store):
    pid = planned(store)
    seen = []

    def fetch(url):
        seen.append(url)
        if url == URL:
            return 403, url, b""
        final = "https://web.archive.org/web/20261001120000id_/" + URL
        return 200, final, f"<p>{SUPPORTING}</p>".encode()

    with use_clock(FixedClock("2026-10-07T03:00:00Z")):
        [row] = refresh.run_window(store, fetch=fetch)
    assert seen[1] == "https://web.archive.org/web/20261006id_/" + URL  # dated from the real clock
    assert row["result"]["decision"] == "unchanged"
    src = refresh.latest(store, pid)["sources"][0]
    assert src["status"] == "ok_archive" and src["capture"] == "2026-10-01" and src["http"] == 403
    assert "archive capture 2026-10-01" in row["result"]["reason"]


def test_scheduled_cloud_post_is_held_not_rewritten(store, monkeypatch):
    pid = planned(store)
    monkeypatch.setattr(cloud, "load_delegation", lambda s, p: {"post_id": p})
    with use_clock(FixedClock("2026-10-07T03:00:00Z")):
        [row] = refresh.run_window(store, fetch=page("The report was withdrawn."))
    assert row["escalation"]["escalated"] is False and "withdraws" in row["escalation"]["why"]
    assert "refresh_request" not in store.load_post(pid)


@pytest.mark.parametrize("escalate", [False])
def test_escalation_can_be_switched_off(store, escalate):
    pid = planned(store)
    (store.root / "config" / "automation.yaml").write_text(f"freshness_escalate: {str(escalate).lower()}\n")
    with use_clock(FixedClock("2026-10-07T03:00:00Z")):
        [row] = refresh.run_window(store, fetch=page("The report was withdrawn."))
    assert row["result"]["decision"] == "update_required" and "escalation" not in row
    assert "refresh_request" not in store.load_post(pid)
