"""Personal Brand Engine: deterministic content strategy on top of the private brand.

Inputs (all private): profile.yaml (pillars, audience, avoid lists),
brand.yaml (objective, narrative chapters, target, themes, content mix,
credibility rules), the story bank and the post history.

It answers: which pillar is under-served, which theme should come next, and
whether personal evidence exists for it. It never invents evidence: when a
theme needs a personal story and no PUBLIC story exists, the recommendation is
"needs personal input", and `select` puts such a post into NEEDS_INPUT.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from lce.store import DataStore

DEFAULT_WINDOW = 28
EVIDENCE_MODES = ("personal", "external")


def _pillars(store: DataStore) -> dict[str, dict]:
    return {p["id"]: p for p in store.profile().get("pillars", [])}


def problems(store: DataStore) -> list[str]:
    """Cross-reference problems in brand.yaml (schema validation covers the rest)."""
    brand, pillars, stories = store.brand(), _pillars(store), store.stories()
    out = []
    mix = (brand.get("mix") or {}).get("pillars") or {}
    for pid in mix:
        if pid not in pillars:
            out.append(f"mix: unknown pillar {pid!r}")
    if sum(mix.values()) > 1.0001:
        out.append(f"mix: pillar shares add up to {sum(mix.values()):.2f} (> 1)")
    for theme in brand.get("themes", []):
        for pid in theme.get("pillars", []):
            if pid not in pillars:
                out.append(f"theme {theme['id']}: unknown pillar {pid!r}")
    for ch in (brand.get("narrative") or {}).get("chapters", []):
        for sid in ch.get("stories", []):
            if sid not in stories:
                out.append(f"chapter {ch['id']}: unknown story {sid!r}")
    ids = [t["id"] for t in brand.get("themes", [])]
    if len(ids) != len(set(ids)):
        out.append("themes: duplicate ids")
    return out


def theme(store: DataStore, theme_id: str) -> dict | None:
    return next((t for t in store.brand().get("themes", []) if t["id"] == theme_id), None)


def chapter(store: DataStore, chapter_id: str) -> dict | None:
    chapters = (store.brand().get("narrative") or {}).get("chapters", [])
    return next((c for c in chapters if c["id"] == chapter_id), None)


def public_stories_for(store: DataStore, *, pillar: str | None = None,
                       theme_id: str | None = None) -> list[str]:
    """PUBLIC stories usable as personal evidence for a pillar/theme.

    A story qualifies when its `topics` name the pillar or the theme. Without a
    pillar or theme, every PUBLIC story qualifies.
    """
    keys = {k for k in (pillar, theme_id) if k}
    out = []
    for sid, story in store.stories().items():
        if story.get("publication_status") != "PUBLIC":
            continue
        if not keys or keys & set(story.get("topics", [])):
            out.append(sid)
    return sorted(out)


def resolve_evidence(store: DataStore, theme_id: str | None, requested: str | None,
                     stories: list[str]) -> str:
    """Evidence mode for a post: explicit request, else the theme's rule, else by stories."""
    if requested:
        if requested not in EVIDENCE_MODES:
            raise ValueError(f"evidence must be one of {', '.join(EVIDENCE_MODES)}")
        return requested
    rule = (theme(store, theme_id) or {}).get("evidence", "either") if theme_id else "either"
    if rule in EVIDENCE_MODES:
        return rule
    return "personal" if stories else "external"


def status(store: DataStore, today: date) -> dict:
    from lce.planning import recent_posts

    brand, pillars = store.brand(), _pillars(store)
    mix = brand.get("mix") or {}
    window = mix.get("window_days", DEFAULT_WINDOW)
    posts = recent_posts(store, today, window)
    total = len(posts)
    by_pillar = Counter(p.get("pillar") for p in posts)
    by_theme = Counter((p.get("brand") or {}).get("theme") for p in posts)
    by_chapter = Counter((p.get("brand") or {}).get("chapter") for p in posts)
    personal = sum(1 for p in posts if (p.get("brand") or {}).get("evidence") == "personal"
                   or ((p.get("brand") or {}).get("evidence") is None and p.get("stories_used")))
    targets = mix.get("pillars") or {}
    pillar_rows = []
    for pid, p in pillars.items():
        actual = by_pillar[pid] / total if total else 0.0
        target = targets.get(pid)
        pillar_rows.append({
            "id": pid, "name": p.get("name", pid), "posts": by_pillar[pid],
            "actual": round(actual, 2), "target": target,
            "deficit": round(max(0.0, target - actual), 2) if target is not None else None,
            "evidence_stories": len(public_stories_for(store, pillar=pid)),
        })
    theme_rows = []
    for t in brand.get("themes", []):
        stories = sorted({sid for pid in (t.get("pillars") or [None])
                          for sid in public_stories_for(store, pillar=pid, theme_id=t["id"])})
        needs = t.get("evidence", "either") == "personal" and not stories
        theme_rows.append({"id": t["id"], "name": t["name"], "posts": by_theme[t["id"]],
                           "evidence": t.get("evidence", "either"),
                           "evidence_stories": len(stories), "needs_personal_input": needs})
    chapters = [{"id": c["id"], "title": c["title"], "posts": by_chapter[c["id"]],
                 "stories": len(c.get("stories", []))}
                for c in (brand.get("narrative") or {}).get("chapters", [])]
    min_personal = mix.get("min_personal_share")
    return {
        "configured": bool(brand),
        "window_days": window,
        "posts_in_window": total,
        "pillars": pillar_rows,
        "themes": theme_rows,
        "chapters": chapters,
        "personal_share": round(personal / total, 2) if total else 0.0,
        "min_personal_share": min_personal,
        "problems": problems(store),
    }


def recommend(store: DataStore, today: date, count: int = 3) -> list[dict]:
    """Next content moves: under-served pillar → least-used theme → evidence mode."""
    st = status(store, today)
    if not st["pillars"]:
        return []
    order = sorted(st["pillars"], key=lambda r: (-(r["deficit"] or 0.0), r["posts"]))
    themes = {t["id"]: t for t in store.brand().get("themes", [])}
    theme_use = {t["id"]: t for t in st["themes"]}
    personal_low = (st["min_personal_share"] is not None
                    and st["personal_share"] < st["min_personal_share"])
    candidates = store.candidates()
    out = []
    for row in order[:count]:
        fitting = [t for t in themes.values() if not t.get("pillars") or row["id"] in t["pillars"]]
        fitting.sort(key=lambda t: (theme_use[t["id"]]["posts"], t["id"]))
        chosen = fitting[0] if fitting else None
        stories = public_stories_for(store, pillar=row["id"],
                                     theme_id=chosen["id"] if chosen else None)
        rule = (chosen or {}).get("evidence", "either")
        if rule == "personal" or (rule == "either" and personal_low and stories):
            evidence = "personal"
        elif rule == "external":
            evidence = "external"
        else:
            evidence = "personal" if (stories and personal_low) else "external"
        needs_input = evidence == "personal" and not stories
        reasons = []
        if row["deficit"]:
            reasons.append(f"pillar below target share ({row['actual']:.2f} < {row['target']:.2f})")
        elif row["target"] is None:
            reasons.append("pillar has no target share; least recent use")
        if personal_low and evidence == "personal":
            reasons.append("personal-evidence share below minimum")
        out.append({
            "pillar": row["id"],
            "theme": chosen["id"] if chosen else None,
            "evidence": evidence,
            "stories": stories,
            "needs_personal_input": needs_input,
            "candidates": sorted(cid for cid, c in candidates.items()
                                 if c.get("status") == "new" and c.get("pillar") == row["id"]),
            "reasons": reasons or ["balanced; least recent use"],
        })
    return out
