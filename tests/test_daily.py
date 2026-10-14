"""LCE-060: daily preparation without a language model (briefs only; never post copy or approval)."""

import json
import re

from conftest import awaiting_post
from test_radar import NOW, configure, transport

from lce import daily, radar, rolling
from lce.clock import FixedClock, use_clock
from lce.state import PostState


def _ready(store):
    configure(store)
    pid = awaiting_post(store)
    with use_clock(FixedClock(NOW)):
        radar.collect(store, transport=transport)
        rolling.roll(store)
    return pid


def _prepare(store, **kw):
    with use_clock(FixedClock(NOW)):
        return daily.prepare(store, **kw)


def test_dry_run_is_the_default_and_writes_nothing(store):
    _ready(store)
    before = sorted(p.relative_to(store.root).as_posix() for p in store.root.rglob("*") if p.is_file())
    out = _prepare(store)
    after = sorted(p.relative_to(store.root).as_posix() for p in store.root.rglob("*") if p.is_file())
    assert out["dry_run"] is True and out["slots"]
    assert before == after
    assert {s["action"] for s in out["slots"]} == {"would_write"}


def test_a_brief_is_evidence_and_questions_never_post_copy_or_an_opinion(store):
    _ready(store)
    out = _prepare(store, write=True)
    s = next(x for x in out["slots"] if x["status"] == "NEEDS_INPUT")
    b = __import__("yaml").safe_load(daily.brief_path(store, s["plan_date"]).read_text("utf-8"))
    assert b["status"] == "NEEDS_INPUT" and b["questions"]
    assert b["evidence"][0]["url"].startswith("https://") and b["evidence"][0]["published_at"]
    assert all("picnic" not in e["title"].lower() for e in b["evidence"])        # pillar relevance
    assert "text" not in b and "post" not in b                                   # no copy anywhere
    skip = ("questions", "evidence", "owner_views_to_consider")
    blob = json.dumps({k: v for k, v in b.items() if k not in skip})
    assert not re.search(r"\b(I|my|me|we|our)\b", blob)                          # no generated first person
    for v in b["owner_views_to_consider"]:                       # owner material only, as candidates
        assert v["id"] and v["kind"]


def test_rerunning_the_same_day_changes_nothing(store):
    _ready(store)
    first = _prepare(store, write=True)
    written = {p: p.read_text("utf-8") for p in (store.root / "research" / "briefs").glob("*.yaml")}
    again = _prepare(store, write=True)
    assert {s["action"] for s in again["slots"]} == {"unchanged"}
    assert written == {p: p.read_text("utf-8") for p in (store.root / "research" / "briefs").glob("*.yaml")}
    assert len(first["slots"]) == len(again["slots"])


def test_no_fresh_relevant_source_is_no_evidence_not_an_invented_post(store):
    configure(store)
    awaiting_post(store)
    with use_clock(FixedClock(NOW)):
        radar.collect(store, transport=lambda url: (503, b""))     # every source down / rate limited
        rolling.roll(store)
    out = _prepare(store, write=True)
    assert out["slots"] and {s["status"] for s in out["slots"]} == {"NO_EVIDENCE"}
    b = __import__("yaml").safe_load(daily.brief_path(store, out["slots"][0]["plan_date"]).read_text("utf-8"))
    assert b["evidence"] == [] and b["questions"] == []


def test_a_source_an_earlier_post_used_is_not_served_again(store):
    _ready(store)
    item = next(i for i in radar.load_items(store) if "routing" in i["url"])
    pid = next(iter(store.post_ids()))
    post = store.load_post(pid)
    post["sources"] = [*post.get("sources", []), {"url": item["url"], "title": "x", "accessed_at": NOW}]
    store.save_post(post)
    with use_clock(FixedClock(NOW)):
        for d in daily.open_slots(store, FixedClock(NOW).now().date(), 14):
            urls = [radar.canonical(e["url"]) for e in daily.brief(store, d)["evidence"]]
            assert radar.canonical(item["url"]) not in urls


def test_preparation_never_creates_posts_approvals_or_schedules(store):
    pid = _ready(store)
    ids, states = set(store.post_ids()), {p: store.load_post(p)["state"] for p in store.post_ids()}
    plan = json.dumps(store.plan(), default=str)
    _prepare(store, write=True)
    assert set(store.post_ids()) == ids
    assert {p: store.load_post(p)["state"] for p in store.post_ids()} == states
    assert store.load_post(pid)["state"] == PostState.AWAITING_APPROVAL.value
    assert json.dumps(store.plan(), default=str) == plan                       # the calendar is not changed


def test_daily_preparation_never_starts_the_claude_writer(store, monkeypatch):
    """Routine daily work is model-free: preparing briefs makes no network call and starts no Routine,
    even when the Routine endpoint is configured; dispatch itself stays off unless explicitly enabled."""
    from lce import work

    _ready(store)
    monkeypatch.setenv("LCE_ROUTINE_FIRE_URL", "https://routines.example.test/v1/claude_code/routines/trig_X1/fire")
    monkeypatch.setenv("LCE_ROUTINE_FIRE_TOKEN", "test-token-not-real")
    monkeypatch.delenv("LCE_WRITER_DISPATCH", raising=False)
    calls = []
    monkeypatch.setattr(work, "_post", lambda *a, **k: calls.append(a) or (200, {}))
    monkeypatch.setattr(radar, "http_get", lambda url: calls.append(url) or (503, b""))
    _prepare(store, write=True)
    assert calls == []                                       # no feed fetch, no Routine start
    with use_clock(FixedClock(NOW)):
        out = work.dispatch(store, post=lambda *a: calls.append(a) or (200, {}))
    assert out["fired"] is False and "LCE_WRITER_DISPATCH" in out["why"] and calls == []
    assert not work.fired_path(store).exists()


def test_written_briefs_reach_the_dashboard_snapshot(store):
    from lce.dashboard.snapshot import build_snapshot

    _ready(store)
    assert build_snapshot(store, mode="real")["briefs"] == []
    out = _prepare(store, write=True)
    briefs = {b["plan_date"]: b for b in build_snapshot(store, mode="real")["briefs"]}
    assert set(briefs) == {s["plan_date"] for s in out["slots"]}
    b = next(x for x in briefs.values() if x["status"] == "NEEDS_INPUT")
    assert b["evidence"][0]["url"].startswith("https://") and b["questions"]
    # only what is needed, plus which confirmed views an item touches (LCE-068)
    assert set(b["evidence"][0]) == {"title", "url", "source", "published_at", "opinion_fit"}


# ── LCE-068: prefer items that genuinely touch a confirmed owner view; never invent one ──────────
VIEW = {"id": "op-chatbot", "kind": "opinion",
        "text": "Service desk chatbots are a good idea only after the guardrails are settled: "
                "prompt injection, what the bot may say and which data it may touch."}


def test_an_item_sharing_distinctive_concepts_with_a_view_fits():
    item = {"title": "Vendor ships a service desk chatbot with prompt injection guardrails", "excerpt": ""}
    fit = daily.opinion_fit(item, [VIEW])
    assert [f["id"] for f in fit] == ["op-chatbot"]
    assert {"chatbot", "guardrail"} <= set(fit[0]["shared"]) or len(fit[0]["shared"]) >= daily.MIN_FIT


def test_generic_words_alone_are_not_a_fit():
    item = {"title": "Why every team needs a new way to make good use of the system",
            "excerpt": "It may matter."}
    assert daily.opinion_fit(item, [VIEW]) == []


def test_no_view_no_fit_and_nothing_is_created():
    item = {"title": "Vendor ships a service desk chatbot with prompt injection guardrails", "excerpt": ""}
    assert daily.opinion_fit(item, []) == []


def test_brief_lists_fitting_items_first_and_only_as_candidates(store, monkeypatch):
    fresh = [
        {"title": "A big cloud migration report", "url": "https://e.org/1", "source": "S", "quality": 1,
         "published_at": "2026-10-01", "score": 0.9, "excerpt": ""},
        {"title": "Service desk chatbot gets prompt injection guardrails", "url": "https://e.org/2",
         "source": "S", "quality": 1, "published_at": "2026-10-01", "score": 0.4, "excerpt": ""},
    ]
    packet = {"pillar": "itsm-it-operations", "fresh_items": fresh}
    monkeypatch.setattr(radar, "packet", lambda *a, **k: packet)
    monkeypatch.setattr(radar, "load_items", lambda s: [])
    from lce import persona
    monkeypatch.setattr(persona, "opinion_for", lambda s, p: {"candidates": [VIEW]})
    monkeypatch.setattr(persona, "confirmed", lambda s, *a: {VIEW["id"]: VIEW})
    b = daily.brief(store, "2026-10-20")
    # the fit first, despite its lower radar score
    assert [e["url"] for e in b["evidence"]] == ["https://e.org/2", "https://e.org/1"]
    assert b["evidence"][0]["opinion_fit"][0]["id"] == "op-chatbot"
    assert b["evidence"][1]["opinion_fit"] == []
    assert b["status"] == "NEEDS_INPUT"                       # still evidence only: no text, no opinion used
    assert "opinions_used" not in b and "text" not in b
