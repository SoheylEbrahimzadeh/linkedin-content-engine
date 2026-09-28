"""Post lifecycle state machine.

Phase 1 ends at READY_TO_PUBLISH. There is deliberately no PUBLISHING or
PUBLISHED state: those are added only when a real publisher exists and has
been verified. APPROVED is reachable only through an explicit human approval
(see lce.approval), never because QA or the duplicate check passed.
"""

from __future__ import annotations

from enum import StrEnum


class PostState(StrEnum):
    RESEARCHED = "RESEARCHED"
    SELECTED = "SELECTED"
    NEEDS_INPUT = "NEEDS_INPUT"
    DRAFTED = "DRAFTED"
    HUMANIZED = "HUMANIZED"
    NEEDS_REVISION = "NEEDS_REVISION"
    QA_PASSED = "QA_PASSED"
    DUPLICATE_CHECKED = "DUPLICATE_CHECKED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    REJECTED = "REJECTED"


S = PostState

TRANSITIONS: dict[PostState, frozenset[PostState]] = {
    S.RESEARCHED: frozenset({S.SELECTED, S.NEEDS_INPUT, S.REJECTED}),
    S.SELECTED: frozenset({S.DRAFTED, S.NEEDS_INPUT, S.REJECTED}),
    S.NEEDS_INPUT: frozenset({S.SELECTED, S.REJECTED}),
    S.DRAFTED: frozenset({S.DRAFTED, S.HUMANIZED, S.REJECTED}),
    S.HUMANIZED: frozenset({S.HUMANIZED, S.QA_PASSED, S.NEEDS_REVISION, S.REJECTED}),
    S.NEEDS_REVISION: frozenset({S.DRAFTED, S.HUMANIZED, S.REJECTED}),
    S.QA_PASSED: frozenset({S.DUPLICATE_CHECKED, S.NEEDS_REVISION, S.HUMANIZED, S.REJECTED}),
    S.DUPLICATE_CHECKED: frozenset({S.AWAITING_APPROVAL, S.HUMANIZED, S.REJECTED}),
    # Any edit during review sends the post back through QA and the duplicate check.
    S.AWAITING_APPROVAL: frozenset({S.APPROVED, S.HUMANIZED, S.REJECTED}),
    # Reopening an approved post discards the approval.
    S.APPROVED: frozenset({S.READY_TO_PUBLISH, S.HUMANIZED, S.REJECTED}),
    S.READY_TO_PUBLISH: frozenset({S.HUMANIZED, S.REJECTED}),
    S.REJECTED: frozenset(),
}

TERMINAL = frozenset(state for state, nxt in TRANSITIONS.items() if not nxt)

# States whose text may still be replaced without reopening.
EDITABLE = frozenset({S.SELECTED, S.DRAFTED, S.HUMANIZED, S.NEEDS_REVISION, S.QA_PASSED,
                      S.DUPLICATE_CHECKED, S.AWAITING_APPROVAL})


class InvalidTransition(ValueError):
    pass


def transition(current: PostState, new: PostState) -> PostState:
    if new not in TRANSITIONS[current]:
        raise InvalidTransition(f"{current.value} -> {new.value} is not allowed")
    return new
