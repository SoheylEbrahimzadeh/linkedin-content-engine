import pytest

from lce.state import TERMINAL, TRANSITIONS, InvalidTransition, PostState, transition

S = PostState


def test_every_state_has_rules():
    assert set(TRANSITIONS) == set(PostState)


def test_publishing_states_are_exactly_the_phase3_set():
    names = {s.value for s in PostState}
    assert {"PUBLISHING", "PUBLISHED", "PUBLISH_FAILED", "NEEDS_RECONCILE"} <= names
    assert "SCHEDULED" not in names  # scheduling belongs to jobs, not to posts


def test_publishing_only_after_ready_and_published_only_via_publishing():
    assert {s for s, nxt in TRANSITIONS.items() if S.PUBLISHING in nxt} == {S.READY_TO_PUBLISH}
    assert {s for s, nxt in TRANSITIONS.items() if S.PUBLISHED in nxt} == {
        S.PUBLISHING, S.NEEDS_RECONCILE}
    # An ambiguous result never goes back to PUBLISHING directly (no silent re-send).
    assert S.PUBLISHING not in TRANSITIONS[S.NEEDS_RECONCILE]
    assert S.PUBLISHING not in TRANSITIONS[S.PUBLISH_FAILED]


def test_only_human_approval_reaches_approved():
    sources = {s for s, nxt in TRANSITIONS.items() if S.APPROVED in nxt}
    assert sources == {S.AWAITING_APPROVAL}


def test_ready_only_from_approved_or_a_resolved_publish_attempt():
    sources = {s for s, nxt in TRANSITIONS.items() if S.READY_TO_PUBLISH in nxt}
    assert sources == {S.APPROVED, S.PUBLISH_FAILED, S.NEEDS_RECONCILE}


def test_qa_or_dupcheck_never_skip_approval():
    for state in (S.QA_PASSED, S.DUPLICATE_CHECKED, S.HUMANIZED):
        with pytest.raises(InvalidTransition):
            transition(state, S.APPROVED)
        with pytest.raises(InvalidTransition):
            transition(state, S.READY_TO_PUBLISH)


def test_happy_path():
    s = S.RESEARCHED
    for nxt in (S.SELECTED, S.DRAFTED, S.HUMANIZED, S.QA_PASSED, S.DUPLICATE_CHECKED,
                S.AWAITING_APPROVAL, S.APPROVED, S.READY_TO_PUBLISH):
        s = transition(s, nxt)
    assert s == S.READY_TO_PUBLISH


def test_ready_goes_to_publishing_editing_or_rejected():
    assert TRANSITIONS[S.READY_TO_PUBLISH] == {S.PUBLISHING, S.HUMANIZED, S.REJECTED}
    assert TERMINAL == {S.REJECTED, S.PUBLISHED}
