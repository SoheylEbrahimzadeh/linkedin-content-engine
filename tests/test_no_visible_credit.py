"""LCE-052 (owner rule 2026-10-06): a selected image is published without a visible source
label in the post (no "Image: CIO.com", "Photo: ...", "Credit: ...", "Image source: ...").
The attribution stays in the image record for provenance, auditing and copyright tracking."""

import pytest
from test_real_media import SC, png_shade, requested
from test_repackage import PKG
from test_reviewed_media import PAGE, SOURCE_CHECK, SOURCE_SEL, _policy, page_transport

from lce import images, repackage
from lce.credit import find_image_labels, image_credit_names, strip_image_labels
from lce.dashboard.snapshot import build_snapshot
from lce.posts import current_text
from lce.revise import autofix


@pytest.mark.parametrize("line", ["Image: CIO.com", "image: The New Stack", "Photo: Jane Doe",
                                  "Photos: Unsplash", "Image source: CIO.com", "Image credit: InfoQ",
                                  "Photo credit: Reuters", "Credit: CIO.com", "Picture: Example",
                                  "Illustration: Example Studio", "Header image by: InfoQ",
                                  # LCE-053: source label lines are credit lines too
                                  "Source: https://example.org/report", "Sources: Gartner, IDC",
                                  "Via: CIO.com"])
def test_every_kind_of_image_label_is_recognised(line):
    assert find_image_labels(f"Body.\n\n{line}\n\n#AI") == [line]


@pytest.mark.parametrize("line", ["The image of AI in IT is changing.", "Photo booths are back.",
                                  "The source of the problem was a config change.",
                                  "Via the team, we heard it first.",
                                  "Credit where it's due, the team found it first."])
def test_text_citations_and_prose_are_kept(line):
    assert find_image_labels(f"Body.\n\n{line}\n") == []


def test_a_bare_via_line_naming_the_image_itself_is_a_label():
    names = image_credit_names({"credit": "CIO.com", "attribution": "Image: CIO.com"})
    assert find_image_labels("Body.\n\nVia CIO.com\n", names) == ["Via CIO.com"]
    assert find_image_labels("Body.\n\nVia Gartner\n", names) == []


def test_stripping_keeps_the_text_and_hashtags_tidy():
    text = "Hook.\n\nBody line.\n\nImage: CIO.com\n\n#DigitalTransformation #CIO\n"
    out, removed = strip_image_labels(text)
    assert out == "Hook.\n\nBody line.\n\n#DigitalTransformation #CIO\n" and removed == ["Image: CIO.com"]


def test_the_writing_gate_autofix_removes_a_label_a_writer_typed():
    text, changes = autofix("It is old.\n\nImage: CIO.com\n\n#CIO\n")
    assert "Image:" not in text and text.startswith("It's old.")
    assert "removed image label: Image: CIO.com" in changes


def test_selected_source_image_is_published_without_a_label_and_keeps_its_provenance(store):
    """End to end through the real package path: owner policy source image (the CIO.com case)."""
    pid = requested(store)
    _policy(store, "owner_accepts_copyright_risk")
    old = repackage._TRANSPORT
    repackage._TRANSPORT = page_transport
    try:
        # the writer even typed a label: it must not survive into the approved text
        text = PKG["text"].rstrip() + "\n\nImage: Example News\n"
        rec = repackage.package(store, pid, {**PKG, "text": text,
                                             "media": {"source_check": SOURCE_CHECK, "reviewed": SOURCE_SEL}})
    finally:
        repackage._TRANSPORT = old
    final = current_text(store, pid)
    assert find_image_labels(final) == [] and "Example News" not in final
    post = store.load_post(pid)
    assert post["state"] == "AWAITING_APPROVAL" and rec["approval"] == "pending"
    # the approval artifact (what the owner approves, and what the cloud publishes) has no label
    assert "Image: Example News" not in (store.post_dir(pid) / "APPROVAL.md").read_text("utf-8")
    # provenance is intact for auditing and copyright tracking
    prov = images.load(store, pid)["provenance"]
    assert prov["attribution"] == "Image: Example News" and prov["attribution_required"] is True
    assert prov["attribution_display"] == "metadata_only"             # LCE-053: explicit in the record
    assert prov["source_url"] == PAGE and prov["credit"] == "Example News"
    assert images.check(store, pid)[0] == []
    snap = next(p for p in build_snapshot(store, mode="real")["posts"] if p["post_id"] == pid)
    assert "Image:" not in snap["text"]


def test_qa_refuses_a_label_in_pipeline_text_but_only_warns_on_the_owners_own_edit(store):
    from conftest import awaiting_post

    from lce.posts import reopen, save_humanized
    from lce.qa import run_qa

    pid = awaiting_post(store)
    reopen(store, pid, "test")
    body = current_text(store, pid).rstrip()
    save_humanized(store, pid, body + "\n\nPhoto: Example\n", source="session", by="writer")
    rep = run_qa(store, pid)
    assert "media.visible_credit" in {e["code"] for e in rep["errors"]}
    save_humanized(store, pid, body + "\n\nPhoto: Example\n", source="owner_edit", by="owner")
    rep = run_qa(store, pid)
    assert "media.visible_credit" in {w["code"] for w in rep["warnings"]}


def test_png_helper_is_a_real_image():
    assert png_shade(10)[:8] == b"\x89PNG\r\n\x1a\n" and SC["status"]


def test_attribution_display_policy_is_explicit_and_metadata_only(store):
    """LCE-053: the settings schema names where attribution goes; only metadata_only is valid."""
    from lce.validate import validate_doc

    s = store.settings()
    s.setdefault("visuals", {})["attribution_display"] = "metadata_only"
    assert validate_doc("settings", s) == []
    s["visuals"]["attribution_display"] = "in_post"
    assert validate_doc("settings", s) != []
