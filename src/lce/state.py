"""Post lifecycle state machine.

Publishing is only reachable from APPROVED, and APPROVED is only reachable
through an explicit human approval. Passing the audit never implies approval.
"""

from __future__ import annotations

from enum import StrEnum


class PostState(StrEnum):
    PLANNED = "PLANNED"
    DRAFTED = "DRAFTED"
    AUDIT_PASSED = "AUDIT_PASSED"
    NEEDS_INPUT = "NEEDS_INPUT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    PUBLISHING = "PUBLISHING"
    NEEDS_RECONCILE = "NEEDS_RECONCILE"
    PUBLISHED = "PUBLISHED"
    MISSED = "MISSED"
    FAILED = "FAILED"


S = PostState

TRANSITIONS: dict[PostState, frozenset[PostState]] = {
    S.PLANNED: frozenset({S.DRAFTED, S.NEEDS_INPUT, S.EXPIRED}),
    S.DRAFTED: frozenset({S.AUDIT_PASSED, S.DRAFTED, S.NEEDS_INPUT, S.EXPIRED}),
    S.NEEDS_INPUT: frozenset({S.DRAFTED, S.REJECTED, S.EXPIRED}),
    S.AUDIT_PASSED: frozenset({S.AWAITING_APPROVAL, S.DRAFTED}),
    # Human edits during review send the post back through the checks.
    S.AWAITING_APPROVAL: frozenset({S.APPROVED, S.REJECTED, S.EXPIRED, S.DRAFTED}),
    S.APPROVED: frozenset({S.PUBLISHING, S.MISSED, S.REJECTED}),
    S.PUBLISHING: frozenset({S.PUBLISHED, S.NEEDS_RECONCILE, S.FAILED}),
    S.NEEDS_RECONCILE: frozenset({S.PUBLISHED, S.PUBLISHING, S.FAILED}),
    S.PUBLISHED: frozenset(),
    S.REJECTED: frozenset(),
    S.EXPIRED: frozenset(),
    S.MISSED: frozenset(),
    S.FAILED: frozenset(),
}

TERMINAL = frozenset(state for state, nxt in TRANSITIONS.items() if not nxt)


class InvalidTransition(ValueError):
    pass


def transition(current: PostState, new: PostState) -> PostState:
    if new not in TRANSITIONS[current]:
        raise InvalidTransition(f"{current.value} -> {new.value} is not allowed")
    return new
