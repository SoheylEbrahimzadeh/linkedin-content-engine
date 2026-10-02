"""LCE-038: explicit media decision, real assets, rights metadata, sync and hash integrity."""

import hashlib
import json

import pytest
from conftest import GOOD_POST, awaiting_post, selected_post
from test_images import png

from lce import cloud, commons, images
from lce.approval import prepare
from lce.cloud import CloudClient, CloudResponse
from lce.dupcheck import run_dupcheck
from lce.posts import current_text, save_draft, save_humanized
from lce.qa import run_qa
from lce.store import StoreError
from lce.visuals import concept, diagram

ITEMS = ["Start with the boring rules",
         "They cover more than you expect, and they are easy to explain to the team"]
TITLE = "Most small service teams do not need a model to sort tickets"
# LCE-041: a conceptual visual of GOOD_POST's idea (its own short labels, not the post's sentences).
SPEC = {"visual_type": "flow", "concept": "rules first; a model only when rules stop being enough",
        "relevance_reason": "shows the order of decisions the post argues for, which the text only states",
        "title": "Ticket triage: rules before models",
        "nodes": [{"label": "Incoming tickets"}, {"label": "Keyword rules", "note": "simple, explainable"},
                  {"label": "Routed queues"}],
        "outcomes": ["Rules suffice", "Consider a model"],
        "alt_text": "Flow diagram: incoming tickets pass keyword rules into routed queues, ending in two "
                    "outcomes, rules suffice or consider a model."}


def humanized(store):
    pid = selected_post(store)
    save_draft(store, pid, GOOD_POST)
    save_humanized(store, pid, GOOD_POST)
    return pid


def test_text_only_needs_a_recorded_reason(store, capsys):
    from lce import cli

    pid = humanized(store)
    assert cli.main(["--data-dir", str(store.root), "image", "decide", pid, "--kind", "none",
                     "--rationale", "x"]) == 1
    assert "--text-only-reason" in capsys.readouterr().out
    doc = images.decide(store, pid, kind="none", rationale="the text carries it",
                        text_only_reason="text_carries_point")
    assert doc["text_only_reason"] == "text_carries_point"
    with pytest.raises(StoreError):
        images.decide(store, pid, kind="none", rationale="x", text_only_reason="lazy")
    view = images.media_view(store, pid)
    assert view["media_required"] is False and view["media_status"] == "text_only"
    assert view["text_only_reason"] == "text_carries_point"


def test_check_flags_unexplained_text_only_and_unused_figures(store):
    pid = humanized(store)
    images.decide(store, pid, kind="none", rationale="x")
    _, warnings = images.check(store, pid)
    assert any("reason" in w for w in warnings)
    post = store.load_post(pid)
    post["claims"] = [{"text": "Manual triage took 40 minutes a day", "source_url": "https://example.com/a"}]
    store.save_post(post)
    _, warnings = images.check(store, pid)
    assert any("lce image chart" in w for w in warnings)


def test_undecided_media_is_reported_as_such(store):
    pid = humanized(store)
    assert images.media_view(store, pid)["media_status"] == "undecided"


def test_text_dump_diagram_is_rejected_and_nothing_is_attached(store):
    pid = humanized(store)
    with pytest.raises(StoreError, match="text dump") as e:
        diagram(store, pid, title=TITLE, items=ITEMS)          # the post's own sentences
    assert "label is a sentence" in str(e.value)
    assert images.load(store, pid) is None                     # nothing attached


def test_conceptual_diagram_is_a_real_asset_with_a_relevance_record(store):
    pid = humanized(store)
    doc = concept(store, pid, SPEC)
    f = store.post_dir(pid) / doc["file"]
    data = f.read_bytes()
    assert data.startswith(b"\x89PNG") and hashlib.sha256(data).hexdigest() == doc["sha256"]
    assert doc["kind"] == "diagram" and doc["mime"] == "image/png" and doc["bytes"] == len(data)
    assert doc["width"] == 1200 and doc["height"] == 1200
    assert doc["provenance"]["origin"] == "own_creation" and doc["provenance"]["usage"] == "owned"
    rel = doc["media_relevance"]
    assert rel["media_decision"] == "accepted" and rel["visual_type"] == "flow"
    assert rel["concept"] == SPEC["concept"] and rel["relevance_reason"] == SPEC["relevance_reason"]
    assert rel["copied_post_text_ratio"] < 0.35 and rel["factual_claims"] == []
    assert doc["alt_text"] == SPEC["alt_text"] and doc["spec"]["nodes"][1]["note"] == "simple, explainable"
    errors, _ = images.check(store, pid)
    assert errors == []
    view = images.media_view(store, pid)
    assert view["media_status"] == "attached" and view["media_relevance"]["media_decision"] == "accepted"


def test_media_change_discards_approval_and_rebinds_the_new_image_hash(store):
    pid = awaiting_post(store)
    text_hash = store.load_post(pid)["content_hash"]
    assert store.load_post(pid)["approval"]["image_hash"] == "none"
    doc = concept(store, pid, SPEC)
    post = store.load_post(pid)
    assert post["state"] == "HUMANIZED" and "approval" not in post          # approval discarded
    assert run_qa(store, pid, denylist=[])["status"] == "passed"
    assert run_dupcheck(store, pid)["status"] == "passed"
    prepare(store, pid)
    post = store.load_post(pid)
    assert post["state"] == "AWAITING_APPROVAL" and post["content_hash"] == text_hash
    assert post["approval"]["image_hash"] == doc["sha256"]
    assert doc["sha256"] in (store.post_dir(pid) / "APPROVAL.md").read_text()


class FakeCommons:
    def __init__(self, license_name="CC BY-SA 4.0", data=None, sha1=None):
        self.data = data or png_bytes()
        self.license, self.sha1 = license_name, sha1 or hashlib.sha1(self.data).hexdigest()  # noqa: S324
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if url.startswith(commons.API):
            return json.dumps({"query": {"pages": {"1": {"imageinfo": [{
                "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Example.png",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Example.png",
                "sha1": self.sha1, "mime": "image/png", "width": 1, "height": 1, "size": len(self.data),
                "extmetadata": {"LicenseShortName": {"value": self.license},
                                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                                "Artist": {"value": "<a href='x'>Jane Example</a>"}}}]}}}}).encode()
        return self.data


def png_bytes():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        return png(Path(tmp) / "x.png").read_bytes()


def test_commons_image_with_reuse_licence_is_attached_with_rights_metadata(store):
    pid = humanized(store)
    fake = FakeCommons()
    doc = commons.attach(store, pid, "File:Example.png",
                         relation="shows the ticket routing board the post describes",
                         alt_text="Photo of a physical ticket routing board with colour-coded queues",
                         rationale="a real photo of the artefact", transport=fake,
                         relevance={"concept": "what a rule-based routing board looks like",
                                    "visual_type": "photo",
                                    "reason": "shows the physical artefact the post describes in words"})
    prov = doc["provenance"]
    assert prov == {"origin": "licensed_stock", "usage": "licensed", "license": "CC BY-SA 4.0",
                    "source_url": "https://commons.wikimedia.org/wiki/File:Example.png",
                    "credit": "Jane Example via Wikimedia Commons", "title": "File:Example.png",
                    "license_url": "https://creativecommons.org/licenses/by-sa/4.0"}
    assert (store.post_dir(pid) / doc["file"]).read_bytes() == fake.data
    assert images.check(store, pid)[0] == []
    rel = doc["media_relevance"]
    assert rel["text_checked"] is False and rel["media_decision"] == "accepted"
    hosts = ("https://commons.wikimedia.org/", "https://upload.wikimedia.org/")
    assert all(u.startswith(hosts) for u in fake.urls)


@pytest.mark.parametrize("name", ["CC BY-NC 4.0", "CC BY-ND 2.0", "Fair use", "All rights reserved", ""])
def test_commons_refuses_licences_that_are_not_rights_safe(store, name):
    pid = humanized(store)
    with pytest.raises(StoreError, match="not rights-safe"):
        commons.attach(store, pid, "File:Example.png", relation="x" * 30, alt_text="a", rationale="r",
                       transport=FakeCommons(name))
    assert images.load(store, pid) is None


def test_commons_refuses_a_file_that_does_not_match_its_reported_hash(store):
    pid = humanized(store)
    with pytest.raises(StoreError, match="SHA-1"):
        commons.attach(store, pid, "File:Example.png", relation="x" * 30, alt_text="a", rationale="r",
                       transport=FakeCommons(sha1="0" * 40))


@pytest.mark.parametrize("name,usage", [("CC0", "public_domain"), ("Public domain", "public_domain"),
                                        ("CC BY 2.0", "licensed"), ("CC BY-SA 3.0 de", "licensed")])
def test_licence_classification(name, usage):
    assert commons.license_usage(name) == usage


class MediaCloud:
    def __init__(self):
        self.put = {}

    def request(self, method, url, headers, body):
        path = url.split("/api", 1)[1]
        if method == "GET" and path == "/preview-media":
            return CloudResponse(200, {"media": []})
        if method == "PUT" and path.startswith("/preview-media/"):
            self.put[path.split("/")[2]] = json.loads(body)
            return CloudResponse(200, {"stored": True})
        return CloudResponse(404, {})


def test_cloud_sync_uploads_the_real_diagram_asset(store):
    import base64

    pid = humanized(store)
    doc = concept(store, pid, SPEC)
    fake = MediaCloud()
    out = cloud.sync_preview_media(store, CloudClient("https://lce.example", lambda: "x", fake))
    assert out["uploaded"] == [pid]
    sent = base64.b64decode(fake.put[pid]["data_base64"])
    assert sent == (store.post_dir(pid) / doc["file"]).read_bytes()
    assert fake.put[pid]["sha256"] == doc["sha256"] and fake.put[pid]["alt_text"] == doc["alt_text"]


def test_snapshot_carries_media_fields(store):
    from lce.dashboard.snapshot import build_snapshot

    pid = humanized(store)
    doc = concept(store, pid, SPEC)
    view = next(p for p in build_snapshot(store, mode="real")["posts"] if p["post_id"] == pid)["image"]
    assert view["media_status"] == "attached" and view["width"] == doc["width"]
    assert view["mime"] == "image/png" and view["provenance"]["origin"] == "own_creation"
    assert current_text(store, pid) == GOOD_POST
