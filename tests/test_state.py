import pytest

from lce.state import TERMINAL, TRANSITIONS, InvalidTransition, PostState, transition

S = PostState


def test_every_state_has_rules():
    assert set(TRANSITIONS) == set(PostState)


def test_audit_pass_never_publishes_directly():
    with pytest.raises(InvalidTransition):
        transition(S.AUDIT_PASSED, S.PUBLISHING)
    with pytest.raises(InvalidTransition):
        transition(S.AWAITING_APPROVAL, S.PUBLISHING)


def test_only_approved_reaches_publishing():
    sources = {s for s, nxt in TRANSITIONS.items() if S.PUBLISHING in nxt}
    assert sources == {S.APPROVED, S.NEEDS_RECONCILE}


def test_happy_path():
    s = S.PLANNED
    for nxt in (S.DRAFTED, S.AUDIT_PASSED, S.AWAITING_APPROVAL, S.APPROVED, S.PUBLISHING,
                S.PUBLISHED):
        s = transition(s, nxt)
    assert s in TERMINAL


def test_published_is_terminal():
    with pytest.raises(InvalidTransition):
        transition(S.PUBLISHED, S.PUBLISHING)
