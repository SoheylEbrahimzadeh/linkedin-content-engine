"""Fake LinkedIn transport for tests. No test ever talks to LinkedIn."""

import json

from lce.publish import linkedin as provider

HttpResponse = provider.HttpResponse
TransportError = provider.TransportError

FAKE_TOKEN = "fake.token.for.tests.only"


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers, body, timeout):
        try:
            parsed = json.loads(body) if body else None
        except (ValueError, UnicodeDecodeError):
            parsed = {"raw_bytes": len(body)}
        self.calls.append({"method": method, "url": url, "headers": dict(headers),
                           "body": parsed})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def created(urn="urn:li:share:7000000000000000001"):
    return HttpResponse(201, {"x-restli-id": urn})


def status(code, body=b'{"message": "error", "code": "X"}'):
    return HttpResponse(code, {}, body)


def not_sent():
    return TransportError("network error: gaierror", sent=False)


def timeout_after_send():
    return TransportError("network error after sending: TimeoutError", sent=True)


def image_init(urn="urn:li:image:C4E10AQFakeImage1",
               url="https://www.linkedin.com/dms-uploads/fake/uploaded-image/0?ut=x"):
    return HttpResponse(200, {}, json.dumps({"value": {"uploadUrl": url, "image": urn,
                                                       "uploadUrlExpiresAt": 1}}).encode())


def uploaded():
    return HttpResponse(201, {})
