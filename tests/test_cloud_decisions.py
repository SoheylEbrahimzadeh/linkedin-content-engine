"""LCE-036: owner decisions from the cloud Control Center, applied to the git-tracked
pipeline with every hash re-checked; nothing is published here."""

import json

import pytest
from conftest import GOOD_POST, awaiting_post

from lce import cloud, decisions
from lce.cloud import CloudClient, CloudResponse
from lce.posts import current_text
from lce.textutil import content_hash

OWNER = "owner@example.com"


class Inbox:
    """In-memory Worker: pending decisions, resolutions and pushed posts."""

    def __init__(self, items=(), fail_push=False):
        self.items, self.resolved, self.pushed, self.fail_push = list(items), {}, {}, fail_push

    def request(self, method, url, headers, body):
        path = url.split("/api", 1)[1]
        data = json.loads(body) if body else None
        if method == "GET" and path == "/decisions?status=pending":
            open_ = [d for d in self.items if d["decision_id"] not in self.resolved]
            return CloudResponse(200, {"decisions": open_})
        if method == "POST" and path.endswith("/resolve"):
            self.resolved[path.split("/")[2]] = data
            return CloudResponse(200, {"status": data["status"]})
        if method == "PUT" and path.startswith("/posts/"):
            if self.fail_push:
                return CloudResponse(503, {"error": "unavailable"})
            self.pushed[path.split("/")[2]] = data
            return CloudResponse(200, {"state": "READY_TO_PUBLISH"})
        return CloudResponse(404, {"error": "not found"})


def decision(action, post_id=None, **kw):
    n = decision.n = getattr(decision, "n", 0) + 1
    return {"decision_id": f"d-{n:08d}-0000-0000-0000-000000000000", "action": action, "post_id": post_id,
            "plan_date": kw.pop("plan_date", None), "content_hash": kw.pop("content_hash", None),
            "payload": kw, "created_by": OWNER, "created_at": "2026-10-05T12:00:00+00:00"}


def run(store, *items, **kw):
    inbox = Inbox(items, **kw)
    out = decisions.apply_all(store, CloudClient("https://lce.example", lambda: "fake.access.jwt", inbox))
    return inbox, out


def test_approve_applies_the_exact_reviewed_text_and_delegates(store):
    pid = awaiting_post(store)
    h = content_hash(current_text(store, pid))
    inbox, out = run(store, decision("approve", pid, content_hash=h, image_sha256=None))
    assert out[0]["status"] == "applied"
    post = store.load_post(pid)
    assert post["state"] == "READY_TO_PUBLISH"
    assert post["approval"]["approved_by"] == f"cloud-access:{OWNER}"
    assert post["approval"]["decision_id"] == out[0]["decision_id"]
    assert inbox.pushed[pid]["approved_hash"] == h
    assert cloud.load_delegation(store, pid)["approved_hash"] == h
    assert inbox.resolved[out[0]["decision_id"]]["status"] == "applied"


@pytest.mark.parametrize("bad", [{"content_hash": "0" * 64, "image_sha256": None},
                                 {"image_sha256": "a" * 64}])
def test_approve_is_refused_when_text_or_image_differ(store, bad):
    pid = awaiting_post(store)
    bad.setdefault("content_hash", content_hash(current_text(store, pid)))
    inbox, out = run(store, decision("approve", pid, **bad))
    assert out[0]["status"] == "refused"
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"
    assert inbox.pushed == {} and inbox.resolved[out[0]["decision_id"]]["status"] == "refused"


def test_failed_push_stays_pending_and_resumes(store):
    pid = awaiting_post(store)
    d = decision("approve", pid, content_hash=content_hash(current_text(store, pid)), image_sha256=None)
    inbox, out = run(store, d, fail_push=True)
    assert out[0]["status"] == "pending" and inbox.resolved == {}
    assert store.load_post(pid)["state"] == "READY_TO_PUBLISH"
    inbox, out = run(store, d)
    assert out[0]["status"] == "applied" and pid in inbox.pushed


def test_reject_and_regenerate(store):
    pid = awaiting_post(store)
    _, out = run(store, decision("regenerate", pid, reason="sharper opening"))
    post = store.load_post(pid)
    assert out[0]["status"] == "applied" and post["state"] == "NEEDS_REVISION"
    assert post["regeneration"]["reason"] == "sharper opening"
    assert "approval" not in post
    _, out = run(store, decision("reject", pid, reason="off-topic"))
    assert store.load_post(pid)["state"] == "REJECTED"


def test_edit_replaces_text_and_reruns_the_checks(store):
    pid = awaiting_post(store)
    h = content_hash(current_text(store, pid))
    new = GOOD_POST.replace("\n\n", "\n\nToday, ", 1)
    _, out = run(store, decision("edit", pid, content_hash=h, text=new))
    assert out[0]["status"] == "applied", out
    post = store.load_post(pid)
    assert current_text(store, pid).strip() == new.strip()
    assert post["state"] in {"AWAITING_APPROVAL", "NEEDS_REVISION", "DUPLICATE_CHECKED"}
    assert post.get("approval", {}).get("state") != "approved"
    _, out = run(store, decision("edit", pid, content_hash=h, text="Another text."))
    assert out[0]["status"] == "refused" and "changed" in out[0]["result"]


def test_reschedule_moves_post_and_plan_entry(store):
    pid = awaiting_post(store)
    _, out = run(store, decision("reschedule", pid, plan_date="2026-10-20"))
    assert out[0]["status"] == "applied"
    assert store.load_post(pid)["plan_date"] == "2026-10-20"
    entries = [e for e in store.plan()["entries"] if e.get("draft_ref") == pid]
    assert all(e["date"] == "2026-10-20" for e in entries)


def test_skip_plan_entry_and_post(store):
    plan = store.plan()
    plan["entries"].append({"date": "2026-11-03", "topic": "Fictional planned topic", "pillar": "automation",
                            "status": "planned"})
    store.write_doc(store.plan_path, "plan", plan)
    _, out = run(store, decision("skip", None, plan_date="2026-11-03"))
    assert out[0]["status"] == "applied"
    assert [e["status"] for e in store.plan()["entries"] if e["date"] == "2026-11-03"] == ["skipped"]
    pid = awaiting_post(store)
    _, out = run(store, decision("skip", pid, reason="not this week"))
    assert store.load_post(pid)["state"] == "REJECTED"


def test_duplicate_creates_a_new_unapproved_copy(store):
    pid = awaiting_post(store)
    _, out = run(store, decision("duplicate", pid, plan_date="2026-11-10"))
    assert out[0]["status"] == "applied"
    copies = [p for p in store.post_ids() if store.load_post(p).get("duplicated_from") == pid]
    assert len(copies) == 1
    copy = store.load_post(copies[0])
    assert copy["state"] == "HUMANIZED" and copy["plan_date"] == "2026-11-10" and "approval" not in copy
    assert current_text(store, copies[0]) == current_text(store, pid)


def test_delegated_posts_cannot_be_changed_from_git_side(store):
    pid = awaiting_post(store)
    h = content_hash(current_text(store, pid))
    run(store, decision("approve", pid, content_hash=h, image_sha256=None))
    for d in (decision("reject", pid, reason="x"), decision("regenerate", pid, reason="x"),
              decision("reschedule", pid, plan_date="2026-12-01")):
        _, out = run(store, d)
        assert out[0]["status"] == "refused" and "cloud publisher" in out[0]["result"]


def test_already_applied_decisions_are_only_resolved(store):
    pid = awaiting_post(store)
    d = decision("regenerate", pid, reason="x")
    run(store, d)
    inbox, out = run(store, d)      # the cloud did not record the first resolution
    assert out[0]["status"] == "applied" and inbox.resolved[d["decision_id"]]["status"] == "applied"


def test_cli_lists_and_applies(store, monkeypatch, capsys):
    from lce import cli

    pid = awaiting_post(store)
    inbox = Inbox([decision("regenerate", pid, reason="tighter")])
    monkeypatch.setattr(cloud, "make_client",
                        lambda s: CloudClient("https://lce.example", lambda: "fake.access.jwt", inbox))
    assert cli.main(["--data-dir", str(store.root), "cloud", "decisions"]) == 0
    assert "1 pending" in capsys.readouterr().out
    assert cli.main(["--data-dir", str(store.root), "cloud", "decisions", "--apply"]) == 0
    assert "✓ regenerate" in capsys.readouterr().out


def _override(store, did, then=None):
    o = {"decision_id": did, "reason": "owner decided in chat: treat as Refresh",
         "decided_at": "2026-10-03T01:00:00+00:00", "decided_by": "owner"}
    if then:
        o["then"] = then
    (store.root / "decisions").mkdir(exist_ok=True)
    store.write_doc(store.root / "decisions" / "overrides.yaml", "decision_overrides", {"overrides": [o]})


def test_overridden_skip_is_refused_and_the_refresh_applies_instead(store):
    """LCE-043: a recorded skip the owner withdrew is resolved `refused` (never applied),
    and the refresh the owner chose instead archives the version as rejected."""
    pid = awaiting_post(store)
    d = decision("skip", pid, reason="")
    _override(store, d["decision_id"], {"action": "refresh", "by": "owner (chat)", "note": "i dont like it"})
    inbox, out = run(store, d)
    assert out[0]["status"] == "refused" and "overridden by the owner" in out[0]["result"]
    assert inbox.resolved[d["decision_id"]]["status"] == "refused"
    post = store.load_post(pid)
    assert post["state"] == "NEEDS_REVISION"
    assert post["refresh_request"]["note"] == "i dont like it"
    from lce import versions
    assert [v["status"] for v in versions.listing(store, pid)] == ["rejected"]


def test_override_without_follow_up_only_withdraws(store):
    pid = awaiting_post(store)
    d = decision("skip", pid)
    _override(store, d["decision_id"], {"action": "none"})
    _, out = run(store, d)
    assert out[0]["status"] == "refused"
    assert store.load_post(pid)["state"] == "AWAITING_APPROVAL"


def test_override_file_is_validated(store):
    from lce.store import StoreError
    (store.root / "decisions").mkdir(exist_ok=True)
    with pytest.raises(StoreError):
        store.write_doc(store.root / "decisions" / "overrides.yaml", "decision_overrides",
                        {"overrides": [{"decision_id": "x", "reason": "short"}]})


def test_override_can_match_by_post_action_and_recording_time(store):
    pid = awaiting_post(store)
    d = decision("skip", pid)
    other = decision("skip", None, plan_date="2026-11-03")
    (store.root / "decisions").mkdir(exist_ok=True)
    store.write_doc(store.root / "decisions" / "overrides.yaml", "decision_overrides", {"overrides": [
        {"match": {"post_id": pid, "action": "skip", "recorded_at_prefix": "2026-10-05T12:00"},
         "reason": "owner decided in chat: treat as Refresh", "decided_at": "2026-10-05T13:00:00+00:00",
         "decided_by": "owner", "then": {"action": "refresh", "by": "owner (chat)"}}]})
    _, out = run(store, d, other)
    by_id = {o["decision_id"]: o for o in out}
    assert by_id[d["decision_id"]]["status"] == "refused"
    assert "overridden" not in str(by_id[other["decision_id"]]["result"])
    assert store.load_post(pid)["state"] == "NEEDS_REVISION"
