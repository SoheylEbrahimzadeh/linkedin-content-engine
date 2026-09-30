import json
import shutil

import pytest
from conftest import DEMO, ROOT

from lce import jobs, scheduler
from lce.clock import FixedClock, use_clock
from lce.dashboard.build import build_demo
from lce.dashboard.server import DEMO_NOW, demo_store, snapshot_for
from lce.dashboard.snapshot import build_snapshot, to_json
from lce.store import DataStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    deny = tmp_path / "deny.txt"
    deny.write_text("")
    monkeypatch.setenv("LCE_DENYLIST_PATH", str(deny))
    shutil.copytree(DEMO, tmp_path / "d")
    with use_clock(FixedClock("2025-05-04T12:00:00-05:00")):
        yield DataStore.open(str(tmp_path / "d"))


def test_real_mode_without_any_run_shows_no_jobs_and_no_trigger(store):
    a = build_snapshot(store, mode="real")["automation"]
    assert a["jobs"] == [] and a["counts"] == {} and a["last_run"] is None
    assert a["trigger"].startswith("none configured")
    assert a["schedule_ok"] and a["next_slot"]["job_state"] is None
    assert a["next_slot"]["slot_id"] == "2025-05-06-tue-0830"


def test_real_mode_reflects_job_state(store):
    scheduler.run_once(store)
    a = build_snapshot(store, mode="real")["automation"]
    assert a["counts"]["BLOCKED"] == 1 and a["counts"]["SKIPPED"] == 3
    assert a["last_run"]["event"] == "scheduler.finish"
    nxt = a["next_slot"]
    assert nxt["job_state"] == "BLOCKED" and nxt["blocked_reason"] == "awaiting_agent"
    assert all(u["publication_status"] in (None, "not_published") for u in a["upcoming"])


def test_failed_and_reconcile_jobs_become_issues(store, monkeypatch):
    scheduler.run_once(store)
    job = jobs.load_job(store, "job-2025-05-06-tue-0830")
    jobs.transition(store, job, jobs.J.READY, "x", "t")
    jobs.acquire_lease(job, "scheduler", "t", 30)
    jobs.transition(store, job, jobs.J.RUNNING, "x", "t")
    with use_clock(FixedClock("2025-05-04T13:00:00-05:00")):
        issues = build_snapshot(store, mode="real")["issues"]
    assert any(i["kind"] == "NEEDS_RECONCILE" and "lease expired" in i["message"] for i in issues)
    with use_clock(FixedClock("2025-05-04T13:00:00-05:00")):
        scheduler.run_once(store)  # reconcile → READY → next pass
        job = jobs.load_job(store, "job-2025-05-06-tue-0830")
        jobs.transition(store, job, jobs.J.RUNNING, "x", "t")
        jobs.transition(store, job, jobs.J.FAILED, "boom", "t")
        kinds = {i["kind"] for i in build_snapshot(store, mode="real")["issues"]}
    assert "FAILED" in kinds


def test_invalid_schedule_is_reported_not_hidden(store):
    s = store.settings()
    s.pop("cadence")
    store.write_doc(store.settings_path, "settings", s)
    a = build_snapshot(store, mode="real")["automation"]
    assert a["schedule_ok"] is False and "cadence" in a["schedule_error"]
    assert a["upcoming"] == []


def test_stale_lock_is_flagged(store):
    jobs.acquire_lock(store, "crashed", 1)
    with use_clock(FixedClock("2025-05-04T12:05:00-05:00")):
        snap = build_snapshot(store, mode="real")
    assert snap["automation"]["lock"]["expired"] is True
    assert any("stale scheduler lock" in i["message"] for i in snap["issues"])


def test_pipeline_lists_scheduling_as_available_and_publishing_not():
    snap = snapshot_for(demo_store(ROOT), "demo")
    caps = {p["id"]: p["status"] for p in snap["pipeline"]}
    assert caps["scheduling"] == "available" and caps["publishing"] == "manual"
    assert caps["verification"] == "not_implemented"


def test_demo_uses_fixed_demo_clock_and_fictional_jobs():
    snap = snapshot_for(demo_store(ROOT), "demo")
    assert snap["meta"]["mode"] == "demo"
    assert snap["automation"]["now"] == FixedClock(DEMO_NOW).now().isoformat()
    states = {j["job_id"]: j["state"] for j in snap["automation"]["jobs"]}
    assert states["job-2025-05-06-tue-0830"] == "SUCCEEDED"
    assert states["job-2025-05-07-wed-0830"] == "BLOCKED"
    assert snap["issues"] == []


def test_demo_build_contains_no_private_or_real_schedule(tmp_path, monkeypatch):
    private = DataStore.init(tmp_path / "private")
    s = private.settings()
    s["timezone"] = "Asia/Kathmandu"  # canary: must never reach the demo
    s["cadence"] = {"posts_per_week": 1, "slots": [{"day": "fri", "time": "06:45"}]}
    private.write_doc(private.settings_path, "settings", s)
    monkeypatch.setenv("LCE_DATA_DIR", str(private.root))
    out = tmp_path / "site"
    build_demo(ROOT, out)
    blob = (out / "data" / "snapshot.json").read_text()
    assert "Kathmandu" not in blob and "06:45" not in blob
    assert json.loads(blob)["automation"]["upcoming"][0]["timezone"] == "America/Chicago"
    assert str(tmp_path) not in blob and "/Users/" not in blob


def test_snapshot_never_exposes_lock_paths_or_secrets(store):
    scheduler.run_once(store)
    out = to_json(build_snapshot(store, mode="demo"))
    assert str(store.root) not in out


def test_ui_takes_time_from_the_snapshot_not_the_browser():
    app = (ROOT / "src" / "lce" / "dashboard" / "static" / "app.js").read_text()
    assert "new Date()" not in app and "Date.now()" not in app
