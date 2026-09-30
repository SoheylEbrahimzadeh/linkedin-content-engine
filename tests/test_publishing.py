import hashlib
import json

import pytest
from conftest import awaiting_post
from fakes import FAKE_TOKEN, FakeTransport, created, not_sent, status, timeout_after_send

from lce import publishing
from lce.approval import approve, mark_ready
from lce.publish.credentials import MemoryTokenStore
from lce.store import StoreError
from lce.textutil import content_hash

TTY = {"is_tty": lambda: True}


def setup_ready(store):
    pid = awaiting_post(store)
    h = store.load_post(pid)["content_hash"]
    approve(store, pid, h[:12], confirm=lambda _: f"APPROVE {pid}", is_tty=lambda: True)
    mark_ready(store, pid)
    s = store.settings()
    s["publisher"] = {"provider": "linkedin_api"}
    store.write_doc(store.settings_path, "settings", s)
    (store.root / "config" / "linkedin.yaml").write_text(
        "api_version: '202609'\nperson_urn: urn:li:person:TestPerson1\n")
    return pid


def publisher(store, *responses, token=FAKE_TOKEN):
    t = FakeTransport(*responses)
    return publishing.make_publisher(store, transport=t, tokens=MemoryTokenStore(token)), t


def ok(pid):
    return lambda _: f"PUBLISH {pid}"


def tree(root):
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def events(store):
    return [json.loads(line) for p in (store.root / "runs").glob("*.jsonl")
            for line in p.read_text().splitlines() if line.strip()]


# ── human gate ────────────────────────────────────────────────────────────
def test_no_tty_no_publish_and_nothing_sent(store):
    pid = setup_ready(store)
    p, t = publisher(store, created())
    with pytest.raises(StoreError, match="interactive terminal"):
        publishing.publish(store, pid, p, confirm=ok(pid), is_tty=lambda: False)
    assert t.calls == [] and store.load_post(pid)["state"] == "READY_TO_PUBLISH"


def test_wrong_phrase_sends_nothing(store):
    pid = setup_ready(store)
    p, t = publisher(store, created())
    with pytest.raises(StoreError, match="confirmation"):
        publishing.publish(store, pid, p, confirm=lambda _: "yes", **TTY)
    assert t.calls == [] and publishing.load_publication(store, pid) is None


# ── gates ─────────────────────────────────────────────────────────────────
def test_only_ready_posts_with_matching_approved_hash(store):
    pid = setup_ready(store)
    p, t = publisher(store, created())
    (store.post_dir(pid) / "post.md").write_text("tampered after approval\n")
    with pytest.raises(StoreError, match="approved hash"):
        publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    assert t.calls == []


def test_unapproved_post_cannot_be_published(store):
    pid = awaiting_post(store)
    s = store.settings()
    s["publisher"] = {"provider": "linkedin_api"}
    store.write_doc(store.settings_path, "settings", s)
    (store.root / "config" / "linkedin.yaml").write_text(
        "api_version: '202609'\nperson_urn: urn:li:person:TestPerson1\n")
    p, t = publisher(store, created())
    with pytest.raises(StoreError, match="READY_TO_PUBLISH"):
        publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    assert t.calls == []


def test_provider_must_be_enabled(store):
    pid = setup_ready(store)
    s = store.settings()
    s["publisher"] = {"provider": "none"}
    store.write_doc(store.settings_path, "settings", s)
    p, t = publisher(store, created())
    with pytest.raises(StoreError, match="linkedin_api"):
        publishing.publish(store, pid, p, confirm=ok(pid), **TTY)


# ── outcomes ──────────────────────────────────────────────────────────────
def test_success_records_urn_and_exact_text(store):
    pid = setup_ready(store)
    p, t = publisher(store, created("urn:li:share:42"))
    out = publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    post, rec = store.load_post(pid), publishing.load_publication(store, pid)
    assert post["state"] == "PUBLISHED" and out["post"]["state"] == "PUBLISHED"
    assert rec["state"] == "published" and rec["remote_id"] == "urn:li:share:42"
    assert rec["verified_by"] == "api_response" and rec["attempts"][0]["http_status"] == 201
    sent = t.calls[0]["body"]["commentary"]
    assert rec["commentary_hash"] == hashlib.sha256(sent.encode()).hexdigest()
    assert rec["approved_hash"] == content_hash((store.post_dir(pid) / "post.md").read_text())
    plan = next(e for e in store.plan()["entries"] if e.get("draft_ref") == pid)
    assert plan["publication_status"] == "published" and plan["status"] == "published"
    assert FAKE_TOKEN not in (store.post_dir(pid) / "publication.json").read_text()
    assert all(FAKE_TOKEN not in json.dumps(e) for e in events(store))


def test_definite_rejection_is_publish_failed_and_can_be_retried_by_the_owner(store):
    pid = setup_ready(store)
    p, t = publisher(store, status(422), created("urn:li:share:7"))
    publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    assert store.load_post(pid)["state"] == "PUBLISH_FAILED"
    assert publishing.load_publication(store, pid)["attempts"][0]["outcome"] == "rejected"
    publishing.publish(store, pid, p, confirm=ok(pid), **TTY)  # explicit human retry
    rec = publishing.load_publication(store, pid)
    assert store.load_post(pid)["state"] == "PUBLISHED" and len(rec["attempts"]) == 2


@pytest.mark.parametrize("resp", [timeout_after_send(), status(500), status(503), status(409)])
def test_ambiguous_results_need_reconcile_and_block_resend(store, resp):
    pid = setup_ready(store)
    p, t = publisher(store, resp, created())
    publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    assert store.load_post(pid)["state"] == "NEEDS_RECONCILE"
    assert publishing.load_publication(store, pid)["state"] == "needs_reconcile"
    with pytest.raises(StoreError, match="reconcile"):
        publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    assert len(t.calls) == 1  # never re-sent


def test_unexpected_exception_during_send_is_treated_as_ambiguous(store):
    pid = setup_ready(store)
    p, t = publisher(store, RuntimeError("bug"))
    publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    assert store.load_post(pid)["state"] == "NEEDS_RECONCILE"


def test_not_sent_failure_is_publish_failed(store):
    pid = setup_ready(store)
    p, _ = publisher(store, not_sent())
    publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    assert store.load_post(pid)["state"] == "PUBLISH_FAILED"


def test_missing_token_fails_without_sending(store):
    pid = setup_ready(store)
    p, t = publisher(store, created(), token=None)
    publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    assert t.calls == [] and store.load_post(pid)["state"] == "PUBLISH_FAILED"


def test_intent_is_written_before_the_request(store):
    pid = setup_ready(store)
    seen = {}

    class Spy(FakeTransport):
        def request(self, *a, **k):
            seen["record"] = publishing.load_publication(store, pid)
            seen["state"] = store.load_post(pid)["state"]
            return super().request(*a, **k)

    t = Spy(created())
    p = publishing.make_publisher(store, transport=t, tokens=MemoryTokenStore(FAKE_TOKEN))
    publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    assert seen["state"] == "PUBLISHING" and seen["record"]["state"] == "publishing"
    assert seen["record"]["attempts"][0]["outcome"] == "pending"


# ── reconciliation ────────────────────────────────────────────────────────
def test_reconcile_published_with_url(store):
    pid = setup_ready(store)
    p, _ = publisher(store, timeout_after_send())
    publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    url = "https://www.linkedin.com/feed/update/urn:li:share:99/"
    with pytest.raises(StoreError):
        publishing.reconcile(store, pid, published_url=url, confirm=lambda _: "x", **TTY)
    publishing.reconcile(store, pid, published_url=url, confirm=lambda _: f"RECONCILE {pid}", **TTY)
    rec = publishing.load_publication(store, pid)
    assert store.load_post(pid)["state"] == "PUBLISHED"
    assert rec["remote_id"] == "urn:li:share:99" and rec["verified_by"] == "owner"


def test_reconcile_not_published_allows_a_new_human_attempt(store):
    pid = setup_ready(store)
    p, t = publisher(store, status(502), created("urn:li:share:5"))
    publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    publishing.reconcile(store, pid, not_published=True, confirm=lambda _: f"RECONCILE {pid}",
                         **TTY)
    assert store.load_post(pid)["state"] == "READY_TO_PUBLISH"
    publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    assert store.load_post(pid)["state"] == "PUBLISHED" and len(t.calls) == 2


def test_interrupted_run_is_reconciled_not_resent(store):
    pid = setup_ready(store)

    class Crash(FakeTransport):
        def request(self, *a, **k):
            raise KeyboardInterrupt  # the process dies mid-request

    p = publishing.make_publisher(store, transport=Crash(), tokens=MemoryTokenStore(FAKE_TOKEN))
    with pytest.raises(KeyboardInterrupt):
        publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    assert store.load_post(pid)["state"] == "PUBLISHING"
    p2, t2 = publisher(store, created())
    with pytest.raises(StoreError, match="reconcile"):
        publishing.publish(store, pid, p2, confirm=ok(pid), **TTY)
    publishing.reconcile(store, pid, not_published=True, confirm=lambda _: f"RECONCILE {pid}",
                         **TTY)
    rec = publishing.load_publication(store, pid)
    assert rec["attempts"][0]["outcome"] == "interrupted" and t2.calls == []
    assert store.load_post(pid)["state"] == "READY_TO_PUBLISH"


def test_reconcile_requires_tty_valid_url_and_one_decision(store):
    pid = setup_ready(store)
    p, _ = publisher(store, status(500))
    publishing.publish(store, pid, p, confirm=ok(pid), **TTY)
    c = lambda _: f"RECONCILE {pid}"  # noqa: E731
    with pytest.raises(StoreError, match="interactive"):
        publishing.reconcile(store, pid, not_published=True, confirm=c, is_tty=lambda: False)
    with pytest.raises(StoreError, match="LinkedIn post URL"):
        publishing.reconcile(store, pid, published_url="https://evil.example/x", confirm=c, **TTY)
    with pytest.raises(StoreError, match="exactly one"):
        publishing.reconcile(store, pid, confirm=c, **TTY)


# ── dry run & scheduler ───────────────────────────────────────────────────
def test_dry_run_reads_no_token_sends_nothing_writes_nothing(store):
    pid = setup_ready(store)

    class NoToken:
        def exists(self):
            raise AssertionError("dry run must not touch the token store")

        get = exists

    t = FakeTransport()
    p = publishing.make_publisher(store, transport=t, tokens=NoToken())
    before = tree(store.root)
    plan = publishing.dry_run(store, pid, p)
    assert tree(store.root) == before and t.calls == []
    assert plan["headers"]["Authorization"] == "Bearer <from Keychain>"
    assert plan["body"]["lifecycleState"] == "PUBLISHED"


def test_scheduler_never_publishes(store):
    from lce import jobs, scheduler

    pid = setup_ready(store)
    for _ in range(3):
        scheduler.run_once(store)
    assert store.load_post(pid)["state"] == "READY_TO_PUBLISH"
    assert publishing.load_publication(store, pid) is None
    assert all(j["state"] != "RUNNING" for j in jobs.list_jobs(store))
