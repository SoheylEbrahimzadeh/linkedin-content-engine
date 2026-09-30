# Project state

Single source of truth for where the parent project is. The agent updates this
file whenever work, PRs, holds or gates change (see
[AGENT_EXECUTION_CONTRACT.md](AGENT_EXECUTION_CONTRACT.md)). Phase scope is in
[ROADMAP.md](ROADMAP.md). `lce readiness` checks the same chain against the
owner's real setup.

_Last updated: 2026-09-30 (production alignment, LCE-001…004)_

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
| Cloud scheduling | DEPLOYED (owner-reported), not operational | Workers Builds deploys `main` to Worker `linkedin-content-engine` (D1 `lce`). Migrations and Access not verified; `ACCESS_*` reset to empty on each deploy (fail-closed). See CLOUD.md "Current production state" |
| LinkedIn publishing | COMPLETED, not live | local + cloud, image upload, fake-transport tests — **credential-gated**, first live post **live-action-gated** |
| Publication verification | VERIFIED | API 201 + URN; `NEEDS_RECONCILE` + human reconcile (LinkedIn grants no read-back); `lce cloud pull` mirrors the cloud record |
| Analytics | VERIFIED | manual/CSV metrics, cloud publications matched by URL; official API adapter **credential-gated** (7B) |
| Learning | VERIFIED | insights, theme tie-break, bounded mix suggestion (needs real metrics to say anything) |
| Personal-experience content | NEEDS_USER_INPUT | 0 PUBLIC stories; experience themes stay `NEEDS_INPUT` |

Operating procedure: [OPERATING.md](OPERATING.md).

## Dependency graph (what blocks what)

```
Worker deployed by Workers Builds on every push to main (owner-reported)
   ├─► D1 migrations applied to `lce` (OWNER ACTION, unverified)
   └─► Access app + how ACCESS_* survive Builds deploys (OWNER DECISION)
         └─► dashboard + API usable (runbook steps 7–8)
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
| D1 migrations on the production `lce` (`wrangler d1 migrations apply lce --remote`) | cron and API work (until then `schema_missing` / 503) |
| Access app + a decision on how `ACCESS_TEAM_DOMAIN`/`ACCESS_AUD` reach the Builds deploy | remote dashboard + API (fail-closed until then) |
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
- `cta.allowed` in the voice profile (unset): until set, QA does not check the closing against a CTA policy.

## Production alignment (2026-09-30)

- LCE-002: `wrangler.toml` name set to the production Worker `linkedin-content-engine`
  (the manual workflow would otherwise create a second Worker `lce-cloud`).
- LCE-003: an unmigrated D1 no longer makes every cron run throw; it reports
  `schema_missing`, and the API answers 503 with the fix instead of 500.
- LCE-004: docs and `lce readiness` no longer claim "not deployed".

## Reference re-audit (2026-09-30)

`sergebulaev/linkedin-skills` (commit `14d332b`) re-audited against `main`. Only
gap worth closing without a paid service: QA had no check for generic closers,
reveal bridges, staccato stacks or performed sincerity, and ignored the voice
profile's `cta.allowed`. Added as QA warnings. Everything else is present,
out of scope (engagement/comment tooling, Apify, Publora, Pixfaro, hook-formula
catalogue) or already rejected in [ATTRIBUTION.md](ATTRIBUTION.md).

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
