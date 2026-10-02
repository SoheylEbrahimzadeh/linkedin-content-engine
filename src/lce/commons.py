"""Rights-safe third-party images from Wikimedia Commons (LCE-038).

Only the Commons API is used (no scraping). A file is accepted only when its
machine-readable licence is public domain, CC0, CC BY or CC BY-SA (no NC/ND,
no fair use, no unknown); the credit, licence and licence URL are recorded from
the API, and the downloaded bytes must match the SHA-1 the API reports. The
session still has to say how the image relates to the post (no decoration).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from lce import images
from lce.store import DataStore, StoreError

API = "https://commons.wikimedia.org/w/api.php"
UPLOAD_PREFIX = "https://upload.wikimedia.org/"
UA = "lce-cli (linkedin-content-engine; https://github.com/SoheylEbrahimzadeh/linkedin-content-engine)"
MAX_BYTES = 8_000_000
ALLOWED = [(re.compile(r"^(public domain|pd(-.*)?)$", re.I), "public_domain"),
           (re.compile(r"^cc0( 1\.0)?$", re.I), "public_domain"),
           (re.compile(r"^cc by(-sa)? \d\.\d( [a-z\-]+)?$", re.I), "licensed")]
REJECT = re.compile(r"\b(nc|nd|non-?commercial|no ?deriv|fair use)\b", re.I)


def _get(url: str, transport=None) -> bytes:
    if transport:
        return transport(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        return r.read(MAX_BYTES + 1)


def _plain(value: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def license_usage(short_name: str) -> str | None:
    name = (short_name or "").strip()
    if not name or REJECT.search(name):
        return None
    for rx, usage in ALLOWED:
        if rx.match(name):
            return usage
    return None


def lookup(title: str, transport=None) -> dict:
    """Image URL, size, SHA-1 and licence metadata for one Commons file."""
    if not title.startswith("File:"):
        title = "File:" + title
    q = urllib.parse.urlencode({"action": "query", "titles": title, "prop": "imageinfo", "format": "json",
                                "iiprop": "url|size|mime|sha1|extmetadata"})
    data = json.loads(_get(f"{API}?{q}", transport))
    page = next(iter(data.get("query", {}).get("pages", {}).values()), {})
    info = (page.get("imageinfo") or [None])[0]
    if not info:
        raise StoreError(f"{title} not found on Wikimedia Commons")
    meta = info.get("extmetadata") or {}
    field = lambda k: _plain((meta.get(k) or {}).get("value"))  # noqa: E731
    return {"title": title, "url": info["url"], "description_url": info.get("descriptionurl"),
            "sha1": info.get("sha1"), "mime": info.get("mime"), "width": info.get("width"),
            "height": info.get("height"), "bytes": info.get("size"),
            "license": field("LicenseShortName"), "license_url": field("LicenseUrl") or None,
            "artist": field("Artist"), "credit": field("Credit"),
            "attribution_required": field("AttributionRequired").lower() == "true"}


def attach(store: DataStore, post_id: str, title: str, *, relation: str, alt_text: str,
           rationale: str, transport=None) -> dict:
    info = lookup(title, transport)
    usage = license_usage(info["license"])
    if usage is None:
        raise StoreError(f"licence {info['license']!r} is not rights-safe for reuse "
                         "(needs PD, CC0, CC BY or CC BY-SA)")
    if info["mime"] not in images.MIME.values():
        raise StoreError(f"unsupported image type {info['mime']}")
    if not info["url"].startswith(UPLOAD_PREFIX):
        raise StoreError("unexpected image host")
    data = _get(info["url"], transport)
    if len(data) > MAX_BYTES:
        raise StoreError("image is too large")
    if info["sha1"] and hashlib.sha1(data).hexdigest() != info["sha1"]:  # noqa: S324 - Commons reports SHA-1
        raise StoreError("downloaded file does not match the SHA-1 reported by Commons")
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif"}[info["mime"]]
    credit = info["artist"] or info["credit"] or "Wikimedia Commons contributor"
    prov = {"origin": "licensed_stock", "usage": usage, "license": info["license"],
            "source_url": info["description_url"] or info["url"], "credit": f"{credit} via Wikimedia Commons",
            "title": info["title"]}
    if info["license_url"]:
        prov["license_url"] = info["license_url"]
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / f"commons{ext}"
        f.write_bytes(data)
        return images.decide(store, post_id, kind="source_image", rationale=rationale, source_file=str(f),
                             relation=relation, alt_text=alt_text, provenance=prov, decided_by="agent")
