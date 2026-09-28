import http.client
import json
import shutil
import threading

import pytest
from conftest import ROOT

from lce.dashboard.server import make_server
from lce.store import DataStore


@pytest.fixture
def server(tmp_path):
    dest = tmp_path / "data"
    shutil.copytree(ROOT / "examples" / "demo-dashboard", dest)
    srv = make_server(DataStore.open(str(dest)), mode="real", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def req(port, method, path, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, headers={"Host": host or f"127.0.0.1:{port}"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp, body


def test_index_and_security_headers(server):
    resp, body = req(server, "GET", "/")
    assert resp.status == 200 and b"LCE Control Center" in body
    assert "script-src 'self'" in resp.getheader("Content-Security-Policy")
    assert resp.getheader("X-Content-Type-Options") == "nosniff"


def test_config_and_snapshot_are_real_mode(server):
    _, cfg = req(server, "GET", "/config.js")
    assert b'"mode": "real"' in cfg
    resp, body = req(server, "GET", "/api/snapshot")
    assert resp.status == 200
    assert json.loads(body)["meta"]["mode"] == "real"


def test_foreign_host_is_rejected(server):
    resp, _ = req(server, "GET", "/api/snapshot", host="evil.example:80")
    assert resp.status == 403


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_read_only(server, method):
    resp, _ = req(server, method, "/api/snapshot")
    assert resp.status == 405


@pytest.mark.parametrize("path", ["/../pyproject.toml", "/static/../../cli.py", "/.env",
                                  "/posts/x/post.md", "/api/other"])
def test_no_arbitrary_files(server, path):
    resp, _ = req(server, "GET", path)
    assert resp.status == 404


def test_server_binds_localhost_only(tmp_path):
    srv = make_server(DataStore.init(tmp_path / "d"), mode="real", port=0)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()
