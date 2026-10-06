"""Operational archive (owner request 2026-10-06): a rejected or published post leaves the
working views; its state, history, versions and audit records stay; nothing re-enters the
pipeline and nothing is published or scheduled."""

import json

import pytest
from conftest import awaiting_post
from test_cloud_decisions import OWNER, decision, run

from lce import approval, archive, cloud
from lce.dashboard.snapshot import build_snapshot
from lce.posts import current_text
from lce.store import StoreError
from lce.textutil import content_hash


def rejected_post(store, reason="not my voice"):
    pid = awaiting_post(store)
    approval.reject(store, pid, reason)
    return pid


def events(store, name):
    out = []
    for f in sorted((store.root / "runs").glob("*.jsonl")):
        out += [json.loads(x) for x in f.read_text("utf-8").splitlines() if f'"{name}"' in x]
    return out


def test_rejected_post_is_archived_with_everything_kept(store):
    pid = rejected_post(store)
    before = store.load_post(pid)
    text = current_text(store, pid)
    post = archive.archive(store, pid, by="owner", reason="dead topic", decision_id="d-1")
    assert post["state"] == "REJECTED"                                   # the state is not touched
    assert post["archived"]["state"] == "REJECTED" and post["archived"]["reason"] == "dead topic"
    assert post["history"] == before["history"]                          # history kept as it was
    assert post["approval"] == before["approval"]                        # the rejection reason too
    assert current_text(store, pid) == text and (store.post_dir(pid) / "APPROVAL.md").exists()
    assert post["archive_history"][0]["action"] == "archived"
    assert events(store, "post.archived")[-1]["post_id"] == pid


def test_archived_post_leaves_the_views_but_stays_in_the_snapshot(store):
    pid = rejected_post(store)
    archive.archive(store, pid, by="owner")
    snap = build_snapshot(store, mode="real")
    p = next(x for x in snap["posts"] if x["post_id"] == pid)          # History still has it
    assert p["archived"]["by"] == "owner" and p["state"] == "REJECTED"


@pytest.mark.parametrize("state_fn", ["awaiting", "ready"])
def test_actionable_posts_cannot_be_archived(store, state_fn):
    pid = awaiting_post(store)
    if state_fn == "ready":
        h = content_hash(current_text(store, pid))
        run(store, decision("approve", pid, content_hash=h, image_sha256=None))
        assert store.load_post(pid)["state"] == "READY_TO_PUBLISH"
    with pytest.raises(StoreError, match="not archived"):
        archive.archive(store, pid, by="owner")
    assert "archived" not in store.load_post(pid)


def test_published_post_is_archived_only_as_display_state(store):
    pid = rejected_post(store)
    post = store.load_post(pid)
    post["state"] = "PUBLISHED"                                          # a published post
    store.save_post(post)
    archive.archive(store, pid, by="owner")
    assert store.load_post(pid)["state"] == "PUBLISHED"                 # never deleted, never changed
    assert (store.post_dir(pid) / "post.md").exists()


def test_published_post_with_open_cloud_work_is_refused(store):
    pid = rejected_post(store)
    post = store.load_post(pid)
    post["state"] = "PUBLISHED"
    store.save_post(post)
    (store.post_dir(pid) / "delegation.json").write_text(json.dumps({"post_id": pid}), "utf-8")
    (store.post_dir(pid) / "cloud.json").write_text(json.dumps({"state": "NEEDS_RECONCILE"}), "utf-8")
    with pytest.raises(StoreError, match="cloud publisher"):
        archive.archive(store, pid, by="owner")


def test_restore_keeps_the_state_and_appends_to_the_audit(store):
    pid = rejected_post(store)
    archive.archive(store, pid, by="owner")
    post = archive.restore(store, pid, by="owner")
    assert "archived" not in post and post["state"] == "REJECTED"
    assert [a["action"] for a in post["archive_history"]] == ["archived", "restored"]


def test_archived_post_cannot_be_approved_prepared_or_delegated(store):
    pid = awaiting_post(store)
    post = store.load_post(pid)
    post["archived"] = {"at": "2026-10-06T00:00:00+00:00", "by": "test", "state": "AWAITING_APPROVAL"}
    store.save_post(post)                       # even if the flag were set on an actionable post
    h = content_hash(current_text(store, pid))
    with pytest.raises(StoreError, match="archived"):
        approval.approve_recorded(store, pid, h, None, approver="x", decision_id="d")
    with pytest.raises(cloud.CloudError, match="archived"):
        cloud.push(store, pid, CloudClientStub(), decision_id="d-x")
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"


class CloudClientStub:
    def call(self, *a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("an archived post reached the cloud publisher")


def test_archive_and_restore_through_owner_decisions(store):
    pid = rejected_post(store)
    _, out = run(store, decision("archive", pid, reason="done with it"))
    assert out[0]["status"] == "applied", out
    assert store.load_post(pid)["archived"]["by"] == f"cloud-access:{OWNER}"
    _, out = run(store, decision("refresh", pid))                     # nothing else acts on it
    assert out[0]["status"] == "refused" and "archived" in out[0]["result"]
    _, out = run(store, decision("restore", pid))
    assert out[0]["status"] == "applied" and "archived" not in store.load_post(pid)


def test_archive_decision_on_an_awaiting_post_is_refused(store):
    pid = awaiting_post(store)
    _, out = run(store, decision("archive", pid))
    assert out[0]["status"] == "refused"
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL" and "archived" not in store.load_post(pid)


def test_failure_records_survive_the_archive(store):
    """A failed refresh or source check stays history: archiving only appends to the run log."""
    pid = rejected_post(store)
    store.log_event("cloud.decision_refused", post_id=pid, action="refresh",
                    reason="Worker network policy blocks source sites; claims cannot be verified")
    log = {f: f.read_text("utf-8") for f in (store.root / "runs").glob("*.jsonl")}
    archive.archive(store, pid, by="owner")
    for f, before in log.items():
        assert f.read_text("utf-8").startswith(before)
    assert events(store, "cloud.decision_refused")[-1]["post_id"] == pid
