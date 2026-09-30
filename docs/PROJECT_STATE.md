# Project state

Single source of truth for where the parent project is. The agent updates this
file whenever work, PRs, holds or gates change (see
[AGENT_EXECUTION_CONTRACT.md](AGENT_EXECUTION_CONTRACT.md)). Phase scope is in
[ROADMAP.md](ROADMAP.md). `lce readiness` checks the same chain against the
owner's real setup.

_Last updated: 2026-09-30_

## Objective

A personal brand and professional reputation engine that, for the owner's
private identity: researches → selects topics strategically → writes authentic
English content → verifies credibility → selects a relevant image → binds text
and image to one human approval → publishes to LinkedIn on schedule without
depending on the owner's devices → verifies/reconciles → records results →
learns → improves selection.

## Capability map

| # | Capability | State | Where |
|---|---|---|---|
| 1 | Private identity & positioning | ✅ engine; owner data present | profile.yaml, brand.yaml (private) |
| 2 | Research | ✅ | `lce-research`, `lce research` |
| 3 | Strategic topic selection | ✅ incl. per-job content brief | `lce brand next`, `lce jobs brief` |
| 4 | Authentic English writing | ✅ (Claude Code skills, owner voice) | `lce-create-post`, `lce-run-jobs` |
| 5 | Factual/credibility checks | ✅ | QA, evidence gating, duplicate check |
| 6 | Relevant image | ✅ decision + provenance; charts generated from recorded, sourced figures | `lce image decide`, `lce image chart` |
| 7 | Text + image bound to one approval | ✅ | approval `image_hash` |
| 8 | Human approval | ✅ (terminal, typed phrase) | `lce approve` |
| 9a | Publish, owner-triggered (local) | ✅ code; needs LinkedIn token | `lce publish`, `lce publish manual` |
| 9b | Scheduled, device-independent publishing | code on held PR #5; image upload on #14 (stacked) | Cloudflare Worker |
| 10 | Verify / reconcile | ✅ manual (LinkedIn grants no read-back) | `lce publish reconcile` |
| 11 | Record results | ✅ manual/CSV | `lce analytics record/import` |
| 12–13 | Learn and improve selection | ✅ (needs data) | `lce analytics insights`, `suggest-mix` |

## Dependency graph (what blocks what)

```
PR #5 (HOLD) ──► 9b scheduled publishing ──► 4C remote dashboard (owner: not before #5 merges)
     │                 └──► cloud image upload (Worker code exists only on phase-4b)
     └──► deployment ──► Cloudflare account + deploy token (HARD GATE)
                              └──► live test 4D ──► LinkedIn token (HARD GATE) + live authorization (HARD GATE)
LinkedIn token (HARD GATE) ──► 9a real publishing (manual posting + `lce publish manual` works without it)
LinkedIn Community Management access (HARD GATE) ──► 7B analytics API adapter
PUBLIC stories (owner input) ──► personal-evidence themes
```

## Hard human gates

| Gate | Unlocks |
|---|---|
| **HOLD on PR #5** (owner, 2026-09-30) | merging the cloud runtime; 4C; cloud image upload integration |
| Cloudflare account, D1, Access app, deploy-only token; authorization to deploy | scheduled device-independent publishing |
| LinkedIn developer app + `w_member_social` token | real publishing (local or cloud) |
| Authorization of a live publication | 4D live test |
| LinkedIn Community Management access (`r_member_postAnalytics`) | 7B analytics API |
| Private-repo changes needing explicit approval: `ENGINE_REF` bump + skills sync in the private data repo | private CI validates brand/image/metrics files with the current engine |

## Owner input (not gates; the engine runs without them, with less reach)

- PUBLIC stories in the story bank (personal-evidence themes are unusable without them).
- Optional brand fields: throughline, career chapters, target roles, pillar mix.

## Tooling blocks

| Block | Status |
|---|---|
| `MERGE_BLOCKED_BY_TOOLING` on PR #10 (earlier) | cleared: #10–#13 merged |

## Open PRs

| PR | State | Why it waits |
|---|---|---|
| #5 `phase-4b` | CI green; **HOLD** | owner hold |
| #14 `cloud-images` | stacked on #5 | needs `cloud/` (Worker source), which exists only in #5 |

## Next unblocked work

None that materially advances the objective without crossing a gate: the
remaining capability (scheduled, device-independent publishing) needs PR #5,
then Cloudflare and LinkedIn credentials. Improvements that remain possible
without gates (e.g. analytics import from the owner's own LinkedIn export)
need a sample of the owner's export format (owner input).

## Housekeeping

- The gh-pages demo is stale; rebuilding it publishes public content (owner's call).
- An exact-SHA view of a rewritten `phase-4b` commit may stay cached on GitHub until
  garbage collection; purging needs GitHub Support (owner's choice).
