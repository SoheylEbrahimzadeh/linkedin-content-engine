from lce.publish.base import Outcome, PostPayload, Publisher, PublishResult


class FakePublisher:
    name = "fake"
    supports_native_scheduling = False

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


def test_no_adapters_shipped_yet():
    from importlib import resources

    import lce.publish as pkg

    modules = {p.name for p in resources.files(pkg).iterdir() if p.name.endswith(".py")}
    assert modules == {"__init__.py", "base.py"}
