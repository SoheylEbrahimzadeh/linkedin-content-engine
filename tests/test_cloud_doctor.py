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


class Pages:
    """Dashboard pages behind Access: 200 with the expected markers when credentials are sent."""

    PAGES = {"/": b"<title>LCE Cloud Control Center</title>",
             "/pipeline/": b"<title>LCE Control Center</title>",
             "/pipeline/config.js": b'window.LCE_CONFIG = {"mode": "real", "snapshotUrl": "/api/pipeline"};'}

    def __init__(self, code=200):
        self.code, self.calls = code, []

    def status(self, method, url, body=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        return self.code, self.PAGES.get(url[len(BASE):], b"")


@pytest.fixture(autouse=True)
def fake_pages(monkeypatch):
    monkeypatch.setattr(cloud, "StatusTransport", lambda: Pages())


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


def test_dashboard_pages_load_through_access(configured):
    pages = Pages()
    checks = cloud.doctor(configured, transport=Worker(), token=lambda: "fake.access.jwt",
                          today=date(2026, 10, 1), probe=pages)
    by = {c["check"]: c for c in checks}
    assert by["dashboard"]["status"] == "ok"
    assert {u for u, _ in pages.calls} == {f"{BASE}/", f"{BASE}/pipeline/", f"{BASE}/pipeline/config.js"}
    assert all(h.get("cf-access-token") == "fake.access.jwt" for _, h in pages.calls)


def test_dashboard_refused_behind_access_is_a_failure(configured):
    checks = cloud.doctor(configured, transport=Worker(), token=lambda: "fake.access.jwt",
                          today=date(2026, 10, 1), probe=Pages(code=403))
    assert {c["check"]: c for c in checks}["dashboard"]["status"] == "fail"


GOOD_ID, GOOD_SECRET = "0" * 32 + ".access", "f" * 64


@pytest.mark.parametrize("cid,secret,expected", [
    (GOOD_ID, GOOD_SECRET, "ok"),
    ('"' + GOOD_ID + '"', GOOD_SECRET, "label or quotes were removed"),
    ("CF-Access-Client-Id: " + GOOD_ID, GOOD_SECRET, "label or quotes were removed"),
    ("Client ID: abc", GOOD_SECRET, "client id is not"),
    (GOOD_ID, GOOD_SECRET[:40], "client secret is neither"),
    (GOOD_ID + "\n", GOOD_SECRET, "ok"),
])
def test_service_token_shape_is_checked_without_revealing_it(monkeypatch, cid, secret, expected):
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_ID", cid)
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_SECRET", secret)
    result = cloud.service_token_format()
    assert expected in result
    assert GOOD_SECRET[:40] not in result and "0" * 32 not in result


def test_rejected_token_with_bad_shape_names_the_secrets(configured, monkeypatch):
    class Rejecting(EdgeWorker):
        def request(self, method, url, headers, body):
            return CloudResponse(302, {"_raw": "Cloudflare Access login redirect (credential not accepted)"})

    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_ID", "not-a-client-id")
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_SECRET", GOOD_SECRET)
    checks = cloud.doctor(configured, transport=Rejecting(), today=date(2026, 10, 1))
    by = {c["check"]: c for c in checks}
    assert by["service token format"]["status"] == "fail"
    assert GOOD_SECRET not in str(checks)


def test_rejected_token_with_good_shape_points_at_the_policy(configured, monkeypatch):
    class Rejecting(EdgeWorker):
        def request(self, method, url, headers, body):
            return CloudResponse(302, {"_raw": "Cloudflare Access login redirect (credential not accepted)"})

    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_ID", GOOD_ID)
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_SECRET", GOOD_SECRET)
    checks = cloud.doctor(configured, transport=Rejecting(), today=date(2026, 10, 1))
    by = {c["check"]: c for c in checks}
    assert by["worker"]["status"] == "fail" and "Service Auth policy" in by["worker"]["action"]
    assert "login redirect" in by["worker"]["detail"] and GOOD_SECRET not in str(checks)


def test_pasted_labels_are_removed_before_sending(monkeypatch):
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_ID", "CF-Access-Client-Id: " + GOOD_ID)
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_SECRET", "Client Secret: " + GOOD_SECRET)
    assert cloud.default_access("https://x.example") == {
        "CF-Access-Client-Id": GOOD_ID, "CF-Access-Client-Secret": GOOD_SECRET}


def test_unrecognised_values_are_sent_unchanged(monkeypatch):
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_ID", "something-else")
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_SECRET", "label: not-hex")
    assert cloud.default_access("https://x.example") == {
        "CF-Access-Client-Id": "something-else", "CF-Access-Client-Secret": "label: not-hex"}


@pytest.mark.parametrize("raw", [
    "CF-Access-Client-Secret=" + GOOD_SECRET,
    "Client Secret\n" + GOOD_SECRET + "\n(copy once)",
    "secret: " + GOOD_SECRET + " ",
])
def test_one_secret_run_inside_any_label_is_used(monkeypatch, raw):
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_ID", GOOD_ID)
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_SECRET", raw)
    assert cloud.default_access("https://x.example")["CF-Access-Client-Secret"] == GOOD_SECRET


def test_ambiguous_or_foreign_values_are_described_not_revealed(monkeypatch):
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_ID", GOOD_ID)
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_SECRET", GOOD_SECRET + " " + "e" * 64)
    shape = cloud.service_token_format()
    assert "client secret is neither" in shape and "129 chars" in shape
    assert GOOD_SECRET not in shape and "e" * 20 not in shape


NEW_SECRET = "cfast_" + "Ab3" * 16   # 2026-08-26 format: cfast_ + 40 alnum + 8 checksum


@pytest.mark.parametrize("raw", [NEW_SECRET, "CF-Access-Client-Secret: " + NEW_SECRET,
                                 "Client Secret: " + NEW_SECRET + "\n"])
def test_new_cfast_secret_format_is_accepted(monkeypatch, raw):
    assert len(NEW_SECRET) == 54
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_ID", "CF-Access-Client-Id: " + GOOD_ID)
    monkeypatch.setenv("LCE_CF_ACCESS_CLIENT_SECRET", raw)
    assert cloud.default_access("https://x.example") == {
        "CF-Access-Client-Id": GOOD_ID, "CF-Access-Client-Secret": NEW_SECRET}
    assert cloud.service_token_format().startswith("ok")
    assert NEW_SECRET not in cloud.service_token_format()
