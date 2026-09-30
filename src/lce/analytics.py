"""Analytics & learning loop: metrics of published posts → insights → brand strategy.

Sources are only those the owner can legitimately access: manual entry, a CSV
the owner exported or typed, or (later, if LinkedIn grants the owner's app the
permission) the official member post analytics API. Nothing is estimated or
scraped.

Learning is conservative and explainable:
- per-post features (pillar, theme, evidence, format, image, length, hook,
  weekday/hour, hashtags, closing question) joined with the latest snapshot;
- group medians only for groups with at least `min_sample` posts;
- topic saturation from repeated similar topics;
- a suggested pillar mix that moves the owner's targets by a bounded step
  toward better-performing pillars. It is a suggestion; the owner applies it
  (e.g. `lce interview set brand_mix`). Nothing changes the strategy silently.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from statistics import median
from zoneinfo import ZoneInfo

from lce.clock import iso_utc, now, parse_iso
from lce.state import PostState
from lce.store import DataStore, StoreError

METRICS = ("impressions", "members_reached", "reactions", "comments", "reposts", "clicks",
           "followers_gained")
FEATURES = ("pillar", "theme", "evidence", "format", "image", "length", "hook", "weekday", "hour",
            "hashtags", "question")
DEFAULT_MIN_SAMPLE = 3
SATURATION_DAYS = 60
SATURATION_COUNT = 3


def path(store: DataStore, post_id: str):
    return store.post_dir(post_id) / "metrics.yaml"


def load(store: DataStore, post_id: str) -> dict:
    return store.read_doc(path(store, post_id), {"snapshots": []})


def record(store: DataStore, post_id: str, values: dict, *, at: str | None = None,
           source: str = "manual", note: str = "") -> dict:
    post = store.load_post(post_id)
    if PostState(post["state"]) != PostState.PUBLISHED:
        raise StoreError(f"metrics belong to PUBLISHED posts; this one is {post['state']}")
    snap = {"at": iso_utc(parse_iso(at)) if at else iso_utc(now()), "source": source}
    for key in METRICS:
        if values.get(key) is not None:
            v = int(values[key])
            if v < 0:
                raise StoreError(f"{key} cannot be negative")
            snap[key] = v
    if not any(k in snap for k in ("impressions", "reactions", "comments", "reposts")):
        raise StoreError("give at least one of impressions, reactions, comments, reposts")
    if note:
        snap["note"] = note
    doc = load(store, post_id)
    doc["snapshots"] = sorted([s for s in doc["snapshots"] if s["at"] != snap["at"]] + [snap],
                              key=lambda s: s["at"])
    store.write_doc(path(store, post_id), "metrics", doc)
    store.log_event("analytics.recorded", post_id=post_id, source=source)
    return snap


def _publication(store: DataStore, post_id: str) -> dict:
    """Read-only view of posts/<id>/publication.json (analytics never touches publishing)."""
    f = store.post_dir(post_id) / "publication.json"
    return json.loads(f.read_text("utf-8")) if f.exists() else {}


def _post_for_url(store: DataStore) -> dict[str, str]:
    out = {}
    for pid in store.post_ids():
        pub = _publication(store, pid)
        for key in ("url", "remote_id"):
            if pub.get(key):
                out[pub[key].rstrip("/")] = pid
    return out


def import_csv(store: DataStore, csv_path: str) -> dict:
    """Rows: post_id or url (a LinkedIn post URL/URN), optional at, and metric columns
    named as in METRICS. Rows that match no published post are reported, not guessed."""
    by_url = _post_for_url(store)
    known = set(store.post_ids())
    done, unmatched = 0, []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for n, row in enumerate(csv.DictReader(fh), 2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            pid = row.get("post_id") if row.get("post_id") in known else None
            pid = pid or by_url.get(row.get("url", "").rstrip("/"))
            if not pid:
                unmatched.append(n)
                continue
            values = {k: row[k].replace(",", "") for k in METRICS if row.get(k)}
            record(store, pid, values, at=row.get("at") or None, source="csv")
            done += 1
    return {"recorded": done, "unmatched_rows": unmatched}


# ── features & performance ────────────────────────────────────────────
def _bucket(n: int, edges: tuple[int, ...], labels: tuple[str, ...]) -> str:
    for edge, label in zip(edges, labels, strict=False):
        if n <= edge:
            return label
    return labels[-1]


def features(store: DataStore, post_id: str, published_at: str | None) -> dict:
    from lce import images

    post = store.load_post(post_id)
    text = (store.post_text(post_id, "post.md") or "").strip()
    first = text.split("\n", 1)[0] if text else ""
    brand = post.get("brand") or {}
    img = images.load(store, post_id) or {}
    out = {
        "pillar": post.get("pillar"),
        "theme": brand.get("theme"),
        "evidence": brand.get("evidence") or ("personal" if post.get("stories_used") else "external"),
        "format": post.get("format") or "text",
        "image": img.get("kind", "unknown"),
        "length": _bucket(len(text), (600, 1200, 2000), ("short", "medium", "long", "very long")),
        "hook": _bucket(len(first), (60, 120), ("short", "medium", "long")),
        "hashtags": _bucket(text.count("#"), (0, 3), ("none", "1-3", "4+")),
        "question": "yes" if text.rstrip().endswith("?") else "no",
        "weekday": None, "hour": None,
    }
    if published_at:
        tz = ZoneInfo(store.settings().get("timezone") or "UTC")
        local = parse_iso(published_at).astimezone(tz)
        out["weekday"] = local.strftime("%a").lower()
        out["hour"] = f"{local.hour:02d}"
    return out


def performance(store: DataStore) -> list[dict]:
    rows = []
    for pid in store.post_ids():
        snaps = load(store, pid)["snapshots"]
        if not snaps:
            continue
        last = snaps[-1]
        pub = _publication(store, pid)
        engagement = sum(last.get(k, 0) for k in ("reactions", "comments", "reposts"))
        imp = last.get("impressions")
        age = None
        if pub.get("published_at"):
            age = (parse_iso(last["at"]) - parse_iso(pub["published_at"])).days
        rows.append({"post_id": pid, "topic": store.load_post(pid).get("topic"),
                     "published_at": pub.get("published_at"), "snapshot_at": last["at"],
                     "age_days": age, "impressions": imp, "engagement": engagement,
                     "rate": round(engagement / imp, 4) if imp else None,
                     "features": features(store, pid, pub.get("published_at"))})
    return rows


def _median(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return round(median(values), 4) if values else None


def saturated_topics(store: DataStore, today: date) -> list[dict]:
    from lce.planning import recent_posts, topic_similarity

    threshold = store.tuning()["duplicates"]["topic_similarity"]
    posts = recent_posts(store, today, SATURATION_DAYS)
    out, seen = [], set()
    for p in posts:
        if p["post_id"] in seen:
            continue
        similar = [q for q in posts
                   if topic_similarity(p.get("topic", ""), q.get("topic", "")) >= threshold]
        if len(similar) >= SATURATION_COUNT:
            seen |= {q["post_id"] for q in similar}
            out.append({"topic": p.get("topic"), "posts": sorted(q["post_id"] for q in similar)})
    return out


def insights(store: DataStore, today: date, min_sample: int = DEFAULT_MIN_SAMPLE) -> dict:
    rows = performance(store)
    overall = {"posts": len(rows), "median_impressions": _median([r["impressions"] for r in rows]),
               "median_rate": _median([r["rate"] for r in rows])}
    groups = {}
    for feat in FEATURES:
        values: dict[str, list[dict]] = {}
        for r in rows:
            v = r["features"].get(feat)
            if v is not None:
                values.setdefault(str(v), []).append(r)
        groups[feat] = [{
            "value": v, "posts": len(rs),
            "median_impressions": _median([r["impressions"] for r in rs]),
            "median_rate": _median([r["rate"] for r in rs]),
            "enough_data": len(rs) >= min_sample,
        } for v, rs in sorted(values.items())]
    return {"overall": overall, "min_sample": min_sample, "groups": groups,
            "saturated_topics": saturated_topics(store, today), "posts": rows}


def theme_rates(store: DataStore, min_sample: int = DEFAULT_MIN_SAMPLE) -> dict[str, float]:
    """Median engagement rate per theme, only for themes with enough data."""
    rows = performance(store)
    by: dict[str, list] = {}
    for r in rows:
        if r["features"].get("theme") and r["rate"] is not None:
            by.setdefault(r["features"]["theme"], []).append(r["rate"])
    return {t: _median(v) for t, v in by.items() if len(v) >= min_sample}


def suggest_mix(store: DataStore, min_sample: int = DEFAULT_MIN_SAMPLE, step: float = 0.1) -> dict:
    """Bounded move of the pillar targets toward better-performing pillars. Suggestion only."""
    mix = (store.brand().get("mix") or {}).get("pillars") or {}
    pillars = [p["id"] for p in store.profile().get("pillars", [])]
    rows = [r for r in performance(store) if r["rate"] is not None]
    overall = _median([r["rate"] for r in rows])
    rates = {}
    for pid in pillars:
        rs = [r["rate"] for r in rows if r["features"]["pillar"] == pid]
        if len(rs) >= min_sample:
            rates[pid] = _median(rs)
    if not overall or not rates:
        return {"suggested": None, "reason": f"not enough data (need ≥{min_sample} posts with "
                                             "impressions per pillar)", "rates": rates}
    base = {p: mix.get(p, round(1 / len(pillars), 2)) for p in pillars}
    total = sum(base.values())
    moved = {}
    for p, share in base.items():
        if p in rates:
            delta = max(-step, min(step, step * (rates[p] / overall - 1)))
            moved[p] = min(0.6, max(0.05, share + delta))
        else:
            moved[p] = share
    scale = total / sum(moved.values())
    suggested = {p: round(v * scale, 2) for p, v in moved.items()}
    return {"suggested": suggested, "current": base, "rates": rates, "overall_rate": overall,
            "reason": "pillars above the overall median rate gain share, below lose share "
                      f"(±{step} max, only pillars with ≥{min_sample} posts)"}
