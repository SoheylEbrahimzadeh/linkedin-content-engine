"""Duplicate and over-use detection (deterministic).

Detects, against every other non-rejected post and every imported past post:
- exact duplicates (same canonical text),
- near duplicates (word-shingle Jaccard or containment above thresholds),
- a story reused too soon or too often,
- the same pillar + angle, or a very similar topic, within a time window.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from lce.planning import _post_date, recent_posts, topic_similarity
from lce.posts import current_text, set_state
from lce.state import PostState
from lce.store import DataStore, StoreError, now_iso
from lce.textutil import canonical, containment, content_hash, jaccard, shingles, slugify, words


def _fingerprint(text: str, k: int) -> tuple[str, set]:
    canon = canonical(text)
    return hashlib.sha256(canon.encode()).hexdigest(), shingles(words(text), k)


def compare_texts(text: str, corpus: dict[str, str], cfg: dict) -> dict:
    k = cfg["shingle_size"]
    exact_h, sh = _fingerprint(text, k)
    exact, near, similar = [], [], []
    for ref, other in corpus.items():
        other_h, other_sh = _fingerprint(other, k)
        if other_h == exact_h:
            exact.append(ref)
            continue
        j, c = jaccard(sh, other_sh), containment(sh, other_sh)
        item = {"ref": ref, "jaccard": round(j, 3), "containment": round(c, 3)}
        if j >= cfg["near_duplicate_jaccard"] or c >= cfg["near_duplicate_containment"]:
            near.append(item)
        elif j >= cfg["similar_warn_jaccard"]:
            similar.append(item)
    return {"exact": exact, "near": near, "similar": similar}


def run_dupcheck(store: DataStore, post_id: str, today: date | None = None) -> dict:
    post = store.load_post(post_id)
    if PostState(post["state"]) != PostState.QA_PASSED:
        raise StoreError(f"duplicate check runs on QA_PASSED posts; this one is {post['state']}")
    text = current_text(store, post_id)
    h = content_hash(text)
    if post.get("qa", {}).get("content_hash") != h:
        raise StoreError("text changed after QA; save it again and re-run QA")
    cfg = store.tuning()["duplicates"]
    today = today or _post_date(post)

    corpus: dict[str, str] = {}
    for pid in store.post_ids():
        if pid == post_id:
            continue
        other = store.load_post(pid)
        if other["state"] == PostState.REJECTED.value:
            continue
        t = store.post_text(pid, "post.md") or store.post_text(pid, "draft.md")
        if t:
            corpus[f"post:{pid}"] = t
    for name, t in store.external_posts().items():
        corpus[f"external:{name}"] = t
    result = compare_texts(text, corpus, cfg)

    # Story reuse
    story_issues = []
    all_stories = store.stories()
    window = recent_posts(store, today, cfg["story_reuse_window_days"], exclude=post_id)
    for sid in post.get("stories_used", []):
        uses_all = [p for p in (store.load_post(x) for x in store.post_ids() if x != post_id)
                    if sid in p.get("stories_used", []) and p["state"] != PostState.REJECTED.value]
        recent = [p["post_id"] for p in window if sid in p.get("stories_used", [])]
        story = all_stories.get(sid, {})
        if story and not story.get("reusable", False) and uses_all:
            story_issues.append({"story": sid, "reason": "story is marked not reusable",
                                 "used_in": [p["post_id"] for p in uses_all]})
        elif recent:
            story_issues.append({"story": sid, "reason": "used within the reuse window",
                                 "used_in": recent})
        elif len(uses_all) >= cfg["story_max_uses"]:
            story_issues.append({"story": sid, "reason": "used too often",
                                 "used_in": [p["post_id"] for p in uses_all]})

    # Topic / angle reuse
    angle_issues, topic_issues = [], []
    angle = (post.get("angle") or "").strip().lower()
    for p in recent_posts(store, today, cfg["angle_window_days"], exclude=post_id):
        if angle and p.get("pillar") == post.get("pillar") and (
            (p.get("angle") or "").strip().lower() == angle
        ):
            angle_issues.append(p["post_id"])
    for p in recent_posts(store, today, cfg["topic_window_days"], exclude=post_id):
        sim = topic_similarity(post.get("topic", ""), p.get("topic", ""))
        if sim >= cfg["topic_similarity"]:
            topic_issues.append({"ref": p["post_id"], "similarity": round(sim, 3)})

    failed = bool(result["exact"] or result["near"] or story_issues or angle_issues
                  or topic_issues)
    report = {
        "post_id": post_id,
        "content_hash": h,
        "at": now_iso(),
        "status": "failed" if failed else "passed",
        "compared_against": len(corpus),
        **result,
        "story_reuse": story_issues,
        "angle_reuse": angle_issues,
        "topic_reuse": topic_issues,
    }
    store.write_text(store.post_dir(post_id) / "duplicate.json",
                     json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    errors = (len(result["exact"]) + len(result["near"]) + len(story_issues) + len(angle_issues)
              + len(topic_issues))
    post["duplicate"] = {"status": report["status"], "content_hash": h, "at": report["at"],
                         "errors": errors, "warnings": len(result["similar"])}
    store.save_post(post)
    store.log_event("duplicate", post_id=post_id, status=report["status"],
                    exact=len(result["exact"]), near=len(result["near"]),
                    similar=len(result["similar"]), story_reuse=len(story_issues),
                    angle_reuse=len(angle_issues), topic_reuse=len(topic_issues))
    new = PostState.NEEDS_REVISION if failed else PostState.DUPLICATE_CHECKED
    set_state(store, post, new, f"duplicate check {report['status']}")
    return report


def import_external(store: DataStore, name: str, text: str) -> str:
    """Import a previously published post (plain text) so new posts are checked against it."""
    slug = slugify(name, 60)
    path = store.root / "history" / "external" / f"{slug}.md"
    if path.exists():
        raise StoreError(f"history entry {slug} already exists")
    store.write_text(path, text.strip() + "\n")
    store.log_event("history.import", name=slug)
    return slug
