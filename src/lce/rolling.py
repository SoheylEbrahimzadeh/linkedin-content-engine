"""Rolling content calendar (LCE-049).

The calendar never runs dry: every cadence slot (settings `cadence`, in the
configured time zone) from today up to `plan_horizon_days` ahead gets a calendar
entry. As days pass the horizon moves with them, so new slots appear on their
own (past any month end).

- `roll` reserves the missing slots as `open` entries (origin `rolling`) with a
  target pillar that keeps the mix balanced. It never touches an existing entry:
  a date that already has an entry (planned, written, approved, published, or
  skipped by the owner) is left exactly as it is; no slot is reserved twice.
- `fill` turns one open slot into a real candidate: the writing session's package
  (fresh research with sources and claims, new text, humanization, media decided
  source-first) goes through `repackage.package`, i.e. the same checks as any
  refresh: QA, duplicate check against the archive, media rights and relevance,
  approval artifact -> AWAITING_APPROVAL. Any failure restores everything.

Nothing here approves or publishes; every candidate waits for the owner.
"""

from __future__ import annotations

import shutil
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from lce import clock
from lce.store import DataStore, StoreError, now_iso

ROLLING_TOPIC = "Open slot (a candidate is being prepared)"


def _horizon(store: DataStore) -> int:
    from lce.jobs import automation_config

    return automation_config(store)["plan_horizon_days"]


def cadence_slots(store: DataStore, now=None, days: int | None = None) -> list:
    from lce.schedule import load_schedule, slots_between

    now = now or clock.now()
    days = days if days is not None else _horizon(store)
    return slots_between(load_schedule(store.settings()), now, now + timedelta(days=days))


def _pillar_for(store: DataStore, entries: list[dict], today: date) -> str:
    """The pillar furthest below its share (brand mix targets, else an even split)."""
    pillars = [p["id"] for p in store.profile().get("pillars", [])]
    if not pillars:
        raise StoreError("the profile has no pillars")
    targets = (store.brand().get("mix") or {}).get("pillars") or {}
    if not targets:
        targets = {p: 1 / len(pillars) for p in pillars}
    window = today - timedelta(days=28)
    used = Counter(
        e["pillar"]
        for e in entries
        if e.get("status") != "skipped" and date.fromisoformat(str(e["date"])) >= window
    )
    total = sum(used.values()) + 1
    return max(pillars, key=lambda p: (targets.get(p, 0.0) - used.get(p, 0) / total, -pillars.index(p)))


def roll(store: DataStore, *, now=None, dry_run: bool = False) -> dict:
    """Reserve every cadence slot inside the horizon that has no calendar entry yet."""
    now = now or clock.now()
    plan = store.plan()
    entries = plan.setdefault("entries", [])
    taken = {str(e["date"]) for e in entries}
    added = []
    slots = cadence_slots(store, now)
    for slot in slots:
        day = slot.local.date()
        if day.isoformat() in taken:
            continue
        entry = {
            "date": day.isoformat(),
            "topic": ROLLING_TOPIC,
            "pillar": _pillar_for(store, entries, day),
            "status": "open",
            "origin": "rolling",
            "reserved_at": now_iso(),
            "duplicate_check": "pending",
            "approval_status": "pending",
            "publication_status": "not_published",
        }
        entries.append(entry)
        taken.add(entry["date"])
        added.append({**entry, "slot_local": slot.local.isoformat()})
    if added and not dry_run:
        entries.sort(key=lambda e: str(e["date"]))
        store.write_doc(store.plan_path, "plan", plan)
        store.log_event("plan.rolled", added=[a["date"] for a in added])
    last = slots[-1].local.date().isoformat() if slots else None
    return {"horizon_days": _horizon(store), "through": last, "slots": len(slots), "added": added}


def open_slots(store: DataStore, *, today: date | None = None) -> list[dict]:
    """Open entries (no candidate yet), nearest first; past ones are listed as missed."""
    from lce import refresh

    today = today or refresh.today_local(store)
    out = []
    for e in store.plan().get("entries", []):
        if e.get("status") == "open" and not e.get("draft_ref"):
            out.append({**e, "missed": date.fromisoformat(str(e["date"])) < today})
    return sorted(out, key=lambda e: str(e["date"]))


PKG_KEYS = ("plan_date", "topic", "angle", "candidate", "text", "sources", "media", "reason")


def fill(store: DataStore, pkg: dict, *, by: str = "session") -> dict:
    """Write the candidate for one open slot (atomic; restores everything on failure)."""
    from lce import refresh, repackage
    from lce.planning import select
    from lce.research import add_candidate

    missing = [k for k in PKG_KEYS if not pkg.get(k)]
    if missing:
        raise StoreError("a slot package needs " + ", ".join(missing))
    day = date.fromisoformat(str(pkg["plan_date"]))
    if day < refresh.today_local(store):
        raise StoreError(f"{day} has passed; a candidate is written only for a future slot")
    plan = store.plan()
    entry = next((e for e in plan.get("entries", []) if str(e["date"]) == day.isoformat()), None)
    if entry is None or entry.get("status") != "open" or entry.get("draft_ref"):
        raise StoreError(
            f"{day} is not an open slot (status {entry.get('status') if entry else 'none'}); "
            "an existing plan is never overwritten"
        )
    pillar = pkg.get("pillar") or entry["pillar"]
    cand = pkg["candidate"]
    if not cand.get("sources"):
        raise StoreError("the candidate needs the sources its research used")
    with tempfile.TemporaryDirectory() as tmp:
        backup = Path(tmp)
        for rel in ("plan", "research", "posts"):
            src = store.root / rel
            if src.exists():
                shutil.copytree(src, backup / rel)
        try:
            doc = add_candidate(
                store,
                title=cand.get("title") or pkg["topic"],
                origin="web_search",
                summary=cand.get("summary", ""),
                urls=[s["url"] for s in cand["sources"]],
                publisher=cand["sources"][0].get("publisher", ""),
                pillar=pillar,
                claims=[{"text": c["text"], "source_url": c["source_url"]} for c in cand.get("claims") or []],
            )
            post = select(
                store,
                candidate_id=doc["candidate_id"],
                pillar=pillar,
                angle=pkg["angle"],
                fmt=pkg.get("format", "text"),
                plan_date=day,
                topic=pkg["topic"],
                objective=pkg.get("objective"),
            )
            if post["state"] != "SELECTED":
                raise StoreError(f"the candidate needs input first ({post['state']})")
            rec = repackage.package(store, post["post_id"], pkg, by=by)
        except Exception:
            for rel in ("plan", "research", "posts"):
                dest = store.root / rel
                if dest.exists():
                    shutil.rmtree(dest)
                if (backup / rel).exists():
                    shutil.copytree(backup / rel, dest)
            raise
    store.log_event("plan.filled", plan_date=day.isoformat(), post_id=post["post_id"])
    return {"post_id": post["post_id"], "plan_date": day.isoformat(), "decision": rec["decision"],
            "state": rec["state"]}
