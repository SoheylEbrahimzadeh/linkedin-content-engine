"""LCE-048: Refresh stages are reported when they actually happen; never invented."""

import json

import pytest
from test_real_media import SC, requested
from test_repackage import PKG

from lce import cloud, repackage
from lce.cloud import CloudClient, CloudResponse


class Recorder:
    def __init__(self):
        self.calls = []

    def request(self, method, url, headers, body):
        self.calls.append((method, url.split("/api", 1)[1], json.loads(body) if body else None))
        return CloudResponse(200, {"ok": True})


def test_package_reports_each_stage_after_it_happened(store, monkeypatch):
    rec = Recorder()
    client = CloudClient("https://lce.example", lambda: "jwt", rec)
    real = cloud.report_progress
    monkeypatch.setattr(cloud, "report_progress", lambda *a, **k: real(*a, **{**k, "client": client}))
    pid = requested(store)
    store.load_post(pid)
    repackage.package(
        store,
        pid,
        {
            **PKG,
            "media": {
                "source_check": SC,
                "text_only": {
                    "reason": "no_suitable_licensed_image",
                    "rationale": "no licensed source visual",
                },
            },
        },
    )
    stages = [c[2]["stage"] for c in rec.calls]
    assert stages == ["humanization", "qa", "duplicate_check", "approval_prepared"]
    assert all(c[0] == "PUT" and c[1] == f"/refresh-progress/{pid}" for c in rec.calls)


def test_a_failing_package_never_reports_later_stages(store, monkeypatch):
    rec = Recorder()
    client = CloudClient("https://lce.example", lambda: "jwt", rec)
    real = cloud.report_progress
    monkeypatch.setattr(cloud, "report_progress", lambda *a, **k: real(*a, **{**k, "client": client}))
    pid = requested(store)
    bad = {
        **PKG,
        "text": PKG["text"].replace("about 10", "about 25"),
        "media": {
            "source_check": SC,
            "text_only": {"reason": "no_suitable_licensed_image", "rationale": "x"},
        },
    }
    with pytest.raises(Exception, match="QA failed"):
        repackage.package(store, pid, bad)
    assert [c[2]["stage"] for c in rec.calls] == ["humanization"]


def test_without_credentials_nothing_is_sent_and_nothing_breaks(store, monkeypatch):
    monkeypatch.delenv("LCE_CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("LCE_CF_ACCESS_CLIENT_SECRET", raising=False)
    assert cloud.report_progress(store, "x-post", "qa") is False
    with pytest.raises(cloud.CloudError):
        cloud.report_progress(store, "x-post", "made_up")
