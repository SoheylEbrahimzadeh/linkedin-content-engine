"""Content briefs: the brand strategy handed to the writing agent for each scheduled job.

The scheduler decides *when*; the brand engine decides *what*; this module joins
them so an unattended agent run picks topics strategically instead of ad hoc.
Deterministic, read-only.
"""

from __future__ import annotations

from datetime import date

from lce import brand, images
from lce.state import PostState
from lce.store import DataStore

P = PostState


def _task(store: DataStore, job: dict) -> tuple[str, str | None]:
    pid = job.get("post_id")
    if not pid:
        return "create_post", None
    post = store.load_post(pid)
    if job.get("blocked_reason") == "awaiting_revision" or post["state"] == P.NEEDS_REVISION.value:
        return "revise", pid
    if post["state"] == P.DUPLICATE_CHECKED.value and images.load(store, pid) is None:
        return "image_decision", pid
    return "continue", pid


def briefs(store: DataStore, today: date, jobs: list[dict]) -> list[dict]:
    """One brief per job, earliest slot first. New posts get successive recommendations,
    so several open slots do not all chase the same pillar."""
    from lce.analytics import saturated_topics
    from lce.planning import rank_candidates

    doc = store.brand()
    narrative = doc.get("narrative") or {}
    ordered = sorted(jobs, key=lambda j: j["slot"]["utc"])
    creating = [j for j in ordered if _task(store, j)[0] == "create_post"]
    recs = brand.recommend(store, today, count=max(len(creating), 1)) if creating else []
    ranked = rank_candidates(store, today)
    avoid = [s["topic"] for s in saturated_topics(store, today)]
    out, i = [], 0
    for job in ordered:
        task, pid = _task(store, job)
        b = {"job_id": job["job_id"], "slot_local": job["slot"]["local"], "task": task,
             "post_id": pid, "objective": doc.get("objective"),
             "throughline": narrative.get("throughline"),
             "credibility_rules": (doc.get("credibility") or {}).get("rules", []),
             "avoid_saturated_topics": avoid}
        if task == "create_post" and recs:
            rec = recs[i % len(recs)]
            i += 1
            b.update({
                "pillar": rec["pillar"], "theme": rec["theme"], "evidence": rec["evidence"],
                "needs_personal_input": rec["needs_personal_input"], "stories": rec["stories"],
                "reasons": rec["reasons"],
                "candidates": [c["candidate_id"] for c in ranked
                               if c["pillar"] == rec["pillar"] and c["score"] > 0][:5],
            })
            if rec["needs_personal_input"]:
                b["instruction"] = ("This theme needs personal evidence and no PUBLIC story "
                                    "exists: choose external evidence for this slot, or release "
                                    "the job asking the owner for a story. Never invent one.")
            elif not b["candidates"]:
                b["instruction"] = ("No research candidate fits this pillar yet: research first "
                                    "(lce-research), then select.")
            else:
                b["instruction"] = ("Select one of the candidates with the given pillar, theme "
                                    "and evidence, write, humanize, then decide the image.")
        elif task == "revise":
            b["instruction"] = (f"Fix the text from posts/{pid}/qa.json or duplicate.json and "
                                "save it with `lce humanize save`.")
        elif task == "image_decision":
            has_figures = bool(store.load_post(pid).get("claims"))
            b["instruction"] = (f"Decide the image with `lce image decide {pid}` ('none' is "
                                "valid; never a decorative image)"
                                + (f", or draw the cited figures with `lce image chart {pid}`."
                                   if has_figures else "."))
        else:
            b["instruction"] = "Run `lce automation run-once`; the scheduler continues."
        out.append(b)
    return out
