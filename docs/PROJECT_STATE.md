# Project state

Single source of truth for where the parent project is. The agent updates this
file whenever work, PRs, holds or gates change (see
[AGENT_EXECUTION_CONTRACT.md](AGENT_EXECUTION_CONTRACT.md)). Phase scope is in
[ROADMAP.md](ROADMAP.md). `lce readiness` checks the same chain against the
owner's real setup.

_Last updated: 2026-10-02 (engine PRs #19–#45, lce-data PRs #1–#17; LCE-001…033)_

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

- LCE-022 (PR #32): doctor loads `/`, `/pipeline/`, `/pipeline/config.js` through
  Access; sync refuses payloads containing the private repo URL or credential values.
- LCE-023 (PR #33): explicit `lce-cli/<version>` User-Agent (Cloudflare's Browser
  Integrity Check refused `Python-urllib` with 403 before Access); no redirects;
  refusal classification.
- LCE-024…027 (PRs #34–#37): service token shape check (values never printed);
  pasted dashboard labels removed; Cloudflare's `cfast_` secret format
  (since 2026-08-26) accepted and added to gitleaks, privacy scan and redaction.
- LCE-028 (PR #38): smoke/doctor name the Access application that answers
  (team host, AUD prefix from the login redirect); actions in annotations.

- LCE-029 (PR #40): `lce cloud sync --verify` reads the mirror back from
  production (same sha256 and posts, no leaks).
- LCE-030: `keep_vars = true`, so dashboard variables survive Workers Builds deploys.

- LCE-031 (PR #43): every deploy carries the public Access identifiers
  (`[vars]` `LCE_ACCESS_TEAM_DOMAIN` / `LCE_ACCESS_AUD`, read from the login
  redirect); the Worker uses the `ACCESS_*` secret pair when both are set.
- LCE-032 (PR #44): the Worker applies pending D1 migrations on request
  (`POST /api/migrations`, Access + CLI header + typed phrase), recorded in
  Wrangler's `d1_migrations`; `lce cloud migrate [--apply]`.
- LCE-033: doctor treats an unconfigured schedule as a setup step; the private
  workflow sends timezone/cadence with `lce cloud configure` (never the kill
  switch or a token).
- LCE-034 (PR #46): public `/privacy` page (LinkedIn Developer Portal). Verified
  2026-10-02 (lce-data PR #21): 200 without credentials after the owner moved
  `/privacy` into its own Access application; every other path still 302.
- LCE-035: read-only LinkedIn identity check in the Worker,
  `GET /api/linkedin/identity` (Access-protected): one `GET /v2/userinfo` with
  the runtime `LINKEDIN_TOKEN` secret, `sub` validated, `urn:li:person:{sub}`
  derived and compared with the D1 setting; never returns the token or profile
  data, writes nothing. Doctor check `linkedin identity`; `lce cloud identity
  [--write]` records the URN in the private `config/linkedin.yaml`; smoke checks
  the route is refused without credentials. Endpoint/claims/version re-checked
  against LinkedIn's docs on 2026-10-02 (userinfo is still the OIDC `/v2`
  endpoint, unversioned; latest `Linkedin-Version` 202609).
  **VERIFIED in production** (engine `c9fbeb6`, lce-data PR #23 → `d19d768`,
  cloud-sync 2026-10-02T13:49:50Z): doctor `linkedin identity`: token verified
  with LinkedIn, member person URN resolved (recorded only in the private
  repo's run), person_urn not yet set in settings; smoke: the route is refused
  without credentials (302); `/privacy` 200; auto-publish OFF.
- LCE-036: Personal LinkedIn Control Center at `/` (Overview, Upcoming
  calendar, feed-style preview, content types, controls, controlled test
  publish, history, diagnostics; responsive). Decision inbox (D1 `decisions`,
  migration 0004) applied to the private repo by `lce cloud decisions --apply`
  with hash checks; `POST /api/posts/:id/publish-now` (person + phrase +
  approved hash + identity match; independent of auto-publish); emergency stop;
  only text and text + one image are implemented media types. Publishing target
  is the personal profile (person URN), never a company page.
- LCE-037: Upcoming/Overview driven by the content plan (pipeline mirror =
  git source of truth) with cloud facts layered on: statuses Planned →
  Awaiting approval → Approved → Scheduled → Published (+ rejected, needs
  regeneration, skipped, failed, needs reconcile), filters, 7/14/30 days,
  past-due section, planned times from free slots; redesigned UI; real image
  thumbnails via `preview_media` (migration 0005, uploaded by `lce cloud
  sync`). Humanization audit and design in [HUMANIZATION.md](HUMANIZATION.md):
  voice profile v2 with sources/review, post objective, humanization record
  and voice checklist.
  **VERIFIED in production** (engine `c8e4d17`, Workers Builds version
  `ab6cfdb3`; lce-data `2ef0e6f` cloud-sync 2026-10-02T15:59:34Z): migration
  0005 applied through the Worker (only this build embeds it); doctor all ok
  (identity matches person URN, provider linkedin_api, auto-publish OFF);
  smoke: `/privacy` 200, every other route 302 incl. decisions and
  publish-now. Decisions workflow (lce-data `f437574`, 16:48:40Z): service
  token through Access, 0 pending, nothing committed. Not yet exercised in
  production: an owner decision end to end (needs the owner's browser login),
  a real image preview (no post in the private data has an image yet).
- LCE-038: explicit media pipeline ([MEDIA.md](MEDIA.md)): text-only needs a
  recorded reason; `lce image diagram` (verbatim post text only), `lce image
  commons` (Commons API, reuse licences only, SHA-1 checked), `lce image show`;
  dimensions/mime recorded; dashboard media card (type, status, real thumbnail,
  source, rights, size, alt) and a useful Technical details section.
  **VERIFIED in production** (engine `596e74c`, Workers version `c31c4d88`;
  lce-data `b8f938d`): the Gartner post carries a real 1200×1200 PNG checklist
  diagram (own creation, verbatim post text, sha256 `6c1a4eeb63a3…`); cloud-sync
  uploaded it (`preview_media=1`); the production Control Center loaded the
  real image (naturalWidth 1200) on desktop and phone with source, rights, size
  and alt text; post still AWAITING_APPROVAL; auto-publish OFF. The 2026-09-29
  post is unchanged (no media decision: it predates the media stage). Commons
  sourcing is tested with recorded API responses only (Commons is blocked from
  the build container).

- LCE-040: same-day content refresh ([REFRESH.md](REFRESH.md)): `lce refresh
  run/research/apply/finish/push/show`, `posts/<id>/freshness.yaml`, skill
  `lce-refresh`, D1 `freshness` (migration 0006), CLI-only `PUT
  /api/freshness/:id` (today only), scheduled-publish gate
  (`freshness_pending`), Control Center freshness state + evidence. Test-mode
  (`--as-of` another day) records are never sent or counted.
  **VERIFIED in production (test mode)** (engine `2081dad`/`8d22214`, Workers
  version `bd1d82ed`; lce-data `refresh.yml`, PRs #36–#39): migration 0006
  applied by cloud-sync; the refresh workflow checked the Gartner post as of
  2026-10-08 in GitHub Actions: Gartner page HTTP 403 → `unverifiable` /
  `needs_review`, diagram `still_relevant`, approval preserved; session
  research (search summaries only; pages blocked from the session) found no
  revision → `confirmed`, not material. Text `24c149a5…`, APPROVAL.md
  `df7ca9c9…` and image `6c1a4eeb…` unchanged; still AWAITING_APPROVAL.
  The production Control Center shows "Freshness: Not checked · test run as of
  2026-10-08" with evidence on desktop and phone. The material-change path
  (new text → humanization → QA → archive duplicate check → stale diagram →
  new diagram → new approval artifact, approval invalidated) was run on a
  temporary copy only. NOT YET VERIFIED: a real same-day run on a publication
  day (first one: 2026-10-08 03:41 UTC) and the gate releasing a scheduled
  publication (nothing is scheduled; auto-publish OFF).

- LCE-041: manual post Refresh + semantically relevant media ([REFRESH.md](REFRESH.md),
  [MEDIA.md](MEDIA.md)). `media_relevance` record (concept, visual type, reason,
  copied-post-text ratio, factual claims, source requirements, decision) checked
  on every image check; text dumps rejected; `lce image diagram --spec` draws
  conceptual visuals. Control Center Refresh (Approve | Refresh | Edit |
  Reschedule | Skip | Reject) → `refresh` decision → `refresh_request` → a
  Claude Code session runs `lce refresh package` — the Routine
  `trig_01734Ek3cKiMV4Vzdyykj6ws` (every 2 h, 07:37–23:37 Berlin) fires into the
  dedicated worker session `session_01BzwBhNwT5QU5DNK3Scv7pz` (started with
  lce-data as its repository: a fresh-session Routine has no access to the
  private repo — tried, failed safely, replaced), or a session started by hand: new text, sources,
  claims, media, humanization, QA, archive duplicate check, relevance, fresh
  approval artifact; the previous version kept in `versions/vN` (exact rollback on
  failure, `lce versions restore`). Engine `afb3d64`, Workers version `68df9644`,
  migration 0007 applied by cloud-sync (lce-data PRs #40–#43).
  **VERIFIED in production**: the Control Center shows Refresh for the unpublished
  Gartner post, its media card reports the old image as "Not relevant: restates the
  post's text" (100% copied post text; approval blocked until replaced), a service
  token gets 403 for approve and refresh, auto-publish OFF. The whole package
  chain ran on a temporary copy of the real data (conceptual decision tree
  accepted, QA and duplicate check passed, new image hash bound). The Routine
  fired into the worker session: lce-data pulled, engine `afb3d64` installed,
  `refresh pending` → "No refresh requested." NOT YET
  VERIFIED: the owner's own Refresh click end to end (Dashboard → decision →
  Routine session → new version in the Control Center) — waiting for the owner.

- LCE-042: Refresh = reject the current version and write a new replacement
  (archived as `rejected`, deactivated at once; new hook, no rewording, new
  image enforced; v1 → v2 → v3 …); Skip releases the slot and generates nothing;
  every Control Center change carries a `request_id` (no duplicates on retry),
  a session check, sign-in window + exactly one retry, and an explicit
  "recorded" / "failed — not recorded" outcome; a pending decision is never
  replaced silently (409 `pending_conflict`, owner confirms). Evidence tables
  `access_sessions` and `client_reports` (migration 0008) and the decision
  timeline are printed by the production dashboard check.
  **Production findings:** the owner's Refresh WAS recorded (22:26:01 UTC) and
  was silently superseded by a Skip 58 s later (fixed, engine #65); a browser
  POST (that Skip) passed Access, so Access does not block POSTs per se; the
  earlier "signed out" refusal happened before the evidence tables existed —
  cause still unproven, now recorded on the next occurrence.
  **VERIFIED in production:** skip undone as Refresh on the owner's decision
  (`lce refresh unskip`), replacement v3 (agent-washing angle, framework visual,
  0% copied text, sourced figure) shown as "Refreshed · Awaiting approval" with
  v1 and v2 recoverable and their images in the Control Center.
  **NOT VERIFIED / OPEN:** the Routine fired into a new session without the
  private repository (the persistent-session binding did not hold; no repository
  can be attached to a Routine through the API), so v3 was written by a manual
  session; the owner's own Refresh click → v4 end to end is still open.

**LCE-029 VERIFIED — authenticated production path end to end (lce-data
`cloud-sync` on `42cfadf`, GitHub Actions → Cloudflare Access service token →
Worker → D1 → cloud-sync → dashboard):**
- doctor (before): `/api/health` 200 through Access; Access authenticated
  (Worker JWT verification); migrations: none applied, 0001–0003 pending.
- `lce cloud migrate --apply`: applied 0001_init, 0002_images, 0003_pipeline;
  pending none (production D1 `lce`, existing database, no new one).
- `lce cloud sync --verify`: D1 row sha256 `128e80321116…` equals the upload;
  1 post and 2 research items read back; no local path, private remote or
  credential in the stored snapshot.
- doctor (after): migrations 0001–0003 applied; schema present; pipeline mirror
  synced 2026-10-01T12:40:21Z; `/`, `/pipeline/` and its config load through
  Access. Open owner steps: LinkedIn config + token; kill switch stays OFF.
- Unauthenticated: every path refused by Access (302 login redirect).
- Not checkable from GitHub: the D1 database ID (no Cloudflare API credential);
  the binding resolves the database named `lce`.

**Earlier authenticated attempt (LCE-029, lce-data `cloud-sync` on `bbd4380`, after the owner
fixed the Access policies):** the service token now passes Cloudflare Access and
the Worker answers **`/api/health` 200 through Access** (VERIFIED). The Worker then
reports **"Cloudflare Access is not configured"**: `ACCESS_TEAM_DOMAIN` /
`ACCESS_AUD` are missing at runtime, so `/api/snapshot`, `/api/pipeline` and the
sync answer 503 (fail-closed). Wrangler never deletes secrets on deploy, so the
values were either plain-text variables removed by a deploy before LCE-030, or
set on another Worker/environment. OWNER_ACTION: add both on the production
Worker as type Secret (team `small-wood-2de3.cloudflareaccess.com`; AUD = the
application's AUD tag, starting `2e41a088ef87`).

**Earlier (LCE-022, lce-data `cloud-sync` on `58f6db0`):** the request
now reaches Cloudflare Access with a well-formed service token, and Access answers
with its login redirect from application `small-wood-2de3.cloudflareaccess.com`,
AUD `2e41a088ef87…`: the token is not accepted by that application. BLOCKED on the
Access configuration (owner). Not yet verified behind Access: Worker
`ACCESS_*` secrets, D1 schema 0001–0003, `/pipeline/` with data, sync. The D1
database ID cannot be checked from GitHub without a Cloudflare API credential;
`wrangler.toml` binds `DB` to the database named `lce` in the account.

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

## LCE-044 (2026-10-03): images chosen by visual review, several sources

Engine #72; lce-data #53–#55. Verified in production (dashboard-check PASS, rights record and History visible).
- `lce image search` (Commons + Openverse APIs, PD/CC0/CC BY/CC BY-SA only; previews and licence
  records, nothing attached) → the session views the previews → `media.reviewed` (refetched, licence
  re-checked at the source, hashed, attached with depicts / why / reviewer). Unsplash/Pexels not
  searched (API key would be a new secret; no scraping).
- Gartner v4 (text, hook, claims, sources unchanged): image = GAO-25-108519 figure 1 "Properties that
  Characterize AI Systems as More Agentic" (U.S. GAO, public domain, sha256 a62f8440…), chosen from 160
  candidates (37 viewed). History: v4 = post-office photo (replaced), v5 = iceBw screenshot (replaced).
  AWAITING_APPROVAL; nothing approved or published.

## LCE-043 (2026-10-03): Skip/Refresh as real transitions; real licensed images

Engine PRs #68, #69, #70; lce-data PRs #49–#52. Verified in production (dashboard-check PASS, run after #52).

- Root cause of "Skip requested — recorded" next to Approve/Refresh/Edit: the Worker
  only records decisions and the private workflow applies them later, but the UI locked
  controls only for a pending refresh. A pending skip/reject/refresh now changes the status
  at once and leaves only "Undo …". The Worker refuses other decisions over a pending
  skip/reject (`409 post_closed`).
- `decisions/overrides.yaml`: the owner's Skip `d-095407fc…` on the Gartner post
  (00:44:37 UTC) was withdrawn in chat ("it's a Refresh"). It is resolved `refused` in D1;
  v3 is archived as rejected.
- Media: `media.commons` (licence PD/CC0/CC BY/CC BY-SA; metadata must name at least two
  subject terms plus any required terms; personality-rights files refused; never an
  earlier file; every candidate recorded); otherwise text-only `no_suitable_licensed_image`.
  Generated diagrams are used only on the owner's request. The `refresh-package` workflow
  in lce-data runs packages where Commons is reachable. `lce refresh media` corrects the
  media of an unapproved candidate (text kept, old package in History as `replaced`).
- Gartner post now (AWAITING_APPROVAL, nothing approved or published):
  - v1–v3: earlier texts.
  - v4: new text ("agents arrive inside licensed apps"; Gartner 2025-08-26 forecast)
    with a 1997 post-office photo. The photo matched one generic term, so v4 is kept
    as `replaced`.
  - Current: same text with File:IceB-iceBw screenshot.png (CC BY-SA 4.0, Appsoft4),
    an accounting/ERP application. The owner rejected it as weakly related; replaced in LCE-044.
  - The current text has its credit line after the hashtags (voice warning). Fixed for
    future packages in #70.
- Owner actions open:
  - Cloudflare Access session duration is 10 s (D1 `access_sessions` evidence).
    Set it in Zero Trust → Access → Applications.
  - The writing Routine cannot reach the private repository; attach lce-data to it.

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

The cloud path is verified up to publishing. Remaining gates:

1. **LinkedIn config:** the Worker secret `LINKEDIN_TOKEN` is present (doctor,
   2026-10-02). Next: doctor's `linkedin identity` verifies it with LinkedIn;
   `config/linkedin.yaml` (api_version, person_urn from `lce cloud identity`)
   in the private repo; the private workflow then sends it with
   `lce cloud configure` and doctor shows settings/provider ✓.
2. **First live post (live gate, 4D):** [LIVE_TEST.md](LIVE_TEST.md), only with the
   owner's explicit approval.
3. **Content:** PUBLIC stories for experience-based themes; drafting resumes on
   the owner's go-ahead.

## Housekeeping

- The gh-pages demo is stale; rebuilding it publishes public content (owner's call).
- An exact-SHA view of a rewritten `phase-4b` commit may stay cached on GitHub until
  garbage collection; purging needs GitHub Support (owner's choice).
