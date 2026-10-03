"""LCE-043: a refreshed post gets a real, licensed image chosen by subject (Wikimedia
Commons API, rights and metadata checked), or text-only "no suitable licensed image";
a generated diagram is never the automatic choice."""

import hashlib
import json
import urllib.parse

import pytest
from test_repackage import PKG, THIRD_TEXT, legacy_post

from lce import commons, images, repackage, versions
from lce.posts import current_text
from lce.store import StoreError


def png_shade(shade):
    import tempfile
    from pathlib import Path

    from test_images import png

    with tempfile.TemporaryDirectory() as tmp:
        return png(Path(tmp) / "x.png", shade).read_bytes()


class FakeCommons:
    """The Commons API for a few fictional files: lookup by title, search, file download."""

    def __init__(self, files):
        self.files = {f["title"]: f for f in files}
        self.urls = []

    def _info(self, f):
        meta = {
            "LicenseShortName": {"value": f["license"]},
            "Artist": {"value": f.get("artist", "Jane Example")},
            "ImageDescription": {"value": f.get("description", "")},
            "Categories": {"value": "|".join(f.get("categories", []))},
            "AttributionRequired": {"value": "true" if f["license"].startswith("CC BY") else "false"},
        }
        name = f["title"].split(":", 1)[1]
        return {
            "url": f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{name}",
            "descriptionurl": f"https://commons.wikimedia.org/wiki/{f['title']}",
            "sha1": hashlib.sha1(f["data"]).hexdigest(),
            "mime": "image/png",
            "width": 1,
            "height": 1,  # noqa: S324
            "size": len(f["data"]),
            "thumburl": None,
            "extmetadata": meta,
        }

    def __call__(self, url):
        self.urls.append(url)
        if url.startswith(commons.API):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            if "titles" in q:
                f = self.files.get(q["titles"][0])
                return json.dumps(
                    {"query": {"pages": {"1": {"imageinfo": [self._info(f)]} if f else {"missing": ""}}}}
                ).encode()
            words = q["gsrsearch"][0].lower().split()
            hits = [
                f
                for f in self.files.values()
                if all(w in (f["title"] + f.get("description", "")).lower() for w in words)
            ]
            return json.dumps(
                {
                    "query": {
                        "pages": {
                            str(i): {"title": f["title"], "index": i, "imageinfo": [self._info(f)]}
                            for i, f in enumerate(hits)
                        }
                    }
                }
            ).encode()
        name = url.rsplit("/", 1)[1]
        return self.files["File:" + name]["data"]


SERVER_ROOM = {
    "title": "File:Server room racks.png",
    "license": "CC BY-SA 4.0",
    "description": "Racks of servers in a data center operations room",
    "categories": ["Data centers", "IT operations"],
    "data": png_shade(10),
}
NC_PHOTO = {
    "title": "File:Ops team at work.png",
    "license": "CC BY-NC 2.0",
    "description": "IT operations team triaging tickets",
    "categories": ["IT operations"],
    "data": png_shade(20),
}
OFF_TOPIC = {
    "title": "File:Sunset beach.png",
    "license": "CC0",
    "description": "A sunset over a beach",
    "categories": ["Beaches"],
    "data": png_shade(30),
}
PD_DESK = {
    "title": "File:Service desk ticket queue.png",
    "license": "Public domain",
    "description": "A service desk ticket queue on a monitor",
    "categories": ["Help desks", "IT operations"],
    "data": png_shade(40),
}

COMMONS = {
    "subject": "IT operations teams routing service tickets",
    "concept": "the real operations floor where ticket routing rules are applied",
    "subject_terms": ["IT operations", "service desk", "data center"],
    "relevance_reason": "shows the operations environment the post is about, not a restatement of its text",
    "relation": "a real operations room, the setting where the routing rules in the post run",
    "alt_text": "Photo of server racks in a data center operations room, the setting of IT ticket routing",
}


@pytest.fixture
def fake(monkeypatch):
    f = FakeCommons([NC_PHOTO, OFF_TOPIC, SERVER_ROOM, PD_DESK])
    monkeypatch.setattr(repackage, "_TRANSPORT", f)
    return f


def requested(store):
    pid = legacy_post(store)
    repackage.request(store, pid, by="cloud-access:owner@example.com", note="i dont like it")
    return pid


def test_refresh_attaches_a_real_licensed_image_chosen_by_subject(store, fake):
    pid = requested(store)
    pkg = {
        **PKG,
        "media": {
            "commons": {
                **COMMONS,
                "candidates": ["File:Ops team at work.png", "File:Sunset beach.png"],
                "search": ["server room"],
            }
        },
    }
    rec = repackage.package(store, pid, pkg)
    doc = images.load(store, pid)
    assert doc["kind"] == "source_image" and doc["provenance"]["title"] == "File:Server room racks.png"
    prov = doc["provenance"]
    assert prov["license"] == "CC BY-SA 4.0" and prov["usage"] == "licensed"
    assert prov["source_url"] == "https://commons.wikimedia.org/wiki/File:Server room racks.png"
    assert prov["creator"] == "Jane Example" and prov["attribution_required"] is True
    assert prov["original_sha1"] == hashlib.sha1(SERVER_ROOM["data"]).hexdigest()  # noqa: S324
    assert doc["sha256"] == hashlib.sha256(SERVER_ROOM["data"]).hexdigest()
    # every candidate is recorded with why it was refused or chosen
    tried = {t["title"]: t for t in doc["selection"]["tried"]}
    assert "does not allow reuse" in tried["File:Ops team at work.png"]["why"]
    assert "name none of the subject terms" in tried["File:Sunset beach.png"]["why"]
    assert tried["File:Server room racks.png"]["outcome"] == "selected"
    sem = doc["media_relevance"]["semantic"]
    assert sem["matched_terms"] == ["IT operations", "data center"] and sem["metadata_checked"] is True
    assert (
        doc["media_relevance"]["visual_type"] == "photo"
        and doc["media_relevance"]["media_decision"] == "accepted"
    )
    # the licence asks for credit: it is in the post that will be approved
    assert (
        current_text(store, pid).rstrip().endswith("Image: Jane Example, CC BY-SA 4.0, via Wikimedia Commons")
    )
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"
    assert "Wikimedia Commons" in rec["media"]["note"]
    assert images.check(store, pid)[0] == []
    hosts = ("https://commons.wikimedia.org/", "https://upload.wikimedia.org/")
    assert all(u.startswith(hosts) for u in fake.urls)


def test_no_suitable_licensed_image_means_text_only_never_a_diagram(store, fake):
    pid = requested(store)
    pkg = {
        **PKG,
        "media": {
            "commons": {
                **COMMONS,
                "candidates": [
                    "File:Ops team at work.png",
                    "File:Sunset beach.png",
                    "File:Does not exist.png",
                ],
            }
        },
    }
    repackage.package(store, pid, pkg)
    doc = images.load(store, pid)
    assert doc["kind"] == "none" and doc["text_only_reason"] == "no_suitable_licensed_image"
    assert len(doc["selection"]["tried"]) == 3 and doc["selection"]["selected"] is None
    assert "3 Wikimedia Commons file(s) checked" in doc["rationale"]
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"


def test_a_later_refresh_never_reuses_an_earlier_versions_file(store, fake):
    pid = requested(store)
    repackage.package(store, pid, {**PKG, "media": {"commons": {**COMMONS, "search": ["server"]}}})
    assert versions.listing(store, pid)[-1]["status"] == "rejected"
    repackage.request(store, pid, by="owner", note="again")
    assert versions.listing(store, pid)[-1]["media"]["source_title"] == "File:Server room racks.png"
    third = {
        **PKG,
        "text": THIRD_TEXT,
        "media": {
            "commons": {
                **COMMONS,
                "search": ["server"],
                "candidates": ["File:Server room racks.png", "File:Service desk ticket queue.png"],
            }
        },
    }
    repackage.package(store, pid, third)
    doc = images.load(store, pid)
    assert doc["provenance"]["title"] == "File:Service desk ticket queue.png"
    assert doc["provenance"]["usage"] == "public_domain"
    tried = {t["title"]: t["why"] for t in doc["selection"]["tried"] if t["outcome"] == "refused"}
    assert tried["File:Server room racks.png"] == "an earlier version of this post used this file"


def test_a_generated_diagram_needs_the_owners_request(store, fake):
    pid = requested(store)
    from test_media_pipeline import SPEC

    with pytest.raises(StoreError, match="only when the owner asked"):
        repackage.package(store, pid, {**PKG, "media": {"spec": SPEC}})
    assert store.load_post(pid)["state"] == "NEEDS_REVISION"


@pytest.mark.parametrize("missing", ["subject_terms", "relevance_reason", "alt_text"])
def test_commons_media_must_state_the_subject_and_why(store, fake, missing):
    pid = requested(store)
    spec = {k: v for k, v in COMMONS.items() if k != missing} | {"search": ["server"]}
    with pytest.raises(StoreError, match=missing):
        repackage.package(store, pid, {**PKG, "media": {"commons": spec}})
    assert store.load_post(pid)["state"] == "NEEDS_REVISION"
