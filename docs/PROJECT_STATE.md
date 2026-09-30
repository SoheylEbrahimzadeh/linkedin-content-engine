# Project state

Single source of truth for where the parent project is. The agent updates this
file whenever work, PRs, holds or gates change (see
[AGENT_EXECUTION_CONTRACT.md](AGENT_EXECUTION_CONTRACT.md)). Phase scope is in
[ROADMAP.md](ROADMAP.md). `lce readiness` checks the same chain against the
owner's real setup.

_Last updated: 2026-10-01 (engine PRs #19–#31, lce-data PRs #1–#3; LCE-001…021)_

## Objective

A personal brand and professional reputation engine that, for the owner's
private identity: researches → selects topics strategically → writes authentic
English content → verifies credibility → selects a relevant image → binds text
and image to one human approval → publishes to LinkedIn on schedule without
depending on the owner's devices → verifies/reconciles → records results →
learns → improves selection.

## Outcome audit (2026-09-30, after PR #19)

Labels: VERIFIED (evidence in tests or on real private data) · NOT_VERIFIED
(built, but the production evidence is missing) · BLOCKED (credential or live
gate) · OWNER_ACTION · WAITING_FOR_DATA. `tests/test_end_to_end.py` runs the
local chain in one test: research candidate with a source and claim →
selection → draft/humanize → QA (an invented number is blocked) → duplicate
check → image decision → hash-bound approval → fake-transport publish →
publication record → metrics → learning inputs.

| Stage | Status | Evidence / what remains |
|---|---|---|
| Personal brand | VERIFIED | profile + brand.yaml in the private repo; `lce brand status/next`; `lce readiness` on real data |
| Research | VERIFIED | `lce research add/claim/fetch`, source required for web candidates, claim must cite a candidate source (e2e test); RSS optional, none configured |
| Topic selection | VERIFIED | brand-aware ranking, `lce jobs brief`, avoid list, saturation |
| English content + humanization | VERIFIED (tooling) | Claude Code skills + English ruleset + `lce humanize check`; drafting quality depends on the LLM session |
| QA / credibility | VERIFIED | deterministic QA incl. closing/CTA checks, evidence gating, unsupported-number block (e2e test) |
| Duplicate detection | VERIFIED | `lce dupcheck`, e2e test |
| Relevant image | VERIFIED | image decision + provenance + checks; swapped image never sent |
| Human approval (text + image) | VERIFIED | terminal-only, typed phrase, text + image hash; wrong hash/phrase, edit after approval, tampered artifact, swapped image all refused (tests) |
| Local publishing (Python) | VERIFIED (fake transport) · BLOCKED live | intent before request, one request, URN recorded, re-publish refused (e2e); live needs the LinkedIn token |
| Cloud scheduling (Worker) | VERIFIED (Miniflare) · NOT_VERIFIED in production | 90 vitest incl. atomic claim, kill switch, hash gate, `schema_missing`; production D1 migrations and Access unverified (OWNER_ACTION) |
| Cloud Control Center | VERIFIED (Miniflare) · NOT_VERIFIED in production | fail-closed 503 observed by the owner; Access path needs the owner decision below |
| Publication verification | VERIFIED | API 201 + URN; ambiguous → `NEEDS_RECONCILE`, human reconcile (LinkedIn grants no read-back) |
| Analytics | VERIFIED (manual/CSV) · BLOCKED official API (7B) | e2e: recorded metrics → rate + features |
| Learning | VERIFIED (logic) · WAITING_FOR_DATA | 0 published posts; groups need ≥3 posts; mix suggestion refuses without data (e2e) |
| Personal-experience content | OWNER_ACTION | 0 PUBLIC stories; experience themes stay `NEEDS_INPUT` |

Roadmap completion, counted over the 14 in-scope phases (2.x and 8 are not
approved scope): **12/14 built and tested (86%)**; 4D and 7B are BLOCKED on
owner credentials. Proven against production: 0/4 of the live-dependent
phases (3, 4B, 4C, 6B), because no credentialed production check has run yet.

Operating procedure: [OPERATING.md](OPERATING.md).

## Dependency graph (what blocks what)

```
Worker deployed by Workers Builds on every push to main (owner-reported)
   ├─► D1 migrations applied to `lce` (OWNER ACTION, unverified)
   └─► Access app + ACCESS_* Worker secrets (OWNER ACTION; survive Builds deploys)
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
| Access app + the two Worker secrets `ACCESS_TEAM_DOMAIN`, `ACCESS_AUD` (CLOUD.md) | remote dashboard + API (fail-closed until then) |
| LinkedIn developer app + `w_member_social` token | real publishing (local or cloud) |
| LinkedIn Community Management access (`r_member_postAnalytics`) | 7B analytics API |
| Private-repo change (owner approval): `ENGINE_REF` bump + skills sync in the private data repo | private CI validates brand/image/metrics files with the current engine |

## Live-action gates

| Gate | Unlocks |
|---|---|
| Authorization of the first live publication (4D), runbook [LIVE_TEST.md](LIVE_TEST.md) | end-to-end proof on LinkedIn |

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
- LCE-005 (PR #21): Access identifiers are Worker secrets (deploys never touch
  them); the Worker fails closed unless the team domain is `*.cloudflareaccess.com`
  and the AUD is 64 hex.
- LCE-006 (PR #21): `cd cloud && npm run deploy` applies D1 migrations, then
  deploys; usable as the Workers Builds deploy command.
- LCE-007 (PR #22): `lce cloud doctor`, read-only preflight that names the first
  open production gate and its exact command.
- LCE-008 (PR #22): [LIVE_TEST.md](LIVE_TEST.md), the 4D runbook.
- LCE-011 (PR #25): Access service tokens (`LCE_CF_ACCESS_CLIENT_ID/SECRET`) for
  non-interactive clients; `lce cloud doctor` exit codes 0/1/2.
- LCE-012 (PR #25): `production-smoke` workflow verifies production from GitHub
  (fail-closed without credentials; read-only doctor with a service token).
  Needs the repository variable `LCE_API_BASE` (OWNER_ACTION).
- LCE-013 (PR #26): the Web Control Center at `<api_base>/pipeline/` shows the
  whole private pipeline from any device via `lce cloud sync` → D1 mirror
  (migration 0003). Read-only.
- LCE-014: QA warns when a post opens or closes like a recent post (30 days).
- LCE-016 (lce-data PR #1, owner-approved): ENGINE_REF, skills sync, owner-stated
  pillar topics, `cloud-sync` workflow, `config/cloud.yaml` (production api_base).
- LCE-018 (PR #29): `lce cloud smoke`, tested unauthenticated production check.
- LCE-019 (lce-data PR #2): the private `cloud-sync` workflow verifies production
  on every push and daily (smoke always; doctor + sync with a service token).
- LCE-020 (PR #30): doctor detects a missing migration 0003; results as GitHub
  annotations.
- LCE-021: doctor no longer mistakes Access at the edge (403 on `/api/health`)
  for a broken Worker; it retries health through Access with the credential.

**Production evidence (2026-10-01, lce-data `cloud-sync` on `7fde59c`, GitHub
runner → `https://linkedin-content-engine.<subdomain>.workers.dev`):** every path
(`/api/health`, `/`, `/pipeline/`, `/api/snapshot`, `/api/pipeline`, and PUT
settings / POST consents / PUT pipeline) answered **403 from Cloudflare Access at
the edge** without credentials. Reachable and fail-closed: VERIFIED. Behind the
edge (Worker Access secrets, D1 schema incl. 0003, `/pipeline/` with data, sync):
NOT_VERIFIED until an Access service token is stored in GitHub.
- LCE-017: this owner path.
- LCE-015: sweep fixed stale capability claims (verification shown as "not
  implemented", LinkedIn provider shown as text-only) and stale docstrings.
- LCE-010: `wrangler.toml` no longer carries a placeholder `database_id`; Wrangler
  resolves the existing D1 `lce` by name for deploys and `--remote` migrations
  (the placeholder made `migrations apply lce --remote` target a nonexistent id).

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

No engineering work is open without an owner step. In order:

1. **Access service token** (Zero Trust → Access → Service credentials → create;
   add a policy with action *Service Auth* for it on the Worker's Access
   application). Store `LCE_CF_ACCESS_CLIENT_ID` and `LCE_CF_ACCESS_CLIENT_SECRET`
   as Actions secrets in **lce-data** (and optionally the engine repo together
   with the variable `LCE_API_BASE`). The next `cloud-sync` run then verifies the
   Worker's Access secrets, D1 schema (0001–0003), settings and token presence,
   and fills `<api_base>/pipeline/`.
2. **Migrations** if doctor reports them missing: `git pull`,
   `cd cloud && npx wrangler login && npx wrangler d1 migrations apply lce --remote`,
   or set the Workers Builds deploy command to `npm run deploy`.
3. **LinkedIn token + first live post** (4D): [LIVE_TEST.md](LIVE_TEST.md).
4. **PUBLIC stories** for experience-based themes; `cta.allowed` in the voice profile.

## Housekeeping

- The gh-pages demo is stale; rebuilding it publishes public content (owner's call).
- An exact-SHA view of a rewritten `phase-4b` commit may stay cached on GitHub until
  garbage collection; purging needs GitHub Support (owner's choice).
