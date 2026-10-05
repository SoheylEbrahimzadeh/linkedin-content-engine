"""Owner rule: no stronger position than the owner's confirmed material (semantic, not lexical)."""

import pytest

from lce.owner_scope import check

CONTROL = {"id": "op-control", "text": "AI needs human control."}
OVERSIGHT = {
    "id": "op-oversight",
    "text": "AI does not always get it right. It needs human judgment and control over it, so that "
    "unintended mistakes by the AI, which does not share a real person's view, are caught. "
    "There has to be human management above it.",
}
ROOT = {
    "id": "op-root",
    "text": "Before automating or replacing a system, find the underlying problem first and decide with "
    "the team; many operations depend on that system, so a migration has a cost.",
}


@pytest.mark.parametrize(
    "item, sentence, verdict",
    [
        # the owner's own examples (2026-10-05)
        (CONTROL, "There still needs to be a person in control when AI takes an action.", "faithful"),
        (CONTROL, "I'd always keep approval enabled.", "stronger"),
        (CONTROL, "I would never let AI act without a human.", "stronger"),
        (CONTROL, "I'd require approval for every action.", "stronger"),
        (OVERSIGHT, "I wouldn't cut that checking, though.", "narrower"),
        (OVERSIGHT, "I'd keep that approval on.", "narrower"),
        (OVERSIGHT, "AI doesn't always get it right, and if it repeats a click in a real system, a person "
                    "needs to be there to catch it.", "faithful"),
        # different and broader readings
        (CONTROL, "AI doesn't need human control.", "different"),
        (CONTROL, "AI needs human control in general, for everything.", "broader"),
        (ROOT, "I agree with him.", "different"),
        (ROOT, "You should replace old systems quickly.", "different"),
        (ROOT, "Replacing a system is always a bad idea.", "stronger"),
        # faithful paraphrases with different words
        (ROOT, "Before replacing a system, we first need to find the actual problem and talk it through "
               "with the team.", "faithful"),
        (ROOT, "A lot of daily work depends on that system, so moving off it has a cost too.", "faithful"),
    ],
)
def test_semantic_fidelity(item, sentence, verdict):
    assert check(sentence, [item])["verdict"] == verdict


def test_owner_boundaries_come_first():
    item = {**CONTROL, "allowed_paraphrases": ["A person should stay in control of what the AI does."],
            "forbidden_interpretations": ["Every AI action needs a manual sign-off."],
            "scope": {"actions": ["keep"]}}
    assert check("A person should stay in control of what the AI does.", [item])["verdict"] == "faithful"
    assert check("Each AI action needs a manual sign-off from someone.", [item])["verdict"] == "stronger"
    # an action the owner explicitly confirmed is allowed in the first person
    assert check("I'd keep a person in control of the AI.", [item])["verdict"] == "faithful"


def test_unrelated_first_person_position_is_unsupported():
    assert check("I'd move the whole team to Kubernetes next quarter.", [ROOT])["verdict"] != "faithful"


def test_absolute_already_in_the_material_is_not_stronger():
    item = {"id": "p", "text": "Always test a tool before buying it for the whole company."}
    assert check("Always test a tool before you buy it for everyone in the company.", [item])["verdict"] == \
        "faithful"
