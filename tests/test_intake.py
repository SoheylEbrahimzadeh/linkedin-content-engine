"""Owner-material intake: nothing becomes owner material without the owner confirming it."""

import pytest
import yaml

from lce import intake, persona
from lce.store import StoreError
from lce.validate import validate_dir

Q = [
    {
        "id": "ap-a",
        "kind": "approaches",
        "section": "Approaches",
        "target_id": "ap-root-cause-first",
        "question": "Confirm, correct or reject this draft.",
        "draft": "Find the underlying problem first.",
    },
    {
        "id": "ob-1",
        "kind": "observations",
        "section": "Observations",
        "question": "What keeps repeating on the service desk?",
    },
]
PROP = {
    "text": "When tickets reopen, the first fix usually treated the symptom.",
    "scope": {"meaning": ["reopened tickets often point to a symptom fix"], "strength": "normal"},
    "allowed_paraphrases": ["Reopened tickets often mean the first fix only treated the symptom."],
    "forbidden_interpretations": ["Every reopened ticket is a bad fix."],
    "public_use": True,
}


def test_raw_answer_is_kept_verbatim_and_nothing_is_confirmed_without_the_owner(store):
    intake.init(store, Q, round_=2)
    intake.answer(store, "ob-1", "تیکت‌ها برمی‌گردن چون فقط علامت رو درست کردن")
    assert persona.confirmed(store, ("observations",)) == {}
    with pytest.raises(StoreError, match="no proposal"):
        intake.confirm(store, "ob-1", "x")
    it = intake.propose(store, "ob-1", PROP)
    assert it["status"] == "proposed" and persona.confirmed(store, ("observations",)) == {}
    with pytest.raises(StoreError, match="changed since"):
        intake.confirm(store, "ob-1", "not-the-shown-hash")
    intake.confirm(store, "ob-1", it["proposal_hash"])
    item = persona.confirmed(store, ("observations",))["ob-1"]
    assert item["original"] == "تیکت‌ها برمی‌گردن چون فقط علامت رو درست کردن"
    assert item["text"] == PROP["text"] and item["confidence"] == "confirmed"
    assert item["forbidden_interpretations"] == PROP["forbidden_interpretations"]
    assert item["provenance"]["proposal_hash"] == it["proposal_hash"]
    _, errors = validate_dir(store.root)
    assert not errors


def test_nothing_is_invented_and_ambiguity_blocks_confirmation(store):
    intake.init(store, Q, round_=2)
    with pytest.raises(StoreError, match="nothing is invented"):
        intake.propose(store, "ob-1", PROP)
    intake.answer(store, "ob-1", "it depends")
    it = intake.propose(store, "ob-1", {**PROP, "ambiguities": ["depends on what?"]})
    assert it["status"] == "unresolved"
    with pytest.raises(StoreError, match="unresolved"):
        intake.confirm(store, "ob-1", it["proposal_hash"])


def test_confirmed_material_is_never_overwritten(store):
    intake.init(store, Q, round_=2)
    intake.answer(store, "ob-1", "first answer")
    it = intake.propose(store, "ob-1", PROP)
    intake.confirm(store, "ob-1", it["proposal_hash"])
    with pytest.raises(StoreError, match="already confirmed"):
        intake.answer(store, "ob-1", "a new answer")


def test_draft_confirmation_and_rejection(store):
    intake.init(store, Q, round_=2)
    it = intake.propose(
        store, "ap-a", {"text": "Find the underlying problem first.", "scope": {"actions": ["find"]}}
    )
    intake.answer(store, "ap-a", "yes")
    it = intake.load(store)["items"][0]
    intake.confirm(store, "ap-a", it["proposal_hash"])
    item = persona.confirmed(store, ("approaches",))["ap-root-cause-first"]
    assert "[draft shown]" in item["original"] and "yes" in item["original"]
    intake.reject(store, "ob-1", "not now")
    assert "ob-1" not in persona.confirmed(store)


def test_questionnaire_lists_confirmed_material_without_asking_again(store):
    persona.init_templates(store)
    p = store.root / "profile/golden/opinions.yaml"
    doc = yaml.safe_load(p.read_text())
    doc["items"] = [
        {
            "id": "op-x",
            "text": "AI needs human control over it.",
            "status": "owner_confirmed",
            "source": "owner-2026-10-04",
        }
    ]
    p.write_text(yaml.safe_dump(doc))
    intake.init(store, Q, round_=2)
    md = intake.questionnaire(store)
    assert "Already confirmed (not asked again)" in md and "op-x" in md and "ap-a" in md
