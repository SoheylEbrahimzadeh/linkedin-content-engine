"""LCE-050: Content Radar (feeds -> dedup -> pillar classification), research packets,
radar-driven new developments in the freshness window, writer queue and dispatch."""

import json

import pytest
from conftest import awaiting_post

from lce import radar, refresh, rolling, work
from lce.clock import FixedClock, use_clock
from lce.store import StoreError

NOW = "2026-10-03T18:00:00Z"
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Fictional vendor</title>
<item><title>Ticket routing rules now support approvals</title><link>https://vendor.example.com/news/routing?utm_source=x</link>
<pubDate>Fri, 02 Oct 2026 09:00:00 GMT</pubDate><description>&lt;p&gt;New approvals step for ticket routing.&lt;/p&gt;</description></item>
<item><title>Company picnic photos</title><link>https://vendor.example.com/news/picnic</link>
<pubDate>Thu, 01 Oct 2026 09:00:00 GMT</pubDate><description>Nothing about work.</description></item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>r/fictional</title>
<entry><title>Ticket routing rules now support approvals</title><link href="https://other.example.org/copy"/>
<updated>2026-10-02T10:00:00+00:00</updated><content type="html">same story, other site</content></entry>
<entry><title>How we run incident reviews without blame</title><link rel="alternate" href="https://www.reddit.com/r/fictional/comments/abc/"/>
<published>2026-10-03T08:00:00+00:00</published><content type="html">incident reviews and SLAs in a small team</content></entry>
</feed>"""


def configure(store, extra=""):
    (store.root / "config" / "radar.yaml").write_text(
        "sources:\n"
        "  - {id: vendor, name: Fictional vendor, kind: feed, url: 'https://vendor.example.com/feed', quality: vendor}\n"
        "  - {id: community, name: r/fictional, kind: reddit, subreddit: fictional, quality: community}\n"
        "  - {id: broken, kind: feed, url: 'https://down.example.net/feed'}\n" + extra)


def transport(url):
    return {"https://vendor.example.com/feed": (200, RSS),
            "https://www.reddit.com/r/fictional/.rss": (200, ATOM)}.get(url, (503, b""))


def test_collect_normalizes_dedups_classifies_and_keeps_provenance(store):
    configure(store)
    with use_clock(FixedClock(NOW)):
        out = radar.collect(store, transport=transport)
        again = radar.collect(store, transport=transport)
    titles = [i["title"] for i in out["added"]]
    assert "Ticket routing rules now support approvals" in titles
    assert titles.count("Ticket routing rules now support approvals") == 1  # same title elsewhere = duplicate
    assert again["added"] == []  # nothing is stored twice
    item = next(i for i in out["added"] if i["source_id"] == "vendor" and "routing" in i["title"])
    assert item["url"].startswith("https://vendor.example.com/news/routing")
    assert item["published_at"] == "2026-10-02T09:00:00+00:00" and item["first_seen"] == "2026-10-03T18:00:00+00:00"
    assert item["excerpt"] == "New approvals step for ticket routing." and item["quality"] == "vendor"
    assert item["pillar"] == "automation" and set(item["matched_terms"]) >= {"ticket routing", "approvals"}
    assert item["relevance"] > 0 and item["angle_hint"].startswith("automation:")
    reddit = next(i for i in out["added"] if i["source_id"] == "community")
    assert reddit["pillar"] == "operations"
    picnic = next(i for i in out["added"] if "picnic" in i["title"])
    assert picnic["pillar"] is None and picnic["relevance"] == 0
    status = {s["id"]: s for s in out["sources"]}
    assert status["broken"]["status"] == "unreachable" and status["broken"]["http"] == 503
    assert status["vendor"]["status"] == "ok" and status["vendor"]["new"] == 2


def test_canonical_urls_ignore_tracking_and_case():
    a = radar.canonical("https://WWW.Example.com/a/b/?utm_source=x&id=7#top")
    assert a == radar.canonical("https://example.com/a/b?id=7")


def test_bad_config_is_refused(store):
    (store.root / "config" / "radar.yaml").write_text("sources: [{id: a, kind: tiktok}]\n")
    with pytest.raises(StoreError):
        radar.collect(store, transport=transport)


def test_packet_for_an_open_slot_carries_fresh_evidence_and_history(store):
    configure(store)
    pid = awaiting_post(store)
    with use_clock(FixedClock(NOW)):
        radar.collect(store, transport=transport)
        rolling.roll(store)
        open_auto = next(e for e in store.plan()["entries"] if e.get("status") == "open" and e["pillar"] == "automation")
        doc = radar.packet(store, str(open_auto["date"]))
    assert (store.root / "research" / "packets" / f"{open_auto['date']}.yaml").exists()
    assert doc["pillar"] == "automation" and doc["slot_utc"] and doc["built_at"] == "2026-10-03T18:00:00+00:00"
    assert [i["title"] for i in doc["fresh_items"]][0] == "Ticket routing rules now support approvals"
    assert all("picnic" not in i["title"] for i in doc["fresh_items"])
    hist = {p["post_id"]: p for p in doc["history"]["posts"]}
    assert hist[pid]["hooks"] and len(hist[pid]["content_hash"]) == 64


def test_new_radar_items_make_research_due_in_the_window(store):
    pid = awaiting_post(store)
    post = store.load_post(pid)
    post.update(plan_date="2026-10-08", topic="Ticket routing rules before models", angle="rules and approvals first",
                created_at="2026-09-28T10:00:00+00:00")
    store.save_post(post)
    configure(store)
    with use_clock(FixedClock("2026-10-07T03:00:00Z")):
        radar.collect(store, transport=transport)
        [row] = refresh.window(store)
    assert row["needs_research"] is True
    assert [n["title"] for n in row["new_developments"]][0] == "Ticket routing rules now support approvals"
    with use_clock(FixedClock("2026-10-07T03:00:00Z")):
        refresh.run_window(store, fetch=lambda u: (200, u, b"<p>nothing</p>"))
    rec = refresh.latest(store, pid)
    assert rec["new_developments"]["count"] >= 1


def test_work_queue_order_and_dispatch_once(store, monkeypatch):
    from lce import repackage

    configure(store)
    pid = awaiting_post(store)
    post = store.load_post(pid)
    post["plan_date"] = "2026-10-08"
    store.save_post(post)
    with use_clock(FixedClock(NOW)):
        radar.collect(store, transport=transport)
        rolling.roll(store)
        repackage.request(store, pid, by="owner", decision_id="d-x")
        units = work.units(store)
        assert units[0]["kind"] == "replacement" and units[0]["post_id"] == pid
        assert {u["kind"] for u in units[1:]} == {"slot"}  # open slots within candidate_lead_days only
        assert max(u["plan_date"] for u in units[1:]) <= "2026-10-10"
        monkeypatch.delenv("LCE_ROUTINE_FIRE_URL", raising=False)
        out = work.dispatch(store)
        assert out["fired"] is False and "not configured" in out["why"]
        monkeypatch.setenv("LCE_ROUTINE_FIRE_URL", "https://api.anthropic.com/v1/claude_code/routines/trig_X/fire")
        monkeypatch.setenv("LCE_ROUTINE_FIRE_TOKEN", "test-token-not-real")
        sent = []

        def post_fn(url, token, text):
            sent.append(text)
            return 200, {"claude_code_session_url": "https://claude.ai/code/session_TEST"}

        built = work.build_packets(store)
        out = work.dispatch(store, post=post_fn)
        assert out["fired"] is True and out["session_url"].endswith("session_TEST")
        assert json.loads(sent[0].split(": ", 1)[1])["post_id"] == pid and "d-x" in sent[0]
        again = work.dispatch(store, post=post_fn)
        assert again["fired"] is False and "already started" in again["why"] and len(sent) == 1
        assert built  # packets for the slots with work
    with use_clock(FixedClock("2026-10-03T19:45:00Z")):  # a lost run is started again after 90 min
        assert work.dispatch(store, post=post_fn)["fired"] is True and len(sent) == 2
