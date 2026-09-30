"""Local, read-only HTTP server for the Web Control Center.

- Binds to 127.0.0.1 only and rejects foreign Host headers (DNS rebinding).
- GET/HEAD only: the dashboard cannot change data, approve or publish.
- The snapshot is rebuilt from disk on every /api/snapshot request.
"""

from __future__ import annotations

import json
import mimetypes
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from lce.dashboard.snapshot import build_snapshot, to_json
from lce.store import DataStore

STATIC_FILES = ("index.html", "app.js", "lib.js", "styles.css")
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
                                "form-action 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def snapshot_for(store: DataStore, mode: str) -> dict:
    """Real mode uses the system clock; demo mode uses the fixed demo clock."""
    if mode == "demo":
        from lce.clock import FixedClock, use_clock

        with use_clock(FixedClock(DEMO_NOW)):
            return build_snapshot(store, mode="demo", data_label="fictional demo data")
    return build_snapshot(store, mode=mode)


def static_file(name: str) -> bytes:
    return resources.files("lce.dashboard").joinpath("static", name).read_bytes()


def config_js(mode: str, snapshot_url: str) -> bytes:
    cfg = {"mode": mode, "snapshotUrl": snapshot_url}
    return f"window.LCE_CONFIG = {json.dumps(cfg)};\n".encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "lce-dashboard"
    sys_version = ""

    def __init__(self, *args, store: DataStore, mode: str, port: int, **kwargs):
        self.store, self.mode, self.port = store, mode, port
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):  # keep the terminal quiet; no request logging
        return

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._send(HTTPStatus.FORBIDDEN, b"forbidden host", "text/plain")
        path = self.path.split("?", 1)[0]
        if path == "/api/snapshot":
            try:
                body = to_json(snapshot_for(self.store, self.mode)).encode()
            except Exception as exc:  # report, never fall back to other data
                err = {"error": type(exc).__name__, "message": str(exc)[:300]}
                return self._send(HTTPStatus.INTERNAL_SERVER_ERROR, json.dumps(err).encode(),
                                  "application/json")
            return self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")
        if path == "/config.js":
            return self._send(HTTPStatus.OK, config_js(self.mode, "api/snapshot"),
                              "text/javascript; charset=utf-8")
        name = "index.html" if path in {"/", "/index.html"} else path.lstrip("/")
        if name not in STATIC_FILES:
            return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or name.endswith(".js"):
            ctype = ("text/javascript" if name.endswith(".js") else ctype) + "; charset=utf-8"
        return self._send(HTTPStatus.OK, static_file(name), ctype)

    def _method_not_allowed(self):
        self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"read-only dashboard", "text/plain")

    do_POST = do_PUT = do_PATCH = do_DELETE = _method_not_allowed  # noqa: N815


def make_server(store: DataStore, *, mode: str, port: int = 8765) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, store=store, mode=mode,
                                                              port=port))
    # Port 0 picks a free port; make the Host check use the real one.
    server.RequestHandlerClass = partial(Handler, store=store, mode=mode,
                                         port=server.server_address[1])
    return server


# Fixed clock for the fictional demo, so its schedule and jobs line up.
DEMO_NOW = "2025-05-05T12:00:00-05:00"


def demo_store(engine_root: Path) -> DataStore:
    root = engine_root / "examples" / "demo-dashboard"
    if not (root / ".lce-data-root").exists():
        raise FileNotFoundError("demo data not found (examples/demo-dashboard)")
    # Read-only use of fictional fixtures inside the engine repo; never written.
    return DataStore(root)
