"""Content briefs for scheduled agent work, and end-to-end readiness (fictional data)."""

from datetime import date

from test_scheduler import TUE, WED, env, humanized_post_for  # noqa: F401  (fixture)

from lce import images, jobs, scheduler
from lce.brief import briefs
from lce.cli import main
from lce.readiness import check

DAY = date(2025, 5, 4)


def test_open_slots_get_different_strategic_briefs(env):  # noqa: F811
    store, clk = env
    scheduler.run_once(store)
    clk.advance(days=1)
    scheduler.run_once(store)
    pending = scheduler.pending_agent_tasks(store)
    assert [j["job_id"] for j in pending][:2] == [TUE, WED]
    out = briefs(store, DAY, pending)
    assert [b["task"] for b in out[:2]] == ["create_post", "create_post"]
    assert out[0]["pillar"] != out[1]["pillar"]                 # not the same pillar twice
    first = out[0]
    assert first["theme"] and first["evidence"] in {"personal", "external"}
    assert first["objective"] and first["throughline"] and first["credibility_rules"]
    assert "instruction" in first


def test_brief_asks_for_the_image_decision_when_that_is_the_blocker(env):  # noqa: F811
    store, _ = env
    scheduler.run_once(store)
    pid = humanized_post_for(store, image=False)
    scheduler.run_once(store)
    job = jobs.load_job(store, TUE)
    [b] = briefs(store, DAY, [job])
    assert b["task"] == "image_decision" and b["post_id"] == pid
    assert "lce image decide" in b["instruction"]
    images.decide(store, pid, kind="none", rationale="text only")
    assert briefs(store, DAY, [jobs.load_job(store, TUE)])[0]["task"] == "continue"


def test_personal_theme_without_story_says_never_invent(env):  # noqa: F811
    store, _ = env
    story = store.stories()["demo-ticket-routing"]
    story["publication_status"] = "PRIVATE"
    store.write_doc(store.story_path("demo-ticket-routing"), "story", story)
    doc = store.brand()
    doc["themes"] = [{"id": "field-lessons", "name": "Lessons", "evidence": "personal"}]
    store.write_doc(store.brand_path, "brand", doc)
    scheduler.run_once(store)
    [b] = briefs(store, DAY, [jobs.load_job(store, TUE)])
    assert b["needs_personal_input"] is True and "Never invent" in b["instruction"]


def test_cli_brief(env, capsys):  # noqa: F811
    store, _ = env
    scheduler.run_once(store)
    assert main(["--data-dir", str(store.root), "jobs", "brief"]) == 0
    assert "task create_post" in capsys.readouterr().out


def test_readiness_reports_gates_and_owner_input(store, tmp_path, monkeypatch):
    monkeypatch.setenv("LCE_DENYLIST_GENERATED_PATH", str(tmp_path / "none.txt"))
    rows = {r["step"]: r for r in check(store, DAY, token_present=False,
                                        linkedin_config_ok=False)}
    assert rows["1 identity & positioning"]["status"] == "ok"
    assert rows["9 publishing (local, you trigger)"]["status"] == "gate"
    assert rows["9 publishing (scheduled, device-independent)"]["status"] == "gate"
    assert rows["privacy denylist"]["status"] == "todo"
    ready = {r["step"]: r for r in check(store, DAY, token_present=True, linkedin_config_ok=True,
                                         cloud_available=True)}
    assert ready["9 publishing (scheduled, device-independent)"]["status"] == "todo"


def test_readiness_cli_never_needs_the_keychain(store, capsys):
    assert main(["--data-dir", str(store.root), "readiness", "--no-keychain"]) == 0
    out = capsys.readouterr().out
    assert "token status not checked" in out and "identity" in out
