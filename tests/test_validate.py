from pathlib import Path

from conftest import write

from lce.validate import validate_dir

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo-persona"


def test_demo_persona_valid():
    checked, errors = validate_dir(DEMO)
    assert checked >= 5
    assert errors == [], [e.render() for e in errors]


def test_public_fact_requires_approval_date(tmp_path):
    write(tmp_path, "story_bank/facts/abc-fact.yaml", """
fact_id: abc-fact
title: Title
description: Long enough description
period: 2025
my_role: Role
evidence: []
publication_status: PUBLIC
sensitivity: low
allowed_topics: []
allowed_claims: []
""")
    _, errors = validate_dir(tmp_path)
    assert any("approved_at" in e.message for e in errors)


def test_settings_rejects_llm_api_runtime_and_bad_timezone(tmp_path):
    write(tmp_path, "config/settings.yaml", """
language: en
timezone: Mars/Olympus
cadence: {posts_per_week: 1, slots: [{day: tue, time: "08:30"}]}
approval: {mode: pull_request, expire_unapproved: true}
publisher: {provider: none}
llm: {runtime: api}
""")
    _, errors = validate_dir(tmp_path)
    messages = " ".join(e.message for e in errors)
    assert "timezone" in messages
    assert "llm" in " ".join(e.render() for e in errors)


def test_unapproved_posts_cannot_be_configured_to_publish(tmp_path):
    write(tmp_path, "config/settings.yaml", """
language: en
timezone: UTC
cadence: {posts_per_week: 1, slots: [{day: tue, time: "08:30"}]}
approval: {mode: pull_request, expire_unapproved: false}
publisher: {provider: none}
llm: {runtime: claude_code}
""")
    _, errors = validate_dir(tmp_path)
    assert errors
