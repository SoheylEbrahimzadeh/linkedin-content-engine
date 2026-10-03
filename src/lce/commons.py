"""Rights-safe third-party images from Wikimedia Commons (LCE-038).

Only the Commons API is used (no scraping). A file is accepted only when its
machine-readable licence is public domain, CC0, CC BY or CC BY-SA (no NC/ND,
no fair use, no unknown); the credit, licence and licence URL are recorded from
the API, and the downloaded bytes must match the SHA-1 the API reports. The
session still has to say how the image relates to the post (no decoration).

LCE-043: `select` picks a real image for a post semantically. The session states
the post's subject, the visual concept and the subject terms; candidate files
(named, or found with the Commons search API) are accepted only when the
licence allows reuse AND the file's own metadata (title, description,
categories) names at least one subject term. Every candidate tried is recorded
with why it was refused. Nothing suitable → the caller records text-only.
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
from lce.store import DataStore, StoreError, now_iso

API = "https://commons.wikimedia.org/w/api.php"
UPLOAD_PREFIX = "https://upload.wikimedia.org/"
UA = "lce-cli (linkedin-content-engine; https://github.com/SoheylEbrahimzadeh/linkedin-content-engine)"
MAX_BYTES = 8_000_000
ALLOWED = [
    (re.compile(r"^(public domain|pd(-.*)?)$", re.I), "public_domain"),
    (re.compile(r"^cc0( 1\.0)?$", re.I), "public_domain"),
    (re.compile(r"^cc by(-sa)? \d\.\d( [a-z\-]+)?$", re.I), "licensed"),
]
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


IIPROP = "url|size|mime|sha1|extmetadata"
THUMB_WIDTH = 1600


def _info(title: str, page: dict) -> dict | None:
    info = (page.get("imageinfo") or [None])[0]
    if not info:
        return None
    meta = info.get("extmetadata") or {}
    field = lambda k: _plain((meta.get(k) or {}).get("value"))  # noqa: E731
    return {
        "title": title,
        "url": info["url"],
        "description_url": info.get("descriptionurl"),
        "sha1": info.get("sha1"),
        "mime": info.get("mime"),
        "width": info.get("width"),
        "height": info.get("height"),
        "bytes": info.get("size"),
        "thumb_url": info.get("thumburl"),
        "thumb_width": info.get("thumbwidth"),
        "license": field("LicenseShortName"),
        "license_url": field("LicenseUrl") or None,
        "artist": field("Artist"),
        "credit": field("Credit"),
        "description": field("ImageDescription")[:1000],
        "object_name": field("ObjectName"),
        "categories": [c for c in field("Categories").split("|") if c],
        "attribution_required": field("AttributionRequired").lower() == "true",
    }


def lookup(title: str, transport=None) -> dict:
    """Image URL, size, SHA-1, licence and descriptive metadata for one Commons file."""
    if not title.startswith("File:"):
        title = "File:" + title
    q = urllib.parse.urlencode(
        {
            "action": "query",
            "titles": title,
            "prop": "imageinfo",
            "format": "json",
            "iiprop": IIPROP,
            "iiurlwidth": THUMB_WIDTH,
        }
    )
    data = json.loads(_get(f"{API}?{q}", transport))
    page = next(iter(data.get("query", {}).get("pages", {}).values()), {})
    info = _info(title, page)
    if not info:
        raise StoreError(f"{title} not found on Wikimedia Commons")
    return info


def search(query: str, transport=None, limit: int = 10) -> list[dict]:
    """Files matching `query` (Commons search API, file namespace), with their metadata."""
    q = urllib.parse.urlencode(
        {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": limit,
            "prop": "imageinfo",
            "format": "json",
            "iiprop": IIPROP,
            "iiurlwidth": THUMB_WIDTH,
        }
    )
    data = json.loads(_get(f"{API}?{q}", transport))
    pages = sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    return [i for i in (_info(p.get("title", ""), p) for p in pages) if i]


EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif"}
DRAWABLE = set(EXT) | {"image/svg+xml", "image/tiff", "image/webp"}  # these come as a PNG/JPEG thumbnail


def _fetch(info: dict, transport=None) -> tuple[bytes, str, dict]:
    """The file to post: the original when it is small enough (SHA-1 verified against
    Commons), otherwise the Commons-rendered thumbnail of that same file."""
    if info["mime"] not in DRAWABLE:
        raise StoreError(f"unsupported image type {info['mime']}")
    small = (info.get("width") or 0) <= THUMB_WIDTH and (info.get("bytes") or 0) <= MAX_BYTES
    if info["mime"] in EXT and (small or not info.get("thumb_url")):
        if not info["url"].startswith(UPLOAD_PREFIX):
            raise StoreError("unexpected image host")
        data = _get(info["url"], transport)
        if len(data) > MAX_BYTES:
            raise StoreError("image is too large")
        if info["sha1"] and hashlib.sha1(data).hexdigest() != info["sha1"]:  # noqa: S324 - Commons reports SHA-1
            raise StoreError("downloaded file does not match the SHA-1 reported by Commons")
        return (
            data,
            EXT[info["mime"]],
            {
                "retrieved": "original",
                "retrieved_url": info["url"],
                "original_sha1_verified": bool(info["sha1"]),
            },
        )
    url = info.get("thumb_url") or ""
    if not url.startswith(UPLOAD_PREFIX):
        raise StoreError("no Commons thumbnail for this file")
    data = _get(url, transport)
    if len(data) > MAX_BYTES:
        raise StoreError("thumbnail is too large")
    ext = (
        ".png"
        if data.startswith(images.MAGIC[".png"])
        else ".jpg"
        if data.startswith(images.MAGIC[".jpg"])
        else None
    )
    if ext is None:
        raise StoreError("the thumbnail is not a PNG or JPEG")
    return (
        data,
        ext,
        {
            "retrieved": f"Commons thumbnail ({info.get('thumb_width') or THUMB_WIDTH}px wide)",
            "retrieved_url": url,
            "original_sha1_verified": False,
        },
    )


def _provenance(info: dict, usage: str, got: dict) -> dict:
    creator = info["artist"] or info["credit"] or "Wikimedia Commons contributor"
    prov = {
        "origin": "licensed_stock",
        "usage": usage,
        "license": info["license"],
        "source_url": info["description_url"] or info["url"],
        "credit": f"{creator} via Wikimedia Commons",
        "title": info["title"],
        "creator": creator[:300],
        "attribution_required": bool(info["attribution_required"])
        or info["license"].lower().startswith("cc by"),
        "attribution": attribution(info),
        "retrieved_at": now_iso(),
        "retrieved": got["retrieved"],
        "retrieved_url": got["retrieved_url"],
    }
    if info.get("sha1"):
        prov["original_sha1"] = info["sha1"]
    if info["license_url"]:
        prov["license_url"] = info["license_url"]
    return prov


def attribution(info: dict) -> str:
    """The credit line for the post text (CC BY / BY-SA require it; given for PD too)."""
    creator = (info["artist"] or info["credit"] or "Wikimedia Commons contributor")[:120]
    return f"Image: {creator}, {info['license']}, via Wikimedia Commons"


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def semantic_match(info: dict, terms: list[str]) -> list[str]:
    """The subject terms the file's own metadata names (title, description, categories,
    object name). A term matches when every word of it appears there."""
    hay = _words(
        " ".join(
            [
                info.get("title", ""),
                info.get("description", ""),
                info.get("object_name", ""),
                " ".join(info.get("categories") or []),
            ]
        )
    )
    return [t for t in terms if _words(t) and _words(t) <= hay]


SPEC_MIN = {"subject": 10, "concept": 15, "relevance_reason": 30, "alt_text": 40, "relation": 20}


def select(
    store: DataStore,
    post_id: str,
    spec: dict,
    *,
    transport=None,
    exclude_sha256: set | None = None,
    exclude_titles: set | None = None,
) -> tuple[dict | None, dict]:
    """Try the named candidates, then the search queries; attach the first file whose
    licence allows reuse and whose metadata names the post's subject. Returns
    (image decision or None, the selection record with every candidate tried)."""
    for k, n in SPEC_MIN.items():
        if len((spec.get(k) or "").strip()) < n:
            raise StoreError(f"commons media needs {k} (at least {n} characters)")
    terms = [t.strip() for t in spec.get("subject_terms") or [] if t and t.strip()]
    if not terms:
        raise StoreError("commons media needs subject_terms: what the image must show (e.g. 'data center')")
    if not (spec.get("candidates") or spec.get("search")):
        raise StoreError("commons media needs candidates (file titles) or search queries")
    exclude_sha256, exclude_titles = exclude_sha256 or set(), exclude_titles or set()
    record = {
        "source": "Wikimedia Commons API",
        "subject": spec["subject"].strip(),
        "concept": spec["concept"].strip(),
        "subject_terms": terms,
        "candidates": list(spec.get("candidates") or []),
        "search": list(spec.get("search") or []),
        "tried": [],
        "selected": None,
        "searched_at": now_iso(),
    }
    max_tries = int(spec.get("max_tries", 25))

    def infos():
        for t in spec.get("candidates") or []:
            try:
                yield lookup(t, transport)
            except (StoreError, OSError, ValueError) as exc:
                record["tried"].append(
                    {"title": t, "outcome": "refused", "why": f"lookup failed: {exc}"[:300]}
                )
        for q in spec.get("search") or []:
            try:
                yield from search(q, transport)
            except (OSError, ValueError) as exc:
                record["tried"].append(
                    {"title": f"search: {q}", "outcome": "refused", "why": f"search failed: {exc}"[:300]}
                )

    seen = set()
    for info in infos():
        if len(record["tried"]) >= max_tries:
            break
        if info["title"] in seen:
            continue
        seen.add(info["title"])
        row = {"title": info["title"], "license": info["license"] or None, "url": info["description_url"]}
        usage = license_usage(info["license"])
        matched = semantic_match(info, terms)
        why = None
        if usage is None:
            lic = info["license"] or "unknown"
            why = f"licence {lic!r} does not allow reuse (needs PD, CC0, CC BY or CC BY-SA)"
        elif info["mime"] not in DRAWABLE:
            why = f"unsupported type {info['mime']}"
        elif not matched:
            why = "its title, description and categories name none of the subject terms"
        elif info["title"] in exclude_titles:
            why = "an earlier version of this post used this file"
        if why:
            record["tried"].append({**row, "outcome": "refused", "why": why})
            continue
        try:
            data, ext, got = _fetch(info, transport)
        except (StoreError, OSError) as exc:
            record["tried"].append({**row, "outcome": "refused", "why": f"retrieval failed: {exc}"[:300]})
            continue
        if hashlib.sha256(data).hexdigest() in exclude_sha256:
            record["tried"].append({**row, "outcome": "refused", "why": "same file as an earlier version"})
            continue
        size = images.dimensions(data, ext)
        if not size or size[0] * size[1] >= images.MAX_PIXELS:
            record["tried"].append(
                {**row, "outcome": "refused", "why": "unreadable or too large for LinkedIn"}
            )
            continue
        record["tried"].append({**row, "outcome": "selected", "matched_terms": matched})
        record["selected"] = info["title"]
        prov = _provenance(info, usage, got)
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / f"commons{ext}"
            f.write_bytes(data)
            doc = images.decide(
                store,
                post_id,
                kind="source_image",
                rationale=spec.get("rationale") or spec["relevance_reason"],
                source_file=str(f),
                relation=spec["relation"],
                alt_text=spec["alt_text"],
                provenance=prov,
                decided_by="agent",
                relevance={
                    "concept": spec["concept"],
                    "visual_type": spec.get("visual_type", "photo"),
                    "reason": spec["relevance_reason"],
                },
            )
        doc["media_relevance"]["semantic"] = {
            "subject": record["subject"],
            "subject_terms": terms,
            "matched_terms": matched,
            "metadata_checked": True,
            "source_title": info["title"],
            "source_description": info["description"][:300] or None,
            "source_categories": info["categories"][:12],
        }
        doc["selection"] = record
        store.write_doc(images.path(store, post_id), "image", doc)
        return doc, record
    return None, record


def attach(
    store: DataStore,
    post_id: str,
    title: str,
    *,
    relation: str,
    alt_text: str,
    rationale: str,
    transport=None,
    relevance: dict | None = None,
) -> dict:
    info = lookup(title, transport)
    usage = license_usage(info["license"])
    if usage is None:
        raise StoreError(
            f"licence {info['license']!r} is not rights-safe for reuse (needs PD, CC0, CC BY or CC BY-SA)"
        )
    data, ext, got = _fetch(info, transport)
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / f"commons{ext}"
        f.write_bytes(data)
        return images.decide(
            store,
            post_id,
            kind="source_image",
            rationale=rationale,
            source_file=str(f),
            relation=relation,
            alt_text=alt_text,
            provenance=_provenance(info, usage, got),
            decided_by="agent",
            relevance=relevance,
        )
