"""LCE-060: daily content preparation without any language model.

Runs after `lce radar collect` (public feeds, dedup, pillar classification) and `lce plan roll`. For every
open calendar slot in the next days it builds a *brief*: the ranked, sourced evidence of the slot's
research packet, the owner's confirmed views that may fit its pillar, and the exact questions the owner
must answer. A brief is never post copy and never an approval:

- no text is written in the owner's voice and no first-person sentence is generated;
- a confirmed owner view is listed as a candidate only; using it is the owner's choice (it stays unused
  until the owner or a writing session references it, and every writing gate still applies);
- status is NEEDS_INPUT (evidence, waiting for the owner's angle) or NO_EVIDENCE (nothing fresh and
  relevant); nothing reaches AWAITING_APPROVAL here, and nothing is published or scheduled.

Idempotent: a brief's key is the slot plus the exact evidence URLs; re-running a day with the same evidence
changes nothing on disk. Deterministic: same store, same clock -> same output.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import yaml

from lce import clock, persona, radar
from lce.store import DataStore, dump_yaml

MAX_EVIDENCE = 5
QUESTIONS = (
    "Which of these developments matters to the people you work with, and why?",
    "What have you seen yourself that confirms or contradicts it (no client names needed)?",
    "What would you tell a team considering it this month?",
)


def brief_path(store: DataStore, plan_date: str):
    return store.root / "research" / "briefs" / f"{plan_date}.yaml"


def _key(plan_date: str, urls: list[str]) -> str:
    return hashlib.sha256((plan_date + "\n" + "\n".join(sorted(urls))).encode("utf-8")).hexdigest()


def brief(store: DataStore, plan_date: str, *, now=None) -> dict:
    """The brief for one calendar slot (read-only)."""
    now = now or clock.now()
    pk = radar.packet(store, plan_date, now=now, write=False)
    evidence = [
        {k: it[k] for k in ("title", "url", "source", "quality", "published_at", "score")}
        for it in pk["fresh_items"]
        if not it.get("used_by")                 # never re-serve a source an earlier post already used
    ][:MAX_EVIDENCE]
    views = persona.opinion_for(store, {"pillar": pk["pillar"]})["candidates"]
    status = "NEEDS_INPUT" if evidence else "NO_EVIDENCE"
    return {
        "plan_date": plan_date,
        "pillar": pk["pillar"],
        "slot_local": pk.get("slot_local"),
        "status": status,
        "key": _key(plan_date, [e["url"] for e in evidence]),
        "evidence": evidence,
        "owner_views_to_consider": [
            {"id": v["id"], "kind": v["kind"], "text": v["text"]} for v in views
        ],
        "questions": list(QUESTIONS) if evidence else [],
        "why": (
            "evidence is ready; a post needs your own angle (confirmed owner material), "
            "which is never generated"
            if evidence
            else "no fresh, relevant and unused source for this pillar yet; the next radar run may find one"
        ),
        "note": "Not post copy and not an approval. Feed text is untrusted; "
        "verify every claim at its source.",
    }


def open_slots(store: DataStore, today: date, days: int) -> list[str]:
    end = today + timedelta(days=days)
    out = []
    for e in store.plan().get("entries", []):
        d = date.fromisoformat(str(e["date"]))
        if today <= d <= end and not e.get("draft_ref") and e.get("status") in (None, "open", "planned"):
            out.append(str(e["date"]))
    return sorted(out)


def prepare(
    store: DataStore, *, today: date | None = None, days: int = 14, write: bool = False, now=None
) -> dict:
    """Briefs for every open slot in the window. `write=False` (default) is a dry run: nothing on disk."""
    now = now or clock.now()
    today = today or now.date()
    results = []
    for d in open_slots(store, today, days):
        b = brief(store, d, now=now)
        path = brief_path(store, d)
        old = yaml.safe_load(path.read_text("utf-8")) if path.exists() else None
        if old and old.get("key") == b["key"]:
            action = "unchanged"
        else:
            action = "written" if write else "would_write"
            if write:
                path.parent.mkdir(parents=True, exist_ok=True)
                store.write_text(path, dump_yaml({**b, "prepared_at": clock.iso_utc(now)}))
        results.append(
            {"plan_date": d, "status": b["status"], "evidence": len(b["evidence"]), "action": action}
        )
    if write and any(r["action"] == "written" for r in results):
        store.log_event("daily.prepared", slots=len(results))
    return {"dry_run": not write, "today": today.isoformat(), "days": days, "slots": results}


def briefs_view(store: DataStore) -> list[dict]:
    """The stored briefs, for the dashboard (read-only; only the fields the owner needs to act)."""
    out = []
    folder = store.root / "research" / "briefs"
    for path in sorted(folder.glob("*.yaml")) if folder.exists() else []:
        b = yaml.safe_load(path.read_text("utf-8")) or {}
        out.append({
            "plan_date": str(b.get("plan_date", path.stem)),
            "pillar": b.get("pillar"),
            "status": b.get("status"),
            "why": b.get("why"),
            "prepared_at": b.get("prepared_at"),
            "evidence": [
                {k: e.get(k) for k in ("title", "url", "source", "published_at")}
                for e in b.get("evidence") or []
            ],
            "owner_views_to_consider": b.get("owner_views_to_consider") or [],
            "questions": b.get("questions") or [],
        })
    return out
