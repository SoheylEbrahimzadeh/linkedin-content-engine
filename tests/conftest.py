import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from lce.store import DataStore

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo-persona"
GOOD_POST = (
    "Most small service teams do not need a model to sort tickets.\n\n"
    "In our pilot, manual triage dropped from about 40 minutes a day to about 10, "
    "using twelve keyword rules.\n\n"
    "Start with the boring rules. They cover more than you expect, and they are easy to "
    "explain to the team.\n\n"
    "What was the first rule you automated?\n"
)


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    """Every test runs with outbound connections blocked (localhost is allowed for the
    dashboard server tests). A LinkedIn call can therefore never happen in tests."""
    import socket

    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"network access blocked in tests: {host}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "getaddrinfo", _local_only(socket.getaddrinfo))


def _local_only(real):
    def wrapper(host, *a, **k):
        if host not in ("127.0.0.1", "::1", "localhost", None):
            raise AssertionError(f"DNS lookup blocked in tests: {host}")
        return real(host, *a, **k)
    return wrapper


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture
def store(tmp_path: Path) -> DataStore:
    dest = tmp_path / "data"
    shutil.copytree(DEMO, dest)
    return DataStore.open(str(dest))


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def selected_post(store: DataStore, stories=("demo-ticket-routing",)) -> str:
    from lce.planning import select

    post = select(store, candidate_id="c-demo-rules-first", pillar="automation",
                  angle="rules before models", fmt="text", plan_date=date(2025, 5, 6),
                  stories=list(stories))
    return post["post_id"]


def awaiting_post(store: DataStore, text: str = GOOD_POST) -> str:
    from lce.approval import prepare
    from lce.dupcheck import run_dupcheck
    from lce.images import decide
    from lce.posts import save_draft, save_humanized
    from lce.qa import run_qa

    pid = selected_post(store)
    save_draft(store, pid, text)
    save_humanized(store, pid, text)
    assert run_qa(store, pid, denylist=[])["status"] == "passed"
    assert run_dupcheck(store, pid)["status"] == "passed"
    decide(store, pid, kind="none", rationale="text-only post")
    prepare(store, pid)
    return pid
