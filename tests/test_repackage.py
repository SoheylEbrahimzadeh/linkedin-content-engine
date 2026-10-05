"""LCE-041/042: Refresh rejects the current version and produces a genuinely new replacement
(v1 -> v2 -> v3); nothing is overwritten, approved or published; Skip generates nothing."""

import hashlib
import json

import pytest
import yaml
from conftest import awaiting_post
from test_images import png
from test_media_pipeline import SPEC

from lce import cloud, decisions, images, refresh, repackage, versions
from lce.cloud import CloudClient, CloudResponse
from lce.posts import current_text
from lce.store import StoreError
from lce.textutil import content_hash

URL = "https://example.com/report"
CLAIM = "Manual triage dropped from about 40 minutes a day to about 10"
NEW_TEXT = (
    "Before buying a triage model, look at the rules your team already understands.\n\n"
    "Manual triage dropped from about 40 minutes a day to about 10 in our pilot, and the work was plain "
    "keyword matching.\n\n"
    "I would not buy a model until the rules stop covering the queue, because until then it adds cost and "
    "makes routing harder to explain.\n\n"
    "Which routing rule would you write first?\n"
)
THIRD_TEXT = (
    "Keyword rules are boring, and that is their strength.\n\n"
    "They cut manual triage from about 40 minutes a day to about 10 for us. Everyone on the team could read "
    "them and fix them.\n\n"
    "When a queue outgrows the rules, the misroutes show it first. That is the moment to test a model, not "
    "before.\n\n"
    "What do your misroutes tell you?\n"
)
SPEC3 = {
    **SPEC,
    "title": "When rules stop being enough",
    "concept": "misroutes are the signal that a queue has outgrown its keyword rules",
    "nodes": [{"label": "Keyword rules"}, {"label": "Misroutes rise"}, {"label": "Test a model"}],
    "outcomes": ["Keep the rules", "Pilot a model"],
    "alt_text": "Flow diagram: keyword rules lead to rising misroutes, which trigger a model test; two "
    "outcomes, keep the rules or pilot a model.",
}
PKG = {
    "text": NEW_TEXT,
    "reason": "sharper hook; conceptual visual instead of a text checklist",
    "sources": [{"url": URL, "title": "Fictional report"}],
    "claims": [{"text": CLAIM, "source_url": URL}],
    "media": {"spec": SPEC, "owner_requested": True},   # drawn visuals only on the owner's request (LCE-043)
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


def refresh_click(store, pid, note="", decision_id="d-1"):
    """What the decisions workflow does with the owner's Refresh click."""
    d = {
        "decision_id": decision_id,
        "action": "refresh",
        "post_id": pid,
        "created_by": "owner@example.com",
        "payload": {"note": note},
    }
    return decisions._refresh(store, None, d)


def test_refresh_click_rejects_and_deactivates_the_current_version(store):
    pid = legacy_post(store)
    old = files(store, pid)
    assert "rejected" in refresh_click(store, pid, note="the image just repeats the text")
    post = store.load_post(pid)
    # no longer the active, approvable version
    assert post["state"] == "NEEDS_REVISION" and "approval" not in post
    assert post["refresh_request"]["rejected_version"] == 1
    assert post["refresh_request"]["note"] == "the image just repeats the text"
    [v1] = versions.listing(store, pid)
    assert v1["status"] == "rejected" and v1["approval_state"] == "pending"
    for name in ("post.md", "image.png", "APPROVAL.md"):
        assert (versions.folder(store, pid) / "v1" / name).read_bytes() == old[name]
    # a second click before the replacement exists archives nothing more
    refresh_click(store, pid, note="again", decision_id="d-2")
    assert len(versions.listing(store, pid)) == 1
    assert store.load_post(pid)["refresh_request"]["decision_id"] == "d-2"
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
        refresh_click(store, pid)


def test_replacement_is_a_new_package_bound_to_new_hashes(store):
    pid = legacy_post(store)
    old_post = store.load_post(pid)
    refresh_click(store, pid)
    rec = repackage.package(store, pid, PKG, by="session")
    post = store.load_post(pid)
    assert post["state"] == "AWAITING_APPROVAL" and post["approval"]["state"] == "pending"
    assert current_text(store, pid) == NEW_TEXT
    doc = images.load(store, pid)
    assert doc["media_relevance"]["media_decision"] == "accepted"
    assert post["approval"]["image_hash"] == doc["sha256"] != old_post["approval"]["image_hash"]
    assert post["content_hash"] == content_hash(NEW_TEXT) != old_post["content_hash"]
    art = (store.post_dir(pid) / "APPROVAL.md").read_text()
    assert doc["sha256"] in art and content_hash(NEW_TEXT) in art
    assert [v["version"] for v in versions.listing(store, pid)] == [1]  # archived once, by the click
    assert all(s["accessed_at"] for s in post["sources"])  # sources re-checked now
    assert "refresh_request" not in post
    assert post["refresh"]["outcome"] == "refreshed" and post["refresh"]["previous_version"] == 1
    assert rec["mode"] == "manual" and rec["image_sha256_before"] == old_post["approval"]["image_hash"]


def test_repeated_refresh_makes_v3_and_keeps_every_version(store):
    pid = legacy_post(store)
    refresh_click(store, pid)
    repackage.package(store, pid, PKG)
    v2_image = images.load(store, pid)["sha256"]
    refresh_click(store, pid, note="still not right", decision_id="d-2")
    assert store.load_post(pid)["state"] == "NEEDS_REVISION"
    third = {**PKG, "text": THIRD_TEXT, "media": {"spec": SPEC3, "owner_requested": True}}
    repackage.package(store, pid, third)
    post = store.load_post(pid)
    assert post["state"] == "AWAITING_APPROVAL" and current_text(store, pid) == THIRD_TEXT
    vs = versions.listing(store, pid)
    assert [(v["version"], v["status"]) for v in vs] == [(1, "rejected"), (2, "rejected")]
    assert (versions.folder(store, pid) / "v2" / "post.md").read_text() == NEW_TEXT
    assert vs[1]["image_sha256"] == v2_image != images.load(store, pid)["sha256"]
    assert post["refresh"]["previous_version"] == 2


@pytest.mark.parametrize(
    "change, match",
    [
        ({"text": NEW_TEXT}, "hook is the same"),  # v3 repeats v2's hook
        ({"text": "Keyword rules first.\n\n" + NEW_TEXT.split("\n\n", 1)[1]}, "repeats an earlier version"),
        ({"text": THIRD_TEXT}, "identical to an earlier version's image"),  # same visual as v2
    ],
)
def test_a_replacement_must_be_genuinely_new(store, change, match):
    pid = legacy_post(store)
    refresh_click(store, pid)
    repackage.package(store, pid, PKG)
    refresh_click(store, pid, decision_id="d-2")
    before = files(store, pid)
    with pytest.raises(StoreError, match=match):
        repackage.package(store, pid, {**PKG, **change})
    assert files(store, pid) == before and len(versions.listing(store, pid)) == 2


@pytest.mark.parametrize(
    "change, match",
    [
        ({"media": {"keep": "still fine"}}, "replaces the media too"),
        ({"text": NEW_TEXT.replace("about 10", "about 25")}, "QA failed"),
        (
            {
                "media": {
                    "owner_requested": True,
                    "spec": {**SPEC, "nodes": [{"label": "Start with the boring rules, they cover a lot"}]},
                }
            },
            "media relevance rejected",
        ),
    ],
)
def test_a_failing_refresh_rolls_back_exactly(store, change, match):
    pid = legacy_post(store)
    refresh_click(store, pid)
    before = files(store, pid)
    with pytest.raises(StoreError, match=match):
        repackage.package(store, pid, {**PKG, **change})
    assert files(store, pid) == before
    assert [v["version"] for v in versions.listing(store, pid)] == [1]


def test_session_started_refresh_archives_and_rolls_back_its_own_archive(store):
    pid = legacy_post(store)  # no click: a session refreshes on its own
    before = files(store, pid)
    with pytest.raises(StoreError, match="QA failed"):
        repackage.package(store, pid, {**PKG, "text": NEW_TEXT.replace("about 10", "about 25")})
    assert files(store, pid) == before and versions.listing(store, pid) == []
    repackage.package(store, pid, PKG)
    [v1] = versions.listing(store, pid)
    assert v1["status"] == "replaced"


def test_archive_duplicate_stops_the_refresh(store):
    from lce import dupcheck

    pid = legacy_post(store)
    refresh_click(store, pid)
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
        ({**PKG, "angle_origin": "writer"}, "angle_origin"),
    ]:
        with pytest.raises(StoreError, match=match):
            repackage.package(store, pid, bad)


def test_skip_releases_the_slot_and_generates_nothing(store):
    pid = legacy_post(store)
    refresh_click(store, pid)
    out = decisions._skip(
        store,
        None,
        {
            "decision_id": "d-3",
            "action": "skip",
            "post_id": pid,
            "created_by": "owner@example.com",
            "payload": {"reason": "not this week"},
        },
    )
    post = store.load_post(pid)
    assert out == "skipped" and post["state"] == "REJECTED" and "refresh_request" not in post
    assert repackage.pending(store) == []
    with pytest.raises(StoreError):
        repackage.package(store, pid, PKG)  # nothing is regenerated after Skip


def test_refresh_never_publishes_and_the_gate_needs_a_new_check(store):
    pid = legacy_post(store)
    post = store.load_post(pid)
    post["state"] = "APPROVED"
    post["approval"] = {**post["approval"], "state": "approved", "approved_hash": post["content_hash"]}
    store.save_post(post)
    refresh_click(store, pid)
    rec = repackage.package(store, pid, PKG)
    post = store.load_post(pid)
    assert rec["approval_effect"] == "none"  # the click already discarded the approval
    assert versions.listing(store, pid)[0]["approval_state"] == "approved"
    assert post["state"] == "AWAITING_APPROVAL" and "approved_hash" not in post["approval"]
    assert not (store.post_dir(pid) / "publication.json").exists()
    assert cloud.load_delegation(store, pid) is None
    assert all(r["status"] != "current" for r in refresh.cloud_rows(store, [pid]))


def test_previous_versions_are_recoverable(store):
    pid = legacy_post(store)
    old_text = current_text(store, pid)
    refresh_click(store, pid)
    repackage.package(store, pid, PKG)
    versions.restore(store, pid, 1, by="session")
    post = store.load_post(pid)
    assert current_text(store, pid) == old_text and post["state"] == "HUMANIZED"
    assert "approval" not in post
    vs = versions.listing(store, pid)
    assert [(v["version"], v["status"]) for v in vs] == [(1, "rejected"), (2, "kept_before_restore")]


def test_cli_package_pending_and_versions(store, tmp_path, capsys):
    from lce.cli import main

    pid = legacy_post(store)
    refresh_click(store, pid)
    base = ["--data-dir", str(store.root)]
    assert main([*base, "refresh", "pending"]) == 0
    assert pid in capsys.readouterr().out
    (tmp_path / "post.md").write_text(NEW_TEXT)
    (tmp_path / "visual.yaml").write_text(yaml.safe_dump(SPEC))
    pkg = {**PKG, "media": {"spec": "visual.yaml", "owner_requested": True}, "text_file": "post.md"}
    pkg.pop("text")
    (tmp_path / "pkg.yaml").write_text(yaml.safe_dump(pkg))
    assert main([*base, "refresh", "package", pid, "--file", str(tmp_path / "pkg.yaml")]) == 0
    out = capsys.readouterr().out
    assert "previous version kept as v1" in out and "nothing was approved or published" in out
    with pytest.raises(SystemExit):  # "keep" no longer exists: Refresh always replaces
        main([*base, "refresh", "keep", pid, "--reason", "x"])
    assert main([*base, "versions", "list", pid]) == 0
    assert "v1" in capsys.readouterr().out


def test_snapshot_shows_versions_and_request(store):
    from lce.dashboard.snapshot import build_snapshot

    pid = legacy_post(store)
    refresh_click(store, pid)
    repackage.package(store, pid, PKG)
    refresh_click(store, pid, note="again", decision_id="d-2")
    view = next(p for p in build_snapshot(store, mode="real")["posts"] if p["post_id"] == pid)
    assert [v["version"] for v in view["versions"]] == [1, 2]
    assert view["versions"][1]["text"] == NEW_TEXT and view["versions"][1]["status"] == "rejected"
    assert view["refresh_request"]["note"] == "again" and view["state"] == "NEEDS_REVISION"


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
    refresh_click(store, pid)
    repackage.package(store, pid, PKG)
    fake = VersionCloud()
    out = cloud.sync_version_media(store, CloudClient("https://lce.example", lambda: "x", fake))
    assert out["uploaded"] == [f"{pid}/v1"] and fake.put[f"{pid}/1"]["sha256"] == old_sha


def test_a_skip_can_be_reopened_as_refresh_only_when_skipped_and_unpublished(store):
    pid = legacy_post(store)
    decisions._skip(
        store,
        None,
        {
            "decision_id": "d-3",
            "action": "skip",
            "post_id": pid,
            "created_by": "owner@example.com",
            "payload": {"reason": "i dont like it"},
        },
    )
    out = repackage.unskip_as_refresh(store, pid, by="owner via chat", note="i dont like it")
    post = store.load_post(pid)
    assert post["state"] == "NEEDS_REVISION" and "approval" not in post and out["rejected_version"] == 1
    assert versions.listing(store, pid)[0]["status"] == "rejected"
    assert "skip undone" in post["history"][-1]["note"]
    repackage.package(store, pid, PKG)  # the replacement can now be written
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"
    with pytest.raises(StoreError, match="only a post rejected by Skip"):
        repackage.unskip_as_refresh(store, pid, by="x")  # not skipped any more


def test_a_rejected_post_is_not_reopened(store):
    from lce.approval import reject

    pid = legacy_post(store)
    reject(store, pid, "off-topic")
    with pytest.raises(StoreError, match="only a post rejected by Skip"):
        repackage.unskip_as_refresh(store, pid, by="x")


def test_voice_gate_review_is_bound_to_the_new_text_and_never_carried_over(store):
    pid = legacy_post(store)
    refresh_click(store, pid)
    rev = {"natural_english": "pass", "owner_grounded_opinion": "insufficient",
           "owner_phrasing": "insufficient", "notes": "source-heavy"}
    repackage.package(store, pid, {**PKG, "voice_gate_review": rev})
    post = store.load_post(pid)
    assert post["voice_gate_review"]["content_hash"] == content_hash(current_text(store, pid))
    assert post["voice_gate_review"]["reviewer"] == "writer"
    refresh_click(store, pid, note="again", decision_id="d-2")
    third = {**PKG, "text": THIRD_TEXT, "media": {"spec": SPEC3, "owner_requested": True}}
    repackage.package(store, pid, third)
    assert "voice_gate_review" not in store.load_post(pid)
    with pytest.raises(StoreError, match="voice_gate_review"):
        repackage.package(store, pid, {**PKG, "voice_gate_review": {"natural_english": "great"}})
