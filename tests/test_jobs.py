import json
import shutil

import pytest
from conftest import DEMO

from lce import jobs
from lce.clock import FixedClock, use_clock
from lce.jobs import InvalidJobTransition, J
from lce.store import DataStore, StoreError

SLOT = {"slot_id": "2025-05-06-tue-0915", "day": "tue", "time": "09:15",
        "local": "2025-05-06T09:15:00-05:00", "utc": "2025-05-06T14:15:00+00:00",
        "timezone": "America/Chicago", "dst_adjusted": False}


@pytest.fixture
def store(tmp_path):
    shutil.copytree(DEMO, tmp_path / "d")
    with use_clock(FixedClock("2025-05-04T17:00:00+00:00")):
        yield DataStore.open(str(tmp_path / "d"))


def make(store):
    job = jobs.new_job(SLOT, "2025-05-04T14:15:00+00:00", "inv-1", 3)
    assert jobs.create_job(store, job)
    return jobs.load_job(store, job["job_id"])


def test_identity_is_deterministic():
    assert jobs.job_id_for("2025-05-06-tue-0915") == "job-2025-05-06-tue-0915"
    a = jobs.new_job(SLOT, "2025-05-04T14:15:00+00:00", "x", 3)
    b = jobs.new_job(SLOT, "2025-05-04T14:15:00+00:00", "y", 3)
    assert a["job_id"] == b["job_id"]


def test_create_is_exclusive(store):
    make(store)
    again = jobs.new_job(SLOT, "2025-05-04T14:15:00+00:00", "inv-2", 3)
    assert jobs.create_job(store, again) is False
    assert jobs.load_job(store, again["job_id"])["created_by"] == "inv-1"
    assert len(jobs.list_jobs(store)) == 1


def test_invalid_ids_and_docs(store):
    with pytest.raises(StoreError):
        jobs.job_path(store, "../evil")
    bad = jobs.new_job(SLOT, "2025-05-04T14:15:00", "x", 3)  # naive timestamp
    with pytest.raises(StoreError):
        jobs.create_job(store, bad)


def test_transitions(store):
    job = make(store)
    jobs.transition(store, job, J.READY, "window", "i")
    jobs.transition(store, job, J.RUNNING, "start", "i")
    jobs.transition(store, job, J.SUCCEEDED, "done", "i")
    assert [h["to"] for h in jobs.load_job(store, job["job_id"])["history"]] == [
        "SCHEDULED", "READY", "RUNNING", "SUCCEEDED"]
    with pytest.raises(InvalidJobTransition):
        jobs.transition(store, job, J.RUNNING, "x", "i")  # SUCCEEDED -> RUNNING


def test_running_to_failed_and_needs_reconcile(store):
    job = make(store)
    for to in (J.READY, J.RUNNING, J.FAILED):
        jobs.transition(store, job, to, "t", "i")
    job2 = jobs.new_job({**SLOT, "slot_id": "2025-05-08-thu-0915"}, "2025-05-06T14:15:00+00:00",
                        "i", 3)
    jobs.create_job(store, job2)
    for to in (J.READY, J.RUNNING, J.NEEDS_RECONCILE):
        jobs.transition(store, job2, to, "t", "i")
    assert job2["state"] == "NEEDS_RECONCILE"


def test_blocked_needs_reason_and_skipped_is_terminal(store):
    job = make(store)
    jobs.transition(store, job, J.READY, "w", "i")
    jobs.transition(store, job, J.RUNNING, "r", "i")
    with pytest.raises(InvalidJobTransition):
        jobs.transition(store, job, J.BLOCKED, "b", "i")
    jobs.transition(store, job, J.BLOCKED, "b", "i", blocked_reason="awaiting_agent")
    jobs.transition(store, job, J.SKIPPED, "s", "i")
    assert jobs.JOB_TRANSITIONS[J.SKIPPED] == frozenset()


def test_transitions_are_logged(store):
    job = make(store)
    jobs.transition(store, job, J.READY, "window", "inv-9")
    events = [json.loads(line) for p in (store.root / "runs").glob("*.jsonl")
              for line in p.read_text().splitlines()]
    ev = [e for e in events if e["event"] == "job.transition"][-1]
    assert ev["from"] == "SCHEDULED" and ev["to"] == "READY" and ev["invocation_id"] == "inv-9"


def test_lock_is_exclusive_and_expires(store):
    ok, _ = jobs.acquire_lock(store, "a", 10)
    assert ok
    ok2, holder = jobs.acquire_lock(store, "b", 10)
    assert not ok2 and holder["invocation_id"] == "a"
    jobs.release_lock(store, "b")  # not the holder: ignored
    assert jobs.lock_path(store).exists()
    jobs.release_lock(store, "a")
    assert not jobs.lock_path(store).exists()


def test_stale_lock_takeover(store, tmp_path):
    with use_clock(FixedClock("2025-05-04T17:00:00+00:00")):
        assert jobs.acquire_lock(store, "old", 10)[0]
    with use_clock(FixedClock("2025-05-04T17:11:00+00:00")):
        ok, _ = jobs.acquire_lock(store, "new", 10)
    assert ok
    assert json.loads(jobs.lock_path(store).read_text())["invocation_id"] == "new"


def test_lease_expiry(store):
    job = make(store)
    jobs.acquire_lease(job, "scheduler", "i", 30)
    assert not jobs.lease_expired(job)
    with use_clock(FixedClock("2025-05-04T17:31:00+00:00")):
        assert jobs.lease_expired(job)


def test_automation_config_defaults_and_validation(store):
    assert jobs.automation_config(store)["lead_hours"] == 48
    (store.root / "config" / "automation.yaml").write_text("lead_hours: 0\n")
    with pytest.raises(StoreError):
        jobs.automation_config(store)
    (store.root / "config" / "automation.yaml").write_text("lead_hours: 24\n")
    assert jobs.automation_config(store)["lead_hours"] == 24
