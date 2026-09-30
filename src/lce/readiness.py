"""End-to-end readiness of the owner's real setup against the product objective.

Walks the parent project's chain (identity → research → strategy → writing →
checks → image → approval → publishing → verification → results → learning)
and reports, per step, whether the owner's private data and environment are
ready, what is missing, and whether the gap is owner input, a credential, or
engineering. Read-only; no network; never reads secret values.
"""

from __future__ import annotations

import os
from datetime import date

from lce import brand, interview
from lce.privacy.fingerprint import generated_path
from lce.state import PostState
from lce.store import DataStore
from lce.validate import validate_dir

OK, TODO, GATE = "ok", "todo", "gate"   # todo = owner input or routine setup; gate = credential/live


def _row(step: str, status: str, detail: str, action: str = "") -> dict:
    return {"step": step, "status": status, "detail": detail, "action": action}


def check(store: DataStore, today: date, *, token_present: bool | None,
          linkedin_config_ok: bool, cloud_available: bool = False) -> list[dict]:
    rows = []
    _, errors = validate_dir(store.root)
    rows.append(_row("private data valid", OK if not errors else TODO,
                     f"{len(errors)} validation error(s)" if errors else "all files valid",
                     "lce validate <data dir>" if errors else ""))

    missing = interview.ready_for_drafting(store)
    rows.append(_row("1 identity & positioning", OK if not missing else TODO,
                     "profile and brand answered" if not missing else
                     "missing: " + ", ".join(q.id for q in missing),
                     "" if not missing else "lce interview next --required"))

    st = brand.status(store, today)
    optional = [k for k, v in (("throughline", (store.brand().get("narrative") or {}).get("throughline")),
                               ("chapters", (store.brand().get("narrative") or {}).get("chapters")),
                               ("mix", (store.brand().get("mix") or {}).get("pillars")),
                               ("target roles", (store.brand().get("target") or {}).get("roles")))
                if not v]
    stories = store.stories()
    public = [s for s in stories.values() if s.get("publication_status") == "PUBLIC"]
    personal_gaps = [t["id"] for t in st["themes"] if t["needs_personal_input"]]
    status = OK if st["configured"] and not st["problems"] else TODO
    detail = (f"{len(st['themes'])} theme(s); {len(public)} PUBLIC stor(ies)"
              + (f"; themes without personal evidence: {', '.join(personal_gaps)}"
                 if personal_gaps else "")
              + (f"; optional not set: {', '.join(optional)}" if optional else "")
              + (f"; problems: {'; '.join(st['problems'])}" if st["problems"] else ""))
    rows.append(_row("3 strategy (brand)", status, detail,
                     "add PUBLIC stories (story bank) for personal themes" if personal_gaps else ""))

    feeds = (store.settings().get("research") or {}).get("feeds") or []
    fresh = [c for c in store.candidates().values() if c.get("status") == "new"]
    rows.append(_row("2 research", OK if feeds or fresh else TODO,
                     f"{len(feeds)} feed(s); {len(fresh)} new candidate(s)",
                     "" if feeds else "optional: configure research.feeds; web search via lce-research"))

    cadence = store.settings().get("cadence") or {}
    rows.append(_row("schedule", OK if cadence.get("slots") else TODO,
                     f"{cadence.get('posts_per_week', 0)} post(s)/week, "
                     f"{len(cadence.get('slots', []))} slot(s)",
                     "" if cadence.get("slots") else "lce interview set cadence"))

    posts = [store.load_post(p) for p in store.post_ids()]
    by_state = {s: sum(1 for p in posts if p["state"] == s.value) for s in PostState}
    rows.append(_row("4-5 writing & checks", OK, f"{len(posts)} post(s); "
                     f"{by_state[PostState.AWAITING_APPROVAL]} awaiting approval, "
                     f"{by_state[PostState.APPROVED]} approved (not yet `lce ready`), "
                     f"{by_state[PostState.READY_TO_PUBLISH]} ready to publish, "
                     f"{by_state[PostState.NEEDS_INPUT]} need input, "
                     f"{by_state[PostState.NEEDS_REVISION]} need revision"))

    gen = generated_path()
    newest_data = max((p.stat().st_mtime for p in store.root.rglob("*.yaml")), default=0)
    if not gen.exists():
        rows.append(_row("privacy denylist", TODO, "not generated", "lce privacy-denylist"))
    elif gen.stat().st_mtime < newest_data:
        rows.append(_row("privacy denylist", TODO, "older than your private data",
                         "lce privacy-denylist"))
    else:
        rows.append(_row("privacy denylist", OK, "current"))

    provider = (store.settings().get("publisher") or {}).get("provider")
    if token_present is None:
        tok = "token status not checked"
    else:
        tok = "token present" if token_present else "no token in the Keychain"
    ready = provider == "linkedin_api" and linkedin_config_ok and token_present
    rows.append(_row("9 publishing (local, you trigger)", OK if ready else GATE,
                     f"provider {provider or 'none'}; linkedin.yaml "
                     f"{'ok' if linkedin_config_ok else 'missing/invalid'}; {tok}",
                     "" if ready else "LinkedIn app + token (owner credential); or post by hand "
                     "and record it with `lce publish manual`"))
    rows.append(_row("9 publishing (scheduled, device-independent)",
                     OK if cloud_available else GATE,
                     "cloud publisher available" if cloud_available else
                     "cloud Worker not on main (PR #5 on hold); then Cloudflare account/token "
                     "and deployment", "owner: lift the PR #5 hold, then the deploy runbook"))
    published = [p for p in posts if p["state"] == PostState.PUBLISHED.value]
    with_metrics = [p for p in published
                    if (store.post_dir(p["post_id"]) / "metrics.yaml").exists()]
    rows.append(_row("10-11 verification & results", OK if not published or with_metrics else TODO,
                     f"{len(published)} published; {len(with_metrics)} with metrics",
                     "lce analytics record <post> ..." if published and not with_metrics else ""))
    rows.append(_row("12-13 learning", OK if len(with_metrics) >= 3 else TODO,
                     f"{len(with_metrics)} post(s) with metrics (insights need ≥3 per group)",
                     "keep publishing and recording metrics" if len(with_metrics) < 3 else ""))
    if os.environ.get("LCE_DATA_DIR") is None:
        rows.append(_row("environment", TODO, "LCE_DATA_DIR not set in this shell",
                         "export LCE_DATA_DIR=<private data dir>"))
    return rows
