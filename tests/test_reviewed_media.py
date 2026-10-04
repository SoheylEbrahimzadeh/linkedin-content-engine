"""LCE-044: real images are chosen after looking at them, from several licensed sources
(Commons, Openverse); only the media of the current candidate changes."""

import hashlib
import json
import urllib.parse

import pytest
import yaml
from test_real_media import COMMONS, PD_DESK, SC, SERVER_ROOM, FakeCommons, png_shade, requested
from test_repackage import PKG

from lce import images, media_search, repackage, versions
from lce.posts import current_text
from lce.store import StoreError

ROBOT = {
    "id": "0d4a3c3e-1111-4222-8333-944455556666",
    "title": "Industrial robot arms on an automated production line",
    "creator": "Example Photographer",
    "license": "by",
    "license_version": "2.0",
    "license_url": "https://creativecommons.org/licenses/by/2.0/",
    "foreign_landing_url": "https://www.flickr.com/photos/example/123",
    "url": "https://live.staticflickr.com/1/123_robot.png",
    "provider": "flickr",
    "tags": [{"name": "automation"}, {"name": "robot"}],
    "data": png_shade(70),
}
NC = {**ROBOT, "id": "0d4a3c3e-2222-4222-8333-944455556666", "license": "by-nc", "title": "NC photo"}


class FakeSources(FakeCommons):
    def __init__(self, files, ov):
        super().__init__(files)
        self.ov = {o["id"]: o for o in ov}

    def _ov(self, o):
        return {k: v for k, v in o.items() if k != "data"} | {
            "thumbnail": f"{media_search.OPENVERSE}{o['id']}/thumb/"
        }

    def __call__(self, url):
        if url.startswith(media_search.OPENVERSE):
            self.urls.append(url)
            path = urllib.parse.urlparse(url).path.rstrip("/").split("/")
            if path[-1] == "thumb":
                return self.ov[path[-2]]["data"]
            if path[-1] in self.ov:
                return json.dumps(self._ov(self.ov[path[-1]])).encode()
            return json.dumps({"results": [self._ov(o) for o in self.ov.values()]}).encode()
        if url.startswith("https://live.staticflickr.com/"):
            self.urls.append(url)
            return next(o["data"] for o in self.ov.values() if o["url"] == url)
        return super().__call__(url)


@pytest.fixture
def sources(monkeypatch):
    f = FakeSources([SERVER_ROOM, PD_DESK], [ROBOT, NC])
    monkeypatch.setattr(repackage, "_TRANSPORT", f)
    return f


REVIEW = {
    "subject": "AI automation inside business operations",
    "concept": "automation that acts on its own inside a production process",
    "depicts": "Several industrial robot arms working along an automated production line in a factory hall",
    "why_relevant": "Shows machines carrying out multi-step work without a person at each step, "
    "the real-agent behaviour the post contrasts with relabelled assistants",
    "alt_text": "Photo of industrial robot arms working along an automated production line in a factory",
    "relation": "real automation acting on its own, the standard the post sets for an agent",
    "reviewed_by": "claude-code session (looked at the preview)",
    "association": "same_subject_licensed",
    "why_belongs_to_source": "the report is about automation on factory floors; this photo shows that floor",
    "why_legal": "CC BY 2.0 at the source (Flickr), credit line added to the post as the licence requires",
}


def test_collect_searches_commons_and_openverse_and_saves_previews(tmp_path, sources):
    rec = media_search.collect({"subject": "x", "queries": ["server", "robot"]}, tmp_path, transport=sources)
    by = {(c["source"], c["id"]): c for c in rec["candidates"]}
    assert ("openverse", ROBOT["id"]) in by and ("commons", SERVER_ROOM["title"]) in by
    robot = by[("openverse", ROBOT["id"])]
    assert (
        robot["license"] == "CC BY 2.0"
        and robot["reusable"]
        and robot["landing_url"].startswith("https://www.flickr.com/")
    )
    assert (tmp_path / robot["preview_file"]).read_bytes() == ROBOT["data"]
    nc = by[("openverse", NC["id"])]
    assert nc["reusable"] is False and "preview_file" not in nc
    assert set(rec["sources_not_searched"]) == {"unsplash", "pexels"}
    saved = yaml.safe_load((tmp_path / "candidates.yaml").read_text())
    assert len(saved["candidates"]) == len(rec["candidates"])


def test_media_only_replacement_with_a_reviewed_openverse_image(store, sources):
    pid = requested(store)
    repackage.package(
        store,
        pid,
        {**PKG, "media": {"source_check": SC, "commons": {**COMMONS, "candidates": [SERVER_ROOM["title"]]}}},
    )
    text_before = current_text(store, pid).split("\n\nImage:")[0].rstrip()
    sel = {
        **REVIEW,
        "source": "openverse",
        "id": ROBOT["id"],
        "reviewed": [
            {"title": "File:Server room racks.png", "outcome": "refused", "why": "decorative racks"}
        ],
    }
    repackage.replace_media(
        store, pid, {"source_check": SC, "reviewed": sel}, reason="the old image was weak", by="session"
    )
    doc = images.load(store, pid)
    prov = doc["provenance"]
    assert prov["source_id"] == f"openverse:{ROBOT['id']}" and prov["license"] == "CC BY 2.0"
    assert prov["source_url"] == ROBOT["foreign_landing_url"] and prov["creator"] == "Example Photographer"
    assert prov["attribution"] == "Image: Example Photographer, CC BY 2.0, via flickr"
    assert doc["sha256"] == hashlib.sha256(ROBOT["data"]).hexdigest()
    sem = doc["media_relevance"]["semantic"]
    assert (
        sem["depicts"].startswith("Several industrial robot arms")
        and sem["method"] == "visual review of the candidate image"
    )
    assert doc["selection"]["tried"][-1]["outcome"] == "selected"
    text = current_text(store, pid)
    assert text.split("\n\nImage:")[0].rstrip() == text_before
    assert "Image: Example Photographer, CC BY 2.0, via flickr" in text and "Jane Example" not in text
    assert versions.listing(store, pid)[-1]["status"] == "replaced"
    assert images.check(store, pid)[0] == []


@pytest.mark.parametrize(
    "change, match",
    [
        ({"id": NC["id"]}, "not reusable"),
        ({"depicts": "robots"}, "depicts"),
        ({"reviewed_by": ""}, "reviewed_by"),
    ],
)
def test_a_reviewed_selection_is_refused_without_rights_or_a_real_review(store, sources, change, match):
    pid = requested(store)
    repackage.package(
        store,
        pid,
        {**PKG, "media": {"source_check": SC, "commons": {**COMMONS, "candidates": [SERVER_ROOM["title"]]}}},
    )
    before = images.load(store, pid)["sha256"]
    with pytest.raises(StoreError, match=match):
        repackage.replace_media(
            store,
            pid,
            {"source_check": SC, "reviewed": {**REVIEW, "source": "openverse", "id": ROBOT["id"], **change}},
            reason="x",
            by="session",
        )
    assert images.load(store, pid)["sha256"] == before


# ── owner policy: the cited source's own image, credited (owner accepts the copyright risk) ──
PAGE = "https://news.example.com/2026/agents"
IMG = "https://news.example.com/img/agents-hero.png"
PAGE_HTML = (f'<html><head><meta property="og:image" content="{IMG}"></head>'
             '<body><p>(c) 2026 Example News. All rights reserved.</p></body></html>').encode()
SOURCE_SEL = {
    **REVIEW, "source": "source_page", "page_url": PAGE, "image_url": IMG, "publisher": "Example News",
    "association": "source_visual",
    "why_belongs_to_source": "the hero image of the very article the post cites, chosen by its publisher",
    "why_legal": "no licence; the owner decided to post source images with a credit line (owner policy)",
}
SOURCE_CHECK = {
    "status": "source_visual_used", "source_url": PAGE,
    "evidence": "the article's own Open Graph hero image; page states all rights reserved; owner policy",
}


def page_transport(url):
    if url == PAGE:
        return PAGE_HTML
    if url == IMG:
        return png_shade(90)
    return (404, b"")


def _policy(store, value):
    s = store.settings()
    s.setdefault("visuals", {})["source_image_policy"] = value
    store.write_doc(store.settings_path, "settings", s)


def test_source_page_image_needs_the_owner_policy(store):
    from conftest import awaiting_post

    pid = awaiting_post(store)
    with pytest.raises(StoreError, match="owner_accepts_copyright_risk"):
        media_search.attach_reviewed(store, pid, SOURCE_SEL, transport=page_transport,
                                     source_check=SOURCE_CHECK)


def test_source_page_image_is_attached_with_credit_when_the_owner_accepts_the_risk(store):
    from conftest import awaiting_post

    pid = awaiting_post(store)
    _policy(store, "owner_accepts_copyright_risk")
    doc = media_search.attach_reviewed(store, pid, SOURCE_SEL, transport=page_transport,
                                       source_check=SOURCE_CHECK)
    prov = doc["provenance"]
    assert prov["origin"] == "source_publication" and prov["usage"] == "owner_accepted_risk"
    assert prov["attribution_required"] is True and prov["attribution"] == "Image: Example News"
    assert prov["source_url"] == PAGE and prov["retrieved_url"] == IMG
    errors, _ = images.check(store, pid)
    assert not [e for e in errors if "license" in e or "usage" in e]


def test_source_page_image_must_be_published_on_the_cited_page(store):
    from conftest import awaiting_post

    pid = awaiting_post(store)
    _policy(store, "owner_accepts_copyright_risk")
    other = {**SOURCE_SEL, "image_url": "https://news.example.com/img/unrelated.png"}
    with pytest.raises(StoreError, match="not published on the cited source page"):
        media_search.attach_reviewed(store, pid, other, transport=page_transport, source_check=SOURCE_CHECK)
