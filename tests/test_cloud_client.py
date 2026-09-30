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
