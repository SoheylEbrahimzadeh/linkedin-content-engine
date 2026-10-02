"""LCE-037: the humanization step is explicit and tied to the voice profile files."""

import json

import pytest
import yaml
from conftest import GOOD_POST, selected_post
from test_images import decide_diagram

from lce import cloud, voice
from lce.cloud import CloudClient, CloudResponse
from lce.posts import save_draft, save_humanized, set_objective
from lce.qa import run_checks
from lce.rules import ready_ruleset
from lce.store import StoreError

OBJECTIVES = [{"id": "demonstrate-expertise", "label": "Demonstrate practical expertise",
               "derived_from": "profile.goals"},
              {"id": "share-lesson", "label": "Share a lesson learned", "derived_from": "brand.themes"}]


def with_voice(store, **extra):
    doc = store.voice()
    doc.update({"version": 2, "objectives": OBJECTIVES, **extra,
                "individual_voice": "One person speaking from their own work, never a company page."})
    store.write_doc(store.voice_path, "voice", doc)
    return doc


def findings(store, post, text):
    return run_checks(text, rules=ready_ruleset("en"), voice=store.voice(), profile=store.profile(),
                      post=post, stories=store.stories(), denylist=[], brand=store.brand())


def test_humanized_text_records_the_profile_versions_it_was_written_against(store):
    with_voice(store)
    pid = selected_post(store)
    set_objective(store, pid, "demonstrate-expertise")
    save_draft(store, pid, GOOD_POST)
    post = save_humanized(store, pid, GOOD_POST)
    hz = post["humanization"]
    assert hz["source"] == "session" and hz["voice_version"] == 2
    assert hz["objective"] == "demonstrate-expertise"
    for key, value in voice.profile_hashes(store).items():
        assert hz[key] == value and len(value) == 64
    assert hz["checklist"]["review"] == 2           # tone and positioning are never claimed
    assert set(hz["checklist"]) == {"passed", "failed", "review"}


def test_a_session_must_set_the_objective_first(store):
    with_voice(store)
    pid = selected_post(store)
    save_draft(store, pid, GOOD_POST)
    with pytest.raises(StoreError, match="objective"):
        save_humanized(store, pid, GOOD_POST)
    with pytest.raises(StoreError, match="unknown objective"):
        set_objective(store, pid, "go-viral")
    set_objective(store, pid, "share-lesson")
    assert save_humanized(store, pid, GOOD_POST)["humanization"]["objective"] == "share-lesson"
    entry = [e for e in store.plan()["entries"] if e.get("draft_ref") == pid]
    assert all(e["objective"] == "share-lesson" for e in entry)


def test_owner_edits_are_recorded_as_owner_edits(store):
    with_voice(store)
    pid = selected_post(store)
    save_draft(store, pid, GOOD_POST)
    post = save_humanized(store, pid, GOOD_POST, source="owner_edit", by="cloud-access:owner@example.com")
    assert post["humanization"]["source"] == "owner_edit"
    assert post["humanization"]["by"] == "cloud-access:owner@example.com"


def test_checklist_reports_failed_rules_and_corporate_voice(store):
    with_voice(store)
    pid = selected_post(store)
    post = store.load_post(pid)
    text = GOOD_POST + "\n\nWe at Acme help our clients win.\n\n#a #b #c #d #e"
    fs = findings(store, post, text)
    codes = {f.code for f in fs}
    assert "voice.corporate_voice" in codes and "voice.objective_missing" in codes
    items = {i["rule"]: i for i in voice.checklist(store, post, fs)}
    assert items["individual_voice"]["status"] == "failed"
    assert items["hashtags"]["status"] == "failed"
    assert items["objective"]["status"] == "failed"
    assert items["tone"]["status"] == items["positioning"]["status"] == "review"
    post["objective"] = "nope"
    assert "voice.objective_unknown" in {f.code for f in findings(store, post, GOOD_POST)}


def test_no_objectives_in_voice_keeps_old_behaviour(store):
    pid = selected_post(store)
    save_draft(store, pid, GOOD_POST)
    post = save_humanized(store, pid, GOOD_POST)
    assert "objective" not in {i["rule"] for i in voice.checklist(store, post, [])}


def test_snapshot_shows_whether_the_profile_changed_since_humanization(store):
    from lce.dashboard.snapshot import build_snapshot

    with_voice(store)
    pid = selected_post(store)
    set_objective(store, pid, "demonstrate-expertise")
    save_draft(store, pid, GOOD_POST)
    save_humanized(store, pid, GOOD_POST)
    snap = build_snapshot(store, mode="real")
    view = next(p for p in snap["posts"] if p["post_id"] == pid)
    assert view["objective"] == "demonstrate-expertise"
    assert view["humanization"]["profile_current"] is True
    assert snap["voice"]["version"] == 2 and snap["voice"]["objectives"] == OBJECTIVES
    assert any(r["rule"] == "tone" and r["machine_checkable"] is False for r in snap["voice"]["rules"])
    with_voice(store, formality="formal")
    view = next(p for p in build_snapshot(store, mode="real")["posts"] if p["post_id"] == pid)
    assert view["humanization"]["profile_current"] is False


def test_humanize_check_prints_the_voice_checklist(store, capsys):
    from lce import cli

    with_voice(store)
    pid = selected_post(store)
    save_draft(store, pid, GOOD_POST)
    cli.main(["--data-dir", str(store.root), "humanize", "check", pid])
    out = capsys.readouterr().out
    assert "Voice profile checklist" in out and "owner review" in out


class MediaCloud:
    def __init__(self, have=None):
        self.have, self.put = dict(have or {}), {}

    def request(self, method, url, headers, body):
        path = url.split("/api", 1)[1]
        if method == "GET" and path == "/preview-media":
            return CloudResponse(200, {"media": [{"post_id": k, "sha256": v} for k, v in self.have.items()]})
        if method == "PUT" and path.startswith("/preview-media/"):
            data = json.loads(body)
            self.put[path.split("/")[2]] = data
            self.have[path.split("/")[2]] = data["sha256"]
            return CloudResponse(200, {"stored": True})
        return CloudResponse(404, {"error": "not found"})


def test_recorded_images_are_uploaded_for_preview_once(store, tmp_path):
    import base64
    import hashlib

    pid = selected_post(store)
    save_draft(store, pid, GOOD_POST)
    save_humanized(store, pid, GOOD_POST)
    decide_diagram(store, pid, tmp_path)
    fake = MediaCloud()
    client = CloudClient("https://lce.example", lambda: "fake.access.jwt", fake)
    out = cloud.sync_preview_media(store, client)
    assert out["uploaded"] == [pid]
    data = base64.b64decode(fake.put[pid]["data_base64"])
    assert hashlib.sha256(data).hexdigest() == fake.put[pid]["sha256"]
    assert cloud.sync_preview_media(store, client)["uploaded"] == []      # unchanged: not sent again


def test_large_images_are_skipped_not_faked(store, tmp_path, monkeypatch):
    pid = selected_post(store)
    save_draft(store, pid, GOOD_POST)
    save_humanized(store, pid, GOOD_POST)
    decide_diagram(store, pid, tmp_path)
    monkeypatch.setattr(cloud, "MAX_PREVIEW_BYTES", 10)
    out = cloud.sync_preview_media(store, CloudClient("https://lce.example", lambda: "x", MediaCloud()))
    assert out["uploaded"] == [] and out["skipped"][0][0] == pid


def test_voice_schema_accepts_sources_and_review(store):
    doc = with_voice(store, sources={"tone": "intake", "technical_depth": "derived: profile.audience"},
                     review={"status": "pending_owner_review", "fields": ["technical_depth"]},
                     point_of_view={"person": "first", "first_person": "experience only from PUBLIC stories"})
    assert yaml.safe_load(store.voice_path.read_text())["review"]["status"] == doc["review"]["status"]
