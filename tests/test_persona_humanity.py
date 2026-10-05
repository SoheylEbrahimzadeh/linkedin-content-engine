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
    voice = next(c for c in res["criteria"] if c["id"] == "owner_voice")
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


# ── polished consultant/editorial voice (grammatically natural, not the owner) ──
STRICT = {"formatting": {"editorial_phrases_allowed": False, "symmetry_allowed": False}}
EDITORIAL = (
    "A 2026 survey by Example Org listed three barriers to service desk bots.\n\n"
    "The three barriers do not name the tool itself. My reading: they are content, control and "
    "capacity questions. A bot needs current articles, clear limits and a team that corrects it.\n\n"
    "That preparation is less visible than a demo. In my view, it decides whether the rollout works."
)
SRC = [{"url": "https://example.org/r"}]


def found(text, voice=None, **post):
    base = {"sources": SRC, "claims": [], "stories_used": [], "content_type": "external_insight"}
    return {
        f.code: f.severity
        for f in run_checks(
            text,
            rules=RULES,
            voice=voice or {},
            profile={},
            post={**base, **post},
            stories=STORIES,
            denylist=[],
            golden_items=GOLD,
        )
    }


def test_editorial_phrases_and_labels_are_errors_when_the_owner_forbids_them():
    loose, strict = found(EDITORIAL), found(EDITORIAL, STRICT)
    assert loose["voice.editorial_phrase"] == "warning"
    assert strict["voice.editorial_phrase"] == "error"
    assert strict["pattern.editorial-label"] == "error"
    phrases = ("This suggests that queues age.", "The key takeaway is ownership.", "Bottom line: test it.")
    for phrase in phrases:
        assert {"voice.editorial_phrase", "pattern.editorial-label"} & set(found(STANCE + phrase, STRICT))


def test_symmetry_needs_a_pattern_not_one_list():
    one = "We checked rules, owners and tickets because the queue grew."
    assert "style.symmetry" not in found(one, STRICT)
    two = one + " Then we fixed articles, limits and staffing."
    assert found(two, STRICT)["style.symmetry"] == "error"
    assert "style.symmetry" not in found(two)


def test_a_belief_without_an_owner_opinion_is_not_a_point_of_view():
    c = found("I think ownership matters because rules age.")
    assert c["pov.unbacked_belief"] == "error"
    assert "pov.unbacked_belief" not in found("I think ownership matters because rules age.",
                                              opinions_used=["op-owner"])
    assert "pov.unbacked_belief" not in found("Ownership matters here, because rules age.")


def test_a_reasoned_consequence_is_not_a_summary():
    text = "A report from Example Org found that routing rules age quickly. So who rewrites them?"
    assert "insight.summary_only" not in found(text)


def test_the_editorial_failure_mode_fails_voice_spoken_and_opinion():
    codes_ = set(found(EDITORIAL, STRICT))
    samples = ["I ask which ticket they reopened twice. That list is real."] * 3
    res = score(EDITORIAL, rules=RULES, codes=codes_, post={"sources": SRC}, samples=samples)
    by = {c["id"]: c["result"] for c in res["criteria"]}
    assert by["owner_voice"] == by["spoken"] == by["no_manufactured_opinion"] == "fail"
    assert res["stance_origin"] == "none"


def test_an_owner_angle_needs_an_owner_opinion():
    text = "Ownership matters here, because rules age."
    assert found(text, angle_origin="owner")["pov.unbacked_belief"] == "error"
    assert "pov.unbacked_belief" not in found(text, angle_origin="owner", opinions_used=["op-owner"])
    assert "pov.unbacked_belief" not in found(text, angle_origin="proposed")


# ── humanity v2: the owner's ten-point standard (2026-10-05) ───────────
NATURAL = (
    "Example Org surveyed 300 service desks about their chatbots. 40% have paused one in the first year.\n\n"
    "The reason they give most often is stale knowledge articles. Not the model.\n\n"
    "That fits how these bots work. They answer from whatever's in the knowledge base, so an article "
    "nobody's touched in two years turns into a wrong answer, delivered with total confidence.\n\n"
    "So how many of those paused bots would've been fine with a month of article cleanup first?"
)
CLAIMS = [
    {"text": "Example Org surveyed 300 service desks about their chatbots", "source_url": SRC[0]["url"]},
    {"text": "40% have paused one in the first year", "source_url": SRC[0]["url"]},
]
SAMPLES = [
    "I stopped asking teams for automation ideas in workshops. I ask which ticket they reopened twice "
    "last week, because that list is shorter and it's real.",
    "The vendor demo worked because the data was clean, and ours wasn't. I'd rather start with the mess "
    "we have than the process we wish we had.",
    "Most of my time on rollouts goes into the handover, not the build. If the service desk can't explain "
    "the rule, it won't survive the first incident.",
]


def humanity(text, voice=None, **post):
    base = {"sources": SRC, "claims": CLAIMS, "stories_used": [], "content_type": "external_insight"}
    post = {**base, **post}
    fs = run_checks(text, rules=RULES, voice=voice or STRICT, profile={}, post=post, stories=STORIES,
                    denylist=[], golden_items=GOLD)
    res = score(text, rules=RULES, codes={f.code for f in fs}, post=post, samples=SAMPLES,
                voice=voice, errors={f.code for f in fs if f.severity == "error"})
    return res, {c["id"]: c["result"] for c in res["criteria"]}


def test_ten_out_of_ten_only_when_every_criterion_holds():
    res, by = humanity(NATURAL)
    assert res["score"] == 10 and res["verdict"] == "PASS", res["criteria"]
    assert [c for c, _ in __import__("lce.humanity", fromlist=["CRITERIA"]).CRITERIA] == list(by)
    res, by = humanity(NATURAL.replace("That fits", "Furthermore, that fits"))
    assert by["no_stiffness"] == "fail" and res["verdict"] != "PASS"


def test_unknown_voice_can_never_reach_pass():
    res = score(NATURAL, rules=RULES, codes=set(), post={"sources": SRC}, samples=[])
    assert res["unknown"] == 1 and res["verdict"] != "PASS"


@pytest.mark.parametrize(
    "edit, criterion",
    [
        (lambda t: t.replace("That fits how", "In order to utilize them, that fits how"), "no_stiffness"),
        (lambda t: t.replace("Not the model.", "It is not the model.").replace("would've", "would have"),
         "spoken"),
        (lambda t: t + "\n\nIt's not about the bot. It's about the articles.", "no_symmetry"),
        (lambda t: t.replace("So how many", "I was genuinely surprised. So how many"),
         "no_manufactured_opinion"),
        (lambda t: t.replace("So how many", "I think this matters. So how many"), "no_manufactured_opinion"),
        (lambda t: t.replace("So how many", "I'd look at the articles first. So how many"),
         "no_manufactured_opinion"),
        (lambda t: t.replace("That fits how", "In other words, that fits how"), "no_over_explaining"),
        (lambda t: t + "\n\nThe lesson is simple: clean up the articles first.", "genuine_ending"),
        (lambda t: "Hot take: chatbots are a game-changer.\n\n" + t, "real_person"),
        (lambda t: t.replace("So how many", "This is groundbreaking. So how many"), "real_person"),
    ],
)
def test_each_rule_breaks_its_criterion(edit, criterion):
    _, by = humanity(edit(NATURAL))
    assert by[criterion] == "fail", by


def test_flat_rhythm_and_even_paragraphs_fail_variation():
    flat = "\n\n".join(
        "Example Org asked service desks about chatbots today. Many said their knowledge articles were old."
        for _ in range(4)
    )
    _, by = humanity(flat)
    assert by["variation"] == "fail"


def test_owner_counter_examples_and_avoided_words_fail_owner_voice():
    v = {**STRICT, "avoided_vocabulary": ["total confidence"],
         "counter_examples": ["The three most cited barriers do not name the model itself."]}
    _, by = humanity(NATURAL, voice=v)
    assert by["owner_voice"] == "fail"


def test_credit_lines_and_hashtags_are_not_prose():
    res, _ = humanity(NATURAL + "\n\nSource: https://example.org/survey\nImage: Example Org\n\n#ITSM #AI")
    assert res["verdict"] == "PASS", res["criteria"]


def test_a_list_of_numbers_is_not_ai_symmetry():
    from lce.qa import count_triads

    assert count_triads("another 12, 24 or 36 months") == 0
    assert count_triads("data, control and people") == 1


# ── voice validation on real-post failure modes (synthetic wording) ────
def test_source_framework_voiced_as_a_personal_requirement_is_flagged():
    for line in (
        "Before I would support a full replacement, I want three answers written down.",
        "His question is the one I would put in front of any rip-and-replace plan.",
        "My first question would be whether the app has an export.",
        "So before I sign off a business case, I want three numbers next to the rate.",
    ):
        assert found(STANCE.replace("I think", "Ownership") + line)["pov.unbacked_belief"] == "error", line
    assert "pov.unbacked_belief" not in found("I'd keep that approval on, because rules age.",
                                              opinions_used=["op-owner"])


def test_contractions_count_as_taking_a_position():
    from lce.humanity import signals

    assert signals("I'd keep that approval on.", RULES)["stance"] >= 1


def test_written_inversions_setup_lines_and_editorial_credit_phrases():
    c = found("Age, he says, doesn't tell you much. He asks a simple question. The author puts it clearly.")
    assert {"pattern.written-inversion", "pattern.setup-line", "voice.editorial_phrase"} <= set(c)


def test_credit_lines_are_not_the_closing():
    text = "A survey found rules age.\n\nSo who rewrites them?\n\nImage: Example Org"
    recent = ["Other post.\n\nImage: Example Org"]
    fs = run_checks(text, rules=RULES, voice={}, profile={}, post={"sources": SRC, "claims": [],
                    "stories_used": []}, stories={}, denylist=[], golden_items={}, recent=recent)
    assert "repetition.closing_recent" not in {f.code for f in fs}
