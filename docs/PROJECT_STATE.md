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
| 9b | Scheduled, device-independent publishing (text + image) | ✅ code; not deployed | Cloudflare Worker, `lce cloud` |
| 9c | Remote control from any device | ✅ code (Cloud Control Center); not deployed | Worker `/` behind Access |
| 10 | Verify / reconcile | ✅ manual (LinkedIn grants no read-back) | `lce publish reconcile` |
| 11 | Record results | ✅ manual/CSV | `lce analytics record/import` |
| 12–13 | Learn and improve selection | ✅ (needs data) | `lce analytics insights`, `suggest-mix` |

## Dependency graph (what blocks what)

```
Cloudflare account + Access app + deploy-only token (CREDENTIAL GATE)
   └─► deploy Worker + dashboard (runbook steps 1–8; authorization to deploy)
         └─► LinkedIn token as Worker secret (CREDENTIAL GATE)
               └─► first live scheduled publication (LIVE GATE, 4D)
                     └─► real metrics → learning loop gets data
LinkedIn token in Keychain (CREDENTIAL GATE) ──► owner-triggered local publishing
   (fallback without any credential: post by hand + `lce publish manual`)
LinkedIn Community Management access (CREDENTIAL GATE) ──► 7B analytics API adapter
PUBLIC stories (OWNER INPUT) ──► personal-experience themes (until then NEEDS_INPUT)
```

## Credential gates

| Gate | Unlocks |
|---|---|
| Cloudflare account, D1, Access app, deploy-only token; authorization to deploy | scheduled publishing + remote dashboard |
| LinkedIn developer app + `w_member_social` token | real publishing (local or cloud) |
| LinkedIn Community Management access (`r_member_postAnalytics`) | 7B analytics API |
| Private-repo change (owner approval): `ENGINE_REF` bump + skills sync in the private data repo | private CI validates brand/image/metrics files with the current engine |

## Live-action gates

| Gate | Unlocks |
|---|---|
| Authorization of the first live publication (4D) | end-to-end proof on LinkedIn |

## Owner input (not gates; the engine runs without them, with less reach)

- PUBLIC stories in the story bank (personal-evidence themes are unusable without them).
- Optional brand fields: throughline, career chapters, target roles, pillar mix.

## Tooling blocks

None open.

## Open PRs

See GitHub; merged when green (no holds).

## Next unblocked work

Nothing left on the objective's critical path without a credential: the cloud
path (Worker, image upload, dashboard, settings sync) is built and tested, and
the post-credential path is runbook steps 6–10 in `docs/CLOUD.md`.

## Housekeeping

- The gh-pages demo is stale; rebuilding it publishes public content (owner's call).
- An exact-SHA view of a rewritten `phase-4b` commit may stay cached on GitHub until
  garbage collection; purging needs GitHub Support (owner's choice).
