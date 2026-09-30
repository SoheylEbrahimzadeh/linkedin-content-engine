"""Personal Brand Engine: brand.yaml, strategy, evidence gating, brand QA (fictional data)."""

from datetime import date

import pytest
from conftest import GOOD_POST

from lce import brand
from lce.cli import main
from lce.interview import answer, ready_for_drafting
from lce.planning import rank_candidates, select
from lce.qa import run_checks
from lce.rules import ready_ruleset
from lce.store import StoreError
from lce.validate import validate_doc

DAY = date(2025, 5, 6)


def pick(store, **kw):
    args = {"candidate_id": "c-demo-rules-first", "pillar": "automation", "angle": "rules first",
            "fmt": "text", "plan_date": DAY}
    return select(store, **{**args, **kw})


def set_brand(store, **changes):
    doc = store.brand()
    doc.update(changes)
    store.write_doc(store.brand_path, "brand", doc)


def test_demo_brand_is_valid_and_consistent(store):
    assert validate_doc("brand", store.brand()) == []
    assert brand.problems(store) == []
    assert validate_doc("brand", {"themes": [{"id": "x", "name": "X", "evidence": "maybe"}]})


def test_cross_reference_problems(store):
    set_brand(store, mix={"pillars": {"automation": 0.8, "ghost": 0.5}},
              themes=[{"id": "t", "name": "T", "pillars": ["nope"]}],
              narrative={"chapters": [{"id": "c", "title": "C", "stories": ["missing-story"]}]})
    found = " | ".join(brand.problems(store))
    for needle in ("unknown pillar 'ghost'", "add up to 1.30", "theme t: unknown pillar 'nope'",
                   "unknown story 'missing-story'"):
        assert needle in found


def test_personal_theme_without_story_needs_input(store):
    post = pick(store, theme="field-lessons")
    assert post["state"] == "NEEDS_INPUT"
    assert post["brand"] == {"evidence": "personal", "theme": "field-lessons"}
    assert "personal evidence required" in post["history"][-1]["note"]


def test_personal_theme_with_public_story_is_selected(store):
    post = pick(store, theme="field-lessons", chapter="service-desk-years",
                stories=["demo-ticket-routing"])
    assert post["state"] == "SELECTED"
    assert post["brand"] == {"evidence": "personal", "theme": "field-lessons",
                             "chapter": "service-desk-years"}


def test_external_evidence_needs_no_story_and_unknown_ids_fail(store):
    assert pick(store, theme="observations")["brand"] == {"evidence": "external",
                                                          "theme": "observations"}
    with pytest.raises(StoreError, match="unknown brand theme"):
        pick(store, theme="nope")
    with pytest.raises(StoreError, match="unknown narrative chapter"):
        pick(store, chapter="nope")


def test_status_and_recommendation_follow_the_mix(store):
    pick(store, theme="observations")  # one automation post in the window
    st = brand.status(store, DAY)
    rows = {r["id"]: r for r in st["pillars"]}
    assert rows["automation"]["actual"] == 1.0 and rows["operations"]["deficit"] == 0.35
    assert st["personal_share"] == 0.0 and st["min_personal_share"] == 0.3
    recs = brand.recommend(store, DAY)
    assert recs[0]["pillar"] == "operations"
    assert all(r["pillar"] != "automation" for r in recs[:2])


def test_recommendation_flags_missing_personal_evidence(store):
    story = store.stories()["demo-ticket-routing"]
    story["publication_status"] = "PRIVATE"
    store.write_doc(store.story_path("demo-ticket-routing"), "story", story)
    set_brand(store, themes=[{"id": "field-lessons", "name": "Lessons", "evidence": "personal"}])
    rec = brand.recommend(store, DAY, count=1)[0]
    assert rec["evidence"] == "personal" and rec["needs_personal_input"] is True
    assert brand.status(store, DAY)["themes"][0]["needs_personal_input"] is True


def test_ranking_uses_brand_target_and_avoid_list(store):
    base = rank_candidates(store, DAY)[0]
    assert "pillar automation below its brand target" in base["reasons"]
    prof = store.profile()
    prof["topics"]["avoid"].append("routing rules")
    store.write_doc(store.profile_path, "profile", prof)
    assert "matches a topic on the avoid list" in rank_candidates(store, DAY)[0]["reasons"]


def _qa(store, post, text=GOOD_POST):
    return {f.code for f in run_checks(text, rules=ready_ruleset("en"), voice=store.voice(),
                                       profile=store.profile(), post=post,
                                       stories=store.stories(), denylist=[],
                                       brand=store.brand())}


def test_qa_brand_checks(store):
    post = {"sources": [], "claims": [], "stories_used": [], "brand": {"evidence": "personal"}}
    assert "brand.personal_evidence_missing" in _qa(store, post, "Plain text without numbers.\n")
    post = {"sources": [], "claims": [], "stories_used": [], "brand": {"evidence": "external"}}
    codes = _qa(store, post, "Teams in the Nordics ask about this every week.\n")
    assert "brand.market_as_subject" in codes and "brand.personal_evidence_missing" not in codes


def test_brand_interview_writes_the_private_brand_file(store):
    store.brand_path.unlink()
    assert {q.id for q in ready_for_drafting(store)} == {"brand_objective", "brand_themes"}
    answer(store, "brand_objective", "Known for practical automation.")
    answer(store, "brand_themes", "[{id: how-to, name: How-to, evidence: either}]")
    answer(store, "brand_target_markets", "Region A; Region B")
    assert ready_for_drafting(store) == []
    doc = store.brand()
    assert doc["target"]["markets"] == ["Region A", "Region B"]
    assert doc["themes"][0]["id"] == "how-to"


def test_cli_brand_commands(store, capsys):
    assert main(["--data-dir", str(store.root), "brand", "status", "--date", "2025-05-06"]) == 0
    assert main(["--data-dir", str(store.root), "brand", "next", "--date", "2025-05-06"]) == 0
    out = capsys.readouterr().out
    assert "pillar automation" in out and "evidence" in out


def test_dashboard_snapshot_has_a_brand_view(store):
    from lce.dashboard.snapshot import build_snapshot

    pick(store, theme="observations")
    snap = build_snapshot(store, mode="demo")
    b = snap["brand"]
    assert b["configured"] and b["as_of"] == "2025-05-06" and b["posts_in_window"] == 1
    assert b["objective"] and b["next"] and b["next"][0]["pillar"] == "operations"


def test_theme_evidence_counts_stories_of_its_pillars(store):
    themes = {t["id"]: t for t in brand.status(store, DAY)["themes"]}
    assert themes["field-lessons"]["evidence_stories"] == 1          # via pillar automation
    assert themes["field-lessons"]["needs_personal_input"] is False
    assert themes["observations"]["evidence_stories"] == 0            # no pillars, no tag
