from conftest import DEMO, write

from lce.validate import validate_dir, validate_doc


def test_demo_persona_valid():
    checked, errors = validate_dir(DEMO)
    assert checked >= 7
    assert errors == [], [e.render() for e in errors]


def test_public_story_requires_approval_date():
    doc = {"story_id": "abc-story", "title": "Title", "experience": "Long enough text",
           "publication_status": "PUBLIC", "sensitivity": "low", "reusable": True}
    assert any("approved_at" in e for e in validate_doc("story", doc))
    doc["publication_status"] = "PRIVATE"
    assert validate_doc("story", doc) == []


def test_settings_guards():
    base = {"approval": {"mode": "local", "expire_unapproved": True},
            "publisher": {"provider": "none"}, "llm": {"runtime": "claude_code"}}
    assert validate_doc("settings", base) == []
    assert validate_doc("settings", {**base, "llm": {"runtime": "api"}})
    assert validate_doc("settings", {**base, "publisher": {"provider": "linkedin_official"}})
    assert validate_doc("settings", {**base, "approval": {"mode": "local",
                                                          "expire_unapproved": False}})
    assert validate_doc("settings", {**base, "timezone": "Mars/Olympus"})
    feeds = {**base, "research": {"feeds": [{"name": "x", "url": "http://insecure.example"}]}}
    assert validate_doc("settings", feeds)


def test_story_file_name_must_match(tmp_path):
    write(tmp_path, "story_bank/stories/other.yaml",
          "story_id: abc-story\ntitle: T x\nexperience: Long enough text\n"
          "publication_status: PRIVATE\nsensitivity: low\nreusable: false\n")
    _, errors = validate_dir(tmp_path)
    assert any("file name" in e.message for e in errors)


def test_plan_publication_status_cannot_be_published():
    doc = {"entries": [{"date": "2025-01-01", "topic": "t", "pillar": "p", "status": "planned",
                        "publication_status": "published"}]}
    assert validate_doc("plan", doc)
