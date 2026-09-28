import pytest
from conftest import GOOD_POST, awaiting_post

from lce import approval
from lce.posts import reopen, save_humanized
from lce.store import StoreError
from lce.textutil import content_hash

H = content_hash(GOOD_POST)


def tty():
    return True


def test_artifact_contents(store):
    pid = awaiting_post(store)
    art = (store.post_dir(pid) / "APPROVAL.md").read_text()
    for needle in ("Not published", "Content pillar", "Sources", "QA — passed",
                   "Duplicate check — passed", H, "Approval state", "Generated at",
                   "Most small service teams"):
        assert needle in art
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"


def test_no_tty_no_approval(store):
    pid = awaiting_post(store)
    with pytest.raises(StoreError, match="interactive terminal"):
        approval.approve(store, pid, H[:12], confirm=lambda _: f"APPROVE {pid}",
                         is_tty=lambda: False)
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"


def test_wrong_hash_or_phrase(store):
    pid = awaiting_post(store)
    with pytest.raises(StoreError, match="hash"):
        approval.approve(store, pid, "0" * 12, confirm=lambda _: f"APPROVE {pid}", is_tty=tty)
    with pytest.raises(StoreError, match="hash"):
        approval.approve(store, pid, H[:6], confirm=lambda _: f"APPROVE {pid}", is_tty=tty)
    with pytest.raises(StoreError, match="confirmation"):
        approval.approve(store, pid, H[:12], confirm=lambda _: "yes", is_tty=tty)
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"


def test_approve_then_ready_records_hash(store):
    pid = awaiting_post(store)
    post = approval.approve(store, pid, H[:12], confirm=lambda _: f"APPROVE {pid}", is_tty=tty)
    assert post["state"] == "APPROVED" and post["approval"]["approved_hash"] == H
    post = approval.mark_ready(store, pid)
    assert post["state"] == "READY_TO_PUBLISH"
    entry = next(e for e in store.plan()["entries"] if e.get("draft_ref") == pid)
    assert entry["approval_status"] == "approved"
    assert entry["publication_status"] == "ready_to_publish"


def test_edit_after_approval_is_blocked_and_tamper_detected(store):
    pid = awaiting_post(store)
    approval.approve(store, pid, H[:12], confirm=lambda _: f"APPROVE {pid}", is_tty=tty)
    with pytest.raises(StoreError, match="reopen"):
        save_humanized(store, pid, GOOD_POST + "More.\n")
    (store.post_dir(pid) / "post.md").write_text(GOOD_POST + "sneaky edit\n")
    with pytest.raises(StoreError, match="changed after approval"):
        approval.mark_ready(store, pid)
    post = store.load_post(pid)
    assert post["state"] == "HUMANIZED" and "approval" not in post


def test_edit_during_review_requires_new_checks(store):
    pid = awaiting_post(store)
    save_humanized(store, pid, GOOD_POST.replace("boring", "simple"))
    post = store.load_post(pid)
    assert post["state"] == "HUMANIZED" and "qa" not in post and "approval" not in post
    with pytest.raises(StoreError):
        approval.prepare(store, pid)


def test_modified_artifact_blocks_approval(store):
    pid = awaiting_post(store)
    path = store.post_dir(pid) / "APPROVAL.md"
    path.write_text(path.read_text() + "\nextra")
    with pytest.raises(StoreError, match="APPROVAL.md changed"):
        approval.approve(store, pid, H[:12], confirm=lambda _: f"APPROVE {pid}", is_tty=tty)


def test_reject_and_reopen(store):
    pid = awaiting_post(store)
    assert reopen(store, pid, "rethink")["state"] == "HUMANIZED"
    assert approval.reject(store, pid, "not now")["state"] == "REJECTED"
