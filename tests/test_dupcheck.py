from datetime import date

import pytest
from conftest import GOOD_POST, selected_post

from lce.dupcheck import compare_texts, import_external, run_dupcheck
from lce.posts import save_draft, save_humanized
from lce.qa import run_qa
from lce.research import add_candidate
from lce.store import DEFAULT_TUNING, StoreError

CFG = DEFAULT_TUNING["duplicates"]


def to_qa_passed(store, text, pid=None):
    pid = pid or selected_post(store)
    save_draft(store, pid, text)
    save_humanized(store, pid, text)
    assert run_qa(store, pid, denylist=[])["status"] == "passed"
    return pid


def second_post(store, title, angle, pillar="operations", stories=()):
    from lce.planning import select

    c = add_candidate(store, title=title, origin="manual")
    return select(store, candidate_id=c["candidate_id"], pillar=pillar, angle=angle, fmt="text",
                  plan_date=date(2025, 5, 8), stories=list(stories))["post_id"]


def test_compare_exact_near_similar():
    base = "Routing rules beat models for small teams. Start simple and measure the result."
    corpus = {"a": base.upper() + " #ops", "b": base + " Then iterate weekly.",
              "c": "Something entirely unrelated about gardening and tomatoes in summer."}
    r = compare_texts(base, corpus, CFG)
    assert r["exact"] == ["a"] and [x["ref"] for x in r["near"]] == ["b"]


def test_first_post_passes(store):
    pid = to_qa_passed(store, GOOD_POST)
    assert run_dupcheck(store, pid)["status"] == "passed"
    assert store.load_post(pid)["state"] == "DUPLICATE_CHECKED"
    assert list((store.root / "runs").glob("*.jsonl"))


def test_exact_duplicate_of_external_history_fails(store):
    import_external(store, "old post", GOOD_POST)
    pid = to_qa_passed(store, GOOD_POST)
    r = run_dupcheck(store, pid)
    assert r["status"] == "failed" and r["exact"] == ["external:old-post"]
    assert store.load_post(pid)["state"] == "NEEDS_REVISION"


def test_near_duplicate_between_posts_fails(store):
    first = to_qa_passed(store, GOOD_POST)
    run_dupcheck(store, first)
    pid = second_post(store, "Queue hygiene for service desks", "queue hygiene",
                      stories=["demo-ticket-routing"])
    to_qa_passed(store, GOOD_POST.replace("What was", "Which was"), pid)
    r = run_dupcheck(store, pid)
    assert r["status"] == "failed" and r["near"]


def test_story_reuse_within_window_fails(store):
    first = to_qa_passed(store, GOOD_POST)
    run_dupcheck(store, first)
    other = ("Incident reviews work better when they are short.\n\nWe keep them to one page and "
             "one owner per action, and we read them again a month later.\n\nThat habit caught "
             "more repeat incidents than any dashboard we tried. Rules first, tools later.\n")
    pid = second_post(store, "Short incident reviews", "one-page reviews",
                      stories=["demo-ticket-routing"])
    to_qa_passed(store, other, pid)
    r = run_dupcheck(store, pid)
    assert r["status"] == "failed" and r["story_reuse"]


def test_same_pillar_and_angle_fails(store):
    first = to_qa_passed(store, GOOD_POST)
    run_dupcheck(store, first)
    other = ("Incident reviews work better when they are short.\n\nWe keep them to one page and "
             "one owner per action, and we read them again a month later.\n\nThat habit caught "
             "more repeat incidents than any dashboard we tried.\n")
    pid = second_post(store, "Short incident reviews", "rules before models", pillar="automation")
    to_qa_passed(store, other, pid)
    r = run_dupcheck(store, pid)
    assert r["status"] == "failed" and r["angle_reuse"] == [first]


def test_requires_qa_on_current_text(store):
    pid = to_qa_passed(store, GOOD_POST)
    (store.post_dir(pid) / "post.md").write_text(GOOD_POST + "edited\n")
    with pytest.raises(StoreError):
        run_dupcheck(store, pid)
