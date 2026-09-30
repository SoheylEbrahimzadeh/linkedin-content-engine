import hashlib
import json
import shutil
from pathlib import Path

import pytest
from conftest import DEMO, GOOD_POST, selected_post

from lce import images, jobs, scheduler
from lce.clock import FixedClock, parse_iso, use_clock
from lce.posts import save_draft, save_humanized
from lce.store import DataStore, StoreError

# Synthetic schedule from the fictional demo persona: tue/wed/thu 09:15 America/Chicago.
T0 = "2025-05-04T12:00:00-05:00"          # Sunday; Tue slot prepares from Sun 09:15
TUE = "job-2025-05-06-tue-0915"
WED = "job-2025-05-07-wed-0915"


@pytest.fixture
def env(tmp_path, monkeypatch):
    deny = tmp_path / "deny.txt"
    deny.write_text("")
    monkeypatch.setenv("LCE_DENYLIST_PATH", str(deny))
    shutil.copytree(DEMO, tmp_path / "d")
    clk = FixedClock(T0)
    with use_clock(clk):
        yield DataStore.open(str(tmp_path / "d")), clk


def events(store, name=None):
    out = [json.loads(line) for p in sorted((store.root / "runs").glob("*.jsonl"))
           for line in p.read_text().splitlines() if line.strip()]
    return [e for e in out if name is None or e["event"] == name]


def humanized_post_for(store, job_id=TUE, text=GOOD_POST, image=True):
    pid = selected_post(store)
    save_draft(store, pid, text)
    save_humanized(store, pid, text)
    if image:
        images.decide(store, pid, kind="none", rationale="text-only post")
    scheduler.link_post(store, job_id, pid)
    return pid


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


# ── creation, idempotency, schedule windows ──────────────────────────────
def test_first_pass_creates_jobs_and_blocks_due_job_without_post(env):
    store, _ = env
    r = scheduler.run_once(store)
    assert r.status == "ok"
    states = {j["job_id"]: j["state"] for j in jobs.list_jobs(store)}
    assert states[TUE] == "BLOCKED"
    assert jobs.load_job(store, TUE)["blocked_reason"] == "awaiting_agent"
    assert states[WED] == "SCHEDULED"  # prepare window not open yet
    future = [j for j in states if j >= "job-2025-05-04"]
    past = [j for j in states if j < "job-2025-05-04"]
    assert len(future) == 6            # 14-day horizon, 3 slots/week
    assert len(past) == 3 and {states[j] for j in past} == {"SKIPPED"}  # 7-day lookback: missed


def test_repeated_invocation_creates_nothing_new(env):
    store, _ = env
    scheduler.run_once(store)
    before = {j["job_id"]: j for j in jobs.list_jobs(store)}
    scheduler.run_once(store)
    scheduler.run_once(store)
    after = {j["job_id"]: j for j in jobs.list_jobs(store)}
    assert before.keys() == after.keys()
    assert len(events(store, "job.created")) == len(before)
    assert after[TUE]["created_by"] == before[TUE]["created_by"]


def test_future_job_becomes_ready_when_window_opens(env):
    store, clk = env
    scheduler.run_once(store)
    clk.advance(days=1)  # Monday 12:00 → Wed prepares from Mon 09:15
    scheduler.run_once(store)
    assert jobs.load_job(store, WED)["state"] == "BLOCKED"


def test_missed_slot_is_skipped_not_generated(env):
    store, clk = env
    scheduler.run_once(store)
    clk.advance(days=2, hours=2)  # Tue 14:00 local, after the slot
    scheduler.run_once(store)
    job = jobs.load_job(store, TUE)
    assert job["state"] == "SKIPPED" and job["history"][-1]["reason"] == "missed_slot"


def test_slots_already_past_on_first_run_are_recorded_as_missed(env):
    store, clk = env
    clk.advance(days=3)  # first run ever on Wed afternoon
    scheduler.run_once(store)
    assert jobs.load_job(store, TUE)["state"] == "SKIPPED"


# ── pipeline execution up to the approval boundary ────────────────────────
def test_humanized_post_runs_to_awaiting_approval_and_stops(env):
    store, _ = env
    scheduler.run_once(store)
    pid = humanized_post_for(store)
    scheduler.run_once(store)
    job = jobs.load_job(store, TUE)
    assert job["state"] == "SUCCEEDED" and job["outcome"] == "awaiting human approval"
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"
    assert [s["name"] for s in job["steps"] if s["status"] == "done"] == [
        "qa", "duplicate_check", "approval_artifact"]
    for _ in range(3):
        scheduler.run_once(store)
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"  # never approved by automation
    assert "approval" not in {e.get("event") for e in events(store)}


def test_automation_code_cannot_approve():
    src = (Path(__file__).resolve().parents[1] / "src" / "lce" / "scheduler.py").read_text()
    assert "approve(" not in src and "mark_ready" not in src and "import approve" not in src
    assert "lce.publish" not in src and "Publisher" not in src and ".publish(" not in src


def test_approved_post_is_recognized(env):
    from lce.approval import approve

    store, _ = env
    scheduler.run_once(store)
    pid = humanized_post_for(store)
    scheduler.run_once(store)
    h = store.load_post(pid)["content_hash"]
    approve(store, pid, h[:12], confirm=lambda _: f"APPROVE {pid}", is_tty=lambda: True)
    scheduler.run_once(store)
    job = jobs.load_job(store, TUE)
    assert job["state"] == "SUCCEEDED" and store.load_post(pid)["state"] == "APPROVED"


def test_qa_failure_blocks_for_revision_then_fails(env, monkeypatch):
    store, _ = env
    (store.root / "config" / "automation.yaml").write_text("max_revisions: 1\n")
    scheduler.run_once(store)
    bad = "Comment YES below if you agree! " * 3 + "\n"
    pid = humanized_post_for(store, text=bad)
    scheduler.run_once(store)
    job = jobs.load_job(store, TUE)
    assert job["state"] == "BLOCKED" and job["blocked_reason"] == "awaiting_revision"
    save_humanized(store, pid, bad + "still bait\n")
    scheduler.run_once(store)
    job = jobs.load_job(store, TUE)
    assert job["state"] == "FAILED" and job["last_error"]["kind"] == "qa_failed"
    assert job["last_error"]["retryable"] is False
    scheduler.run_once(store)
    assert jobs.load_job(store, TUE)["state"] == "FAILED"  # no automatic retry


def test_rejected_post_skips_job(env):
    from lce.approval import reject

    store, _ = env
    scheduler.run_once(store)
    pid = humanized_post_for(store)
    scheduler.run_once(store)
    reject(store, pid, "not now")
    job = jobs.load_job(store, TUE)
    assert job["state"] == "SUCCEEDED"  # was done; a rejection does not reopen it


def test_reopened_post_is_followed(env):
    from lce.posts import reopen

    store, _ = env
    scheduler.run_once(store)
    pid = humanized_post_for(store)
    scheduler.run_once(store)
    reopen(store, pid, "edit")
    scheduler.run_once(store)
    assert jobs.load_job(store, TUE)["state"] == "SUCCEEDED"
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"


# ── retries ───────────────────────────────────────────────────────────────
def test_retryable_failure_retries_after_delay_up_to_max(env, monkeypatch):
    store, clk = env
    scheduler.run_once(store)
    humanized_post_for(store)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError("disk hiccup")

    monkeypatch.setattr("lce.qa.run_qa", boom)
    scheduler.run_once(store)
    job = jobs.load_job(store, TUE)
    assert job["state"] == "FAILED" and job["last_error"]["retryable"] and job["attempts"] == 1
    scheduler.run_once(store)
    assert len(calls) == 1  # delay not over
    clk.advance(minutes=16)
    scheduler.run_once(store)
    assert jobs.load_job(store, TUE)["attempts"] == 2
    clk.advance(minutes=16)
    scheduler.run_once(store)
    job = jobs.load_job(store, TUE)
    assert job["attempts"] == 3 and job["last_error"]["retryable"] is False
    clk.advance(minutes=16)
    scheduler.run_once(store)
    assert len(calls) == 3  # max_attempts reached


def test_non_retryable_failure_needs_manual_retry(env, monkeypatch):
    store, _ = env
    scheduler.run_once(store)
    humanized_post_for(store)
    monkeypatch.setattr("lce.qa.run_qa", lambda *a, **k: (_ for _ in ()).throw(StoreError("bad")))
    scheduler.run_once(store)
    job = jobs.load_job(store, TUE)
    assert job["state"] == "FAILED" and job["last_error"]["retryable"] is False
    monkeypatch.undo()
    scheduler.retry(store, TUE)
    scheduler.run_once(store)
    assert jobs.load_job(store, TUE)["state"] == "SUCCEEDED"


# ── lock, crash, reconciliation ──────────────────────────────────────────
def test_concurrent_invocation_is_rejected(env):
    store, _ = env
    assert jobs.acquire_lock(store, "other-run", 10)[0]
    r = scheduler.run_once(store)
    assert r.status == "locked" and jobs.list_jobs(store) == []
    assert events(store, "scheduler.locked")


def test_crash_mid_step_is_reconciled_and_resumed(env):
    store, clk = env
    scheduler.run_once(store)
    humanized_post_for(store)
    job = jobs.load_job(store, TUE)
    jobs.transition(store, job, jobs.J.READY, "agent done", "t")
    jobs.acquire_lease(job, "scheduler", "crashed-run", 30)
    jobs.transition(store, job, jobs.J.RUNNING, "execution started", "crashed-run")
    jobs.record_step(store, job, "qa", "started", "crashed-run")  # process dies here
    scheduler.run_once(store)
    assert jobs.load_job(store, TUE)["state"] == "RUNNING"  # lease still valid
    clk.advance(minutes=31)
    scheduler.run_once(store)
    job = jobs.load_job(store, TUE)
    assert job["state"] == "READY" and "interrupted step qa" in job["history"][-1]["reason"]
    scheduler.run_once(store)
    assert jobs.load_job(store, TUE)["state"] == "SUCCEEDED"


def test_ambiguous_state_needs_reconcile_and_a_human_decision(env):
    store, clk = env
    scheduler.run_once(store)
    pid = humanized_post_for(store)
    job = jobs.load_job(store, TUE)
    jobs.transition(store, job, jobs.J.READY, "x", "t")
    jobs.acquire_lease(job, "scheduler", "crashed", 30)
    jobs.transition(store, job, jobs.J.RUNNING, "x", "crashed")
    (store.post_dir(pid) / "post.md").write_text("changed outside the pipeline\n")
    clk.advance(minutes=31)
    scheduler.run_once(store)
    assert jobs.load_job(store, TUE)["state"] == "NEEDS_RECONCILE"
    with pytest.raises(StoreError, match="ambiguous"):
        scheduler.reconcile(store, TUE)
    with pytest.raises(StoreError, match="inconsistent"):
        scheduler.reconcile(store, TUE, "retry")
    assert scheduler.reconcile(store, TUE, "skip")["state"] == "SKIPPED"


# ── agent handoff ────────────────────────────────────────────────────────
def test_claim_and_release_for_agent_work(env):
    store, _ = env
    scheduler.run_once(store)
    assert [j["job_id"] for j in scheduler.pending_agent_tasks(store)] == [TUE]
    job = scheduler.claim(store, TUE, "claude-code")
    assert job["state"] == "RUNNING" and job["lease"]["holder"] == "agent:claude-code"
    scheduler.run_once(store)
    assert jobs.load_job(store, TUE)["state"] == "RUNNING"  # scheduler leaves it alone
    humanized_post_for(store)
    scheduler.release(store, TUE, "draft saved")
    scheduler.run_once(store)
    assert jobs.load_job(store, TUE)["state"] == "SUCCEEDED"
    with pytest.raises(StoreError):
        scheduler.claim(store, TUE, "x")


def test_link_post_is_unique(env):
    store, _ = env
    scheduler.run_once(store)
    pid = humanized_post_for(store)
    with pytest.raises(StoreError):
        scheduler.link_post(store, "job-2025-05-08-thu-0915", pid)


# ── configuration, dry run ───────────────────────────────────────────────
def test_invalid_configuration_stops_safely(env):
    store, _ = env
    s = store.settings()
    s.pop("cadence")
    store.write_doc(store.settings_path, "settings", s)
    r = scheduler.run_once(store)
    assert r.status == "config_invalid" and "cadence" in r.message
    assert jobs.list_jobs(store) == [] and events(store, "scheduler.config_invalid")


def test_dry_run_performs_zero_writes(env):
    store, _ = env
    scheduler.run_once(store)
    humanized_post_for(store)
    before = tree_hash(store.root)
    r = scheduler.run_once(store, dry_run=True,
                           now=parse_iso("2025-05-05T12:00:00-05:00"))
    assert tree_hash(store.root) == before
    assert r.dry_run and r.results == []
    kinds = {(a.job_id, a.action) for a in r.actions}
    assert (WED, "ready") in kinds and (TUE, "run") in kinds
    assert any("stops at human approval" in a.detail for a in r.actions)


def test_simulated_time_requires_dry_run(env):
    store, _ = env
    with pytest.raises(ValueError):
        scheduler.run_once(store, now=parse_iso("2025-05-05T12:00:00-05:00"))


def test_dst_slot_job(tmp_path, monkeypatch):
    shutil.copytree(DEMO, tmp_path / "d")
    with use_clock(FixedClock("2026-03-27T12:00:00+01:00")):
        store = DataStore.open(str(tmp_path / "d"))
        s = store.settings()
        s["timezone"] = "Europe/Berlin"
        s["cadence"] = {"posts_per_week": 1, "slots": [{"day": "sun", "time": "02:30"}]}
        store.write_doc(store.settings_path, "settings", s)
        scheduler.run_once(store)
        job = jobs.load_job(store, "job-2026-03-29-sun-0230")
    assert job["slot"]["dst_adjusted"] is True
    assert job["slot"]["local"] == "2026-03-29T03:30:00+02:00"
    assert job["slot"]["utc"] == "2026-03-29T01:30:00+00:00"
    assert job["prepare_from"] == "2026-03-27T01:30:00+00:00"  # 48 h in real time


def test_year_boundary_jobs(tmp_path):
    shutil.copytree(DEMO, tmp_path / "d")
    with use_clock(FixedClock("2026-12-28T12:00:00-06:00")):
        store = DataStore.open(str(tmp_path / "d"))
        scheduler.run_once(store)
    ids = [j["job_id"] for j in jobs.list_jobs(store)]
    assert "job-2026-12-29-tue-0915" in ids and "job-2027-01-05-tue-0915" in ids


def test_events_explain_every_job(env):
    store, _ = env
    scheduler.run_once(store)
    inv = events(store, "scheduler.start")[0]["invocation_id"]
    created = [e for e in events(store, "job.created") if e["job_id"] == TUE][0]
    assert created["invocation_id"] == inv and created["slot_id"] == "2025-05-06-tue-0915"
    trans = [e for e in events(store, "job.transition") if e["job_id"] == TUE]
    assert [(t["from"], t["to"]) for t in trans] == [
        ("SCHEDULED", "READY"), ("READY", "RUNNING"), ("RUNNING", "BLOCKED")]
    assert events(store, "scheduler.finish")
    assert not jobs.lock_path(store).exists()


def test_missing_image_decision_blocks_before_approval(env):
    store, _ = env
    scheduler.run_once(store)
    pid = humanized_post_for(store, image=False)
    scheduler.run_once(store)
    job = jobs.load_job(store, TUE)
    assert job["state"] == "BLOCKED" and job["blocked_reason"] == "awaiting_agent"
    assert store.load_post(pid)["state"] == "DUPLICATE_CHECKED"
    images.decide(store, pid, kind="none", rationale="text-only post")
    scheduler.run_once(store)
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"
