"""Post lifecycle state machine.

Publishing (Phase 3) starts only from READY_TO_PUBLISH and only through the
explicit, human-confirmed `lce publish` command (lce.publishing):

    READY_TO_PUBLISH → PUBLISHING → PUBLISHED
                                  → PUBLISH_FAILED   (definitely not created)
                                  → NEEDS_RECONCILE  (may or may not exist on LinkedIn)

APPROVED is reachable only through an explicit human approval (lce.approval),
never because QA or the duplicate check passed.
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
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    NEEDS_RECONCILE = "NEEDS_RECONCILE"
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
    S.READY_TO_PUBLISH: frozenset({S.PUBLISHING, S.HUMANIZED, S.REJECTED}),
    S.PUBLISHING: frozenset({S.PUBLISHED, S.PUBLISH_FAILED, S.NEEDS_RECONCILE}),
    # Definitely not created on LinkedIn: may be sent again (human-triggered) or dropped.
    S.PUBLISH_FAILED: frozenset({S.READY_TO_PUBLISH, S.REJECTED}),
    # Unknown outcome: only a human decision resolves it.
    S.NEEDS_RECONCILE: frozenset({S.PUBLISHED, S.READY_TO_PUBLISH}),
    S.PUBLISHED: frozenset(),
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
