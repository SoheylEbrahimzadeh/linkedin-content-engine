import pytest

from lce import interview
from lce.store import DataStore, StoreError


@pytest.fixture
def empty(tmp_path):
    return DataStore.init(tmp_path / "d")


def test_empty_profile_is_not_ready(empty):
    missing = {q.id for q in interview.ready_for_drafting(empty)}
    assert {"positioning", "pillars", "tone", "timezone", "cadence"} <= missing


def test_questions_are_unique_and_grouped():
    qs = interview.questions()
    assert len({q.id for q in qs}) == len(qs)
    assert {q.group for q in qs} <= set(interview.GROUP_ORDER)


def test_answers_are_coerced_and_stored_privately(empty):
    interview.answer(empty, "expertise", "Logistics; Beekeeping ; ")
    interview.answer(empty, "emoji_max", "0")
    interview.answer(empty, "cta_allowed", "no")
    interview.answer(empty, "timezone", "Asia/Tokyo")
    assert empty.profile()["expertise"] == ["Logistics", "Beekeeping"]
    assert empty.voice()["emoji_policy"]["max_per_post"] == 0
    assert empty.voice()["cta"]["allowed"] is False
    assert empty.settings()["timezone"] == "Asia/Tokyo"
    assert (empty.root / "interview" / "log.jsonl").exists()


def test_invalid_answers_rejected(empty):
    with pytest.raises(StoreError):
        interview.answer(empty, "formality", "very")
    with pytest.raises(StoreError):
        interview.answer(empty, "timezone", "Nowhere/City")
    with pytest.raises(StoreError):
        interview.answer(empty, "emoji_max", "a few")
    with pytest.raises(StoreError):
        interview.answer(empty, "positioning", "   ")
    assert "timezone" not in empty.settings()


def test_demo_is_ready(store):
    assert interview.ready_for_drafting(store) == []
