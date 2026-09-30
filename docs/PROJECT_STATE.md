# Project state

Single source of truth for where the project is. The agent updates this file
whenever a phase, PR, hold or gate changes (see
[AGENT_EXECUTION_CONTRACT.md](AGENT_EXECUTION_CONTRACT.md)). Phase scope is in
[ROADMAP.md](ROADMAP.md).

_Last updated: 2026-09-30_

## Current position

| | |
|---|---|
| Current phase | **4B** — cloud runtime (Cloudflare Worker + D1 + Cron + Access), not deployed |
| Status | Implemented, privacy-cleaned, deploy workflow hardened, owner runbook written; CI green; PR open |
| Current gate | **Owner hold**: PR #5 must not be merged until the owner says so |
| Next phase | **4C** — remote dashboard on the Worker, behind Access (code + tests, no deployment) |
| Blocked by | 4C depends on 4B being on `main` (owner instruction: do not start 4C before PR #5 is merged) |

## Phases

| Phase | State | PR |
|---|---|---|
| 0 scaffold | done | — |
| 1 local pipeline to human approval | merged | #1 |
| 1.5 read-only Web Control Center | merged | #2 |
| 2 automation & scheduling | merged | #3 |
| 3 human-triggered publishing (official API) | merged; not tested live | #4 |
| 4A cloud architecture | done (design) | — |
| 4B cloud runtime | CI green, **held** | #5 |
| Operating contract (`CLAUDE.md`, contract, this file) | merged | #6 |
| 4C remote dashboard (behind Access) | not started | — |
| 4D controlled live publishing test | not started; needs gates below | — |
| 5 more languages, approval channels, analytics | not started | — |

## Open PRs and holds

| Item | Hold | Set by / when |
|---|---|---|
| PR #5 `phase-4b` | Do not merge until the owner says so | owner, 2026-09-30 |

## Known human gates ahead

| Needed for | Gate |
|---|---|
| Deploying the Worker (4B/4C) | Cloudflare account, D1 database, Access application and a deploy-only API token created by the owner (credential gate); authorization to deploy (production gate). Step-by-step: the owner runbook in `docs/CLOUD.md` (on `phase-4b`) |
| 4D live test | LinkedIn developer app and access token supplied by the owner as a Worker secret (credential gate); explicit authorization of a live publication (production gate) |

## Open follow-ups (routine)

- After PR #5 merges: record 4B as merged here, then start 4C.

- The gh-pages demo is built from an older commit and is stale; rebuild after 4B/4C land.
- An exact-SHA view of a rewritten `phase-4b` commit may remain cached on GitHub
  until GitHub garbage-collects it; purging it needs the owner to contact GitHub
  Support (external action, owner's choice).
