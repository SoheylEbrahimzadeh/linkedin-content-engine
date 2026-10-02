import json

import pytest
from test_publishing import TTY, ok, setup_ready

from lce import cloud, publishing
from lce.cloud import CloudClient, CloudError, CloudResponse
from lce.store import StoreError

ACCESS = "fake.access.jwt"  # short-lived Access token stand-in (never stored)


class FakeCloud:
    """In-memory stand-in for the Worker API."""

    def __init__(self):
        self.calls, self.posts, self.consents = [], {}, {}
        self.publications = {}

    def request(self, method, url, headers, body):
        data = json.loads(body) if body else None
        self.calls.append((method, url, dict(headers), data))
        path = url.split("/api", 1)[1]
        if method == "PUT" and path.startswith("/posts/"):
            pid = path.split("/")[2]
            self.posts[pid] = {"post_id": pid, "state": "READY_TO_PUBLISH", **data}
            return CloudResponse(200, {"post_id": pid, "state": "READY_TO_PUBLISH"})
        if method == "POST" and path == "/consents":
            if data["post_id"] in {c["post_id"] for c in self.consents.values()}:
                return CloudResponse(409, {"error": "already has an active consent"})
            cid = f"c{len(self.consents) + 1}"
            self.consents[cid] = data
            return CloudResponse(201, {"consent_id": cid, "slot": {"local": "2026-10-07T00:30:00-06:00"}})
        if method == "GET" and path == "/snapshot":
            return CloudResponse(200, {"posts": list(self.posts.values()),
                                       "publications": list(self.publications.values())})
        return CloudResponse(404, {"error": "not found"})


@pytest.fixture
def env(store):
    pid = setup_ready(store)
    fake = FakeCloud()
    return store, pid, fake, CloudClient("https://lce.example", lambda: ACCESS, fake)


def phrase(p):
    return lambda _: p


def test_push_requires_terminal_and_phrase(env):
    store, pid, fake, client = env
    with pytest.raises(CloudError, match="interactive"):
        cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), is_tty=lambda: False)
    with pytest.raises(CloudError, match="phrase"):
        cloud.push(store, pid, client, confirm=phrase("yes"), **TTY)
    assert fake.calls == [] and cloud.load_delegation(store, pid) is None


def test_push_sends_approved_text_and_blocks_local_publish(env):
    store, pid, fake, client = env
    cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)
    method, url, headers, data = fake.calls[0]
    assert (method, url) == ("PUT", f"https://lce.example/api/posts/{pid}")
    assert headers["cf-access-token"] == ACCESS
    post = store.load_post(pid)
    assert data["approved_hash"] == post["approval"]["approved_hash"] == post["content_hash"]
    assert cloud.load_delegation(store, pid)["runtime"] == "cloud"
    p = publishing.make_publisher(store, transport=None,
                                  tokens=__import__("lce.publish.credentials",
                                                    fromlist=["MemoryTokenStore"]).MemoryTokenStore("x"))
    with pytest.raises(StoreError, match="delegated to the cloud"):
        publishing.publish(store, pid, p, confirm=ok(pid), **TTY)


def test_push_refuses_unapproved_or_changed_text(env):
    store, pid, fake, client = env
    (store.post_dir(pid) / "post.md").write_text("changed after approval\n")
    with pytest.raises(CloudError, match="approved hash"):
        cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)
    assert fake.calls == []


def test_consent_needs_delegation_terminal_and_phrase(env):
    store, pid, fake, client = env
    with pytest.raises(CloudError, match="push the post"):
        cloud.consent(store, pid, "2026-10-07-wed-0030", client, confirm=phrase(f"SCHEDULE {pid}"), **TTY)
    cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)
    with pytest.raises(CloudError, match="phrase"):
        cloud.consent(store, pid, "2026-10-07-wed-0030", client, confirm=phrase("ok"), **TTY)
    out = cloud.consent(store, pid, "2026-10-07-wed-0030", client, confirm=phrase(f"SCHEDULE {pid}"), **TTY)
    assert out["consent_id"] == "c1"
    with pytest.raises(CloudError, match="HTTP 409"):
        cloud.consent(store, pid, "2026-10-09-fri-1545", client, confirm=phrase(f"SCHEDULE {pid}"), **TTY)


@pytest.mark.parametrize("cloud_state, local", [("PUBLISHED", "PUBLISHED"),
                                               ("NEEDS_RECONCILE", "NEEDS_RECONCILE"),
                                               ("PUBLISH_FAILED", "PUBLISH_FAILED"),
                                               ("READY_TO_PUBLISH", "READY_TO_PUBLISH")])
def test_pull_mirrors_cloud_outcomes(env, cloud_state, local):
    store, pid, fake, client = env
    cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)
    fake.posts[pid]["state"] = cloud_state
    cloud.pull(store, client)
    assert store.load_post(pid)["state"] == local
    assert json.loads((store.post_dir(pid) / "cloud.json").read_text())["state"] == cloud_state


def test_access_token_is_never_stored(env):
    store, pid, fake, client = env
    cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)
    blob = "".join(p.read_text() for p in store.root.rglob("*") if p.is_file())
    assert ACCESS not in blob


def test_access_token_comes_from_env_or_cloudflared(monkeypatch):
    monkeypatch.setenv("LCE_CF_ACCESS_TOKEN", "from-env")
    assert cloud.access_token_from_cloudflared("https://x") == "from-env"
    monkeypatch.delenv("LCE_CF_ACCESS_TOKEN")

    class R:
        returncode, stdout = 0, "from-cloudflared\n"

    seen = []
    assert cloud.access_token_from_cloudflared(
        "https://x", runner=lambda cmd, **k: (seen.append(cmd), R())[1]) == "from-cloudflared"
    assert seen[0] == ["cloudflared", "access", "token", "-app=https://x"]


def test_real_transport_requires_https():
    with pytest.raises(CloudError, match="https"):
        cloud.UrllibCloudTransport().request("GET", "http://insecure.example/api", {}, None)


def test_cloud_config_is_validated(store):
    (store.root / "config" / "cloud.yaml").write_text("api_base: http://insecure.example\n")
    with pytest.raises(CloudError, match="invalid"):
        cloud.load_cloud_config(store)


def _with_image(store, pid, tmp_path, size=None):
    from test_images import png

    from lce import images

    f = png(tmp_path / "i.png")
    if size:
        f.write_bytes(f.read_bytes() + b"\0" * size)
    post = store.load_post(pid)
    images.decide(store, pid, kind="chart", rationale="one number carries the point",
                  source_file=str(f), relation="plots the figure the post discusses",
                  alt_text="Bar chart", provenance={"origin": "own_creation", "usage": "owned",
                                                    "generation": {"method": "chart script"}})
    # re-approve text + image the way the owner would (test shortcut on fictional data)
    post = store.load_post(pid)
    post["state"] = "READY_TO_PUBLISH"
    post["approval"] = {**post.get("approval", {}), "state": "approved",
                        "approved_hash": post["content_hash"],
                        "approved_at": "2026-09-30T10:00:00+00:00",
                        "image_hash": images.approval_hash(store, pid)}
    store.save_post(post)


def test_push_sends_the_approved_image(env, tmp_path):
    store, pid, fake, client = env
    _with_image(store, pid, tmp_path)
    cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)
    sent = fake.posts[pid]
    assert sent["image"]["sha256"] == store.load_post(pid)["approval"]["image_hash"]
    assert sent["image"]["alt_text"] == "Bar chart" and sent["image"]["data_base64"]


def test_push_refuses_changed_or_oversized_images(env, tmp_path):
    store, pid, fake, client = env
    _with_image(store, pid, tmp_path, size=cloud.CLOUD_MAX_IMAGE_BYTES)
    with pytest.raises(CloudError, match="publish this post locally|Publish this post locally"):
        cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)
    (store.post_dir(pid) / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nchanged")
    with pytest.raises(CloudError, match="approved image"):
        cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)
    assert not (store.post_dir(pid) / "delegation.json").exists()


def test_cli_header_and_consent_phrase_are_sent(env):
    store, pid, fake, client = env
    cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)
    cloud.consent(store, pid, "2026-10-07-wed-0030", client, confirm=phrase(f"SCHEDULE {pid}"), **TTY)
    assert all(c[2]["x-lce-client"] == "cli" for c in fake.calls)
    assert fake.consents["c1"]["confirm"] == f"SCHEDULE {pid}"


def test_configure_sends_settings_but_never_the_kill_switch(env):
    store, pid, fake, client = env
    (store.root / "config" / "linkedin.yaml").write_text(
        "api_version: '202609'\nperson_urn: urn:li:person:TestPerson1\n")
    body = cloud.configure_payload(store)
    assert body["provider"] == "linkedin_api" and body["api_version"] == "202609"
    assert "timezone" in body and "cadence" in body
    assert "auto_publish" not in body and not any("token" == k for k in body)


def _cloud_pub(pid, state, **extra):
    return {"post_id": pid, "idempotency_key": "a" * 64, "approved_hash": "b" * 64,
            "commentary_hash": "c" * 64, "state": state, "api_version": "202609",
            "author": "urn:li:person:TestPerson1", "verified_by": None, "image_urn": None,
            "resolution": None, "attempts": [{"attempt": 1, "intent_at": "2026-10-07T06:31:00+00:00",
                                              "outcome": "pending", "runtime": "cloud"}], **extra}


def test_pull_mirrors_the_cloud_publication_for_verification_and_analytics(env):
    from lce import analytics

    store, pid, fake, client = env
    cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)
    url = "https://www.linkedin.com/feed/update/urn:li:share:9100/"
    fake.posts[pid]["state"] = "PUBLISHED"
    pub = _cloud_pub(pid, "published", remote_id="urn:li:share:9100", url=url,
                     published_at="2026-10-07T06:31:02+00:00", verified_by="api_response")
    pub["attempts"][0]["outcome"] = "published"
    fake.publications[pid] = pub
    cloud.pull(store, client)
    rec = json.loads((store.post_dir(pid) / "publication.json").read_text())
    assert rec["runtime"] == "cloud" and rec["url"] == url and rec["verified_by"] == "api_response"
    assert "runtime" not in rec["attempts"][0]                    # only schema fields are kept
    assert store.load_post(pid)["state"] == "PUBLISHED"
    assert analytics._post_for_url(store)[url.rstrip("/")] == pid     # CSV import can match it
    analytics.record(store, pid, {"impressions": 10})
    assert analytics.performance(store)[0]["features"]["weekday"] == "wed"


def test_pull_follows_rearm_and_not_published_back_to_ready(env):
    store, pid, fake, client = env
    cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)
    fake.posts[pid]["state"] = "NEEDS_RECONCILE"
    fake.publications[pid] = _cloud_pub(pid, "needs_reconcile")
    cloud.pull(store, client)
    assert store.load_post(pid)["state"] == "NEEDS_RECONCILE"
    fake.posts[pid]["state"] = "READY_TO_PUBLISH"                 # owner: not on LinkedIn
    fake.publications[pid] = _cloud_pub(pid, "not_published_confirmed",
                                        resolution=json.dumps({"at": "2026-10-07T07:00:00+00:00",
                                                               "decision": "not_published",
                                                               "by": "owner@example.com"}))
    changes = cloud.pull(store, client)
    assert changes == [{"post_id": pid, "state": "READY_TO_PUBLISH"}]
    assert json.loads((store.post_dir(pid) / "publication.json").read_text())["resolution"][
        "decision"] == "not_published"


def test_withdrawal_ends_the_delegation_and_allows_redelegation(env):
    store, pid, fake, client = env
    cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)
    fake.posts[pid]["state"] = "PUBLISH_FAILED"
    fake.publications[pid] = _cloud_pub(pid, "publish_failed")
    cloud.pull(store, client)
    fake.posts[pid]["state"] = "WITHDRAWN"
    changes = cloud.pull(store, client)
    assert changes[0]["delegation"] == "ended" and cloud.load_delegation(store, pid) is None
    cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)   # re-delegate
    assert cloud.load_delegation(store, pid) is not None


def test_pull_refuses_to_mirror_a_different_approved_hash(env):
    store, pid, fake, client = env
    cloud.push(store, pid, client, confirm=phrase(f"DELEGATE {pid}"), **TTY)
    fake.posts[pid]["approved_hash"] = "f" * 64
    fake.posts[pid]["state"] = "PUBLISHED"
    changes = cloud.pull(store, client)
    assert "differs" in changes[0]["problem"]
    assert store.load_post(pid)["state"] == "READY_TO_PUBLISH"


def test_consent_slot_defaults_to_the_linked_job(env):
    store, pid, fake, client = env
    with pytest.raises(CloudError, match="give --slot"):
        cloud.slot_for_post(store, pid)


def test_sync_uploads_a_real_snapshot_without_local_paths_or_remote(store):
    class Sink:
        def __init__(self):
            self.calls = []

        def request(self, method, url, headers, body):
            self.calls.append((method, url, json.loads(body) if body else None))
            return CloudResponse(200, {"stored": True, "bytes": len(body or b""), "media": [],
                                       "sha256": "ab" * 32})

    sink = Sink()
    client = CloudClient("https://lce.example", lambda: ACCESS, sink)
    out = cloud.sync(store, client)
    method, url, snap = sink.calls[0]
    assert (method, url) == ("PUT", "https://lce.example/api/pipeline") and out["stored"]
    assert snap["meta"]["mode"] == "real" and isinstance(snap["schema"], int)
    assert "remote" not in snap["meta"]["data"]["git"]
    assert str(store.root) not in json.dumps(snap)


def test_sync_refuses_a_snapshot_that_leaks_a_local_path(store, monkeypatch):
    from lce.dashboard import snapshot as snapmod

    real = snapmod.build_snapshot
    monkeypatch.setattr(snapmod, "build_snapshot",
                        lambda s, **k: {**real(s, **k), "leak": str(s.root)})
    with pytest.raises(CloudError, match="local path"):
        cloud.sync_payload(store)


@pytest.mark.parametrize("name", ["LCE_CF_ACCESS_CLIENT_SECRET", "GITHUB_TOKEN"])
def test_sync_refuses_a_snapshot_containing_a_credential_value(store, monkeypatch, name):
    from lce.dashboard import snapshot as snapmod

    value = "s3cr3t-value-for-test-only"
    monkeypatch.setenv(name, value)
    real = snapmod.build_snapshot
    monkeypatch.setattr(snapmod, "build_snapshot", lambda s, **k: {**real(s, **k), "x": value})
    with pytest.raises(CloudError, match=name):
        cloud.sync_payload(store)


def test_sync_refuses_a_snapshot_containing_the_private_remote(store, monkeypatch):
    from lce.dashboard import snapshot as snapmod

    url = "https://github.com/example/private-data"
    real = snapmod.build_snapshot

    def fake(s, **k):
        snap = real(s, **k)
        snap["meta"]["data"]["git"] = {"remote": url + ".git", "head": "abc"}
        snap["posts"] = [{"note": f"see {url}"}]
        return snap

    monkeypatch.setattr(snapmod, "build_snapshot", fake)
    with pytest.raises(CloudError, match="private repository URL"):
        cloud.sync_payload(store)


class MirrorWorker:
    """Stores PUT /pipeline like D1 and returns it on GET with mirror metadata."""

    def __init__(self, tamper=None):
        self.row, self.tamper = None, tamper

    def request(self, method, url, headers, body):
        import hashlib

        if method == "PUT" and url.endswith("/api/pipeline"):
            self.row = body
            return CloudResponse(200, {"stored": True, "bytes": len(body),
                                       "sha256": hashlib.sha256(body).hexdigest()})
        if method == "GET" and url.endswith("/api/pipeline"):
            snap = json.loads(self.row)
            sha = hashlib.sha256(self.row).hexdigest()
            if self.tamper:
                snap, sha = self.tamper(snap, sha)
            snap["meta"]["mirror"] = {"sha256": sha, "received_at": "2026-10-01T00:00:00+00:00",
                                      "received_by": "lce-gh-actions"}
            return CloudResponse(200, snap)
        return CloudResponse(404, {"error": "not found"})


def test_sync_verify_reads_the_mirror_back(store):
    out = cloud.sync(store, CloudClient("https://lce.example", lambda: ACCESS, MirrorWorker()), verify=True)
    assert [c["status"] for c in out["checks"]] == ["ok", "ok", "ok"], out["checks"]


def test_sync_verify_detects_a_different_row_and_a_leak(store):
    def other_sha(snap, sha):
        return snap, "0" * 64

    out = cloud.sync(store, CloudClient("https://lce.example", lambda: ACCESS, MirrorWorker(other_sha)),
                     verify=True)
    assert {c["check"]: c["status"] for c in out["checks"]}["mirror stored"] == "fail"

    def leaky(snap, sha):
        snap["posts"] = snap.get("posts", []) + [{"post_id": "x", "note": str(store.root)}]
        return snap, sha

    out = cloud.sync(store, CloudClient("https://lce.example", lambda: ACCESS, MirrorWorker(leaky)),
                     verify=True)
    by = {c["check"]: c["status"] for c in out["checks"]}
    assert by["mirror privacy"] == "fail" and by["mirror content"] == "fail"
