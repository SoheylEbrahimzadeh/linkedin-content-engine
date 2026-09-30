"""Phase 6B: image upload through the official Images API (fake transport only)."""

import json
import struct

import pytest
from fakes import FAKE_TOKEN, FakeTransport, created, image_init, status, timeout_after_send, uploaded
from test_images import checked_post, decide_diagram, png

from lce import approval, images, publishing
from lce.publish.credentials import MemoryTokenStore
from lce.store import StoreError

TTY = lambda: True  # noqa: E731


def ready_with_image(store, tmp_path):
    pid = checked_post(store)
    doc = decide_diagram(store, pid, tmp_path)
    approval.prepare(store, pid)
    h = store.load_post(pid)["content_hash"]
    approval.approve(store, pid, h[:12], confirm=lambda _: f"APPROVE {pid}", is_tty=TTY)
    approval.mark_ready(store, pid)
    s = store.settings()
    s["publisher"] = {"provider": "linkedin_api"}
    store.write_doc(store.settings_path, "settings", s)
    (store.root / "config" / "linkedin.yaml").write_text(
        "api_version: '202609'\nperson_urn: urn:li:person:TestPerson1\n")
    return pid, doc


def run(store, pid, *responses):
    t = FakeTransport(*responses)
    pub = publishing.make_publisher(store, transport=t, tokens=MemoryTokenStore(FAKE_TOKEN))
    out = publishing.publish(store, pid, pub, confirm=lambda _: f"PUBLISH {pid}", is_tty=TTY)
    return out, t.calls


def test_image_is_uploaded_then_referenced_in_the_post(store, tmp_path):
    pid, doc = ready_with_image(store, tmp_path)
    out, calls = run(store, pid, image_init(), uploaded(), created())
    assert out["post"]["state"] == "PUBLISHED"
    init, put, post = calls
    assert init["url"].endswith("/rest/images?action=initializeUpload")
    assert init["body"] == {"initializeUploadRequest": {"owner": "urn:li:person:TestPerson1"}}
    assert put["method"] == "PUT" and put["url"].startswith("https://www.linkedin.com/dms-uploads/")
    assert put["body"] == {"raw_bytes": doc["bytes"]}
    assert put["headers"]["Authorization"] == f"Bearer {FAKE_TOKEN}"
    assert post["body"]["content"] == {"media": {"id": "urn:li:image:C4E10AQFakeImage1",
                                                 "altText": doc["alt_text"]}}
    rec = json.loads((store.post_dir(pid) / "publication.json").read_text())
    assert rec["image"] == {"urn": "urn:li:image:C4E10AQFakeImage1", "sha256": doc["sha256"]}


@pytest.mark.parametrize(("responses", "calls_made"), [
    ((status(500),), 1),                                   # initialize failed
    ((timeout_after_send(),), 1),                          # initialize outcome unknown
    ((image_init(url="https://evil.example/upload"),), 1),  # untrusted upload URL: no PUT
    ((image_init(urn="urn:li:share:1"),), 1),              # not an image URN
    ((image_init(), status(400)), 2),                      # upload rejected
])
def test_image_failures_create_no_post_and_are_retryable(store, tmp_path, responses, calls_made):
    pid, _ = ready_with_image(store, tmp_path)
    out, calls = run(store, pid, *responses)
    assert out["post"]["state"] == "PUBLISH_FAILED"
    assert len(calls) == calls_made
    assert all("evil.example" not in c["url"] for c in calls)
    assert not any(c["url"].endswith("/rest/posts") for c in calls)
    assert out["result"].detail["retryable"] is True


def test_ambiguous_post_after_upload_needs_reconcile(store, tmp_path):
    pid, _ = ready_with_image(store, tmp_path)
    out, calls = run(store, pid, image_init(), uploaded(), status(503))
    assert out["post"]["state"] == "NEEDS_RECONCILE" and len(calls) == 3


def test_dry_run_shows_the_image_steps_and_sends_nothing(store, tmp_path):
    pid, doc = ready_with_image(store, tmp_path)
    t = FakeTransport()
    pub = publishing.make_publisher(store, transport=t, tokens=MemoryTokenStore(FAKE_TOKEN))
    plan = publishing.dry_run(store, pid, pub)
    assert plan["image"]["sha256"] == doc["sha256"] and len(plan["image"]["steps"]) == 3
    assert plan["body"]["content"]["media"]["id"] == "<urn:li:image from upload>"
    assert t.calls == []


def test_swapped_image_is_never_sent(store, tmp_path):
    pid, _ = ready_with_image(store, tmp_path)
    png(store.post_dir(pid) / "image.png", shade=123)
    t = FakeTransport()
    pub = publishing.make_publisher(store, transport=t, tokens=MemoryTokenStore(FAKE_TOKEN))
    with pytest.raises(StoreError, match="approved image"):
        publishing.publish(store, pid, pub, confirm=lambda _: f"PUBLISH {pid}", is_tty=TTY)
    assert t.calls == []


def test_dimensions_and_pixel_limit(tmp_path):
    p = png(tmp_path / "a.png")
    assert images.dimensions(p.read_bytes(), ".png") == (1, 1)
    big = bytearray(p.read_bytes())
    big[16:24] = struct.pack(">II", 7000, 6000)
    assert images.dimensions(bytes(big), ".png") == (7000, 6000)
    assert images.dimensions(b"GIF89a" + struct.pack("<HH", 640, 480), ".gif") == (640, 480)
    jpeg = (b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"JF"
            + b"\xff\xc0" + struct.pack(">HBHH", 11, 8, 300, 400) + b"\x03\x00\x00")
    assert images.dimensions(jpeg, ".jpg") == (400, 300)


def test_oversized_image_fails_the_check(store, tmp_path):
    pid = checked_post(store)
    decide_diagram(store, pid, tmp_path)
    f = store.post_dir(pid) / "image.png"
    data = bytearray(f.read_bytes())
    data[16:24] = struct.pack(">II", 7000, 6000)
    f.write_bytes(bytes(data))
    doc = images.load(store, pid)
    doc["sha256"] = images.file_sha256(f)
    store.write_doc(images.path(store, pid), "image", doc)
    assert any("too large" in e for e in images.check(store, pid)[0])
