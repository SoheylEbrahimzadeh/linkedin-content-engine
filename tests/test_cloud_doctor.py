"""`lce cloud doctor`: read-only production preflight, one result per owner gate."""

from datetime import date

import pytest

from lce import cloud
from lce.cloud import CloudError, CloudResponse

BASE = "https://lce.example.workers.dev"
READY = {"settings": {"timezone": "Europe/Berlin", "cadence": {"posts_per_week": 3},
                      "api_version": "202609", "person_urn": "urn:li:person:TestPerson1",
                      "provider": "linkedin_api", "token_present": True,
                      "token_expires_at": "2026-11-20T00:00:00+00:00", "auto_publish": False},
         "schedule_error": None}


class Worker:
    """Scripted Worker: health and snapshot answers; records every request."""

    def __init__(self, health=(200, {"ok": True}), snapshot=(200, READY)):
        self.health, self.snapshot, self.calls = health, snapshot, []

    def request(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers)))
        if isinstance(self.health, Exception):
            raise self.health
        path = url.split("/api", 1)[1]
        return CloudResponse(*(self.health if path == "/health" else self.snapshot))


@pytest.fixture
def configured(store):
    (store.root / "config" / "cloud.yaml").write_text(f"api_base: {BASE}\n")
    return store


def run(store, worker, token=lambda: "fake.access.jwt"):
    checks = cloud.doctor(store, transport=worker, token=token, today=date(2026, 10, 1))
    return {c["check"]: c for c in checks}, checks


def test_missing_config_is_an_owner_action(store):
    out = cloud.doctor(store, transport=Worker())
    assert [c["check"] for c in out] == ["config"] and out[0]["status"] == "action"


def test_unreachable_worker_stops_early(configured):
    by, checks = run(configured, Worker(health=CloudError("cloud API unreachable: URLError")))
    assert by["worker"]["status"] == "fail" and checks[-1]["check"] == "worker"


def test_missing_access_login_is_reported_before_any_authenticated_call(configured):
    w = Worker()

    def no_login():
        raise CloudError("no Cloudflare Access token")

    by, _ = run(configured, w, token=no_login)
    assert by["access login"]["status"] == "action"
    assert [c[1] for c in w.calls] == [f"{BASE}/api/health"]


@pytest.mark.parametrize("error", ["Cloudflare Access is not configured",
                                   "Cloudflare Access is misconfigured"])
def test_access_not_configured_names_the_secrets(configured, error):
    by, checks = run(configured, Worker(snapshot=(503, {"error": error})))
    assert by["access"]["status"] == "action" and "ACCESS_AUD" in by["access"]["action"]
    assert checks[-1]["check"] == "access"


def test_rejected_access_token(configured):
    by, _ = run(configured, Worker(snapshot=(401, {"error": "wrong audience"})))
    assert by["access"]["status"] == "fail"


def test_missing_schema_names_the_migration_command(configured):
    by, _ = run(configured, Worker(snapshot=(503, {"error": "database schema missing: apply "
                                                             "cloud/migrations to D1"})))
    assert by["access"]["status"] == "ok"
    assert by["database"]["status"] == "action"
    assert "migrations apply lce --remote" in by["database"]["action"]


def test_fully_ready_cloud_passes_and_sends_no_mutation(configured):
    w = Worker()
    by, checks = run(configured, w)
    assert all(c["status"] == "ok" for c in checks), checks
    assert "50 day(s) left" in by["linkedin token"]["detail"]
    assert {c[0] for c in w.calls} == {"GET"}
    assert all("fake.access.jwt" not in c["detail"] for c in checks)


def test_open_gates_after_deploy(configured):
    snap = {"settings": {"provider": "none", "token_present": False, "auto_publish": False},
            "schedule_error": None}
    by, _ = run(configured, Worker(snapshot=(200, snap)))
    assert by["settings"]["status"] == "action" and "lce cloud configure" in by["settings"]["action"]
    assert by["provider"]["status"] == "action"
    assert by["linkedin token"]["status"] == "action"
    assert "OFF" in by["kill switch"]["detail"]


def test_expired_or_expiring_token(configured):
    for exp, word in (("2026-09-20T00:00:00+00:00", "expired"), ("2026-10-05T00:00:00+00:00", "left")):
        snap = {**READY, "settings": {**READY["settings"], "token_expires_at": exp}}
        by, _ = run(configured, Worker(snapshot=(200, snap)))
        assert by["linkedin token"]["status"] == "action" and word in by["linkedin token"]["detail"]


def test_cli_exit_code(configured, monkeypatch, capsys):
    from lce.cli import main

    monkeypatch.setattr(cloud, "UrllibCloudTransport",
                        lambda: Worker(snapshot=(503, {"error": "Cloudflare Access is not configured"})))
    monkeypatch.setenv("LCE_CF_ACCESS_TOKEN", "fake.access.jwt")
    assert main(["--data-dir", str(configured.root), "cloud", "doctor"]) == 1
    out = capsys.readouterr().out
    assert "→ access" in out and "fake.access.jwt" not in out
