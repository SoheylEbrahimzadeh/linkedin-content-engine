import pytest
from fakes import FAKE_TOKEN, FakeTransport, created
from test_publishing import setup_ready, tree

from lce import publishing
from lce.cli import main
from lce.publish.credentials import KeychainTokenStore, MemoryTokenStore
from lce.publish.linkedin import HttpResponse


@pytest.fixture
def ready(store, monkeypatch):
    pid = setup_ready(store)
    t = FakeTransport(created(), HttpResponse(200, {}, b'{"sub": "S1", "name": "N"}'))
    real = publishing.make_publisher
    monkeypatch.setattr(publishing, "make_publisher",
                        lambda s, **k: real(s, transport=t, tokens=MemoryTokenStore(FAKE_TOKEN)))
    return store, pid, t


def run(store, *argv):
    return main(["--data-dir", str(store.root), *argv])


def test_cli_publish_refuses_without_terminal(ready, capsys):
    store, pid, t = ready
    assert run(store, "publish", pid) == 1
    assert "interactive terminal" in capsys.readouterr().out
    assert t.calls == [] and store.load_post(pid)["state"] == "READY_TO_PUBLISH"


def test_cli_dry_run_prints_request_without_secret_and_writes_nothing(ready, capsys):
    store, pid, t = ready
    before = tree(store.root)
    assert run(store, "publish", pid, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "https://api.linkedin.com/rest/posts" in out
    assert "Bearer <from Keychain>" in out and FAKE_TOKEN not in out
    assert tree(store.root) == before and t.calls == []


def test_cli_reconcile_refuses_without_terminal_and_validates_usage(ready, capsys):
    store, pid, _ = ready
    assert run(store, "publish", "reconcile", pid, "--not-published") == 1
    assert run(store, "publish", "reconcile") == 1
    assert run(store, "publish", pid, "--not-published") == 1
    capsys.readouterr()


def test_cli_linkedin_status_never_prints_the_token(ready, capsys, monkeypatch):
    store, _, _ = ready
    monkeypatch.setattr(KeychainTokenStore, "exists", lambda self: True)
    monkeypatch.setattr(KeychainTokenStore, "get", lambda self: (_ for _ in ()).throw(
        AssertionError("status must not read the token")))
    assert run(store, "linkedin", "status") == 0
    out = capsys.readouterr().out
    assert "present" in out and "value never shown" in out and FAKE_TOKEN not in out
    assert "expiry: unknown" in out


def test_cli_whoami_uses_injected_transport(ready, capsys):
    store, _, t = ready
    t.responses.pop(0)
    assert run(store, "linkedin", "whoami") == 0
    assert "urn:li:person:S1" in capsys.readouterr().out
