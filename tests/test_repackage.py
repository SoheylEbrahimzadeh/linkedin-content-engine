"""LCE-041: manual Refresh regenerates the whole post package; nothing is overwritten,
approved or published, and the approval is bound to the new text and image hashes."""

import hashlib
import json

import pytest
import yaml
from conftest import GOOD_POST, awaiting_post
from test_images import png
from test_media_pipeline import SPEC

from lce import cloud, decisions, images, refresh, repackage, versions
from lce.cloud import CloudClient, CloudResponse
from lce.posts import current_text
from lce.store import StoreError
from lce.textutil import content_hash

URL = "https://example.com/report"
CLAIM = "Manual triage dropped from about 40 minutes a day to about 10"
NEW_TEXT = GOOD_POST.replace(
    "Most small service teams do not need a model to sort tickets.",
    "Before buying a triage model, try the rules you already understand.",
)
PKG = {
    "text": NEW_TEXT,
    "reason": "sharper hook; conceptual visual instead of a text checklist",
    "sources": [{"url": URL, "title": "Fictional report"}],
    "claims": [{"text": CLAIM, "source_url": URL}],
    "media": {"spec": SPEC},
}


def legacy_post(store):
    """An AWAITING_APPROVAL post whose image is a pre-LCE-041 text dump (the Gartner case)."""
    pid = awaiting_post(store)
    post = store.load_post(pid)
    post["sources"] = [{"url": URL, "title": "Fictional report"}]
    post["claims"] = [{"text": CLAIM, "source_url": URL}]
    f = png(store.post_dir(pid) / "image.png")
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    store.write_doc(
        images.path(store, pid),
        "image",
        {
            "kind": "diagram",
            "rationale": "checklist made scannable",
            "decided_at": "2026-09-28T19:00:00+00:00",
            "decided_by": "agent",
            "file": "image.png",
            "sha256": sha,
            "bytes": f.stat().st_size,
            "mime": "image/png",
            "width": 1,
            "height": 1,
            "alt_text": "The post's checklist, written out",
            "relation": "restates the post's checklist verbatim: Start with the boring rules — They cover "
            "more than you expect, and they are easy to explain to the team; What was the first rule "
            "you automated?",
            "provenance": {
                "origin": "own_creation",
                "usage": "owned",
                "generation": {"method": "lce image diagram"},
            },
        },
    )
    post["approval"] = {**post["approval"], "image_hash": sha}
    store.save_post(post)
    return pid


def files(store, pid):
    d = store.post_dir(pid)
    return {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}


def test_refresh_decision_only_records_the_request(store):
    pid = legacy_post(store)
    before = files(store, pid)
    d = {
        "decision_id": "d-9",
        "action": "refresh",
        "post_id": pid,
        "created_by": "owner@example.com",
        "payload": {"note": "the image just repeats the text"},
    }
    assert "refresh requested" in decisions._refresh(store, None, d)
    post = store.load_post(pid)
    assert post["state"] == "AWAITING_APPROVAL" and post["refresh_request"]["decision_id"] == "d-9"
    assert post["refresh_request"]["note"] == "the image just repeats the text"
    assert {k: v for k, v in files(store, pid).items() if k != "post.yaml"} == {
        k: v for k, v in before.items() if k != "post.yaml"
    }
    assert [r["post_id"] for r in repackage.pending(store)] == [pid]


def test_refresh_is_refused_for_published_and_cloud_posts(store):
    pid = legacy_post(store)
    post = store.load_post(pid)
    post["state"] = "PUBLISHED"
    store.save_post(post)
    with pytest.raises(StoreError, match="never rewritten"):
        repackage.request(store, pid, by="x")
    post["state"] = "AWAITING_APPROVAL"
    store.save_post(post)
    (store.post_dir(pid) / "delegation.json").write_text("{}")
    with pytest.raises(decisions.Refused, match="cloud publisher"):
        decisions._refresh(
            store, None, {"decision_id": "d", "post_id": pid, "created_by": "o", "payload": {}}
        )


def test_package_creates_a_new_version_bound_to_new_hashes_and_keeps_the_old_one(store):
    pid = legacy_post(store)
    repackage.request(store, pid, by="cloud-access:owner@example.com", decision_id="d-1")
    old = files(store, pid)
    old_post = store.load_post(pid)
    rec = repackage.package(store, pid, PKG, by="session")
    post = store.load_post(pid)
    # new version, awaiting approval, bound to the new text and image
    assert post["state"] == "AWAITING_APPROVAL" and post["approval"]["state"] == "pending"
    assert current_text(store, pid).startswith("Before buying a triage model")
    doc = images.load(store, pid)
    assert doc["media_relevance"]["media_decision"] == "accepted"
    assert post["approval"]["image_hash"] == doc["sha256"] != old_post["approval"]["image_hash"]
    assert post["content_hash"] == content_hash(NEW_TEXT) != old_post["content_hash"]
    art = (store.post_dir(pid) / "APPROVAL.md").read_text()
    assert doc["sha256"] in art and content_hash(NEW_TEXT) in art
    # the previous version is preserved byte for byte
    [v1] = versions.listing(store, pid)
    vdir = versions.folder(store, pid) / "v1"
    for name in ("post.md", "image.png", "image.yaml", "APPROVAL.md"):
        assert (vdir / name).read_bytes() == old[name]
    assert (
        v1["content_hash"] == old_post["content_hash"]
        and v1["image_sha256"] == old_post["approval"]["image_hash"]
    )
    assert v1["approval_state"] == "pending" and v1["hook"].startswith("Most small service teams")
    # recorded: request closed, refresh outcome, freshness record with before/after hashes
    assert "refresh_request" not in post
    assert post["refresh"]["outcome"] == "refreshed" and post["refresh"]["previous_version"] == 1
    assert post["refresh"]["decision_id"] == "d-1"
    assert rec["mode"] == "manual" and rec["approval_effect"] == "invalidated"
    assert rec["content_hash_before"] == old_post["content_hash"] and rec["image_sha256"] == doc["sha256"]
    assert rec["steps"]["qa"] == "passed" and rec["steps"]["duplicate"]["status"] == "passed"
    assert refresh.latest(store, pid)["mode"] == "manual"


def test_refresh_invalidates_an_existing_approval_and_never_publishes(store):
    pid = legacy_post(store)
    post = store.load_post(pid)
    post["state"] = "APPROVED"
    post["approval"] = {**post["approval"], "state": "approved", "approved_hash": post["content_hash"]}
    store.save_post(post)
    rec = repackage.package(store, pid, PKG)
    post = store.load_post(pid)
    assert rec["approval_before"] == "approved" and rec["approval_effect"] == "invalidated"
    assert post["state"] == "AWAITING_APPROVAL" and "approved_hash" not in post["approval"]
    assert not (store.post_dir(pid) / "publication.json").exists()
    assert cloud.load_delegation(store, pid) is None
    # LCE-040: the scheduled-publish gate sees no same-day `current` check for the new text
    row = refresh.cloud_rows(store, [pid])
    assert all(r["status"] != "current" for r in row)


@pytest.mark.parametrize(
    "change, match",
    [
        ({"media": {"keep": "still fine"}}, "keep refused"),  # old text-dump image
        ({"text": GOOD_POST.replace("about 10", "about 25")}, "QA failed"),  # unsupported number
        (
            {
                "media": {
                    "spec": {**SPEC, "nodes": [{"label": "Start with the boring rules, they cover a lot"}]}
                }
            },
            "media relevance rejected",
        ),
    ],
)
def test_a_failing_refresh_rolls_back_exactly(store, change, match):
    pid = legacy_post(store)
    before = files(store, pid)
    with pytest.raises(StoreError, match=match):
        repackage.package(store, pid, {**PKG, **change})
    assert files(store, pid) == before
    assert versions.listing(store, pid) == []


def test_archive_duplicate_stops_the_refresh(store):
    from lce import dupcheck

    pid = legacy_post(store)
    dupcheck.import_external(store, "published-earlier", NEW_TEXT)
    before = files(store, pid)
    with pytest.raises(StoreError, match="duplicate check failed"):
        repackage.package(store, pid, PKG)
    assert files(store, pid) == before


def test_package_rejects_incomplete_packages(store):
    pid = legacy_post(store)
    for bad, match in [
        ({**PKG, "sources": []}, "sources"),
        ({**PKG, "reason": " "}, "reason"),
        ({**PKG, "media": {}}, "media must be"),
        ({**PKG, "claims": [{"text": CLAIM, "source_url": "https://other.example"}]}, "claim"),
    ]:
        with pytest.raises(StoreError, match=match):
            repackage.package(store, pid, bad)


def test_keep_only_when_the_current_version_is_valid(store):
    pid = legacy_post(store)
    repackage.request(store, pid, by="o")
    with pytest.raises(StoreError, match="cannot be kept"):
        repackage.keep(store, pid, reason="looks fine")  # text-dump image
    repackage.package(store, pid, PKG)
    repackage.request(store, pid, by="o")
    before = store.load_post(pid)["approval"]
    out = repackage.keep(store, pid, reason="sources unchanged; visual still fits")
    post = store.load_post(pid)
    assert out["outcome"] == "kept" and post["approval"] == before and "refresh_request" not in post


def test_previous_versions_are_recoverable(store):
    pid = legacy_post(store)
    old_text = current_text(store, pid)
    repackage.package(store, pid, PKG)
    versions.restore(store, pid, 1, by="session")
    post = store.load_post(pid)
    assert current_text(store, pid) == old_text and post["state"] == "HUMANIZED"
    assert "approval" not in post  # restored version needs approval again
    assert [v["version"] for v in versions.listing(store, pid)] == [1, 2]  # nothing lost


def test_cli_package_pending_and_versions(store, tmp_path, capsys):
    from lce.cli import main

    pid = legacy_post(store)
    repackage.request(store, pid, by="o")
    base = ["--data-dir", str(store.root)]
    assert main([*base, "refresh", "pending"]) == 0
    assert pid in capsys.readouterr().out
    (tmp_path / "post.md").write_text(NEW_TEXT)
    (tmp_path / "visual.yaml").write_text(yaml.safe_dump(SPEC))
    pkg = {**PKG, "media": {"spec": "visual.yaml"}, "text_file": "post.md"}
    pkg.pop("text")
    (tmp_path / "pkg.yaml").write_text(yaml.safe_dump(pkg))
    assert main([*base, "refresh", "package", pid, "--file", str(tmp_path / "pkg.yaml")]) == 0
    out = capsys.readouterr().out
    assert "previous version kept as v1" in out and "nothing was approved or published" in out
    assert main([*base, "versions", "list", pid]) == 0
    assert "v1" in capsys.readouterr().out


def test_snapshot_shows_versions_and_request(store):
    from lce.dashboard.snapshot import build_snapshot

    pid = legacy_post(store)
    repackage.package(store, pid, PKG)
    repackage.request(store, pid, by="o", note="again")
    view = next(p for p in build_snapshot(store, mode="real")["posts"] if p["post_id"] == pid)
    assert view["versions"][0]["version"] == 1 and view["versions"][0]["text"].startswith("Most small")
    assert view["refresh_request"]["note"] == "again" and view["refresh"]["outcome"] == "refreshed"
    assert view["image"]["media_relevance"]["media_decision"] == "accepted"


class VersionCloud:
    def __init__(self):
        self.put = {}

    def request(self, method, url, headers, body):
        path = url.split("/api", 1)[1]
        if method == "GET" and path == "/version-media":
            return CloudResponse(200, {"media": []})
        if method == "PUT" and path.startswith("/version-media/"):
            self.put[path.removeprefix("/version-media/")] = json.loads(body)
            return CloudResponse(200, {"stored": True})
        return CloudResponse(404, {})


def test_sync_uploads_previous_version_images(store):
    pid = legacy_post(store)
    old_sha = images.load(store, pid)["sha256"]
    repackage.package(store, pid, PKG)
    fake = VersionCloud()
    out = cloud.sync_version_media(store, CloudClient("https://lce.example", lambda: "x", fake))
    assert out["uploaded"] == [f"{pid}/v1"] and fake.put[f"{pid}/1"]["sha256"] == old_sha
