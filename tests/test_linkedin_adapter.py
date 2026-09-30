import pytest
from fakes import FAKE_TOKEN, FakeTransport, created, not_sent, status, timeout_after_send

from lce.publish.base import Outcome, PostPayload, UnsupportedCapability
from lce.publish.credentials import (
    CredentialError,
    KeychainTokenStore,
    MemoryTokenStore,
    Secret,
)
from lce.publish.linkedin import (
    POSTS_URL,
    HttpResponse,
    LinkedInConfig,
    LinkedInPublisher,
    UrllibTransport,
)

CFG = LinkedInConfig(api_version="202609", person_urn="urn:li:person:TestPerson1")
PAYLOAD = PostPayload(post_id="20260101-x", idempotency_key="k", text="Hello (world) #tag\n",
                      content_hash="0" * 64, language="en")


def pub(*responses, token=FAKE_TOKEN):
    t = FakeTransport(*responses)
    return LinkedInPublisher(CFG, MemoryTokenStore(token), t), t


def test_request_shape_follows_the_posts_api():
    p, t = pub(created())
    p.publish(PAYLOAD)
    call = t.calls[0]
    assert call["method"] == "POST" and call["url"] == POSTS_URL
    h = call["headers"]
    assert h["Linkedin-Version"] == "202609" and h["X-Restli-Protocol-Version"] == "2.0.0"
    assert h["Authorization"] == f"Bearer {FAKE_TOKEN}"
    b = call["body"]
    assert b["author"] == "urn:li:person:TestPerson1"
    assert b["commentary"] == "Hello \\(world\\) #tag"
    assert b["lifecycleState"] == "PUBLISHED" and b["visibility"] == "PUBLIC"
    assert b["distribution"]["feedDistribution"] == "MAIN_FEED"


def test_201_with_urn_is_published():
    p, _ = pub(created("urn:li:share:123"))
    r = p.publish(PAYLOAD)
    assert r.outcome == Outcome.PUBLISHED and r.remote_id == "urn:li:share:123"
    assert r.url == "https://www.linkedin.com/feed/update/urn:li:share:123/"


def test_201_without_urn_is_ambiguous():
    p, _ = pub(HttpResponse(201, {}))
    assert p.publish(PAYLOAD).outcome == Outcome.AMBIGUOUS


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_client_errors_are_rejected_not_retryable(code):
    p, _ = pub(status(code))
    r = p.publish(PAYLOAD)
    assert r.outcome == Outcome.REJECTED and r.detail["retryable"] is False
    assert r.detail["http_status"] == code


def test_429_is_rejected_and_retryable_later():
    p, _ = pub(status(429))
    r = p.publish(PAYLOAD)
    assert r.outcome == Outcome.REJECTED and r.detail["retryable"] is True


@pytest.mark.parametrize("code", [409, 500, 502, 503, 504, 302])
def test_conflict_server_errors_and_unexpected_codes_are_ambiguous(code):
    p, _ = pub(status(code))
    assert p.publish(PAYLOAD).outcome == Outcome.AMBIGUOUS


def test_timeout_after_send_is_ambiguous_and_not_retried():
    p, t = pub(timeout_after_send(), created())
    assert p.publish(PAYLOAD).outcome == Outcome.AMBIGUOUS
    assert len(t.calls) == 1  # exactly one request, no hidden retry


def test_failure_before_send_is_rejected():
    p, _ = pub(not_sent())
    r = p.publish(PAYLOAD)
    assert r.outcome == Outcome.REJECTED and r.detail["sent"] is False


def test_missing_token_sends_nothing():
    p, t = pub(created(), token=None)
    r = p.publish(PAYLOAD)
    assert r.outcome == Outcome.REJECTED and r.detail["reason"] == "credentials"
    assert t.calls == []


def test_validation_blocks_before_sending():
    p, t = pub(created())
    for payload in (PostPayload("x", "k", " ", "0" * 64, "en"),
                    PostPayload("x", "k", "a" * 3001, "0" * 64, "en"),
                    PostPayload("x", "k", "ok", "0" * 64, "en", scheduled_at_utc="2030-01-01"),
                    PostPayload("x", "k", "ok", "0" * 64, "en", media_urls=("u",))):
        assert p.publish(payload).outcome == Outcome.REJECTED
    bad = LinkedInPublisher(LinkedInConfig("26-09", "person:1"), MemoryTokenStore("t"), t)
    assert {i.code for i in bad.validate(PAYLOAD)} == {"config"}
    assert t.calls == []


def test_capabilities_are_truthful():
    p, _ = pub()
    c = p.capabilities
    assert c.can_publish and not c.can_find_existing and not c.can_get_status
    assert not c.can_schedule and not p.supports_native_scheduling and c.supports_media
    with pytest.raises(UnsupportedCapability):
        p.find_existing("k", "0" * 64)
    with pytest.raises(UnsupportedCapability):
        p.get_status("urn:li:share:1")


def test_whoami_builds_person_urn():
    p, t = pub(HttpResponse(200, {}, b'{"sub": "abc_123", "name": "Test"}'))
    assert p.whoami()["person_urn"] == "urn:li:person:abc_123"
    assert t.calls[0]["url"] == "https://api.linkedin.com/v2/userinfo"


def test_token_never_leaks_through_repr_or_results():
    s = Secret(FAKE_TOKEN)
    assert FAKE_TOKEN not in repr(s) and FAKE_TOKEN not in str(s)
    for resp in (created(), status(500), status(400)):
        p, _ = pub(resp)
        assert FAKE_TOKEN not in repr(p.publish(PAYLOAD))
    assert FAKE_TOKEN not in repr(pub()[0])


def test_keychain_store_uses_security_cli_without_printing(monkeypatch):
    calls = []

    class R:
        def __init__(self, code, out=""):
            self.returncode, self.stdout = code, out

    def runner(cmd, **kw):
        calls.append(cmd)
        return R(0, FAKE_TOKEN + "\n") if "-w" in cmd else R(0)

    store = KeychainTokenStore("svc", "acct", runner=runner)
    assert store.exists()
    assert store.get().reveal() == FAKE_TOKEN
    assert calls[0] == ["security", "find-generic-password", "-s", "svc", "-a", "acct"]
    missing = KeychainTokenStore("svc", "acct", runner=lambda cmd, **kw: R(44))
    assert not missing.exists()
    with pytest.raises(CredentialError):
        missing.get()


def test_real_transport_refuses_non_linkedin_urls():
    from lce.publish.linkedin import TransportError

    with pytest.raises(TransportError) as e:
        UrllibTransport().request("POST", "https://example.com/", {}, b"", 1)
    assert e.value.sent is False


def test_network_is_blocked_in_tests():
    import socket

    with pytest.raises(AssertionError, match="blocked"):
        socket.create_connection(("api.linkedin.com", 443), timeout=1)
