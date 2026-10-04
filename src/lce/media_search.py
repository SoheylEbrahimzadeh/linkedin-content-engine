"""Reviewed real images from several licensed sources (LCE-044).

Word matches in metadata are not relevance: in production a 1997 post-office photo
and a generic accounting screenshot both "matched". So a real image is chosen in
two steps:

1. `collect` searches the supported sources through their public APIs (never
   scraping, never Google Images): Wikimedia Commons, then Openverse (an index of
   openly licensed images from Flickr, museums, StockSnap, Rawpixel and others).
   Only licences that allow commercial reuse with adaptation (resizing) are kept:
   public domain, CC0, CC BY, CC BY-SA. Every candidate gets a small preview, its
   licence record and its source identifiers; nothing is attached.
2. A session LOOKS at the previews, writes down what each image actually depicts
   and why it does or does not illustrate the post, and names one candidate (or
   decides text-only). `fetch` retrieves that exact file again, re-checks the
   licence from the source's own record, hashes it, and the image is attached
   with the review (what it depicts, why it is relevant, who reviewed).

Unsplash and Pexels are not searched: their APIs need a key (a secret this
project does not hold) and their websites may not be scraped.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from pathlib import Path

from lce import commons, images
from lce.store import StoreError, now_iso

OPENVERSE = "https://api.openverse.org/v1/images/"
OPENVERSE_LICENSES = {"by": "CC BY", "by-sa": "CC BY-SA", "cc0": "CC0", "pdm": "Public domain"}
SOURCES = ("commons", "openverse")
NOT_SEARCHED = {
    "unsplash": "API needs a key (no secret held for it); the website may not be scraped",
    "pexels": "API needs a key (no secret held for it); the website may not be scraped",
}
PREVIEW_MAX = 2_000_000


def _json(url: str, transport=None) -> dict:
    return json.loads(commons._get(url, transport))


def _commons_candidates(query: str, transport=None, limit: int = 12) -> list[dict]:
    out = []
    for info in commons.search(query, transport, limit=limit):
        usage = commons.license_usage(info["license"])
        out.append(
            {
                "source": "commons",
                "id": info["title"],
                "title": info["title"],
                "creator": (info["artist"] or info["credit"] or "")[:300] or None,
                "license": info["license"] or None,
                "license_url": info["license_url"],
                "landing_url": info["description_url"],
                "file_url": info["url"],
                "preview_url": info.get("thumb_url"),
                "width": info.get("width"),
                "height": info.get("height"),
                "description": info.get("description", "")[:400] or None,
                "tags": info.get("categories", [])[:12],
                "restrictions": info.get("restrictions") or [],
                "reusable": usage is not None and info["mime"] in commons.DRAWABLE,
                "why_not_reusable": None
                if usage is not None
                else f"licence {info['license'] or 'unknown'!r} does not allow reuse",
            }
        )
    return out


def openverse_license(item: dict) -> str | None:
    """'CC BY 2.0' etc., or None when the licence does not allow commercial reuse with adaptation."""
    name = OPENVERSE_LICENSES.get((item.get("license") or "").lower())
    if not name:
        return None
    ver = item.get("license_version") or ""
    return f"{name} {ver}".strip() if name.startswith("CC BY") else name


def _openverse_candidates(query: str, transport=None, limit: int = 20) -> list[dict]:
    q = urllib.parse.urlencode(
        {"q": query, "license": ",".join(OPENVERSE_LICENSES), "page_size": limit, "mature": "false"}
    )
    data = _json(f"{OPENVERSE}?{q}", transport)
    out = []
    for it in data.get("results", []):
        lic = openverse_license(it)
        out.append(
            {
                "source": "openverse",
                "id": it["id"],
                "title": it.get("title") or it["id"],
                "creator": (it.get("creator") or "")[:300] or None,
                "license": lic,
                "license_url": it.get("license_url"),
                "landing_url": it.get("foreign_landing_url"),
                "file_url": it.get("url"),
                "preview_url": it.get("thumbnail") or f"{OPENVERSE}{it['id']}/thumb/",
                "width": it.get("width"),
                "height": it.get("height"),
                "provider": it.get("provider"),
                "description": None,
                "tags": [t.get("name") for t in it.get("tags") or [] if t.get("name")][:12],
                "restrictions": [],
                "reusable": lic is not None,
                "why_not_reusable": None if lic else f"licence {it.get('license')!r} is not reusable",
            }
        )
    return out


def collect(spec: dict, out_dir: Path, transport=None) -> dict:
    """Search every supported source for every query; save a preview of each reusable
    candidate and `candidates.yaml` for a visual review. Attaches nothing."""
    queries = [q for q in spec.get("queries") or [] if q and q.strip()]
    source_urls = [u for u in spec.get("source_urls") or [] if str(u).startswith("http")]
    if not queries and not source_urls:
        raise StoreError(
            "the media search needs source_urls (the post's own sources, inspected first) or queries"
        )
    sources = [s for s in spec.get("sources") or SOURCES if s in SOURCES]
    per_query = int(spec.get("per_query", 12))
    cap = int(spec.get("max_candidates", 60))
    out_dir.mkdir(parents=True, exist_ok=True)
    # LCE-046: the post's own source first - what visuals it has and what its rights allow.
    from lce import source_visuals

    inspection = [source_visuals.inspect(u, out_dir, transport) for u in source_urls]
    # the publisher's own copyright / reuse policy, as rights evidence (text only)
    policies = []
    for u in [u for u in spec.get("rights_policy_urls") or [] if str(u).startswith("http")]:
        (out_dir / "policy").mkdir(exist_ok=True)
        pol = source_visuals.inspect(u, out_dir / "policy", transport)
        policies.append(
            {
                k: pol.get(k)
                for k in ("source_url", "via", "http_status", "archive_status", "title", "rights_signals")
            }
        )
    seen, cands, errors = set(), [], []
    for q in queries:
        for src in sources:
            try:
                found = (_commons_candidates if src == "commons" else _openverse_candidates)(
                    q, transport, per_query
                )
            except (OSError, ValueError, StoreError) as exc:
                errors.append({"source": src, "query": q, "error": str(exc)[:300]})
                continue
            for c in found:
                key = (c["source"], c["id"])
                if key in seen or len(cands) >= cap:
                    continue
                seen.add(key)
                c["query"] = q
                if c["reusable"] and c.get("preview_url"):
                    try:
                        data = commons._get(c["preview_url"], transport)
                        if len(data) > PREVIEW_MAX:
                            raise StoreError("preview too large")
                        ext = ".png" if data.startswith(images.MAGIC[".png"]) else ".jpg"
                        name = f"{len(cands) + 1:02d}-{c['source']}{ext}"
                        (out_dir / name).write_bytes(data)
                        c["preview_file"] = name
                        c["preview_sha256"] = hashlib.sha256(data).hexdigest()
                    except (OSError, StoreError) as exc:
                        c["preview_error"] = str(exc)[:200]
                cands.append(c)
    record = {
        "searched_at": now_iso(),
        "subject": spec.get("subject"),
        "source_inspection": inspection,
        "rights_policies": policies,
        "queries": queries,
        "sources_searched": sources,
        "sources_not_searched": NOT_SEARCHED,
        "errors": errors,
        "candidates": cands,
    }
    from lce.store import dump_yaml

    (out_dir / "candidates.yaml").write_text(dump_yaml(record), encoding="utf-8")
    return record


def _openverse_item(item_id: str, transport=None) -> dict:
    if not re.fullmatch(r"[0-9a-f-]{36}", item_id or ""):
        raise StoreError("an Openverse id is a UUID")
    return _json(f"{OPENVERSE}{item_id}/", transport)


def fetch(selected: dict, transport=None) -> tuple[bytes, str, dict]:
    """Retrieve the chosen file again from its source and re-check its licence there.
    Returns (bytes, extension, provenance)."""
    src, ident = selected.get("source"), selected.get("id")
    if src == "commons":
        info = commons.lookup(ident, transport)
        usage = commons.license_usage(info["license"])
        if usage is None:
            raise StoreError(f"licence {info['license']!r} does not allow reuse")
        if any("personality" in r.lower() for r in info.get("restrictions") or []):
            raise StoreError("identifiable people (personality rights restriction)")
        data, ext, got = commons._fetch(info, transport)
        prov = commons._provenance(info, usage, got)
        prov["source_id"] = info["title"]
        prov["source_name"] = "Wikimedia Commons"
        return data, ext, prov
    if src == "openverse":
        it = _openverse_item(ident, transport)
        lic = openverse_license(it)
        if lic is None:
            raise StoreError(f"licence {it.get('license')!r} is not reusable")
        url = it.get("url") or ""
        if not url.startswith("https://"):
            raise StoreError("the file URL is not HTTPS")
        data = commons._get(url, transport)
        if len(data) > commons.MAX_BYTES:
            raise StoreError("the original file is too large")
        if data.startswith(images.MAGIC[".png"]):
            ext = ".png"
        elif data.startswith(images.MAGIC[".jpg"]):
            ext = ".jpg"
        else:
            raise StoreError("the file is not a PNG or JPEG")
        creator = (it.get("creator") or "unknown creator")[:300]
        host = it.get("provider") or it.get("source") or "the source"
        usage = "public_domain" if lic in ("CC0", "Public domain") else "licensed"
        attribution_required = lic.startswith("CC BY")
        prov = {
            "origin": "licensed_stock",
            "usage": usage,
            "license": lic,
            "source_url": it.get("foreign_landing_url") or f"https://openverse.org/image/{ident}",
            "credit": f"{creator} via {host}",
            "title": (it.get("title") or ident)[:300],
            "creator": creator,
            "attribution_required": attribution_required,
            "attribution": f"Image: {creator[:120]}, {lic}, via {host}",
            "retrieved_at": now_iso(),
            "retrieved": "original",
            "retrieved_url": url,
            "source_id": f"openverse:{ident}",
            "source_name": f"Openverse ({host})",
        }
        if it.get("license_url"):
            prov["license_url"] = it["license_url"]
        return data, ext, prov
    if src == "source_page":
        return _source_page_image(selected, transport)
    raise StoreError("selected.source must be commons, openverse or source_page")


def _source_page_image(selected: dict, transport=None) -> tuple[bytes, str, dict]:
    """The cited source's own image (owner policy owner_accepts_copyright_risk): it must be
    published on that page (Open Graph / Twitter image or an <img>), and it is always credited."""
    from urllib.parse import urljoin, urlparse

    from lce import source_visuals

    page_url, image_url = selected.get("page_url") or "", selected.get("image_url") or ""
    if not page_url.startswith("https://") or not image_url.startswith("https://"):
        raise StoreError("source_page needs https page_url and image_url")
    st, html = source_visuals._fetch(page_url, transport)
    if st != 200 or not html:
        raise StoreError(f"the source page could not be read (HTTP {st})")
    page = source_visuals._Page()
    page.feed(html.decode("utf-8", "replace"))
    image_keys = ("og:image", "og:image:url", "twitter:image")
    on_page = {urljoin(page_url, v) for k, v in page.meta if k in image_keys and v}
    on_page |= {urljoin(page_url, im["src"]) for im in page.imgs}
    if image_url not in on_page:
        raise StoreError("the image is not published on the cited source page")
    st, data = source_visuals._fetch(image_url, transport)
    if st != 200 or not data:
        raise StoreError(f"the image could not be retrieved (HTTP {st})")
    if len(data) > commons.MAX_BYTES:
        raise StoreError("the image is too large")
    if data.startswith(images.MAGIC[".png"]):
        ext = ".png"
    elif data.startswith(images.MAGIC[".jpg"]):
        ext = ".jpg"
    else:
        raise StoreError("the image is not a PNG or JPEG")
    publisher = (selected.get("publisher") or urlparse(page_url).netloc.removeprefix("www.")).strip()[:120]
    prov = {
        "origin": "source_publication",
        "usage": "owner_accepted_risk",
        "license": f"copyright of {publisher}; used without a licence by the owner's decision",
        "source_url": page_url,
        "credit": publisher,
        "title": (selected.get("title") or image_url.rsplit("/", 1)[-1])[:300],
        "creator": publisher,
        "attribution_required": True,
        "attribution": f"Image: {publisher}",
        "retrieved_at": now_iso(),
        "retrieved": "original",
        "retrieved_url": image_url,
        "source_id": f"source:{image_url}",
        "source_name": publisher,
    }
    return data, ext, prov


REVIEW_MIN = {"depicts": 40, "why_relevant": 60, "alt_text": 40, "relation": 20, "concept": 15}


def attach_reviewed(
    store, post_id: str, sel: dict, *, transport=None, exclude_sha256=(), exclude_ids=(), source_check=None
) -> dict:
    """Attach the candidate a reviewer chose after looking at it, with that review."""
    for k, n in REVIEW_MIN.items():
        if len((sel.get(k) or "").strip()) < n:
            raise StoreError(f"the reviewed selection needs {k} (at least {n} characters)")
    if not (sel.get("reviewed_by") or "").strip():
        raise StoreError("say who looked at the image (reviewed_by)")
    from lce import source_visuals

    source_visuals.validate_association(sel, source_check)
    if sel.get("source") == "source_page":
        policy = (store.settings().get("visuals") or {}).get("source_image_policy", "licensed_only")
        if policy != "owner_accepts_copyright_risk":
            raise StoreError("the source's own image is used without a licence only when the owner set "
                             "visuals.source_image_policy: owner_accepts_copyright_risk")
    data, ext, prov = fetch(sel, transport)
    if prov["source_id"] in set(exclude_ids) or prov.get("title") in set(exclude_ids):
        raise StoreError("an earlier version of this post used this file")
    sha = hashlib.sha256(data).hexdigest()
    if sha in set(exclude_sha256):
        raise StoreError("same file as an earlier version")
    size = images.dimensions(data, ext)
    if not size or size[0] * size[1] >= images.MAX_PIXELS:
        raise StoreError("unreadable or too large for LinkedIn")
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / f"selected{ext}"
        f.write_bytes(data)
        doc = images.decide(
            store,
            post_id,
            kind="source_image",
            rationale=sel["why_relevant"],
            source_file=str(f),
            relation=sel["relation"],
            alt_text=sel["alt_text"],
            provenance={k: v for k, v in prov.items() if k not in ("source_id", "source_name")},
            decided_by="agent",
            relevance={
                "concept": sel["concept"],
                "visual_type": sel.get("visual_type", "photo"),
                "reason": sel["why_relevant"],
            },
        )
    doc["provenance"]["source_id"] = prov["source_id"]
    doc["provenance"]["source_name"] = prov["source_name"]
    doc["media_relevance"]["semantic"] = {
        "method": "visual review of the candidate image",
        "subject": sel.get("subject"),
        "depicts": sel["depicts"].strip(),
        "why_relevant": sel["why_relevant"].strip(),
        "reviewed_by": sel["reviewed_by"].strip(),
        "metadata_checked": False,
        "matched_terms": [],
        "association": sel["association"],
        "why_belongs_to_source": sel["why_belongs_to_source"].strip(),
        "why_legal": sel["why_legal"].strip(),
    }
    doc["selection"] = {
        "source": "reviewed search: " + ", ".join(sel.get("sources_searched") or SOURCES),
        "subject": sel.get("subject") or sel["concept"],
        "concept": sel["concept"],
        "subject_terms": [],
        "queries": sel.get("queries") or [],
        "sources_not_searched": sel.get("sources_not_searched") or NOT_SEARCHED,
        "tried": list(sel.get("reviewed") or [])
        + [{"title": prov["title"], "outcome": "selected", "why": sel["depicts"].strip()[:300]}],
        "selected": prov["source_id"],
    }
    store.write_doc(images.path(store, post_id), "image", doc)
    return doc
