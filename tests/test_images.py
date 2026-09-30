"""Image stage: decision, provenance rules, approval binding, publishing refusal (fictional data)."""

import struct
import zlib

import pytest
from conftest import GOOD_POST, selected_post

from lce import approval, images
from lce.dupcheck import run_dupcheck
from lce.posts import save_draft, save_humanized
from lce.qa import run_qa
from lce.store import StoreError

TTY = lambda: True  # noqa: E731


def png(path, shade=0):
    """A valid 1x1 PNG."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(
            ">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"\x00" + bytes([shade, shade, shade])
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


DIAGRAM = {"kind": "diagram", "rationale": "the flow is easier to see than to read",
           "relation": "shows the twelve routing rules feeding the triage queue",
           "alt_text": "Diagram: incoming tickets pass keyword rules into three queues",
           "provenance": {"origin": "own_creation", "usage": "owned",
                          "generation": {"method": "diagram script"}}}


def checked_post(store):
    pid = selected_post(store)
    save_draft(store, pid, GOOD_POST)
    save_humanized(store, pid, GOOD_POST)
    assert run_qa(store, pid, denylist=[])["status"] == "passed"
    assert run_dupcheck(store, pid)["status"] == "passed"
    return pid


def decide_diagram(store, pid, tmp_path, **over):
    doc = {**DIAGRAM, **over}
    return images.decide(store, pid, kind=doc["kind"], rationale=doc["rationale"],
                         source_file=str(png(tmp_path / "d.png")), relation=doc["relation"],
                         alt_text=doc["alt_text"], provenance=doc["provenance"])


def test_no_decision_blocks_approval_and_none_is_valid(store):
    pid = checked_post(store)
    with pytest.raises(StoreError, match="no image decision"):
        approval.prepare(store, pid)
    images.decide(store, pid, kind="none", rationale="the argument needs no visual")
    post, path = approval.prepare(store, pid)
    assert post["approval"]["image_hash"] == "none"
    assert "no image — the argument needs no visual" in open(path).read()


def test_image_is_copied_hashed_and_shown_in_the_artifact(store, tmp_path):
    pid = checked_post(store)
    doc = decide_diagram(store, pid, tmp_path)
    f = store.post_dir(pid) / "image.png"
    assert doc["file"] == "image.png" and doc["sha256"] == images.file_sha256(f)
    assert images.check(store, pid) == ([], [])
    post, path = approval.prepare(store, pid)
    text = open(path).read()
    assert doc["sha256"] in text and "origin: own_creation" in text
    assert post["approval"]["image_hash"] == doc["sha256"]


@pytest.mark.parametrize(("over", "needle"), [
    ({"relation": ""}, "relates to the post"),
    ({"alt_text": ""}, "alt text"),
    ({"provenance": {"origin": "generated", "usage": "owned"}}, "generation.method"),
    ({"provenance": {"origin": "licensed_stock", "usage": "licensed"}}, "does not fit kind"),
    ({"provenance": {"origin": "own_creation", "usage": "needs_review"}}, "owner's review"),
    ({"kind": "source_image", "provenance": {"origin": "source_publication", "usage": "owned"}},
     "source_url and license"),
    ({"kind": "generated_concept", "provenance": {"origin": "own_creation", "usage": "owned"}},
     "does not fit kind"),
])
def test_provenance_and_relation_rules(store, tmp_path, over, needle):
    pid = checked_post(store)
    decide_diagram(store, pid, tmp_path, **over)
    errors, _ = images.check(store, pid)
    assert any(needle in e for e in errors), errors
    with pytest.raises(StoreError, match="image:"):
        approval.prepare(store, pid)


def test_bad_files_are_refused(store, tmp_path):
    pid = checked_post(store)
    with pytest.raises(StoreError, match="existing"):
        images.decide(store, pid, kind="diagram", rationale="x", source_file=str(tmp_path / "no.png"))
    fake = tmp_path / "fake.png"
    fake.write_bytes(b"not an image")
    images.decide(store, pid, kind="diagram", rationale="x", source_file=str(fake),
                  relation=DIAGRAM["relation"], alt_text="a", provenance=DIAGRAM["provenance"])
    assert "does not match its extension" in " ".join(images.check(store, pid)[0])


def test_image_change_discards_approval(store, tmp_path):
    pid = checked_post(store)
    decide_diagram(store, pid, tmp_path)
    approval.prepare(store, pid)
    png(store.post_dir(pid) / "image.png", shade=200)           # swapped after preparing
    h = store.load_post(pid)["content_hash"]
    with pytest.raises(StoreError, match="image changed"):
        approval.approve(store, pid, h[:12], confirm=lambda _: f"APPROVE {pid}", is_tty=TTY)
    decide_diagram(store, pid, tmp_path)                        # new decision → reopened
    assert store.load_post(pid)["state"] == "HUMANIZED"
    assert run_qa(store, pid, denylist=[])["status"] == "passed"
    assert run_dupcheck(store, pid)["status"] == "passed"
    approval.prepare(store, pid)
    approval.approve(store, pid, h[:12], confirm=lambda _: f"APPROVE {pid}", is_tty=TTY)
    png(store.post_dir(pid) / "image.png", shade=99)            # swapped after approval
    with pytest.raises(StoreError, match="image changed after approval"):
        approval.mark_ready(store, pid)
    assert store.load_post(pid)["state"] == "HUMANIZED"


def test_cli_decide_and_check(store, tmp_path, capsys):
    from lce.cli import main

    pid = checked_post(store)
    args = ["--data-dir", str(store.root), "image"]
    assert main([*args, "check", pid]) == 1
    assert main([*args, "decide", pid, "--kind", "chart", "--rationale", "one number matters",
                 "--file", str(png(tmp_path / "c.png")), "--relation", "plots triage minutes per day",
                 "--alt", "Bar chart: 40 minutes before, 10 after", "--origin", "own_creation",
                 "--usage", "owned", "--method", "chart script"]) == 0
    assert "image decision is complete" in capsys.readouterr().out
