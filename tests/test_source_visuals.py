"""LCE-046: the post's own source is inspected for its visual first; a visual is never assumed
reusable because it is visible; the decision travels with the media."""

import pytest
from test_real_media import PKG, SC, png_shade, requested
from test_reviewed_media import REVIEW, ROBOT, FakeSources

from lce import images, repackage, source_visuals, versions
from lce.posts import current_text
from lce.store import StoreError

PAGE = b"""<html><head><title>Analyst firm predicts agents in 40% of apps</title>
<meta property="og:image" content="/img/hero.png">
<meta property="og:image:alt" content="Five stages of agentic AI">
</head><body><img src="/assets/logo.svg" alt="Firm logo">
<figure><img src="https://cdn.example.com/fig1.png"
 alt="Figure 1: Five stages in the evolution of agentic AI">
<figcaption>Figure 1: Five Stages in the Evolution of Agentic AI in Enterprise Software</figcaption></figure>
<p>&copy; 2025 Example Firm, Inc. All rights reserved. This publication may not be reproduced or distributed
in any form without the firm's prior written permission.</p></body></html>"""
CC_PAGE = b"""<html><body><figure><img src="/chart.png" alt="Chart"></figure>
<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a></body></html>"""


def transport(pages):
    def get(url):
        for k, v in pages.items():
            if url.startswith(k):
                return v
        return png_shade(5)

    return get


def test_inspect_records_the_sources_visuals_and_that_reuse_is_not_permitted(tmp_path):
    rec = source_visuals.inspect(
        "https://firm.example/press/1", tmp_path, transport({"https://firm.example/press/1": PAGE})
    )
    kinds = {v["kind"]: v for v in rec["visuals"]}
    assert (
        kinds["og:image"]["src"] == "https://firm.example/img/hero.png"
        and kinds["og:image"]["alt"] == "Five stages of agentic AI"
    )
    fig = kinds["figure_img"]
    assert fig["alt"].startswith("Figure 1") and (tmp_path / fig["inspection_copy"]).exists()
    assert any(v.get("skipped") for v in rec["visuals"])  # the logo is site chrome
    assert rec["figure_captions"] == [
        "Figure 1: Five Stages in the Evolution of Agentic AI in Enterprise Software"
    ]
    sig = {r["kind"] for r in rec["rights_signals"]}
    assert {"copyright_notice", "no_reproduction"} <= sig
    assert rec["reuse_permitted_by_page"] is False and "copyrighted" in rec["assessment"]


def test_inspect_notices_an_open_licence(tmp_path):
    rec = source_visuals.inspect(
        "https://open.example/r", tmp_path, transport({"https://open.example/r": CC_PAGE})
    )
    assert rec["reuse_permitted_by_page"] is True


def test_media_without_a_source_check_is_refused(store, monkeypatch):
    pid = requested(store)
    with pytest.raises(StoreError, match="source_check"):
        repackage.package(
            store,
            pid,
            {**PKG, "media": {"text_only": {"reason": "no_suitable_licensed_image", "rationale": "x"}}},
        )
    with pytest.raises(StoreError, match="evidence"):
        repackage.package(
            store,
            pid,
            {
                **PKG,
                "media": {
                    "source_check": {**SC, "evidence": "no"},
                    "text_only": {"reason": "no_suitable_licensed_image", "rationale": "x"},
                },
            },
        )


def test_a_source_visual_needs_used_status_and_every_choice_says_why_it_belongs(store, monkeypatch):
    monkeypatch.setattr(repackage, "_TRANSPORT", FakeSources([], [ROBOT]))
    pid = requested(store)
    sel = {**REVIEW, "source": "openverse", "id": ROBOT["id"], "association": "source_visual"}
    with pytest.raises(StoreError, match="source_visual_used"):
        repackage.package(store, pid, {**PKG, "media": {"source_check": SC, "reviewed": sel}})
    with pytest.raises(StoreError, match="why_legal"):
        repackage.package(
            store,
            pid,
            {
                **PKG,
                "media": {
                    "source_check": SC,
                    "reviewed": {**sel, "association": "same_subject_licensed", "why_legal": ""},
                },
            },
        )


def test_text_only_after_the_source_check_keeps_the_text_and_records_the_decision(store, monkeypatch):
    monkeypatch.setattr(repackage, "_TRANSPORT", FakeSources([], [ROBOT]))
    pid = requested(store)
    sel = {**REVIEW, "source": "openverse", "id": ROBOT["id"]}
    repackage.package(store, pid, {**PKG, "media": {"source_check": SC, "reviewed": sel}})
    before = current_text(store, pid).split("\n\nImage:")[0].rstrip()
    repackage.replace_media(
        store,
        pid,
        {
            "source_check": SC,
            "text_only": {
                "reason": "no_suitable_licensed_image",
                "rationale": "the source's own figure is copyrighted; no licensed asset tied to the source",
            },
        },
        reason="the photo was not from the source",
        by="session",
    )
    doc = images.load(store, pid)
    assert doc["kind"] == "none" and doc["text_only_reason"] == "no_suitable_licensed_image"
    assert doc["source_visual"]["status"] == "source_visual_unavailable_or_restricted"
    assert current_text(store, pid).rstrip() == before  # the old credit line goes, the text stays
    assert versions.listing(store, pid)[-1]["status"] == "replaced"


def test_a_refusing_publisher_is_read_through_the_internet_archive_and_says_so(tmp_path):
    url = "https://firm.example/press/1"

    def get(u):
        if u == url:
            return (403, b"")
        if u.startswith(source_visuals.ARCHIVE + "id_/"):
            return PAGE
        if u.startswith(source_visuals.ARCHIVE + "im_/"):
            return png_shade(9)
        return (403, b"")

    rec = source_visuals.inspect(url, tmp_path, get)
    assert rec["http_status"] == 403 and rec["archive_status"] == 200
    assert rec["via"].startswith("Internet Archive capture")
    fig = next(v for v in rec["visuals"] if v["kind"] == "figure_img")
    assert fig["via"] == "Internet Archive" and fig["inspection_copy"]
    assert rec["reuse_permitted_by_page"] is False
