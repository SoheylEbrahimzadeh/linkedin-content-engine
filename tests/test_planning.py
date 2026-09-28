from datetime import date

import pytest
from conftest import selected_post

from lce import interview, planning
from lce.store import DataStore, StoreError


def test_select_creates_post_and_plan_link(store):
    pid = selected_post(store)
    post = store.load_post(pid)
    assert post["state"] == "SELECTED"
    assert [h["state"] for h in post["history"]] == ["RESEARCHED", "SELECTED"]
    entry = next(e for e in store.plan()["entries"] if e.get("draft_ref") == pid)
    assert entry["status"] == "selected" and entry["publication_status"] == "not_published"
    assert store.candidates()["c-demo-rules-first"]["status"] == "selected"


def test_private_story_leads_to_needs_input(store):
    pid = selected_post(store, stories=("demo-private-vendor",))
    post = store.load_post(pid)
    assert post["state"] == "NEEDS_INPUT" and post["stories_used"] == []


def test_select_refuses_incomplete_profile(tmp_path):
    empty = DataStore.init(tmp_path / "d")
    with pytest.raises(StoreError, match="incomplete"):
        planning.select(empty, candidate_id="c-none-here", pillar="x", angle="a", fmt="text",
                        plan_date=date(2025, 1, 1))
    assert interview.ready_for_drafting(empty)


def test_unknown_pillar_rejected(store):
    with pytest.raises(StoreError, match="pillar"):
        planning.select(store, candidate_id="c-demo-rules-first", pillar="nope", angle="a",
                        fmt="text", plan_date=date(2025, 5, 6))


def test_rank_penalizes_recent_similar_topics(store):
    selected_post(store)
    from lce.research import add_candidate

    add_candidate(store, title="Why simple routing rules beat a model for small service teams again",
                  origin="manual")
    ranked = planning.rank_candidates(store, date(2025, 5, 7))
    assert ranked[0]["reasons"]
