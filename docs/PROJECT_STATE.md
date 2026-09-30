# Project state

Single source of truth for where the project is. The agent updates this file
whenever a phase, PR, hold or gate changes (see
[AGENT_EXECUTION_CONTRACT.md](AGENT_EXECUTION_CONTRACT.md)). Phase scope is in
[ROADMAP.md](ROADMAP.md).

_Last updated: 2026-09-30_

## Current position

| | |
|---|---|
| Current phase | **6B (local)** — image upload in the local publisher (stacked on 7A) |
| Parallel track | **4B** — cloud runtime, CI green, **held by the owner** (PR #5) |
| Current gate | Owner hold on PR #5 (blocks 4C and deployment only) |
| Next phase | 4C and 6B (cloud part) once PR #5 is merged; 7B at the LinkedIn access gate |
| After that | 7 analytics & learning loop; 4C once PR #5 is merged; 4D at the credential/live gates |

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
| Operating contract | merged | #6, #7 |
| Privacy: derived denylist + history scan | merged | #8 |
| 5 Personal Brand Engine | merged | #9 |
| 6A image stage | CI green; merge blocked by tool permission | #10 |
| 7A analytics & learning loop | in review (stacked on #10) | #11 |
| 6B image upload, local publisher | in review (stacked on #11) | — |
| 4C remote dashboard (behind Access) | waits for PR #5 | — |
| 4D controlled live publishing test | credential + live gates | — |
| 6B image upload, cloud Worker | after PR #5 | — |
| 7B analytics API adapter | credential gate (LinkedIn Community Management access) | — |
| 8 more languages, approval channels | not started | — |

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

- Tooling: this session's permission checks blocked merging PR #10 and bumping `ENGINE_REF` in the private repo; both need the owner (merge button / explicit approval).

- After PR #5 merges: record 4B as merged here, then start 4C.
- Done on `phase-4b` (72b8576): `lce cloud push` refuses posts whose image decision is not `none` (the Worker is text-only until the cloud part of 6B).
- Owner input for the brand: the interview's brand questions (`lce interview next`); personal-evidence themes need PUBLIC stories in the story bank.

- The gh-pages demo is built from an older commit and is stale; rebuild after 4B/4C land.
- An exact-SHA view of a rewritten `phase-4b` commit may remain cached on GitHub
  until GitHub garbage-collects it; purging it needs the owner to contact GitHub
  Support (external action, owner's choice).
