"""Content planning and topic selection (deterministic rules; the LLM proposes, rules decide)."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from lce.interview import ready_for_drafting
from lce.posts import set_state, sync_plan
from lce.state import PostState
from lce.store import DataStore, StoreError, now_iso
from lce.textutil import jaccard, slugify, words

S = PostState
ACTIVE = {s.value for s in PostState} - {S.REJECTED.value}


def _post_date(post: dict) -> date:
    return date.fromisoformat(post.get("plan_date") or post["created_at"][:10])


def recent_posts(store: DataStore, today: date, days: int, exclude: str | None = None) -> list[dict]:
    since = today - timedelta(days=days)
    out = []
    for pid in store.post_ids():
        if pid == exclude:
            continue
        post = store.load_post(pid)
        if post["state"] in ACTIVE and since <= _post_date(post) <= today + timedelta(days=days):
            out.append(post)
    return out


def topic_similarity(a: str, b: str) -> float:
    return jaccard(set(words(a)), set(words(b)))


def pillar_shares(store: DataStore, today: date, days: int = 28) -> dict[str, float]:
    posts = recent_posts(store, today, days)
    counts = Counter(p.get("pillar") for p in posts if p.get("pillar"))
    total = sum(counts.values())
    return {k: v / total for k, v in counts.items()} if total else {}


def rank_candidates(store: DataStore, today: date) -> list[dict]:
    """Rank new candidates: penalize topics close to recent posts and over-used pillars."""
    tuning = store.tuning()["duplicates"]
    recent = recent_posts(store, today, tuning["topic_window_days"])
    shares = pillar_shares(store, today)
    profile = store.profile()
    pillars = {p["id"]: p for p in profile.get("pillars", [])}
    mix = store.brand().get("mix") or {}
    targets = mix.get("pillars") or {}
    brand_shares = pillar_shares(store, today, mix.get("window_days", 28)) if targets else {}
    avoid = [t.lower() for t in profile.get("topics", {}).get("avoid", [])]
    ranked = []
    for cand in store.candidates().values():
        if cand.get("status") != "new":
            continue
        reasons, score = [], 1.0
        worst = max((topic_similarity(cand["title"], p.get("topic", "")) for p in recent),
                    default=0.0)
        if worst >= tuning["topic_similarity"]:
            reasons.append(f"similar to a recent topic ({worst:.2f})")
            score -= 0.6
        pillar = cand.get("pillar")
        if pillar and pillar in pillars:
            limit = pillars[pillar].get("max_share", 0.6)
            if shares.get(pillar, 0.0) >= limit:
                reasons.append(f"pillar {pillar} over its share")
                score -= 0.3
        if pillar in targets and brand_shares.get(pillar, 0.0) < targets[pillar]:
            reasons.append(f"pillar {pillar} below its brand target")
            score += 0.2
        text = f"{cand['title']} {cand.get('summary', '')}".lower()
        if any(set(words(term)) and set(words(term)) <= set(words(text)) for term in avoid):
            reasons.append("matches a topic on the avoid list")
            score -= 1.0
        if not cand.get("sources"):
            score -= 0.1
        ranked.append({"candidate_id": cand["candidate_id"], "title": cand["title"],
                       "pillar": pillar, "score": round(score, 2), "reasons": reasons})
    return sorted(ranked, key=lambda r: -r["score"])


def add_plan_entry(store: DataStore, *, plan_date: date, topic: str, pillar: str, fmt: str = "",
                   angle: str = "") -> dict:
    plan = store.plan()
    entry = {"date": plan_date.isoformat(), "topic": topic, "pillar": pillar, "status": "planned",
             "duplicate_check": "pending", "approval_status": "pending",
             "publication_status": "not_published"}
    if fmt:
        entry["format"] = fmt
    if angle:
        entry["angle"] = angle
    plan.setdefault("entries", []).append(entry)
    store.write_doc(store.plan_path, "plan", plan)
    return entry


def _new_post_id(store: DataStore, plan_date: date, topic: str) -> str:
    base = f"{plan_date:%Y%m%d}-{slugify(topic)}"
    pid, n = base, 2
    while (store.root / "posts" / pid).exists():
        pid, n = f"{base}-{n}", n + 1
    return pid


def select(store: DataStore, *, candidate_id: str, pillar: str, angle: str, fmt: str,
           plan_date: date, topic: str | None = None, stories: list[str] | None = None,
           theme: str | None = None, evidence: str | None = None,
           chapter: str | None = None, objective: str | None = None) -> dict:
    """Create a post from a research candidate: RESEARCHED → SELECTED (or NEEDS_INPUT).

    Brand placement: an optional theme and career chapter from brand.yaml, and an
    evidence mode. A post that needs personal evidence but has no PUBLIC story
    goes to NEEDS_INPUT; evidence is never invented.
    """
    from lce import brand as brand_engine
    missing = ready_for_drafting(store)
    if missing:
        raise StoreError("profile is incomplete; answer first: " + ", ".join(q.id for q in missing))
    if theme and brand_engine.theme(store, theme) is None:
        raise StoreError(f"unknown brand theme {theme!r}")
    if chapter and brand_engine.chapter(store, chapter) is None:
        raise StoreError(f"unknown narrative chapter {chapter!r}")
    cand = store.read_doc(store.candidate_path(candidate_id))
    if not cand:
        raise StoreError(f"candidate {candidate_id} does not exist")
    if cand.get("status") != "new":
        raise StoreError(f"candidate {candidate_id} is already {cand.get('status')}")
    if objective is not None:
        from lce import voice
        if objective not in voice.objectives(store):
            raise StoreError(f"unknown objective {objective!r} (voice.yaml objectives)")
    profile = store.profile()
    if pillar not in {p["id"] for p in profile.get("pillars", [])}:
        raise StoreError(f"unknown pillar {pillar!r}")
    all_stories = store.stories()
    blocked = []
    for sid in stories or []:
        story = all_stories.get(sid)
        if story is None:
            raise StoreError(f"story {sid!r} does not exist")
        if story["publication_status"] != "PUBLIC":
            blocked.append(sid)
    usable = [s for s in (stories or []) if s not in blocked]
    try:
        mode = brand_engine.resolve_evidence(store, theme, evidence, usable)
    except ValueError as exc:
        raise StoreError(str(exc)) from exc
    placement = {"evidence": mode}
    if theme:
        placement["theme"] = theme
    if chapter:
        placement["chapter"] = chapter
    language = profile.get("languages", {}).get("primary", "en")
    topic = topic or cand["title"]
    post = {
        "post_id": _new_post_id(store, plan_date, topic),
        "created_at": now_iso(),
        "plan_date": plan_date.isoformat(),
        "language": language,
        "topic": topic,
        "pillar": pillar,
        "format": fmt,
        "angle": angle,
        "candidate_id": candidate_id,
        "sources": cand.get("sources", []),
        "claims": cand.get("claims", []),
        "stories_used": usable,
        "brand": placement,
        **({"objective": objective} if objective else {}),
        "state": S.RESEARCHED.value,
        "history": [{"at": now_iso(), "state": S.RESEARCHED.value, "note": "from candidate"}],
    }
    store.save_post(post)
    entry = next((e for e in store.plan().get("entries", [])
                  if e["date"] == plan_date.isoformat() and e.get("status") == "planned"
                  and e["pillar"] == pillar), None)
    plan = store.plan()
    if entry is None:
        add_plan_entry(store, plan_date=plan_date, topic=topic, pillar=pillar, fmt=fmt, angle=angle)
        plan = store.plan()
        entry = plan["entries"][-1]
    else:
        entry = next(e for e in plan["entries"] if e == entry)
    entry.update({"topic": topic, "angle": angle, "candidate_id": candidate_id,
                  "draft_ref": post["post_id"], "sources": [s["url"] for s in post["sources"]]})
    if objective:
        entry["objective"] = objective
    if fmt:
        entry["format"] = fmt
    store.write_doc(store.plan_path, "plan", plan)
    cand["status"] = "selected"
    store.write_doc(store.candidate_path(candidate_id), "research_candidate", cand)
    if blocked:
        return set_state(store, post, S.NEEDS_INPUT,
                         "non-public stories requested: " + ", ".join(blocked))
    if mode == "personal" and not usable:
        return set_state(store, post, S.NEEDS_INPUT,
                         "personal evidence required: add or publish a PUBLIC story, "
                         "or choose external evidence")
    post = set_state(store, post, S.SELECTED, "selected")
    sync_plan(store, post)
    return post
