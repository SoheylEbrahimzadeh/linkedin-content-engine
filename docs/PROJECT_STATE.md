# Project state

Single source of truth for where the parent project is. The agent updates this
file whenever work, PRs, holds or gates change (see
[AGENT_EXECUTION_CONTRACT.md](AGENT_EXECUTION_CONTRACT.md)). Phase scope is in
[ROADMAP.md](ROADMAP.md). `lce readiness` checks the same chain against the
owner's real setup.

_Last updated: 2026-09-30 (outcome audit)_

## Objective

A personal brand and professional reputation engine that, for the owner's
private identity: researches → selects topics strategically → writes authentic
English content → verifies credibility → selects a relevant image → binds text
and image to one human approval → publishes to LinkedIn on schedule without
depending on the owner's devices → verifies/reconciles → records results →
learns → improves selection.

## Outcome audit (2026-09-30)

| Stage | Status | Evidence / what remains |
|---|---|---|
| Personal brand | VERIFIED | profile + brand.yaml in the private repo; `lce brand status/next`; `lce readiness` on real data |
| Research | VERIFIED | `lce-research` skill, `lce research add/claim/fetch`; RSS feeds optional (none configured) |
| Topic selection | VERIFIED | brand-aware ranking, `lce jobs brief` per slot, avoid list, saturation |
| English content + humanization | COMPLETED | Claude Code skills with the owner's voice profile; English ruleset |
| QA / credibility | VERIFIED | deterministic QA, evidence gating (personal → PUBLIC story or `NEEDS_INPUT`), duplicate check |
| Relevant image | VERIFIED | image decision + provenance, `lce image chart` from recorded figures, checks |
| Human approval (text + image) | VERIFIED | terminal-only, typed phrase, text hash + image hash |
| Cloud scheduling | COMPLETED, not deployed | Worker cron, consents, kill switch, Cloud Control Center — **credential-gated** |
| LinkedIn publishing | COMPLETED, not live | local + cloud, image upload, fake-transport tests — **credential-gated**, first live post **live-action-gated** |
| Publication verification | VERIFIED | API 201 + URN; `NEEDS_RECONCILE` + human reconcile (LinkedIn grants no read-back); `lce cloud pull` mirrors the cloud record |
| Analytics | VERIFIED | manual/CSV metrics, cloud publications matched by URL; official API adapter **credential-gated** (7B) |
| Learning | VERIFIED | insights, theme tie-break, bounded mix suggestion (needs real metrics to say anything) |
| Personal-experience content | NEEDS_USER_INPUT | 0 PUBLIC stories; experience themes stay `NEEDS_INPUT` |

Operating procedure: [OPERATING.md](OPERATING.md).

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
