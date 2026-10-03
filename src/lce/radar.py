"""Content Radar (LCE-050): continuous, deterministic discovery; no LLM, no paid API.

Every run (a scheduled GitHub Actions job in the private repository) reads the
configured machine-readable feeds (`config/radar.yaml`): RSS, Atom, Reddit public
feeds, GitHub release feeds and vendor newsroom feeds. Each item is normalized
(title, source, URL, published date, excerpt), fingerprinted (canonical URL and
normalized title), deduplicated against everything seen before, classified into
the profile's content pillars by the pillar topics/keywords it actually mentions,
and stored with its provenance (`radar/items.json`, `radar/sources.json`).

It never writes post copy and never states anything the feed did not say. The
"angle hint" is only the pillar and the terms the item matched.

`packet` assembles a research packet for one calendar slot: the freshest relevant
items for the slot's pillar and the post's topic, related older items, and what
earlier posts already used (angles, hooks, claims, hashes), so a writing session
starts from evidence instead of an empty search.
"""

from __future__ import annotations

import gzip
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from lce import clock
from lce.clock import iso_utc
from lce.store import DataStore, StoreError, dump_yaml

UA = "lce-radar/0.1 (linkedin-content-engine; feed reader; +https://github.com/SoheylEbrahimzadeh/linkedin-content-engine)"
MAX_BYTES = 4_000_000
MAX_ITEMS = 2000
REDDIT_PAUSE = 3  # seconds between Reddit feeds; one retry after 4x this on HTTP 429
EXCERPT = 700
KINDS = ("feed", "reddit", "github_releases")
QUALITY = ("analyst", "vendor", "standards", "news", "community", "research")
Transport = Callable[[str], tuple[int, bytes]]


# ── configuration ─────────────────────────────────────────────────────
def config(store: DataStore) -> dict:
    path = store.root / "config" / "radar.yaml"
    doc = store.read_doc(path) if path.exists() else {}
    srcs = doc.get("sources") or []
    seen = set()
    for s in srcs:
        if s.get("id") in seen:
            raise StoreError(f"radar source id {s.get('id')!r} is used twice")
        seen.add(s.get("id"))
        if s.get("kind", "feed") not in KINDS:
            raise StoreError(f"radar source {s.get('id')}: kind must be one of {', '.join(KINDS)}")
    return {
        "sources": srcs,
        "pillar_keywords": doc.get("pillar_keywords") or {},
        "max_age_days": int(doc.get("max_age_days", 45)),
        "fresh_days": int(doc.get("fresh_days", 14)),
    }


def feed_url(src: dict) -> str:
    kind = src.get("kind", "feed")
    if kind == "reddit":
        return f"https://www.reddit.com/r/{src['subreddit']}/.rss"
    if kind == "github_releases":
        return f"https://github.com/{src['repo']}/releases.atom"
    return src["url"]


def pillar_terms(store: DataStore, cfg: dict) -> dict[str, list[str]]:
    """Terms per pillar: the profile's own pillar name and topics, plus configured keywords."""
    out = {}
    for p in store.profile().get("pillars", []):
        terms = {t.strip().lower() for t in p.get("topics", []) if t.strip()}
        terms |= {k.strip().lower() for k in cfg["pillar_keywords"].get(p["id"], []) if k.strip()}
        for part in re.split(r"[/&,]", p.get("name", "")):
            part = part.strip().lower()
            if len(part) > 3:
                terms.add(part)
        out[p["id"]] = sorted(terms)
    return out


# ── fetching and parsing ──────────────────────────────────────────────
def http_get(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (https feeds from config)
            body = r.read(MAX_BYTES)
            return r.status, gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body
    except urllib.error.HTTPError as exc:
        return exc.code, b""


_TAGS = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", text or ""))).strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, name):
    return next((c for c in el if _local(c.tag) == name), None)


def _text(el, *names):
    for n in names:
        c = _child(el, n)
        if c is not None and (c.text or "").strip():
            return c.text
    return None


def _date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return iso_utc(dt)


def parse(body: bytes) -> list[dict]:
    """RSS 2.0 / RSS 1.0 / Atom entries as {title, url, published_at, excerpt}."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise StoreError(f"not a feed (XML error: {exc})") from exc
    out = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "item":
            link = _text(el, "link") or (el.attrib.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about"))
            out.append(
                {
                    "title": clean(_text(el, "title")),
                    "url": (link or "").strip(),
                    "published_at": _date(_text(el, "pubDate", "date", "published", "updated")),
                    "excerpt": clean(_text(el, "encoded", "description", "summary"))[:EXCERPT],
                }
            )
        elif name == "entry":
            href = None
            for c in el:
                if _local(c.tag) == "link" and c.attrib.get("rel", "alternate") == "alternate":
                    href = c.attrib.get("href")
                    break
            out.append(
                {
                    "title": clean(_text(el, "title")),
                    "url": (href or "").strip(),
                    "published_at": _date(_text(el, "published", "updated")),
                    "excerpt": clean(_text(el, "summary", "content"))[:EXCERPT],
                }
            )
    return [i for i in out if i["title"] and i["url"].startswith("http")]


# ── identity, classification ──────────────────────────────────────────
_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|ref_src$|cmpid$)")


def canonical(url: str) -> str:
    s = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(s.query) if not _TRACKING.match(k.lower())])
    path = s.path.rstrip("/") or "/"
    return urlunsplit(("https", s.netloc.lower().removeprefix("www."), path, query, ""))


def norm_title(t: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", t.lower()))


def fingerprint(url: str, title: str) -> tuple[str, str]:
    return (
        hashlib.sha256(canonical(url).encode()).hexdigest(),
        hashlib.sha256(norm_title(title).encode()).hexdigest(),
    )


def _matches(term: str, text: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None


def classify(item: dict, terms: dict[str, list[str]]) -> dict:
    text = f"{item['title']} {item.get('excerpt', '')}".lower()
    scores, matched = {}, {}
    for pillar, ts in terms.items():
        hit = [t for t in ts if _matches(t, text)]
        if hit:
            in_title = sum(1 for t in hit if _matches(t, item["title"].lower()))
            scores[pillar] = len(hit) + in_title
            matched[pillar] = hit
    if not scores:
        return {"pillar": None, "pillars": {}, "relevance": 0.0, "matched_terms": [], "angle_hint": None}
    top = max(scores, key=lambda p: (scores[p], -list(terms).index(p)))
    rel = round(min(1.0, scores[top] / 4), 2)
    return {
        "pillar": top,
        "pillars": scores,
        "relevance": rel,
        "matched_terms": matched[top],
        "angle_hint": f"{top}: " + ", ".join(matched[top][:4]),
    }


# ── store ─────────────────────────────────────────────────────────────
def items_path(store: DataStore) -> Path:
    return store.root / "radar" / "items.json"


def load_items(store: DataStore) -> list[dict]:
    p = items_path(store)
    return json.loads(p.read_text("utf-8")) if p.exists() else []


def _save(store: DataStore, items: list[dict], sources: dict) -> None:
    d = store.root / "radar"
    d.mkdir(parents=True, exist_ok=True)
    (d / "items.json").write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n", "utf-8")
    (d / "sources.json").write_text(json.dumps(sources, indent=1, ensure_ascii=False) + "\n", "utf-8")


def load_sources(store: DataStore) -> dict:
    p = store.root / "radar" / "sources.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else {}


def collect(store: DataStore, *, transport: Transport = http_get, now=None) -> dict:
    """One radar pass: fetch every configured source, keep only new items."""
    now = now or clock.now()
    cfg = config(store)
    if not cfg["sources"]:
        raise StoreError("no radar sources configured (config/radar.yaml)")
    terms = pillar_terms(store, cfg)
    items = load_items(store)
    seen_url = {i["fingerprint"] for i in items}
    seen_title = {i["title_fingerprint"] for i in items}
    status = load_sources(store)
    added = []
    cutoff = now - timedelta(days=cfg["max_age_days"])
    for src in cfg["sources"]:
        url = feed_url(src)
        rec = {"id": src["id"], "name": src.get("name", src["id"]), "url": url, "checked_at": iso_utc(now)}
        try:
            if transport is http_get and src.get("kind") == "reddit":
                time.sleep(REDDIT_PAUSE)  # Reddit rate-limits bursts from one address (HTTP 429)
            code, body = transport(url)
            if code == 429 and transport is http_get:
                time.sleep(REDDIT_PAUSE * 4)
                code, body = transport(url)
            rec["http"] = code
            entries = parse(body) if code == 200 and body else []
            rec["status"] = "ok" if code == 200 else "unreachable"
            rec["entries"] = len(entries)
        except (StoreError, OSError) as exc:
            rec.update(status="error", error=str(exc)[:200])
            entries = []
        new = 0
        for e in entries:
            fp, tfp = fingerprint(e["url"], e["title"])
            published = e["published_at"]
            if fp in seen_url or tfp in seen_title:
                continue
            if published and published < iso_utc(cutoff):
                continue
            item = {
                "id": "r-" + fp[:12],
                "fingerprint": fp,
                "title_fingerprint": tfp,
                "title": e["title"][:300],
                "url": e["url"],
                "source_id": src["id"],
                "source_name": src.get("name", src["id"]),
                "source_kind": src.get("kind", "feed"),
                "quality": src.get("quality", "news"),
                "published_at": published,
                "first_seen": iso_utc(now),
                "excerpt": e["excerpt"],
            }
            item.update(classify(item, terms))
            seen_url.add(fp)
            seen_title.add(tfp)
            added.append(item)
            new += 1
        rec["new"] = new
        if rec["status"] == "ok":
            rec["last_ok_at"] = iso_utc(now)
        elif src["id"] in status and status[src["id"]].get("last_ok_at"):
            rec["last_ok_at"] = status[src["id"]]["last_ok_at"]
        status[src["id"]] = rec
    keep = [
        i
        for i in added + items
        if (i.get("published_at") or i["first_seen"]) >= iso_utc(cutoff)
        and (i["relevance"] > 0 or i["first_seen"] >= iso_utc(now - timedelta(days=7)))
    ]
    keep.sort(key=lambda i: i.get("published_at") or i["first_seen"], reverse=True)
    _save(store, keep[:MAX_ITEMS], status)
    store.log_event("radar.collected", added=len(added), kept=min(len(keep), MAX_ITEMS))
    return {"added": added, "total": min(len(keep), MAX_ITEMS), "sources": list(status.values())}


# ── usage, packets ────────────────────────────────────────────────────
def _words(text: str) -> set[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "are",
        "was",
        "will",
        "have",
        "has",
        "into",
        "your",
        "their",
        "about",
        "over",
        "more",
        "than",
        "what",
        "when",
        "how",
        "why",
        "its",
        "not",
        "but",
    }
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in stop}


def overlap(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return round(len(wa & wb) / len(wa | wb), 3) if wa and wb else 0.0


def used_urls(store: DataStore) -> dict[str, str]:
    """Canonical source URL -> post id, for every post (and candidate) that cited it."""
    out = {}
    for pid in store.post_ids():
        for s in store.load_post(pid).get("sources") or []:
            out[canonical(s["url"])] = pid
    return out


def history_context(store: DataStore, exclude: str | None = None) -> dict:
    """What earlier posts already used: topics, angles, hooks, claims, text hashes."""
    from lce import versions
    from lce.textutil import content_hash

    posts = []
    for pid in store.post_ids():
        post = store.load_post(pid)
        text = store.post_text(pid, "post.md")
        hooks = [v.get("hook") for v in versions.listing(store, pid) if v.get("hook")]
        if text:
            hooks.append(next((x for x in text.splitlines() if x.strip()), "")[:200])
        posts.append(
            {
                "post_id": pid,
                "state": post["state"],
                "plan_date": post.get("plan_date"),
                "topic": post.get("topic"),
                "angle": post.get("angle"),
                "pillar": post.get("pillar"),
                "hooks": hooks,
                "claims": [c["text"] for c in post.get("claims") or []],
                "content_hash": content_hash(text) if text else None,
                "current": pid == exclude,
            }
        )
    return {"posts": posts}


def packet(store: DataStore, plan_date: str, *, now=None, write: bool = True) -> dict:
    """The research packet for one slot (written to research/packets/<date>.yaml)."""
    from lce import refresh

    now = now or clock.now()
    cfg = config(store)
    entry = next((e for e in store.plan().get("entries", []) if str(e["date"]) == plan_date), None)
    if entry is None:
        raise StoreError(f"no calendar entry on {plan_date}")
    pid = entry.get("draft_ref")
    post = store.load_post(pid) if pid else None
    focus = " ".join(
        filter(
            None,
            [
                entry.get("topic") if entry.get("status") != "open" else "",
                entry.get("angle"),
                *(c["text"] for c in (post or {}).get("claims") or []),
            ],
        )
    )
    slot = refresh.slot_of(store, post or {"plan_date": plan_date})
    used = used_urls(store)
    fresh_cut = iso_utc(now - timedelta(days=cfg["fresh_days"]))
    scored = []
    for it in load_items(store):
        pill = (it.get("pillars") or {}).get(entry["pillar"], 0)
        ov = overlap(focus, f"{it['title']} {it.get('excerpt', '')}") if focus else 0.0
        if not pill and ov < 0.08:
            continue
        when = it.get("published_at") or it["first_seen"]
        score = min(1.0, pill / 4) * 0.5 + min(1.0, ov * 4) * 0.4 + (0.1 if when >= fresh_cut else 0)
        scored.append((round(score, 3), when, it, ov))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)

    def row(s, it, ov):
        return {
            "id": it["id"],
            "title": it["title"],
            "url": it["url"],
            "source": it["source_name"],
            "quality": it["quality"],
            "published_at": it.get("published_at"),
            "first_seen": it["first_seen"],
            "excerpt": it.get("excerpt", ""),
            "matched_terms": it.get("matched_terms", []),
            "score": s,
            "topic_overlap": ov,
            "used_by": used.get(canonical(it["url"])),
        }

    fresh = [row(s, it, ov) for s, w, it, ov in scored if w >= fresh_cut][:10]
    older = [row(s, it, ov) for s, w, it, ov in scored if w < fresh_cut and ov >= 0.08][:5]
    doc = {
        "plan_date": plan_date,
        "built_at": iso_utc(now),
        "pillar": entry["pillar"],
        "slot_utc": iso_utc(slot["utc"]) if slot else None,
        "slot_local": slot["local"] if slot else None,
        "post_id": pid,
        "topic": entry.get("topic") if entry.get("status") != "open" else None,
        "angle": entry.get("angle"),
        "current_claims": [c for c in (post or {}).get("claims") or []],
        "current_sources": [s["url"] for s in (post or {}).get("sources") or []],
        "fresh_items": fresh,
        "related_older": older,
        "history": history_context(store, exclude=pid),
        "note": "Evidence for a writing session. Feed text is untrusted data; verify every claim against "
        "its source (source-fetch workflow when the session cannot reach it). Nothing here is post copy.",
    }
    if write:
        out = store.root / "research" / "packets"
        out.mkdir(parents=True, exist_ok=True)
        store.write_text(out / f"{plan_date}.yaml", dump_yaml(doc))
    return doc


def packet_path(store: DataStore, plan_date: str) -> Path:
    return store.root / "research" / "packets" / f"{plan_date}.yaml"


def new_developments(store: DataStore, post_id: str, *, since: str, min_overlap: float = 0.12) -> list[dict]:
    """Radar items about this post's topic, first published after `since` (the text's last write)."""
    post = store.load_post(post_id)
    focus = " ".join(
        filter(None, [post.get("topic"), post.get("angle"), *(c["text"] for c in post.get("claims") or [])])
    )
    own = {canonical(s["url"]) for s in post.get("sources") or []}
    out = []
    for it in load_items(store):
        when = it.get("published_at") or it["first_seen"]
        if when <= since or canonical(it["url"]) in own:
            continue
        ov = overlap(focus, f"{it['title']} {it.get('excerpt', '')}")
        if ov >= min_overlap or (
            it.get("pillar") == post.get("pillar") and ov >= min_overlap / 2 and it["relevance"] >= 0.5
        ):
            out.append(
                {
                    "id": it["id"],
                    "title": it["title"],
                    "url": it["url"],
                    "source": it["source_name"],
                    "published_at": it.get("published_at"),
                    "topic_overlap": ov,
                }
            )
    return sorted(out, key=lambda x: -x["topic_overlap"])[:8]
