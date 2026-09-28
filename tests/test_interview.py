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


# ── explicit "none" for list questions (regression) ──────────────────────
def _required_ids(store):
    return {q.id for q in interview.ready_for_drafting(store)}


def test_avoid_phrases_unanswered_is_still_missing(empty):
    assert "avoid_phrases" in _required_ids(empty)
    assert "avoid_phrases" not in empty.voice()


@pytest.mark.parametrize("raw", ["none", "None", "NONE.", "no avoided phrases", "no phrases"])
def test_avoid_phrases_explicit_none_is_answered(empty, raw):
    assert interview.answer(empty, "avoid_phrases", raw) == []
    assert empty.voice()["avoid_phrases"] == []
    assert "avoid_phrases" not in _required_ids(empty)


@pytest.mark.parametrize("raw", ["[]", "[ ]", '""', "  ;  ; "])
def test_literal_or_blank_empty_values_are_rejected(empty, raw):
    with pytest.raises(StoreError):
        interview.answer(empty, "avoid_phrases", raw)
    assert "avoid_phrases" not in empty.voice()
    assert "avoid_phrases" in _required_ids(empty)


def test_avoid_phrases_with_items(empty):
    assert interview.answer(empty, "avoid_phrases", "game-changer; synergy") == [
        "game-changer", "synergy"]
    assert "avoid_phrases" not in _required_ids(empty)


def test_none_is_rejected_for_other_required_lists(empty):
    for qid in ("expertise", "audience_roles", "goals", "topics_public", "tone", "topics_avoid"):
        with pytest.raises(StoreError, match="at least one item"):
            interview.answer(empty, qid, "none")
        with pytest.raises(StoreError):
            interview.answer(empty, qid, "[]")
        assert qid in _required_ids(empty)


def test_empty_list_in_file_counts_only_where_none_is_allowed(empty):
    voice = {"avoid_phrases": [], "tone": []}
    empty.write_doc(empty.voice_path, "voice", voice)
    missing = _required_ids(empty)
    assert "avoid_phrases" not in missing
    assert "tone" in missing


def test_only_avoid_phrases_allows_none():
    assert {q.id for q in interview.questions() if q.allow_none} == {"avoid_phrases"}


def test_three_states_of_is_answered():
    q = interview.get_question("avoid_phrases")
    other = interview.get_question("tone")
    assert interview.is_answered(q, None) is False
    assert interview.is_answered(q, []) is True
    assert interview.is_answered(q, ["x"]) is True
    assert interview.is_answered(other, []) is False
