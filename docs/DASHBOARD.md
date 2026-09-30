# Web Control Center

A read-only dashboard for the content engine. It shows the real state of a
data directory; it cannot change data, approve or publish.

## Run it against your data (local, private)

```bash
cd ~/Claude/linkedin-content-engine
source .venv/bin/activate
LCE_DATA_DIR=~/Claude/lce-data lce dashboard serve
# open http://127.0.0.1:8765/
```

The banner shows **REAL DATA**. The server binds to `127.0.0.1` only, rejects
other `Host` headers, answers GET/HEAD only, and rebuilds the snapshot from
disk on every request (use **Refresh**).

## Demo mode (fictional data)

```bash
lce dashboard serve --demo          # local, fictional data
lce dashboard build-demo --out _site  # static site for GitHub Pages
```

The banner shows **DEMO MODE — NO PRIVATE DATA**. Demo data comes only from
`examples/demo-dashboard/` (regenerate with `python scripts/make_demo_fixtures.py`).
The build ignores `$LCE_DATA_DIR`, refuses to write into a data directory, and
contains no filesystem paths or git remotes. The page refuses to render when the
page mode and the data mode differ; it never falls back to other data.

## Architecture

| Part | File |
|---|---|
| Snapshot (read-only data layer, issue detection, redaction) | `src/lce/dashboard/snapshot.py` |
| Local server (stdlib, 127.0.0.1) | `src/lce/dashboard/server.py` |
| Static demo build | `src/lce/dashboard/build.py` |
| UI (vanilla JS/CSS, no build step, no dependencies) | `src/lce/dashboard/static/` |

Sections: Dashboard, Posts (+ detail), Research, Calendar (with upcoming
posting slots and their jobs), Approval, Publishing, Automation (jobs, states,
history, configuration), Monitoring (including scheduler and job events),
Errors / Reconciliation (failed jobs, jobs needing reconciliation, expired
leases, stale locks), Analytics (not available), Settings (allowlisted, no
credentials).

The dashboard shows the last scheduler pass and the next slot; it never claims
that automation is active, because the engine configures no trigger. Demo mode
uses a fixed demo clock (`DEMO_NOW` in `server.py`) so its fictional jobs and
slots line up; real mode uses the system clock. The UI takes "today" from the
snapshot, never from the browser clock.

## Honest states

- Publishing, verification and analytics are shown as **not implemented**.
- Every post shows `publication_status` from the data; Phase 1 can only be
  `not_published` or `ready_to_publish` (still not published).
- Missing values are shown as `unknown`.
- Issues: `FAILED`, `NEEDS_RECONCILE` (files and recorded state disagree, e.g.
  text changed after approval) and `INCONSISTENT` (metadata out of sync) are
  detected deterministically; normal pipeline states are not issues.
