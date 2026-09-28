import pytest

from lce.state import TERMINAL, TRANSITIONS, InvalidTransition, PostState, transition

S = PostState


def test_every_state_has_rules():
    assert set(TRANSITIONS) == set(PostState)


def test_no_publishing_states_in_phase_1():
    names = {s.value for s in PostState}
    assert not names & {"PUBLISHING", "PUBLISHED", "SCHEDULED", "NEEDS_RECONCILE"}


def test_only_human_approval_reaches_approved():
    sources = {s for s, nxt in TRANSITIONS.items() if S.APPROVED in nxt}
    assert sources == {S.AWAITING_APPROVAL}


def test_ready_only_from_approved():
    sources = {s for s, nxt in TRANSITIONS.items() if S.READY_TO_PUBLISH in nxt}
    assert sources == {S.APPROVED}


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


def test_ready_only_goes_back_to_editing_or_rejected():
    assert TRANSITIONS[S.READY_TO_PUBLISH] == {S.HUMANIZED, S.REJECTED}
    assert TERMINAL == {S.REJECTED}
