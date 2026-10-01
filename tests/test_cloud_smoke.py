"""`lce cloud smoke`: unauthenticated production check (reachable, fail-closed)."""

import pytest

from lce import cloud
from lce.cloud import CloudError

BASE = "https://lce.example.workers.dev"


class Probe:
    def __init__(self, health=(200, b'{"ok": true}'), protected=403, overrides=None, fail_times=0):
        self.health, self.protected, self.overrides = health, protected, overrides or {}
        self.fail_times, self.calls = fail_times, []

    def status(self, method, url, body=None):
        self.calls.append((method, url, body))
        path = url[len(BASE):]
        if path == "/api/health":
            if self.fail_times:
                self.fail_times -= 1
                raise CloudError("cloud API unreachable: URLError")
            return self.health
        return self.overrides.get((method, path), self.protected), b""


def by(checks):
    return {c["check"]: c for c in checks}


@pytest.mark.parametrize("protected", [302, 401, 403, 503])
def test_fail_closed_worker_passes(protected):
    checks = cloud.smoke(BASE, Probe(protected=protected))
    assert all(c["status"] == "ok" for c in checks), checks
    assert len(checks) == 1 + len(cloud.PROTECTED)


def test_any_unauthenticated_2xx_is_a_failure():
    checks = by(cloud.smoke(BASE, Probe(overrides={("GET", "/api/pipeline"): 200})))
    assert checks["GET /api/pipeline"]["status"] == "fail"
    assert checks["GET /api/snapshot"]["status"] == "ok"


def test_health_behind_access_at_the_edge_counts_as_reachable():
    checks = by(cloud.smoke(BASE, Probe(health=(302, b""))))
    assert checks["worker"]["status"] == "ok" and "edge" in checks["worker"]["detail"]


def test_unreachable_worker_stops_early_and_waits_when_asked():
    slept = []
    p = Probe(fail_times=99)
    checks = cloud.smoke(BASE, p, wait_seconds=30, sleep=slept.append)
    assert [c["check"] for c in checks] == ["worker"] and checks[0]["status"] == "fail"
    assert len(slept) == 2 and len(p.calls) == 3


def test_recovers_while_the_deploy_finishes():
    checks = cloud.smoke(BASE, Probe(fail_times=1), wait_seconds=30, sleep=lambda s: None)
    assert all(c["status"] == "ok" for c in checks)


def test_no_credentials_are_ever_sent():
    p = Probe()
    cloud.smoke(BASE, p)
    assert all(b is None or "token" not in str(b).lower() for _, _, b in p.calls)


def test_status_transport_never_follows_redirects():
    import urllib.request

    t = cloud.StatusTransport()
    handler = next(h for h in t.opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://login.example") is None
    with pytest.raises(CloudError):
        t.status("GET", "http://insecure.example/")


def test_cli_uses_api_base_and_exit_codes(monkeypatch, capsys):
    from lce.cli import main

    monkeypatch.setattr(cloud, "StatusTransport", lambda: Probe(overrides={("GET", "/"): 200}))
    assert main(["cloud", "smoke", "--api-base", BASE]) == 2
    assert "✗ GET /: HTTP 200 without credentials" in capsys.readouterr().out
    monkeypatch.setattr(cloud, "StatusTransport", lambda: Probe())
    assert main(["cloud", "smoke", "--api-base", BASE]) == 0


@pytest.mark.parametrize("body,location,expected", [
    (b"error code: 1010", "", "Browser Integrity Check"),
    (b"<html>error code: 1020</html>", "", "WAF"),
    (b"", "https://team.cloudflareaccess.com/cdn-cgi/access/login/x", "Access login redirect"),
    (b'{"error": "missing Cloudflare Access token"}', "", "Worker: missing Cloudflare Access token"),
    (b"<html><title>Forbidden</title></html>", "", "HTTP 403: Forbidden"),
])
def test_refusals_are_classified(body, location, expected):
    assert expected in cloud.classify_refusal(403, body, location)


def test_requests_carry_an_explicit_user_agent():
    import urllib.request

    seen = {}

    class Opener:
        def open(self, req, timeout):
            seen.update(req.header_items())
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    t = cloud.UrllibCloudTransport()
    t.opener = Opener()
    r = t.request("GET", "https://x.example/api/health", {"x-lce-client": "cli"}, None)
    assert seen["User-agent"].startswith("lce-cli/") and r.status == 403
    s = cloud.StatusTransport()
    s.opener = Opener()
    assert s.status("GET", "https://x.example/")[0] == 403
    assert seen["User-agent"].startswith("lce-cli/")


def test_the_json_transport_does_not_follow_redirects():
    import urllib.request

    t = cloud.UrllibCloudTransport()
    handler = next(h for h in t.opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://login.example") is None


def test_access_redirect_names_the_application():
    loc = ("https://team.cloudflareaccess.com/cdn-cgi/access/login/x.workers.dev?kid="
           + "ab" * 32 + "&redirect_url=%2Fapi%2Fhealth")
    out = cloud.classify_refusal(302, b"", loc)
    assert "team.cloudflareaccess.com" in out and f"AUD {'ab' * 32}" in out
    assert "redirect_url" not in out
    assert "AUD" not in cloud.classify_refusal(302, b"", loc.replace("ab" * 32, "not-hex"))
