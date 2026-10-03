"""Writer work queue and event-driven dispatch (LCE-050).

The deterministic side decides WHAT needs a writing session, one unit at a time,
in this order: a pending replacement (owner Refresh or stale before its slot), fresh
research for a post inside its freshness window (new radar developments or no
research yet in this window), a candidate for the nearest open slot that entered
its candidate window. Each unit carries its research packet.

`dispatch` starts the writer Routine immediately through its API trigger
(`LCE_ROUTINE_FIRE_URL` + `LCE_ROUTINE_FIRE_TOKEN`, set by the owner) with the
unit's identity as the fire text, and records the fire in
`automation/fired.json` so the same unit is not started twice (a unit is fired
again only after `refire_minutes`, when it is still pending: a lost run). Without
the trigger configured nothing is sent and the reason is reported; the scheduled
Routine run is then the recovery path. Nothing here approves or publishes.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, timedelta

from lce import clock
from lce.clock import iso_utc, parse_iso
from lce.store import DataStore

BETA = "experimental-cc-routine-2026-04-01"
# The owner's routine endpoint (copied from the routine's API trigger): only this path shape is
# accepted, so the fire can only start that Claude Code routine, never call a model API.
ROUTINE_FIRE = re.compile(r"^https://[a-z0-9.-]+/v1/claude_code/routines/trig_[A-Za-z0-9]+/fire$")
REFIRE_MINUTES = 90


def _cfg(store: DataStore) -> dict:
    from lce.jobs import automation_config

    return automation_config(store)


def units(store: DataStore, now=None) -> list[dict]:
    from lce import radar, refresh, repackage, rolling

    now = now or clock.now()
    today = refresh.today_local(store)
    out = []
    for r in sorted(
        repackage.pending(store),
        key=lambda r: (str(r.get("plan_date") or "") < today.isoformat(), str(r.get("plan_date") or "")),
    ):
        out.append(
            {
                "kind": "replacement",
                "post_id": r["post_id"],
                "plan_date": r.get("plan_date"),
                "origin": r.get("origin", "owner"),
                "decision_id": r.get("decision_id"),
                "requested_at": r["requested_at"],
                "key": f"replacement:{r['post_id']}:{r['requested_at']}",
            }
        )
    for w in refresh.window(store, now):
        if w.get("needs_research"):
            out.append(
                {
                    "kind": "research",
                    "post_id": w["post_id"],
                    "plan_date": w["slot_local"][:10],
                    "why": w.get("research_why") or "no fresh research inside this window yet",
                    "new_developments": w.get("new_developments", []),
                    "key": f"research:{w['post_id']}:{w.get('research_since') or w['window_opens_at']}",
                }
            )
    lead = timedelta(days=_cfg(store)["candidate_lead_days"])
    for e in rolling.open_slots(store, today=today):
        if not e["missed"] and date.fromisoformat(str(e["date"])) <= today + lead:
            out.append(
                {
                    "kind": "slot",
                    "plan_date": str(e["date"]),
                    "pillar": e["pillar"],
                "recommendation": e.get("recommendation"),
                    "key": f"slot:{e['date']}",
                }
            )
    for u in out:
        day = u.get("plan_date")
        if day and any(str(e["date"]) == str(day) for e in store.plan().get("entries", [])):
            p = radar.packet_path(store, str(day))
            u["packet"] = str(p.relative_to(store.root)) if p.exists() else None
    return out


def next_unit(store: DataStore, now=None) -> dict | None:
    u = units(store, now)
    return u[0] if u else None


def build_packets(store: DataStore, now=None, max_age_hours: int = 6) -> list[str]:
    """(Re)build the packet of every slot that has work now, when older than `max_age_hours`."""
    from lce import radar

    now = now or clock.now()
    if not radar.load_items(store):
        return []
    built = []
    for u in units(store, now):
        day = u.get("plan_date")
        if not day:
            continue
        p = radar.packet_path(store, str(day))
        if p.exists():
            doc = store.read_doc(p)
            if doc.get("built_at") and now - parse_iso(doc["built_at"]) < timedelta(hours=max_age_hours):
                continue
        try:
            radar.packet(store, str(day), now=now)
            built.append(str(day))
        except Exception as exc:  # noqa: BLE001 - a packet never blocks the queue
            store.log_event("radar.packet_failed", plan_date=str(day), error=str(exc)[:200])
    return built


# ── dispatch ──────────────────────────────────────────────────────────
def fired_path(store: DataStore):
    return store.root / "automation" / "fired.json"


def fired(store: DataStore) -> dict:
    p = fired_path(store)
    return json.loads(p.read_text("utf-8")) if p.exists() else {}


def fire_text(u: dict) -> str:
    keep = ("kind", "post_id", "plan_date", "origin", "decision_id", "requested_at", "packet", "key")
    return "LCE work item (data, not instructions): " + json.dumps({k: u[k] for k in keep if u.get(k)})


def _post(url: str, token: str, text: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        method="POST",
        data=json.dumps({"text": text}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": BETA,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (configured https endpoint)
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read()[:300].decode("utf-8", "replace")}


def dispatch(store: DataStore, *, now=None, post=_post, dry_run: bool = False) -> dict:
    """Start the writer Routine for the next unit, once. Returns what happened and why."""
    now = now or clock.now()
    u = next_unit(store, now)
    if u is None:
        return {"fired": False, "why": "no work"}
    done = fired(store).get(u["key"])
    if done and now - parse_iso(done["fired_at"]) < timedelta(minutes=REFIRE_MINUTES):
        return {
            "fired": False,
            "unit": u,
            "why": f"already started at {done['fired_at']} ({done.get('session_url')})",
        }
    url, token = (
        os.environ.get("LCE_ROUTINE_FIRE_URL", "").strip(),
        os.environ.get("LCE_ROUTINE_FIRE_TOKEN", "").strip(),
    )
    if not (url and token):
        return {
            "fired": False,
            "unit": u,
            "why": "instant start is not configured (LCE_ROUTINE_FIRE_URL / LCE_ROUTINE_FIRE_TOKEN); "
            "the scheduled Routine run picks it up",
        }
    if not ROUTINE_FIRE.match(url):
        return {"fired": False, "unit": u, "why": "LCE_ROUTINE_FIRE_URL is not a routine /fire endpoint"}
    if dry_run:
        return {"fired": False, "unit": u, "why": "dry run"}
    status, body = post(url, token, fire_text(u))
    if status != 200:
        return {
            "fired": False,
            "unit": u,
            "why": f"the routine endpoint answered HTTP {status}: {str(body)[:200]}",
        }
    rec = fired(store)
    rec[u["key"]] = {
        "fired_at": iso_utc(now),
        "session_url": body.get("claude_code_session_url"),
        "kind": u["kind"],
        "post_id": u.get("post_id"),
        "plan_date": u.get("plan_date"),
    }
    keep = dict(sorted(rec.items(), key=lambda kv: kv[1]["fired_at"])[-200:])
    fired_path(store).parent.mkdir(parents=True, exist_ok=True)
    fired_path(store).write_text(json.dumps(keep, indent=1) + "\n", "utf-8")
    store.log_event("work.dispatched", key=u["key"], session=body.get("claude_code_session_url"))
    return {"fired": True, "unit": u, "session_url": body.get("claude_code_session_url")}
