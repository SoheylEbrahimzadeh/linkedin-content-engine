"""Research candidates: free RSS/Atom feeds configured by the owner, plus manual/web entries.

Everything fetched from the web is stored with `untrusted: true`. It is data to
be summarised and cited, never instructions to follow. No API keys are used.
"""

from __future__ import annotations

import hashlib
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from urllib.parse import urlparse

from lce.store import DataStore, StoreError, now_iso

MAX_FEED_BYTES = 2_000_000
TAG_RE = re.compile(r"<[^>]+>")
ATOM = "{http://www.w3.org/2005/Atom}"

Fetcher = Callable[[str, int], bytes]


def http_fetch(url: str, timeout: int) -> bytes:
    if urlparse(url).scheme != "https":
        raise StoreError(f"only https feeds are allowed: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "lce-research/0.1 (+rss)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https enforced)
        data = resp.read(MAX_FEED_BYTES + 1)
    if len(data) > MAX_FEED_BYTES:
        raise StoreError(f"feed too large: {url}")
    return data


def _clean(text: str | None, limit: int = 600) -> str:
    t = html.unescape(TAG_RE.sub(" ", text or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def parse_feed(data: bytes) -> list[dict]:
    """Return [{title, url, summary}] from RSS 2.0 or Atom."""
    root = ET.fromstring(data)
    items = []
    for item in root.iter("item"):
        items.append({"title": _clean(item.findtext("title"), 200),
                      "url": (item.findtext("link") or "").strip(),
                      "summary": _clean(item.findtext("description"))})
    for entry in root.iter(f"{ATOM}entry"):
        link = entry.find(f"{ATOM}link[@rel='alternate']")
        if link is None:
            link = entry.find(f"{ATOM}link")
        items.append({"title": _clean(entry.findtext(f"{ATOM}title"), 200),
                      "url": (link.get("href") if link is not None else "") or "",
                      "summary": _clean(entry.findtext(f"{ATOM}summary")
                                        or entry.findtext(f"{ATOM}content"))})
    return [i for i in items if i["title"] and i["url"].startswith(("http://", "https://"))]


def candidate_id_for(url: str) -> str:
    return "c-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def add_candidate(store: DataStore, *, title: str, origin: str, summary: str = "",
                  urls: list[str] | None = None, publisher: str = "", pillar: str | None = None,
                  claims: list[dict] | None = None) -> dict:
    urls = urls or []
    if origin in {"feed", "web_search"} and not urls:
        raise StoreError("web research candidates need at least one source URL")
    key = urls[0] if urls else f"{origin}:{title}"
    cid = candidate_id_for(key)
    path = store.candidate_path(cid)
    if path.exists():
        return store.read_doc(path)
    doc: dict = {
        "candidate_id": cid,
        "title": title,
        "summary": summary,
        "origin": origin,
        "untrusted": origin in {"feed", "web_search"},
        "sources": [{"url": u, "title": title, "publisher": publisher, "accessed_at": now_iso()}
                    for u in urls],
        "claims": claims or [],
        "status": "new",
        "created_at": now_iso(),
    }
    if pillar:
        doc["pillar"] = pillar
    store.write_doc(path, "research_candidate", doc)
    store.log_event("research.add", candidate_id=cid, origin=origin)
    return doc


def add_claim(store: DataStore, candidate_id: str, text: str, source_url: str) -> dict:
    """Attach a verbatim claim (with its source) that a post may cite."""
    path = store.candidate_path(candidate_id)
    doc = store.read_doc(path)
    if not doc:
        raise StoreError(f"candidate {candidate_id} does not exist")
    known = {s["url"] for s in doc.get("sources", [])}
    if source_url not in known:
        raise StoreError("the claim's source URL must be one of the candidate's sources")
    doc.setdefault("claims", []).append({"text": text, "source_url": source_url})
    store.write_doc(path, "research_candidate", doc)
    return doc


def fetch_feeds(store: DataStore, fetch: Fetcher = http_fetch) -> dict:
    """Fetch configured feeds; returns {added: [...], errors: [...]}."""
    cfg = store.tuning()["research"]
    added, errors = [], []
    for feed in cfg.get("feeds", []):
        try:
            items = parse_feed(fetch(feed["url"], cfg["timeout_seconds"]))
        except (OSError, ET.ParseError, StoreError) as exc:
            errors.append({"feed": feed["name"], "error": str(exc)[:200]})
            continue
        for item in items[: cfg["max_items_per_feed"]]:
            cid = candidate_id_for(item["url"])
            if store.candidate_path(cid).exists():
                continue
            add_candidate(store, title=item["title"], summary=item["summary"], origin="feed",
                          urls=[item["url"]], publisher=feed["name"])
            added.append(cid)
    store.log_event("research.fetch", added=len(added), errors=len(errors))
    return {"added": added, "errors": errors}
