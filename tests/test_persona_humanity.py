"""LCE-051: voice system, Golden Voice Set, opinion engine, networking signal, realism, humanity test."""

from datetime import UTC
from importlib import resources

import pytest
import yaml
from conftest import selected_post

from lce import brand, persona, rolling
from lce.humanity import evaluate, score, shape
from lce.qa import run_checks
from lce.rules import load_ruleset
from lce.store import StoreError
from lce.validate import validate_dir, validate_doc

RULES = load_ruleset("en")
GOLD = {
    "op-owner": {
        "id": "op-owner",
        "kind": "opinions",
        "status": "owner_confirmed",
        "source": "owner-2026-10-04",
        "text": "Automation without a named owner for each decision becomes shelfware.",
    },
    "ob-reopen": {
        "id": "ob-reopen",
        "kind": "observations",
        "status": "owner_confirmed",
        "source": "owner-2026-10-04",
        "text": "Reopened tickets show automation candidates.",
    },
}
STORIES = {"pub": {"story_id": "pub", "publication_status": "PUBLIC", "allowed_claims": []}}
STANCE = "I think the owner question matters more than the tool, because it decides who fixes the rule.\n\n"


def codes(text, **post):
    base = {"sources": [], "claims": [], "stories_used": []}
    return {
        f.code
        for f in run_checks(
            text,
            rules=RULES,
            voice={},
            profile={},
            post={**base, **post},
            stories=STORIES,
            denylist=[],
            golden_items=GOLD,
            recent=post.pop("recent", None) if "recent" in post else None,
        )
    }


# ── realism regressions ────────────────────────────────────────────────
def test_generic_corporate_language():
    c = codes(STANCE + "We leverage best-in-class tooling to unlock value and move the needle.")
    assert "voice.consultant_language" in c


def test_generic_ai_openings():
    for first in (
        "In today's fast-paced world, tickets pile up.",
        "AI is transforming service desks.",
        "As technology continues to evolve, teams change.",
        "Now more than ever, ops matters.",
    ):
        assert "pattern.generic-opening" in codes(first + "\n\n" + STANCE), first


def test_empty_thought_leadership():
    assert "pattern.empty-leadership" in codes(STANCE + "It's all about people. Mindset is everything.")


def test_unsupported_first_person_and_invented_experience():
    assert "claim.experience_unsupported" in codes("In my experience, routing fails on Mondays.\n\n" + STANCE)
    assert "claim.experience_unsupported" in codes(
        "A client of mine had the same queue problem.\n\n" + STANCE
    )
    assert "claim.experience_unsupported" in codes("I've seen this in three service desks.\n\n" + STANCE)
    assert "claim.experience_unsupported" not in codes(
        "I've seen this in service desks.\n\n" + STANCE, observations_used=["ob-reopen"]
    )
    assert "claim.experience_unsupported" not in codes(
        "In my experience, it fails.\n\n" + STANCE, stories_used=["pub"]
    )
    assert "claim.personal_without_story" in codes("I led the migration of the service desk.")


def test_fake_opinions_need_an_owner_confirmed_view():
    c = codes(STANCE, content_type="personal_pov")
    assert "pov.no_owner_opinion" in c
    assert "pov.no_owner_opinion" not in codes(
        STANCE, content_type="personal_pov", opinions_used=["op-owner"]
    )
    assert "golden.unconfirmed_ref" in codes(
        STANCE, content_type="personal_pov", opinions_used=["op-invented"]
    )
    assert "pov.no_stance" in codes(
        "Owners matter for automation decisions.\n\nTools change often.",
        content_type="personal_pov",
        opinions_used=["op-owner"],
    )


def test_forced_cta_and_transactional_networking():
    for close in (
        "DM me if you want the checklist.",
        "I help companies automate their service desk.",
        "Looking for new projects in automation.",
        "Let's connect.",
        "Book a call to learn more.",
    ):
        assert "network.transactional" in codes(STANCE + close), close
    assert "network.fake_authority" in codes(STANCE + "As a seasoned expert, trust me on this.")
    assert "network.transactional" not in codes(STANCE + "I would reach out to the vendor before renewing.")


def test_excessive_jargon():
    c = codes(
        STANCE + "An AI-driven, scalable, innovative, transformative ecosystem to streamline and optimize."
    )
    assert "style.jargon" in c


def test_external_summary_without_personal_value():
    src = [{"url": "https://example.org/r"}]
    summary = "A report from Example Org looked at service desks.\n\nIt found that routing rules age quickly."
    assert "insight.summary_only" in codes(summary, content_type="external_insight", sources=src)
    assert "insight.summary_only" not in codes(
        summary + "\n\n" + STANCE, content_type="external_insight", sources=src
    )


def test_repetitive_hooks_and_structures():
    post = "Rules first.\n\nI think the owner matters, because rules age.\n\nWhich rule would you write?"
    recent = [
        post.replace("Rules", "Owners"),
        post.replace("Rules", "Tickets"),
        post.replace("Rules", "Queues"),
    ]
    assert shape(post) == shape(recent[0])
    c = {
        f.code
        for f in run_checks(
            post,
            rules=RULES,
            voice={},
            profile={},
            post={"sources": [], "claims": [], "stories_used": []},
            stories={},
            denylist=[],
            golden_items={},
            recent=recent,
        )
    }
    assert "repetition.structure" in c


def test_lessons_and_observations_need_owner_material():
    assert "lesson.no_story" in codes(STANCE, content_type="personal_lesson")
    assert "lesson.no_story" not in codes(STANCE, content_type="personal_lesson", stories_used=["pub"])
    assert "observation.no_evidence" in codes(STANCE, content_type="observation")
    assert "observation.no_evidence" not in codes(
        STANCE, content_type="observation", observations_used=["ob-reopen"]
    )


# ── humanity test ──────────────────────────────────────────────────────
def test_bundled_evaluation_set_matches_every_expectation():
    doc = yaml.safe_load(resources.files("lce.data").joinpath("humanity_eval.yaml").read_text("utf-8"))
    areas = {s["area"] for s in doc["scenarios"]}
    assert len(areas) >= 10 and len(doc["scenarios"]) >= 10
    rows = evaluate(doc)
    assert all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]
    good = [r["score"] for r in rows if r["kind"] == "good"]
    bad = [r["score"] for r in rows if r["kind"] == "bad"]
    assert min(good) > max(bad)


def test_voice_is_unknown_without_real_samples_never_a_pass():
    res = score(STANCE, rules=RULES, codes=set(), post={"sources": [{"url": "x"}]}, samples=[])
    voice = next(c for c in res["criteria"] if c["id"] == "voice")
    assert voice["result"] == "unknown" and res["unknown"] == 1


# ── private voice system ───────────────────────────────────────────────
def test_golden_init_creates_empty_templates_only_and_keeps_existing(store):
    made = persona.init_templates(store)
    assert len(made) == 5
    for kind in persona.KINDS:
        doc = yaml.safe_load((store.root / "profile/golden" / f"{kind}.yaml").read_text())
        assert doc["items"] == [] and doc["kind"] == kind
        assert not validate_doc("golden", doc)
    assert persona.init_templates(store) == []
    _, errors = validate_dir(store.root)
    assert not errors


def test_only_owner_confirmed_items_count(store):
    persona.init_templates(store)
    path = store.root / "profile/golden/opinions.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["items"] = [
        {
            "id": "op-draft",
            "text": "A draft view that is not confirmed yet.",
            "status": "draft",
            "source": "owner-2026-10-04",
        },
        {
            "id": "op-real",
            "text": "Name an owner for each automated decision.",
            "status": "owner_confirmed",
            "source": "owner-2026-10-04",
        },
    ]
    path.write_text(yaml.safe_dump(doc))
    assert set(persona.confirmed(store)) == {"op-real"}
    assert persona.producible(store)["personal_pov"] is True
    assert persona.status(store)["golden"]["opinions"] == {
        "confirmed": 1,
        "drafts": 1,
        "target_min": 5,
        "target_max": 10,
        "ready": False,
    }


def test_voice_gaps_list_missing_and_owner_input_traits(store):
    gaps = {g["trait"]: g["status"] for g in persona.voice_gaps(store)}
    assert "humor" in gaps and "networking" in gaps
    v = store.voice()
    v["humor"] = {"owner_input_required": True, "question": "How much humour, and what kind?"}
    v["openings"] = "A concrete observation from the work"
    store.write_doc(store.voice_path, "voice", v)
    gaps = {g["trait"]: g["status"] for g in persona.voice_gaps(store)}
    assert gaps["humor"] == "owner_input" and "openings" not in gaps


def test_personal_pov_without_owner_opinion_stays_needs_input(store):
    post = selected_post(store, content_type="personal_pov")
    assert post["state"] == "NEEDS_INPUT"
    assert "point of view required" in post["history"][-1]["note"]
    out = persona.opinion_for(store, post)
    assert out["answer"] == "not recorded"


def test_select_refuses_unknown_golden_refs(store):
    with pytest.raises(StoreError, match="owner-confirmed"):
        selected_post(store, content_type="personal_pov", opinions=["made-up"])


def test_content_mix_reports_what_blocks_each_type(store):
    from datetime import date

    m = brand.content_mix(store, date(2026, 10, 4))
    rows = {r["content_type"]: r for r in m["types"]}
    assert rows["personal_pov"]["producible"] is False and "owner-confirmed" in rows["personal_pov"]["needs"]
    assert rows["external_insight"]["producible"] is True
    assert brand.content_type_for(store, [], date(2026, 10, 4)) in {
        k for k, v in persona.producible(store).items() if v
    }


def test_rolling_entries_validate_with_a_content_type(store):
    from datetime import datetime

    from lce.clock import FixedClock, use_clock

    with use_clock(FixedClock(datetime(2026, 10, 4, 6, tzinfo=UTC))):
        rolling.roll(store)
    assert all(e.get("content_type") for e in store.plan()["entries"] if e.get("origin") == "rolling")
    _, errors = validate_dir(store.root)
    assert not errors


def test_stance_origin_says_whose_view_it_is():
    proposed = score(STANCE, rules=RULES, codes=set(), post={"sources": [{"url": "x"}]}, samples=[])
    owner = score(STANCE, rules=RULES, codes=set(), post={"opinions_used": ["op-owner"]}, samples=[])
    assert proposed["stance_origin"] == "proposed" and owner["stance_origin"] == "owner"


def test_owner_punctuation_rules_block_em_dashes_and_guillemets():
    voice = {"formatting": {"em_dash_allowed": False, "guillemets_allowed": False}}
    base = {"sources": [], "claims": [], "stories_used": []}
    found = {
        f.code: f.severity
        for f in run_checks(
            STANCE + "A rule — and an owner.",
            rules=RULES,
            voice=voice,
            profile={},
            post=base,
            stories={},
            denylist=[],
        )
    }
    assert found.get("style.em_dash_forbidden") == "error"
    found = {
        f.code
        for f in run_checks(
            STANCE + "Das ist «wichtig».",
            rules=RULES,
            voice=voice,
            profile={},
            post=base,
            stories={},
            denylist=[],
        )
    }
    assert "style.guillemets_forbidden" in found
    allowed = {
        f.code
        for f in run_checks(
            STANCE + "A rule — and an owner.",
            rules=RULES,
            voice={},
            profile={},
            post=base,
            stories={},
            denylist=[],
        )
    }
    assert "style.em_dash_forbidden" not in allowed


def test_stance_and_reasoning_cover_plain_owner_phrasing():
    from lce.humanity import signals

    s = signals(
        "In my opinion, we need to discuss this with the team. Since many operations depend on it, "
        "we must first identify the flaws. Only then can we decide.",
        RULES,
    )
    assert s["stance"] >= 2 and s["reasoning"] >= 2
