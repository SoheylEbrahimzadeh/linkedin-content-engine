"""Production safety: any text change invalidates the approval; nothing stale can be published.

Executed end to end (not by inspection): approve → change the text → the approval is invalid →
local publish, cloud delegation and the decisions path refuse, and every rewrite path (same-day
refresh, Refresh replacement) discards the approval and waits for the owner again.
"""

import pytest
from conftest import GOOD_POST, awaiting_post
from test_cloud_client import ACCESS, FakeCloud
from test_publishing import TTY, ok, publisher, setup_ready
from test_refresh import DAY, URL, with_source
from test_repackage import PKG, legacy_post, refresh_click

from lce import cloud, decisions, publishing, refresh, repackage
from lce.approval import approve, mark_ready
from lce.cloud import CloudClient, CloudError
from lce.posts import current_text
from lce.store import StoreError
from lce.textutil import content_hash
from lce.voice_gate import assess, make_review


def approval_valid(store, pid) -> bool:
    post = store.load_post(pid)
    a = post.get("approval") or {}
    return a.get("state") == "approved" and a.get("approved_hash") == content_hash(current_text(store, pid))


def edit(store, pid, text="An edited text after approval.\n"):
    (store.post_dir(pid) / "post.md").write_text(text)


def test_changed_text_makes_the_approval_invalid_and_local_publish_refuses(store):
    pid = setup_ready(store)
    assert approval_valid(store, pid)
    edit(store, pid)
    assert not approval_valid(store, pid)
    p, transport = publisher(store)
    with pytest.raises(StoreError, match="approved hash"):
        publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    assert transport.calls == []


def test_cloud_publisher_rejects_a_stale_approval_and_sends_nothing(store):
    pid = setup_ready(store)
    edit(store, pid)
    fake = FakeCloud()
    client = CloudClient("https://lce.example", lambda: ACCESS, fake)
    with pytest.raises(CloudError, match="approved hash"):
        cloud.push(store, pid, client, decision_id="d-test")
    assert fake.calls == [] and fake.posts == {}


def test_decision_bound_to_an_old_hash_is_refused(store):
    pid = setup_ready(store)
    old = content_hash(current_text(store, pid))
    edit(store, pid)
    with pytest.raises(decisions.Refused, match="changed since the decision"):
        decisions._require_hash(store, pid, old)


def test_marking_ready_after_a_change_discards_the_approval(store):
    pid = awaiting_post(store)
    h = store.load_post(pid)["content_hash"]
    approve(store, pid, h[:12], confirm=lambda _: f"APPROVE {pid}", is_tty=lambda: True)
    edit(store, pid)
    with pytest.raises(StoreError, match="text changed after approval"):
        mark_ready(store, pid)
    post = store.load_post(pid)
    assert post["state"] == "HUMANIZED" and "approval" not in post


def test_same_day_refresh_cannot_keep_the_approval(store):
    pid = with_source(store, setup_ready(store))
    assert approval_valid(store, pid)
    new = GOOD_POST.replace("Start with the boring rules.", "Start with the boring rules first.")
    refresh.apply_update(store, pid, as_of=DAY, text=new, reason="source updated", sources=[URL])
    post = store.load_post(pid)
    assert not approval_valid(store, pid)
    assert post["state"] != "READY_TO_PUBLISH"
    p, transport = publisher(store)
    with pytest.raises(StoreError):
        publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    assert transport.calls == []


def test_replacement_waits_for_a_new_owner_approval(store):
    pid = legacy_post(store)
    refresh_click(store, pid)
    repackage.package(store, pid, PKG)
    post = store.load_post(pid)
    assert post["state"] == "AWAITING_APPROVAL" and post["approval"]["state"] == "pending"
    assert not approval_valid(store, pid)


def test_voice_gate_review_is_bound_to_the_text_hash():
    text = "Copilot asks for approval before it controls an app. So who checks what it clicked?"
    values = {"natural_english": "pass", "owner_grounded_meaning": "insufficient",
              "owner_phrasing": "insufficient"}
    rev = make_review(text, reviewer="owner", values=values)
    post = {"claims": [], "voice_gate_review": rev}
    hum = {"criteria": [], "score": 10, "of": 10}
    same = assess(text, post=post, golden_items={}, stories={}, samples=[], humanity=hum, codes=set())
    changed = assess(text + " Edited.", post=post, golden_items={}, stories={}, samples=[], humanity=hum,
                     codes=set())
    assert not same["review"]["stale"] and changed["review"]["stale"]
    assert changed["verdict"] != "PASS"
