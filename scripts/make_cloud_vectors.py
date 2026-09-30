"""Generate cloud/test/vectors.json from the Python reference implementation.

The TypeScript Worker must produce identical results (tests on both sides):
content hashes, little escaping, schedule slots (incl. DST) and LinkedIn
response → outcome mapping. Synthetic inputs only.

    python scripts/make_cloud_vectors.py
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from lce.publish.base import ImageAttachment, PostPayload
from lce.publish.credentials import MemoryTokenStore
from lce.publish.linkedin import HttpResponse, LinkedInConfig, LinkedInPublisher
from lce.publish.little import to_little
from lce.schedule import load_schedule, slots_between
from lce.textutil import content_hash, normalize_text

OUT = Path(__file__).resolve().parents[1] / "cloud" / "test" / "vectors.json"

TEXTS = [
    "Hello world",
    "Line one  \r\nLine two\t\r\n\r\n",
    "  leading and trailing  \n\n",
    "Café (decomposed) vs Café",
    "Persian: سلام دنیا ۱۲۳ #تست",
    "Emoji 🚀 and symbols (a) [b] {c} <d> @e #f _g *h ~i |j \\k",
    "BOM﻿ inside and NEL\u0085 end\u0085",
    "Separators\u001c\u001d end   x　",
    "#Start hashtag, mid#not, # alone, #1, (#paren)\n#Line",
    "Tabs\tand nbsp ",
]

SUN_0230 = {"posts_per_week": 1, "slots": [{"day": "sun", "time": "02:30"}]}
MON_0600 = {"posts_per_week": 1, "slots": [{"day": "mon", "time": "06:00"}]}
SCHEDULES = [
    ({"timezone": "America/Denver", "cadence": {"posts_per_week": 3, "slots": [
        {"day": "mon", "time": "11:10"}, {"day": "wed", "time": "00:30"}, {"day": "fri", "time": "15:45"}]}},
     ["2026-03-01T00:00:00+00:00", "2026-03-15T00:00:00+00:00"]),
    ({"timezone": "Europe/Berlin", "cadence": SUN_0230},
     ["2026-03-27T00:00:00+00:00", "2026-03-31T00:00:00+00:00"]),
    ({"timezone": "Europe/Berlin", "cadence": SUN_0230},
     ["2026-10-23T00:00:00+00:00", "2026-10-27T00:00:00+00:00"]),
    ({"timezone": "America/Chicago", "cadence": {"posts_per_week": 2, "slots": [
        {"day": "mon", "time": "09:15"}, {"day": "fri", "time": "17:45"}]}},
     ["2026-12-27T00:00:00+00:00", "2027-01-06T00:00:00+00:00"]),
    ({"timezone": "Pacific/Auckland", "cadence": MON_0600},
     ["2026-09-20T00:00:00+00:00", "2026-10-06T00:00:00+00:00"]),
]

IMAGE_URN = "urn:li:image:C4E10AQVector1"
IMAGE_ALT = "  Diagram: three queues (fictional)  "

RESPONSES = [
    (201, {"x-restli-id": "urn:li:share:7000000000000000001"}),
    (201, {"x-restli-id": "urn:li:ugcPost:42"}),
    (201, {}),
    (201, {"x-restli-id": "garbage"}),
    (400, {}), (401, {}), (403, {}), (404, {}), (422, {}), (429, {}),
    (409, {}), (500, {}), (502, {}), (503, {}), (504, {}), (302, {}), (200, {}),
]


def main() -> None:
    iso = datetime.fromisoformat
    pub = LinkedInPublisher(LinkedInConfig("202609", "urn:li:person:Vector1"), MemoryTokenStore("x"),
                            transport=None)
    payload = PostPayload("20260101-x", "k", "t", "0" * 64, "en")
    _, headers, body = pub.build_request(PostPayload("20260101-x", "k", TEXTS[5], "0" * 64, "en"))
    doc = {
        "generated_by": "scripts/make_cloud_vectors.py",
        "texts": [{"text": t, "normalized": normalize_text(t), "hash": content_hash(t),
                   "little": to_little(t.strip())} for t in TEXTS],
        "schedules": [{"settings": s, "start": a, "end": b,
                       "slots": [x.to_dict() for x in slots_between(load_schedule(s), iso(a), iso(b))]}
                      for s, (a, b) in SCHEDULES],
        "responses": [],
        "request": {"text": TEXTS[5], "headers": headers, "body": body},
        "request_image": {"text": TEXTS[5], "urn": IMAGE_URN, "alt": IMAGE_ALT,
                          "body": pub.build_request(PostPayload(
                              "20260101-x", "k", TEXTS[5], "0" * 64, "en",
                              image=ImageAttachment(b"x", IMAGE_ALT, "0" * 64, "image.png")),
                              IMAGE_URN)[2]},
    }
    for code, hdrs in RESPONSES:
        r = pub._map(HttpResponse(code, hdrs, b"{}"))
        doc["responses"].append({"status": code, "headers": hdrs, "outcome": r.outcome.value,
                                 "remote_id": r.remote_id,
                                 "retryable": (r.detail or {}).get("retryable")})
    del payload
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", "utf-8")
    print(f"wrote {OUT.relative_to(OUT.parents[2])} at {datetime.now(UTC):%Y-%m-%d}")


if __name__ == "__main__":
    main()
