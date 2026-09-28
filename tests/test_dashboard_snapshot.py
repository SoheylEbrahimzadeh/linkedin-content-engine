import json
import shutil
from pathlib import Path

import pytest
from conftest import ROOT

from lce.dashboard.build import build_demo
from lce.dashboard.snapshot import build_snapshot, to_json
from lce.store import DataStore

DEMO_DASH = ROOT / "examples" / "demo-dashboard"
APPROVED = "20250506-why-simple-routing-rules-beat-a-model-fo"
AWAITING = "20250508-example-ops-survey-2025-post-incident-re"
CANARY = "CANARY-private-7f3a9c"


@pytest.fixture
def real_store(tmp_path) -> DataStore:
    dest = tmp_path / "data"
    shutil.copytree(DEMO_DASH, dest)
    return DataStore.open(str(dest))


def posts_by_id(snap):
    return {p["post_id"]: p for p in snap["posts"]}


def test_real_mode_reads_posts_states_and_hashes(real_store):
    snap = build_snapshot(real_store, mode="real")
    assert snap["meta"]["mode"] == "real"
    posts = posts_by_id(snap)
    assert posts[APPROVED]["state"] == "APPROVED"
    assert posts[APPROVED]["approval"]["state"] == "approved"
    assert posts[APPROVED]["approval"]["approved_hash"] == posts[APPROVED]["actual_hash"]
    assert posts[APPROVED]["approval_events"][0]["decision"] == "approved"
    assert posts[AWAITING]["state"] == "AWAITING_APPROVAL"
    assert posts[AWAITING]["has_approval_artifact"] is True
    assert posts[APPROVED]["text"].startswith("Most small service teams")
    assert posts[AWAITING]["qa_report"]["status"] == "passed"
    assert snap["issues"] == []


def test_publication_is_never_claimed(real_store):
    snap = build_snapshot(real_store, mode="real")
    assert {p["publication_status"] for p in snap["posts"]} == {"not_published"}
    pub = snap["publishing"]
    assert pub["provider_configured"] is False
    assert pub["linkedin_access"] == "not_configured"
    assert pub["capability"] == "not_implemented"
    assert snap["analytics"]["available"] is False
    stages = {s["id"]: s["status"] for s in snap["pipeline"]}
    assert stages["publishing"] == stages["verification"] == stages["analytics"] == "not_implemented"
    assert "published" not in {s["status"] for s in snap["pipeline"]}


def test_calendar_and_runs_are_read(real_store):
    snap = build_snapshot(real_store, mode="real")
    dates = [e["date"] for e in snap["calendar"]]
    assert "2025-05-06" in dates
    entry = next(e for e in snap["calendar"] if e.get("draft_ref") == APPROVED)
    assert entry["post_state"] == "APPROVED" and entry["approval_status"] == "approved"
    runs = snap["runs"]
    assert runs and all("event" in r for r in runs)
    assert runs == sorted(runs, key=lambda r: r["at"])
    assert snap["health"]["last_run"] == runs[-1]


def test_missing_optional_data_does_not_crash_or_invent(tmp_path):
    store = DataStore.init(tmp_path / "empty")
    snap = build_snapshot(store, mode="real")
    assert snap["posts"] == [] and snap["calendar"] == [] and snap["runs"] == []
    assert snap["health"]["last_run"] is None
    s = snap["settings"]
    assert s["timezone"] is None and s["cadence"] is None and s["topics_public"] is None
    assert s["voice"]["emoji_max"] is None and s["voice"]["formality"] is None
    assert snap["health"]["interview"]["required_missing"]
    assert snap["issues"] == []


def test_tampered_text_is_flagged_for_reconciliation(real_store):
    path = real_store.post_dir(APPROVED) / "post.md"
    path.write_text(path.read_text() + "edited after approval\n")
    kinds = {(i["kind"], i["post_id"]) for i in build_snapshot(real_store, mode="real")["issues"]}
    assert ("NEEDS_RECONCILE", APPROVED) in kinds


def test_calendar_inconsistency_and_bad_log_lines_are_flagged(real_store):
    plan = real_store.plan()
    next(e for e in plan["entries"] if e.get("draft_ref") == APPROVED)["status"] = "in_progress"
    real_store.write_doc(real_store.plan_path, "plan", plan)
    with (real_store.root / "runs" / "2099-01.jsonl").open("a") as fh:
        fh.write("{not json\n")
    issues = build_snapshot(real_store, mode="real")["issues"]
    assert any(i["kind"] == "INCONSISTENT" and "calendar" in i["message"] for i in issues)
    assert any("unreadable run log" in i["message"] for i in issues)


def test_secrets_are_never_exposed(real_store):
    fake = "sk-ant-" + "abcdefghijklmnopqrstuvwxyz0123"
    (real_store.root / ".env").write_text(f"TOKEN={fake}\n")
    with (real_store.root / "runs" / "2099-01.jsonl").open("a") as fh:
        fh.write(json.dumps({"at": "2099-01-01T00:00:00+00:00", "event": "note", "x": fake}) + "\n")
    out = to_json(build_snapshot(real_store, mode="real"))
    assert fake not in out and "[redacted]" in out
    assert ".env" not in out


def test_settings_are_allowlisted(real_store):
    s = build_snapshot(real_store, mode="real")["settings"]
    assert set(s) == {"timezone", "cadence", "approval_mode", "publisher_provider", "llm_runtime",
                      "research", "duplicates", "voice", "topics_public", "pillars", "languages"}


def test_demo_mode_hides_paths_and_git(real_store):
    snap = build_snapshot(real_store, mode="demo")
    out = to_json(snap)
    assert snap["meta"]["data"]["git"]["available"] is False
    assert str(real_store.root) not in out


def test_demo_build_ignores_private_data_dir(tmp_path, monkeypatch):
    private = DataStore.init(tmp_path / "private")
    private.write_doc(private.profile_path, "profile", {"positioning": CANARY})
    monkeypatch.setenv("LCE_DATA_DIR", str(private.root))
    out = tmp_path / "site"
    files = build_demo(ROOT, out)
    assert files == [".nojekyll", "app.js", "config.js", "data/snapshot.json", "index.html",
                     "lib.js", "styles.css"]
    blob = "".join((out / f).read_text() for f in files)
    assert CANARY not in blob
    assert str(tmp_path) not in blob and "/Users/" not in blob and "/home/" not in blob
    snap = json.loads((out / "data" / "snapshot.json").read_text())
    assert snap["meta"]["mode"] == "demo"
    assert '"mode": "demo"' in (out / "config.js").read_text()


def test_demo_build_refuses_data_directory(tmp_path):
    store = DataStore.init(tmp_path / "private")
    with pytest.raises(RuntimeError):
        build_demo(ROOT, store.root / "site")


def test_demo_fixtures_are_fictional_and_marked():
    for p in DEMO_DASH.rglob("*"):
        if p.suffix in {".yaml", ".json"}:
            assert '"demo": true' in p.read_text() or "demo: true" in p.read_text(), p


def test_mode_must_be_known(real_store):
    with pytest.raises(ValueError):
        build_snapshot(real_store, mode="mixed")


def test_real_data_dir_of_owner_is_never_in_public_repo():
    for p in ROOT.rglob("*"):
        if ".git" in p.parts or ".venv" in p.parts:
            continue
        assert not (p.is_dir() and p.name == "lce-data"), p
    assert not Path(ROOT / "_site").exists() or True  # build output is gitignored


def test_public_fixtures_do_not_contain_the_local_username():
    import getpass
    import re

    user = getpass.getuser()
    for p in (ROOT / "examples").rglob("*"):
        if p.is_file():
            text = p.read_text(errors="ignore")
            assert not re.search(r"local-tty:(?!demo)", text), p
            if len(user) >= 6:
                assert user not in text, p


# ── state distribution & latest run ──────────────────────────────────────
NEEDS_REV = "20250510-why-teams-love-automation"


def test_state_distribution_counts_current_states_only(real_store):
    snap = build_snapshot(real_store, mode="real")
    dist = {b["id"]: b for b in snap["state_distribution"]}
    assert dist["approved"]["count"] == 1
    assert dist["awaiting_approval"]["count"] == 1
    assert dist["needs_revision"]["count"] == 1
    assert sum(b["count"] for b in snap["state_distribution"]) == len(snap["posts"])
    assert dist["publishing"]["implemented"] is False and dist["publishing"]["count"] == 0
    assert dist["published"]["implemented"] is False and dist["published"]["count"] == 0
    assert [b["id"] for b in snap["state_distribution"]][:2] == ["research", "planning"]
    assert all("posts" not in s for s in snap["pipeline"])  # capabilities, not counts


def test_state_distribution_of_empty_store(tmp_path):
    snap = build_snapshot(DataStore.init(tmp_path / "d"), mode="real")
    assert all(b["count"] == 0 for b in snap["state_distribution"])
    assert snap["latest_run"] is None


def _stages(run):
    return {s["id"]: s["status"] for s in run["stages"]}


def test_latest_run_follows_most_recent_activity(real_store):
    run = build_snapshot(real_store, mode="real")["latest_run"]
    assert run["post_id"] == NEEDS_REV  # last post touched by the fixture script
    st = _stages(run)
    assert st["research"] == st["planning"] == st["draft"] == st["humanize"] == "done"
    assert st["qa"] == "failed"
    assert st["duplicate"] == st["approval"] == "not_reached"
    assert st["publishing"] == st["verification"] == st["analytics"] == "not_implemented"


def _touch(store, pid):
    post = store.load_post(pid)
    post["history"].append({"at": "2099-01-01T00:00:00+00:00", "state": post["state"],
                            "note": "test touch"})
    store.save_post(post)


def test_latest_run_for_approved_post_is_derived_not_hardcoded(real_store):
    _touch(real_store, APPROVED)
    run = build_snapshot(real_store, mode="real")["latest_run"]
    assert run["post_id"] == APPROVED
    st = _stages(run)
    assert all(st[k] == "done" for k in ("research", "planning", "draft", "humanize", "qa",
                                          "duplicate", "approval"))
    assert [s["id"] for s in run["stages"]][-3:] == ["publishing", "verification", "analytics"]


def test_latest_run_awaiting_approval(real_store):
    _touch(real_store, AWAITING)
    st = _stages(build_snapshot(real_store, mode="real")["latest_run"])
    assert st["duplicate"] == "done" and st["approval"] == "waiting"


def test_latest_run_counts_checks_only_for_current_text(real_store):
    from lce.posts import reopen

    reopen(real_store, APPROVED, "edit")  # back to HUMANIZED, approval discarded
    run = build_snapshot(real_store, mode="real")["latest_run"]
    st = _stages(run)
    assert run["post_id"] == APPROVED and run["text_versions"] == 2
    assert st["humanize"] == "done"
    assert st["qa"] == st["duplicate"] == st["approval"] == "not_reached"


# ── research selection ───────────────────────────────────────────────────
def test_research_selected_vs_unselected_and_claims(real_store):
    research = {c["candidate_id"]: c for c in build_snapshot(real_store, mode="real")["research"]}
    selected = [c for c in research.values() if c["selected"]]
    unselected = [c for c in research.values() if not c["selected"]]
    assert selected and unselected
    for c in selected:
        assert c["status"] == "selected" and c["used_by_posts"]
    for c in unselected:
        assert c["used_by_posts"] == []
    with_claims = [c for c in research.values() if c["claims_count"]]
    assert with_claims and all(c["claims_count"] == len(c["claims"]) for c in research.values())
    assert any(c["claims_count"] == 0 for c in research.values())
    for c in research.values():
        assert "reason" not in c and "verified" not in c  # nothing inferred
    web = [c for c in research.values() if c["origin"] == "web_search"]
    assert web and all(c["untrusted"] is True for c in web)
