import shutil

import pytest
from conftest import DEMO, GOOD_POST, ROOT
from test_scheduler import tree_hash

from lce.cli import build_parser, main
from lce.clock import FixedClock, use_clock
from lce.store import DataStore


@pytest.fixture
def data(tmp_path, monkeypatch):
    deny = tmp_path / "deny.txt"
    deny.write_text("")
    monkeypatch.setenv("LCE_DENYLIST_PATH", str(deny))
    shutil.copytree(DEMO, tmp_path / "d")
    with use_clock(FixedClock("2025-05-04T12:00:00-05:00")):
        yield str(tmp_path / "d")


def run(data, *argv):
    return main(["--data-dir", data, *argv])


def test_command_names_contain_no_publish_or_schedule_verbs():
    sub = next(a for a in build_parser()._actions if a.dest == "command")
    assert {"cadence", "automation", "jobs"} <= set(sub.choices)


def test_cadence_show(data, capsys):
    assert run(data, "cadence", "show", "--days", "7") == 0
    out = capsys.readouterr().out
    assert "America/Chicago" in out and "job-2025-05-06-tue-0915: no job yet" in out


def test_dry_run_writes_nothing_and_simulated_time_needs_dry_run(data, capsys):
    from pathlib import Path

    before = tree_hash(Path(data))
    assert run(data, "automation", "run-once", "--dry-run",
               "--now", "2025-05-05T12:00:00-05:00") == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert tree_hash(Path(data)) == before
    assert run(data, "automation", "run-once", "--now", "2025-05-05T12:00:00-05:00") == 1
    assert run(data, "automation", "run-once", "--dry-run", "--now", "2025-05-05T12:00") == 1
    assert tree_hash(Path(data)) == before


def test_run_once_and_job_commands(data, capsys):
    assert run(data, "automation", "run-once") == 0
    out = capsys.readouterr().out
    assert "Nothing is approved or published" in out
    assert run(data, "jobs", "list", "--state", "BLOCKED") == 0
    assert "job-2025-05-06-tue-0915" in capsys.readouterr().out
    assert run(data, "jobs", "agent-tasks") == 0
    assert run(data, "jobs", "claim", "job-2025-05-06-tue-0915") == 0
    assert run(data, "jobs", "release", "job-2025-05-06-tue-0915", "--note", "x") == 0
    assert run(data, "jobs", "show", "job-2025-05-06-tue-0915") == 0
    assert "agent_work" in capsys.readouterr().out
    assert run(data, "jobs", "skip", "job-2025-05-06-tue-0915", "--reason", "test") == 0
    assert run(data, "jobs", "retry", "job-2025-05-06-tue-0915") == 1  # not FAILED


def test_select_with_job_links_and_runs_to_approval(data, capsys, tmp_path):
    run(data, "automation", "run-once")
    assert run(data, "select", "pick", "c-demo-rules-first", "--pillar", "automation",
               "--angle", "rules first", "--job", "job-2025-05-06-tue-0915",
               "--story", "demo-ticket-routing") == 0
    store = DataStore.open(data)
    pid = store.post_ids()[0]
    assert pid.startswith("20250506-")
    f = tmp_path / "t.txt"
    f.write_text(GOOD_POST)
    assert run(data, "draft", "save", pid, "--file", str(f)) == 0
    assert run(data, "humanize", "save", pid, "--file", str(f)) == 0
    assert run(data, "automation", "run-once") == 0
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"
    assert run(data, "select", "pick", "c-demo-rules-first", "--pillar", "automation",
               "--angle", "x", "--job", "job-2025-05-06-tue-0915", "--date", "2025-05-07") == 1


def test_skill_for_agent_jobs_is_shipped_and_never_approves():
    text = (ROOT / "src" / "lce" / "claude_skills" / "lce-run-jobs" / "SKILL.md").read_text()
    assert "lce jobs claim" in text and "lce jobs release" in text
    assert "lce approve " not in text.replace("never run `lce approve`", "")
