from lce.publish.base import Outcome, PostPayload, ProviderCapabilities, Publisher, PublishResult


class FakePublisher:
    name = "fake"
    supports_native_scheduling = False
    capabilities = ProviderCapabilities(True, False, False, False, False, None)

    def validate(self, post):
        return []

    def publish(self, post):
        return PublishResult(outcome=Outcome.PUBLISHED, remote_id="1")

    def find_existing(self, idempotency_key, content_hash):
        return None

    def get_status(self, remote_id):
        raise NotImplementedError


def test_protocol_shape():
    assert isinstance(FakePublisher(), Publisher)
    payload = PostPayload(post_id="p", idempotency_key="k", text="t",
                          content_hash="0" * 64, language="en")
    assert FakePublisher().publish(payload).outcome is Outcome.PUBLISHED


def test_linkedin_is_the_only_adapter_and_satisfies_the_protocol():
    from importlib import resources

    import lce.publish as pkg
    from lce.publish.credentials import MemoryTokenStore
    from lce.publish.linkedin import LinkedInConfig, LinkedInPublisher

    modules = {p.name for p in resources.files(pkg).iterdir() if p.name.endswith(".py")}
    assert modules == {"__init__.py", "base.py", "credentials.py", "linkedin.py", "little.py"}
    pub = LinkedInPublisher(LinkedInConfig("202609", "urn:li:person:abc"), MemoryTokenStore(None),
                            transport=None)
    assert isinstance(pub, Publisher)
    assert pub.capabilities.can_publish and not pub.capabilities.can_find_existing
