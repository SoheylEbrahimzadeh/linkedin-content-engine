"""Regenerate examples/demo-dashboard/ from the fictional demo persona.

Runs the real Phase 1 pipeline on a temporary copy of examples/demo-persona and
stores the resulting data directory as dashboard demo fixtures. Everything is
fictional. The one approval recorded here is a fixture: it calls the approval
function directly with a simulated terminal, which real use never allows.

    python scripts/make_demo_fixtures.py
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import date
from pathlib import Path

from lce.approval import approve, prepare
from lce.dupcheck import run_dupcheck
from lce.planning import select
from lce.posts import save_draft, save_humanized
from lce.qa import run_qa
from lce.research import add_candidate, add_claim
from lce.store import DataStore, dump_yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "examples" / "demo-persona"
OUT = ROOT / "examples" / "demo-dashboard"

APPROVED = (
    "Most small service teams do not need a model to sort tickets.\n\n"
    "In our pilot, manual triage dropped from about 40 minutes a day to about 10, "
    "using twelve keyword rules.\n\n"
    "Start with the boring rules. They cover more than you expect, and they are easy to "
    "explain to the team.\n\n#automation #servicedesk\n"
)
AWAITING = (
    "Incident reviews get skipped when they feel like paperwork.\n\n"
    "The Example Ops Survey 2025 reports that 58% of small teams run no review after "
    "minor incidents.\n\n"
    "A one-page template with a single owner per action is usually enough to start. "
    "Keep it short enough that people actually fill it in.\n\n"
    "Source: https://example.org/ops-survey-2025\n\n#operations #incidents\n"
)
NEEDS_REVISION = (
    "This is a game-changer for every team! 🚀🚀🚀\n\n"
    "Studies show 73% of teams love it. Comment YES below!\n"
)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "data"
        shutil.copytree(SRC, work)
        store = DataStore.open(str(work))
        tty = dict(is_tty=lambda: True)

        pid = select(store, candidate_id="c-demo-rules-first", pillar="automation",
                     angle="rules before models", fmt="text", plan_date=date(2025, 5, 6),
                     stories=["demo-ticket-routing"])["post_id"]
        save_draft(store, pid, APPROVED)
        save_humanized(store, pid, APPROVED)
        run_qa(store, pid, denylist=[])
        run_dupcheck(store, pid)
        post, _ = prepare(store, pid)
        approve(store, pid, post["content_hash"][:12], confirm=lambda _: f"APPROVE {pid}", **tty)
        # Never record the local OS username in public fixtures.
        approved = store.load_post(pid)
        approved["approval"]["approved_by"] = "demo-fixture"
        store.save_post(approved)

        url = "https://example.org/ops-survey-2025"
        c = add_candidate(store, title="Example Ops Survey 2025: post-incident reviews",
                          origin="web_search", urls=[url], publisher="Example Org (fictional)",
                          pillar="operations", summary="Fictional source for the demo.")
        add_claim(store, c["candidate_id"],
                  "58% of small teams run no review after minor incidents", url)
        pid2 = select(store, candidate_id=c["candidate_id"], pillar="operations",
                      angle="short incident reviews", fmt="text",
                      plan_date=date(2025, 5, 8))["post_id"]
        save_draft(store, pid2, AWAITING)
        save_humanized(store, pid2, AWAITING)
        run_qa(store, pid2, denylist=[])
        run_dupcheck(store, pid2)
        prepare(store, pid2)

        # Found but not selected, no claims extracted (e.g. the page could not be read).
        add_candidate(store, title="Example Analyst Note: automation budgets in 2025",
                      origin="web_search", urls=["https://example.net/analyst-note-2025"],
                      publisher="Example Analysts (fictional)", pillar="automation",
                      summary="Fictional unselected candidate for the demo; no claims recorded.")

        c3 = add_candidate(store, title="Why teams love automation", origin="manual",
                           pillar="lessons")
        pid3 = select(store, candidate_id=c3["candidate_id"], pillar="lessons",
                      angle="enthusiasm", fmt="text", plan_date=date(2025, 5, 10))["post_id"]
        save_draft(store, pid3, NEEDS_REVISION)
        save_humanized(store, pid3, NEEDS_REVISION)
        run_qa(store, pid3, denylist=[])

        # Mark every generated data file as demo data (required under examples/).
        for path in work.rglob("*.yaml"):
            doc = store.read_doc(path)
            if doc.get("demo") is not True:
                path.write_text(dump_yaml({"demo": True, **doc}), "utf-8")
        for path in work.rglob("*.json"):
            doc = json.loads(path.read_text("utf-8"))
            path.write_text(json.dumps({"demo": True, **doc}, indent=2) + "\n", "utf-8")
        if OUT.exists():
            shutil.rmtree(OUT)
        shutil.copytree(work, OUT, ignore=shutil.ignore_patterns(".gitkeep"))
    (OUT / "README.md").write_text(
        "# Dashboard demo data (fictional)\n\nGenerated by `scripts/make_demo_fixtures.py` "
        "from `examples/demo-persona`. Every person, company, survey and URL here is "
        "invented. The approval of the first post was recorded by the fixture script, "
        "not by a human.\n", "utf-8")
    print(f"✓ wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
