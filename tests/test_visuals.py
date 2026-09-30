"""Charts generated from the post's own recorded claims (fictional data)."""

import pytest
from test_images import checked_post

from lce import images, visuals
from lce.store import StoreError

pytest.importorskip("matplotlib")


def with_claims(store, pid, claims):
    post = store.load_post(pid)
    post["claims"] = claims
    store.save_post(post)


def test_figures_keep_the_unit_as_written():
    f = visuals._figure({"text": "58% of small teams run no review", "source_url": "https://example.org/x"})
    assert f["number"] == "58%" and f["percent"] == 58.0 and f["source"] == "example.org"
    g = visuals._figure({"text": "Reviews took 35 minutes on average", "source_url": "https://example.net/y"})
    assert g["number"] == "35 minutes" and g["percent"] is None
    assert visuals._figure({"text": "No figures here at all", "source_url": "https://example.org"}) is None


def test_chart_is_recorded_with_provenance_and_passes_the_check(store):
    pid = checked_post(store)
    with_claims(store, pid, [{"text": "58% of small teams run no review after minor incidents",
                              "source_url": "https://example.org/survey"}])
    doc = visuals.chart(store, pid)
    assert doc["kind"] == "chart" and doc["provenance"]["origin"] == "own_creation"
    assert "matplotlib" in doc["provenance"]["generation"]["method"]
    assert "58%" in doc["relation"] and "example.org" in doc["relation"]
    assert "58% of small teams run no review after minor incidents" in doc["alt_text"]
    assert (store.post_dir(pid) / "image.png").read_bytes().startswith(b"\x89PNG")
    assert images.check(store, pid)[0] == []
    assert images.dimensions((store.post_dir(pid) / "image.png").read_bytes(), ".png") == (1200, 1200)


def test_nothing_true_to_show_means_no_chart(store):
    pid = checked_post(store)
    with_claims(store, pid, [{"text": "Rules are easier to explain", "source_url": "https://example.org"}])
    with pytest.raises(StoreError, match="nothing true to show"):
        visuals.chart(store, pid)
    with pytest.raises(StoreError, match="out of range"):
        visuals.chart(store, pid, claims=[5])
    assert images.load(store, pid) is None
