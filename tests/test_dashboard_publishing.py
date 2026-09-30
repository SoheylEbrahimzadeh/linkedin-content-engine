from fakes import FAKE_TOKEN, FakeTransport, created, status
from test_publishing import TTY, ok, setup_ready

from lce import publishing
from lce.clock import FixedClock, use_clock
from lce.dashboard.snapshot import build_snapshot, to_json
from lce.publish.credentials import MemoryTokenStore


def pub_with(store, *responses):
    t = FakeTransport(*responses)
    return publishing.make_publisher(store, transport=t, tokens=MemoryTokenStore(FAKE_TOKEN))


def test_published_post_shows_record_and_status(store):
    pid = setup_ready(store)
    publishing.publish(store, pid, pub_with(store, created("urn:li:share:77")), confirm=ok(pid),
                       **TTY)
    snap = build_snapshot(store, mode="real")
    post = next(p for p in snap["posts"] if p["post_id"] == pid)
    assert post["state"] == "PUBLISHED" and post["publication_status"] == "published"
    rec = snap["publishing"]["records"][0]
    assert rec["remote_id"] == "urn:li:share:77" and rec["verified_by"] == "api_response"
    stages = {s["id"]: s["status"] for s in snap["latest_run"]["stages"]}
    assert stages["publishing"] == "done" and stages["verification"] == "done"
    dist = {b["id"]: b["count"] for b in snap["state_distribution"]}
    assert dist["published"] == 1
    assert snap["issues"] == []
    assert FAKE_TOKEN not in to_json(snap)


def test_ambiguous_and_failed_attempts_become_issues(store):
    pid = setup_ready(store)
    publishing.publish(store, pid, pub_with(store, status(503)), confirm=ok(pid), **TTY)
    snap = build_snapshot(store, mode="real")
    assert any(i["kind"] == "NEEDS_RECONCILE" and i["post_id"] == pid for i in snap["issues"])
    post = next(p for p in snap["posts"] if p["post_id"] == pid)
    assert post["publication_status"] == "unknown"


def test_publish_failed_is_a_failed_issue(store):
    pid = setup_ready(store)
    publishing.publish(store, pid, pub_with(store, status(403)), confirm=ok(pid), **TTY)
    snap = build_snapshot(store, mode="real")
    assert any(i["kind"] == "FAILED" and i["post_id"] == pid for i in snap["issues"])


def test_config_and_token_expiry_without_touching_the_keychain(store, monkeypatch):
    import lce.publish.credentials as cred

    monkeypatch.setattr(cred.KeychainTokenStore, "exists",
                        lambda self: (_ for _ in ()).throw(AssertionError("no keychain access")))
    setup_ready(store)
    (store.root / "config" / "linkedin.yaml").write_text(
        "api_version: '202509'\nperson_urn: urn:li:person:TestPerson1\n"
        "token_expires_at: '2026-10-05T00:00:00+00:00'\n")
    with use_clock(FixedClock("2026-09-30T12:00:00+00:00")):
        pub = build_snapshot(store, mode="real")["publishing"]
        demo = build_snapshot(store, mode="demo")["publishing"]
    assert pub["provider_enabled"] and pub["token"]["days_left"] == 4
    assert pub["token"]["checked"] is False
    assert pub["linkedin_config"]["api_version_age_months"] == 12
    assert pub["linkedin_config"]["person_urn"] == "urn:li:person:TestPerson1"
    assert demo["linkedin_config"]["person_urn"] is None  # never in a demo build


def test_interrupted_publishing_is_flagged(store):
    from lce.posts import set_state

    pid = setup_ready(store)
    post = store.load_post(pid)
    set_state(store, post, __import__("lce.state", fromlist=["PostState"]).PostState.PUBLISHING,
              "simulated crash")
    issues = build_snapshot(store, mode="real")["issues"]
    assert any("interrupted" in i["message"] for i in issues)
