"""Writing gate: a weak draft never becomes the dashboard candidate (regression: the 6 Oct post).

Synthetic text with the same failure pattern as the real post: editorial credit phrasing, a
source's framework voiced as a personal requirement, 40+ word sentences, no contractions.
"""

import pytest
from test_repackage import PKG, legacy_post, refresh_click

from lce import repackage
from lce.posts import current_text
from lce.revise import autofix, gate
from lce.store import StoreError

URL = PKG["sources"][0]["url"]
CONSULTANT_DRAFT = (
    "Most triage projects start with the size of the queue. That is usually the wrong trigger.\n\n"
    "The author of a fictional report puts it clearly: the useful split is not old versus new, but rules "
    "that still cover the queue versus rules that are becoming a constraint, and that is the distinction "
    "most teams never write down.\n\n"
    "His question is the one I would put in front of any model purchase: what did the team not work on "
    "while it spent about 40 minutes a day on manual triage that keyword rules could have cut to about "
    "10?\n\n"
    "Before I would support a model, I want three answers written down.\n"
)
NATURAL_DRAFT = (
    "Most triage projects start with the size of the queue. That's usually the wrong place to start.\n\n"
    "A fictional report makes a simpler point. Keyword rules cut manual triage from about 40 minutes a day "
    "to about 10, and everyone on the team could read them.\n\n"
    "So the question to ask first is whether the rules still cover the queue. When they stop covering it, "
    "the misroutes show it.\n\n"
    "That's the moment to look at a model.\n"
)


def test_autofix_contracts_outside_quotes_only():
    text, changes = autofix('It is not new. We do not know. He said "it is not ours".\n\n#It is not y')
    assert text == "It isn't new. We don't know. He said \"it is not ours\".\n\n#It is not y"
    assert changes
    # LCE-053: a source label line is not contracted, it is removed (sources are named in sentences)
    text, changes = autofix("It is not new.\nSource: x is not y")
    assert text == "It isn't new." and "removed image label: Source: x is not y" in changes


def test_consultant_draft_is_refused_with_the_exact_revisions(store):
    pid = legacy_post(store)
    refresh_click(store, pid)
    before = current_text(store, pid)
    with pytest.raises(StoreError) as exc:
        repackage.package(store, pid, {**PKG, "text": CONSULTANT_DRAFT})
    msg = str(exc.value)
    assert "writing gate" in msg and "revise and resubmit" in msg.lower()
    assert "split this" in msg  # the long sentences, quoted
    assert "puts it clearly" in msg  # the editorial phrase
    assert "pov.unbacked_belief" in msg and "I want three answers" in msg
    assert current_text(store, pid) == before  # nothing was stored


def test_revised_natural_draft_becomes_the_candidate(store):
    pid = legacy_post(store)
    refresh_click(store, pid)
    repackage.package(store, pid, {**PKG, "text": NATURAL_DRAFT})
    post = store.load_post(pid)
    assert post["state"] == "AWAITING_APPROVAL" and current_text(store, pid).startswith(
        "Most triage projects"
    )


def test_stated_limitation_is_only_for_what_honest_writing_cannot_fix():
    hum = {
        "score": 9,
        "of": 10,
        "verdict": "PARTIAL",
        "criteria": [{"id": "owner_voice", "result": "fail"}, {"id": "spoken", "result": "pass"}],
    }
    assert not gate(hum, set())["pass"]
    assert gate(hum, set(), "source-heavy; no owner material on this topic")["pass"]
    mech = {**hum, "criteria": [{"id": "spoken", "result": "fail"}]}
    assert not gate(mech, set(), "any reason")["pass"]  # a limitation never excuses weak writing
    assert not gate({**hum, "criteria": []}, {"pov.unbacked_belief"}, "x")["pass"]


def test_media_only_refresh_is_not_blocked_by_the_writing_gate(store):
    pid = legacy_post(store)
    refresh_click(store, pid)
    repackage.package(store, pid, {**PKG, "text": NATURAL_DRAFT})
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"


def test_no_bypass_cli_draft_and_scheduler_path_is_gated(store):
    """lce draft/humanize save → QA → duplicate check → prepare: the weak text never reaches approval."""
    from conftest import selected_post

    from lce.approval import prepare
    from lce.dupcheck import run_dupcheck
    from lce.posts import save_draft, save_humanized
    from lce.qa import run_qa

    pid = selected_post(store)
    weak = CONSULTANT_DRAFT.replace(
        "Before I would support a model, I want three answers written down.\n", ""
    ).replace("His question is the one I would put in front of any model purchase", "The question is")
    save_draft(store, pid, weak)
    save_humanized(store, pid, weak)
    run_qa(store, pid, denylist=[])
    assert store.load_post(pid)["state"] == "QA_PASSED"   # warnings only: QA alone would let it through
    run_dupcheck(store, pid)
    with pytest.raises(StoreError, match="writing gate"):
        prepare(store, pid)
    assert store.load_post(pid)["state"] != "AWAITING_APPROVAL"


def test_owner_edit_is_the_owners_own_text_and_not_gated(store):
    from conftest import awaiting_post

    pid = awaiting_post(store)
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"
