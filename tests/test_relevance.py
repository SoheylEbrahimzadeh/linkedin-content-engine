"""LCE-041: semantic media relevance — a visual must carry the idea, not re-type the post."""

import yaml
from conftest import GOOD_POST
from test_images import RELEVANCE, checked_post, png
from test_media_pipeline import ITEMS, SPEC, TITLE, humanized

from lce import images, relevance

POST = {"claims": [{"text": "Manual triage dropped from about 40 minutes a day to about 10",
                    "source_url": "https://example.com/report"}]}


def test_text_dump_scores_high_and_is_rejected():
    dump = {"visual_type": "process", "title": TITLE, "nodes": [{"label": i} for i in ITEMS]}
    rec = relevance.evaluate(dump, POST, GOOD_POST, alt_text="A checklist with the post's points")
    assert rec["copied_post_text_ratio"] > 0.9
    assert rec["media_decision"] == "rejected"
    assert any("text dump" in p for p in rec["problems"])
    assert any("label is a sentence" in p for p in rec["problems"])
    assert any("no concept" in p for p in rec["problems"])
    assert any("relevance reason" in p for p in rec["problems"])


def test_conceptual_visual_is_accepted_with_its_metadata():
    rec = relevance.evaluate(SPEC, POST, GOOD_POST, alt_text=SPEC["alt_text"])
    assert rec["media_decision"] == "accepted", rec["problems"]
    for key in ("concept", "visual_type", "relevance_reason", "copied_post_text_ratio", "factual_claims",
                "source_requirements", "media_decision"):
        assert key in rec
    assert rec["copied_post_text_ratio"] < relevance.MAX_COPIED_RATIO and rec["text_checked"] is True


def test_numbers_in_the_image_must_be_recorded_sourced_claims():
    bad = {**SPEC, "nodes": [*SPEC["nodes"], {"label": "Saves 75% of triage time"}]}
    rec = relevance.evaluate(bad, POST, GOOD_POST, alt_text=SPEC["alt_text"])
    assert rec["media_decision"] == "rejected"
    assert any("unsupported number" in p for p in rec["problems"])
    sourced = {**SPEC, "footer": "Manual triage dropped from about 40 minutes a day to about 10."}
    rec = relevance.evaluate(sourced, POST, GOOD_POST, alt_text=SPEC["alt_text"])
    assert any("no source line" in p for p in rec["problems"])        # a figure needs its source shown
    rec = relevance.evaluate({**sourced, "source_line": "Source: example.com."}, POST, GOOD_POST,
                             alt_text=SPEC["alt_text"])
    assert rec["media_decision"] == "accepted", rec["problems"]
    [fact] = rec["factual_claims"]
    assert fact["supported"] and fact["source_url"] == "https://example.com/report"
    assert rec["source_requirements"] == ["source shown on the image: example.com"]


def test_alt_text_must_describe_the_visual_not_repeat_the_post():
    rec = relevance.evaluate(SPEC, POST, GOOD_POST, alt_text=GOOD_POST)
    assert any("alt text repeats the post" in p for p in rec["problems"])
    rec = relevance.evaluate(SPEC, POST, GOOD_POST, alt_text="")
    assert "alt text is required" in rec["problems"]


def test_copied_questions_are_rejected_but_an_own_question_is_fine():
    copied = {**SPEC, "outcomes": ["What was the first rule you automated?", "Stop"]}
    assert any("question copied" in p for p in relevance.evaluate(copied, POST, GOOD_POST,
                                                                   alt_text=SPEC["alt_text"])["problems"])
    own = {**SPEC, "visual_type": "framework", "center": "Rules or model?"}
    assert relevance.evaluate(own, POST, GOOD_POST, alt_text=SPEC["alt_text"])["media_decision"] == "accepted"


def test_an_image_without_a_relevance_record_cannot_be_approved(store, tmp_path, capsys):
    from lce.cli import main

    pid = checked_post(store)
    args = ["--data-dir", str(store.root), "image"]
    assert main([*args, "decide", pid, "--kind", "screenshot", "--rationale", "the real board",
                 "--file", str(png(tmp_path / "s.png")), "--relation", "the routing board the post describes",
                 "--alt", "Screenshot of a ticket routing board with three queues", "--origin",
                 "owner_screenshot", "--usage", "owned"]) == 1
    assert "no media relevance record" in capsys.readouterr().out
    assert main([*args, "decide", pid, "--kind", "screenshot", "--rationale", "the real board",
                 "--file", str(png(tmp_path / "s.png")), "--relation", "the routing board the post describes",
                 "--alt", "Screenshot of a ticket routing board with three queues", "--origin",
                 "owner_screenshot", "--usage", "owned", "--concept", RELEVANCE["concept"],
                 "--visual-type", "screenshot", "--relevance-reason", RELEVANCE["reason"]]) == 0


def test_cli_diagram_from_a_spec_reports_the_relevance(store, tmp_path, capsys):
    from lce.cli import main

    pid = humanized(store)
    f = tmp_path / "spec.yaml"
    f.write_text(yaml.safe_dump(SPEC), encoding="utf-8")
    assert main(["--data-dir", str(store.root), "image", "diagram", pid, "--spec", str(f)]) == 0
    out = capsys.readouterr().out
    assert "relevance accepted: flow" in out and "copied post text" in out
    dump = tmp_path / "dump.yaml"
    dump.write_text(yaml.safe_dump({**SPEC, "nodes": [{"label": i} for i in ITEMS]}), encoding="utf-8")
    assert main(["--data-dir", str(store.root), "image", "diagram", pid, "--spec", str(dump)]) == 1
    assert "media relevance rejected; nothing attached" in capsys.readouterr().out
    assert images.load(store, pid)["media_relevance"]["media_decision"] == "accepted"   # previous kept


def test_relevance_is_re_evaluated_against_the_current_text(store):
    from lce.posts import save_humanized

    pid = humanized(store)
    from lce.visuals import concept

    concept(store, pid, SPEC)
    # the text changes so that it now contains the image's labels verbatim plus the alt text
    save_humanized(store, pid, GOOD_POST + "\n" + SPEC["alt_text"] + "\n")
    rel = images.relevance_now(store, pid)
    assert rel["media_decision"] == "rejected" and any("alt text" in p for p in rel["problems"])
