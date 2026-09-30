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
    """Scripted Worker: health, snapshot and pipeline answers; records every request."""

    def __init__(self, health=(200, {"ok": True}), snapshot=(200, READY),
                 pipeline=(200, {"meta": {"mirror": {"received_at": "2026-10-01T00:00:00+00:00"}}})):
        self.health, self.snapshot, self.pipeline, self.calls = health, snapshot, pipeline, []

    def request(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers)))
        if isinstance(self.health, Exception):
            raise self.health
        path = url.split("/api", 1)[1]
        if path == "/pipeline":
            return CloudResponse(*self.pipeline)
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


def test_service_token_headers_are_sent_and_never_printed(configured, monkeypatch, capsys):
    from lce.cli import main

    w = Worker()
    monkeypatch.setattr(cloud, "UrllibCloudTransport", lambda: w)
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_ID", "fake-client-id.access")
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_SECRET", "fake-client-secret")
    assert main(["--data-dir", str(configured.root), "cloud", "doctor"]) == 0
    snap_headers = [h for m, u, h in w.calls if u.endswith("/api/snapshot")][0]
    assert snap_headers["CF-Access-Client-Id"] == "fake-client-id.access"
    assert snap_headers["CF-Access-Client-Secret"] == "fake-client-secret"
    assert "cf-access-token" not in snap_headers
    assert "fake-client-secret" not in capsys.readouterr().out


def test_half_configured_service_token_is_an_action(configured, monkeypatch):
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_ID", "only-the-id")
    monkeypatch.delenv("LCE_CF_ACCESS_CLIENT_SECRET", raising=False)
    checks = cloud.doctor(configured, transport=Worker(), today=date(2026, 10, 1))
    assert checks[-1]["check"] == "access login" and checks[-1]["status"] == "action"
    assert "both" in checks[-1]["detail"]


def test_cli_exit_code_distinguishes_broken_from_open_gates(configured, monkeypatch):
    from lce.cli import main

    monkeypatch.setenv("LCE_CF_ACCESS_TOKEN", "fake.access.jwt")
    monkeypatch.setattr(cloud, "UrllibCloudTransport",
                        lambda: Worker(snapshot=(401, {"error": "wrong audience"})))
    assert main(["--data-dir", str(configured.root), "cloud", "doctor"]) == 2
    monkeypatch.setattr(cloud, "UrllibCloudTransport", lambda: Worker())
    assert main(["--data-dir", str(configured.root), "cloud", "doctor"]) == 0


def test_missing_migration_0003_is_detected(configured):
    missing = (503, {"error": "database schema missing: apply cloud/migrations to D1"})
    by, _ = run(configured, Worker(pipeline=missing))
    assert by["database"]["status"] == "action" and "0003" in by["database"]["detail"]


def test_mirror_not_synced_yet_is_an_owner_step(configured):
    by, _ = run(configured, Worker(pipeline=(404, {"error": "no pipeline snapshot yet"})))
    assert by["database"]["status"] == "ok" and by["pipeline mirror"]["status"] == "action"


def test_github_actions_annotation_lists_every_result(configured, monkeypatch, capsys):
    from lce.cli import main

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("LCE_CF_ACCESS_TOKEN", "fake.access.jwt")
    monkeypatch.setattr(cloud, "UrllibCloudTransport", lambda: Worker())
    main(["--data-dir", str(configured.root), "cloud", "doctor"])
    notice = [line for line in capsys.readouterr().out.splitlines() if line.startswith("::notice")]
    assert len(notice) == 1 and "ok: database" in notice[0] and "fake.access.jwt" not in notice[0]


class EdgeWorker(Worker):
    """Cloudflare Access in front of the whole hostname: 403 without credentials."""

    def request(self, method, url, headers, body):
        authed = "CF-Access-Client-Id" in headers or "cf-access-token" in headers
        if not authed:
            self.calls.append((method, url, dict(headers)))
            return CloudResponse(403, {})
        return super().request(method, url, headers, body)


def test_access_at_the_edge_is_not_mistaken_for_a_broken_worker(configured):
    w = EdgeWorker()
    by, checks = run(configured, w)
    assert by["worker"]["status"] == "ok" and "through Cloudflare Access" in by["worker"]["detail"]
    assert all(c["status"] == "ok" for c in checks), checks


def test_edge_without_login_reports_the_login_step(configured):
    def no_login():
        raise CloudError("no Cloudflare Access token")

    by, checks = run(configured, EdgeWorker(), token=no_login)
    assert by["worker"]["status"] == "ok" and checks[-1]["check"] == "access login"


def test_service_token_rejected_at_the_edge_names_the_policy(configured):
    class Rejecting(EdgeWorker):
        def request(self, method, url, headers, body):
            self.calls.append((method, url, dict(headers)))
            return CloudResponse(403, {})

    by, _ = run(configured, Rejecting())
    assert by["worker"]["status"] == "fail" and "Service Auth policy" in by["worker"]["action"]
