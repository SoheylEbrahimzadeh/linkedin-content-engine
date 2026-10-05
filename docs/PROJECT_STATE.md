# Project state

Single source of truth for where the parent project is. The agent updates this
file whenever work, PRs, holds or gates change (see
[AGENT_EXECUTION_CONTRACT.md](AGENT_EXECUTION_CONTRACT.md)). Phase scope is in
[ROADMAP.md](ROADMAP.md). `lce readiness` checks the same chain against the
owner's real setup.

_Last updated: 2026-10-03 (engine PRs up to #90, lce-data PRs up to #79; LCE-001…050)_

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

## LCE-049/050 (2026-10-03): rolling calendar, freshness before the slot, Content Radar, event-driven writer

Engine #84–#90 (and the doctor-marker fix #86); lce-data #73–#79. Production state at 20:35 UTC:

**Verified in production (GitHub Actions runs and the production dashboard check)**
- **Rolling calendar:** `lce plan roll` runs hourly (`freshness` workflow, 30-day horizon). It
  reserved every cadence slot from 6 to 31 Oct (11 open slots, pillars balanced; DST change on
  25 Oct handled). Existing entries were untouched and no date was reserved twice.
- **Content Radar:** `lce radar collect` reads 18 public feeds hourly (RSS/Atom, Reddit, GitHub
  releases, vendor newsrooms); first runs stored 460 items, 168 classified into pillars, with
  provenance. Unreadable from GitHub Actions: Gartner newsroom (403), r/sysadmin and
  r/automation (429 even with pacing).
- **Research packets:** built for the slots with work (`research/packets/<date>.yaml`).
- **Writer dispatch:** runs hourly and records the real reason it did not start a writer:
  the routine API trigger is not configured yet (owner action below).
- **Dashboard (dashboard-lce050 check, desktop + phone, no console errors, no overflow):**
  - right-now board with 7 tiles;
  - Content Radar page (80 items in the mirror, 18 sources with their state, "Use for next post");
  - open slots in Upcoming;
  - live progress of the real Gartner Refresh;
  - Controls first on the post page;
  - Freshness & research card;
  - auto-publish Off.
- **Refresh progress:** the Worker records a `dispatched` progress event on every Refresh (started,
  or "not started: <reason>").

**Update 22:15 UTC.**
- **Gartner request:** the recovery writer (`trig_01KPVxwd9awVqXHcv2rvwSnt`, 21:38 run, following
  `automation/WRITER.md`) processed the real Gartner request `d-22ff5289`. Its steps:
  1. `source-fetch` for the hosts its environment blocks (#79), text committed 21:41;
  2. replacement package (#80);
  3. `refresh-package` → v10 AWAITING_APPROVAL, text-only `no_suitable_licensed_image`, v9 kept
     in History.

  Nothing was approved or published.
- **ITSM request `d-a8954f9d`:** still pending (slot 29 Sep passed).
- **UI pass (engine #93, #94):**
  - `improve-ui` audit, with three proven findings in `design-plans/` (all implemented);
  - `frontend-design` desk pass: segmented board, one side panel, the preview leads, fixed action
    bar on phones, consistent vocabulary.
  - Verified in production by `dashboard-lce050` (desktop + phone, no console errors).
- **Doctor marker:** reverted by mistake in #87 and restored in #92; cloud-sync is green again.

**Update 23:30 UTC (final acceptance round).**
- **Future slots first:** writer queue no longer starts a replacement whose slot passed (29 Sep ITSM
  stays pending; the dashboard asks for a reschedule). Engine #95.
- **`mode: new` verified in production on the open slot 6 Oct (digital-transformation):**
  1. research packet (7 fresh items);
  2. CIO.com 1 Oct 2026 read in full by `source-fetch`;
  3. claims verbatim;
  4. `lce plan fill` → `20261006-modernization-by-constraint-not-by-age` AWAITING_APPROVAL: QA and
     duplicate check passed; text-only, the source visual being restricted stock.

  Humanize warning `style.triads` was left in; WRITER.md now requires clearing such warnings.
  `refresh-package` failed after the sync on mode new (no refreshed.txt); fixed in lce-data #88.
- **Radar sources:**
  - removed: Gartner newsroom (403), r/sysadmin, r/automation (429);
  - added: The New Stack, DevOps.com and InfoQ DevOps;
  - 17 of 18 sources readable (r/servicenow intermittently 429).
- **Scheduled checks:**
  - 4 Oct 06:15 UTC: was slot 10 Oct filled by the 05:37 scheduled writer run?
  - 4 Oct 19:05 UTC: first real freshness window (6 Oct post, opens 18:30 UTC).
- **Instant Refresh trigger:** still not configured (owner action).

**Built and tested, not yet exercised end to end in production**
- **Instant writer start** on a Dashboard Refresh: needs the owner's writer routine with an API trigger.
  Its URL and token go into Worker secrets and lce-data secrets as
  `LCE_ROUTINE_FIRE_URL`/`LCE_ROUTINE_FIRE_TOKEN`.
- **Stale → same-slot replacement** in production: the first real window opens 6 Oct 18:30 UTC
  (Gartner slot 8 Oct 08:30 Berlin, 36 h lead). Verified so far on a copy of the real repository
  (`freshness-acceptance`) and by tests.
- **Slot candidates** written from packets (`lce plan fill`, package `mode: new`): no slot
  candidate has been written yet.
- **The writer itself** reading packets and using `source-fetch` for blocked hosts: the 19:37 run
  (old prompt) failed because the routine environment's network policy blocks itsm.tools,
  biztechmagazine.com and web.archive.org. The recovery trigger `trig_01KPVxwd9awVqXHcv2rvwSnt`
  (every 2 h, bound session) now follows `automation/WRITER.md`.

**Pending real requests (kept, never duplicated):**
- Gartner `d-22ff5289`: v9 archived as rejected 19:45:41.
- ITSM `d-a8954f9d`: slot 29 Sep, already passed.

**Owner actions**
- Create the writer routine (claude.ai/code/routines: repository lce-data, API trigger + a
  schedule as recovery, prompt "Follow automation/WRITER.md …").
- Set its URL and token as the two secrets above, in the Worker and in lce-data.

The skills `improve-ui` and `frontend-design` are not installed in the cloud session. The audit
and the design followed the frontend-design guidance the owner pasted.

## LCE-048 (2026-10-03): live Refresh progress from real events

Engine #81; lce-data #68–#72. The progress model: D1 `refresh_progress` events; `refresh-status`
endpoint; panel with 10 stages, elapsed time, queued / waiting / processing, stalls, failure; 15 s
polling; auto-reveal once the mirror has the replacement.

Acceptance on the real Gartner Refresh `d-bb6a0285` (recorded 17:23:25 UTC, applied 17:41:27, v8
archived as rejected):
- worker_started 17:43:58, researching 17:44:53, writing 17:45:28, media 17:45:48;
- humanization, QA and duplicate check 17:48:36; approval_prepared 17:48:37; replacement_ready 17:48:41.

The new candidate (v9, current) is AWAITING_APPROVAL and text-only, with a Gartner source visual
that is restricted. Found and fixed along the way: cloud-sync overwrote a newer mirror with a stale
checkout (#67, #69).

**Open:**
- The scheduled refresh Routine `trig_01734Ek3cKiMV4Vzdyykj6ws` is paused. Its prompt predates the
  source-first rules and progress reporting, and can only be edited from its own conversation.
- ~~Access session 10 s~~ **Resolved 2026-10-03 by the owner (Session Duration 24 hours).**
  Re-verified at 18:16 UTC with `access-probe`:
  - the owner's token since 16:47:42 lives 86400 s and served 98 requests over 40 minutes with no
    new sign-in;
  - the service-token cookie is `CF_Authorization` (Path /, Secure, SameSite=None) and expires
    after 86401 s;
  - with that cookie alone, the page, app.js, whoami, snapshot and the protected image all return 200.

## LCE-047 (2026-10-03): Access session lost between tabs, pages and images

Engine #79; lce-data #63–#64. Root cause, measured in production (`access-probe` workflow, D1
`access_sessions`): every Access application token issued to the owner lives exactly **10 seconds**
(9 of 9 sessions on 2026-10-03, including 16:20–16:28 UTC). After 10 s Access redirects every request
(new tab, API call, `<img>`) to its sign-in page; right after a sign-in images load. Our side is clean:
same origin, credentials sent, no cookies set by the Worker, image route 200 with valid credentials,
302 to Access without. **Owner action: done (Session Duration 24 h, verified 18:16 UTC, see LCE-048).**
The UI now reports the real length and, below 5 min, where to change it; images refused by Access
wait for the single sign-in banner.

## LCE-045/046 (2026-10-03): no stale decision state; source-first media

Engine #74–#77; lce-data #56–#62. Verified in production: dashboard-check PASS, dashboard-visual PASS
(desktop + phone), which now fails on any contradictory state and checks text-only media.
- **Skip contradiction:** D1 had no pending skip; the skip recorded at 00:44:37 was refused at 01:15
  (owner override). The contradictory page was a tab open since 00:44, still running pre-LCE-043 code,
  with an unreconciled in-memory "recorded" outcome. Fixes: `outcomeNow` (recorded only while D1 has the
  decision pending; afterwards its fate); a UI build id (a page running older code reloads); a tab
  coming back into view reloads its state. Verified on the real Gartner record in all four states.
- **Source first:** `source_urls` inspection (publisher pages; Internet Archive capture when the
  publisher refuses automated clients), `media.source_check` required, `association` / `why_legal`
  for any image.
- **Gartner v4:** the press release's own figure "The future of agentic AI in enterprise
  applications" (sha256 544cf6f3…) fits the post exactly, but it is © Gartner, all rights reserved,
  and external use needs Gartner's approval → `source_visual_unavailable_or_restricted`. The licensed
  Gartner-related files are not tied to this forecast. Media → text-only `no_suitable_licensed_image`;
  text hash 53a6cf3977d5 unchanged; GAO figure kept as v6 (replaced). Owner option: request Gartner's
  permission; with it, record `source_visual_used`.

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

## Update 00:30 UTC 4 Oct (executive briefing redesign)

- Engine #100 (c2d9f29): visual and UX redesign of the Control Center. No business logic changed.
  Record: `design-plans/04-executive-briefing.md`. Newsreader and Inter are self-hosted (OFL); the CSP adds `font-src 'self'` only.
- lce-data #92: ENGINE_REF pinned at c2d9f29. `dashboard-lce050` now also checks History, System, loaded fonts, the preview stage and the phone action bar.
- Production check run 37164748573 passed on desktop 1440 and phone 390, with no failures, no console errors and no overflow. Results:
  - briefing: "2 posts wait for your approval";
  - radar: 20 items, 17 of 18 sources readable;
  - Upcoming: 8 open slots;
  - Gartner post: Approve first;
  - auto-publish: Off.
- Not verified in production: the Refresh progress timeline. No Refresh is active in production, so it has only been checked with synthetic events in `tests/ui/render.mjs`.

## Update 06:30 UTC 4 Oct (LCE-051 production-readiness audit: voice system)

- Engine #102 (LCE-051):
  - content types (external insight, personal POV, personal lesson, how-to, observation);
  - private Golden Voice Set and opinion engine (a personal POV without an owner-confirmed opinion stays NEEDS_INPUT);
  - realism checks: invented experience, summary-only insights, transactional networking, claimed authority, consultant wording, generic openings, filler, jargon, repeated structures;
  - ten-criterion humanity test plus a bundled evaluation set (15 scenarios over 10 areas);
  - content-type mix, with the rolling calendar assigning producible types;
  - dashboard cards.
- Engine #103: `stance_origin` (owner / proposed / none); the post page says when a view was proposed by the writer.
- lce-data:
  - #96: `profile/golden/` templates (empty), voice.yaml v3 with 15 owner-input traits, WRITER.md content-type rules, skills synced;
  - #97: pin at 02c4974;
  - #98: production check covers the new cards.
- Production evidence:
  - the writer Routine filled the 10 Oct slot unattended at 05:27 UTC as an external_insight post (lce-data #95): sources read via source-fetch, source-first media, text-only (no reuse licence), QA and duplicate check passed, humanity 9/10 with voice `unknown`, AWAITING_APPROVAL;
  - freshness run 37182676756 assigned content types to all open slots;
  - dashboard check 37182735735 passed (desktop and phone).
- Measured voice readiness:
  - 5/20 voice traits set;
  - Golden Voice Set empty, 0 PUBLIC stories;
  - producible types: external_insight and how_to only.
- **Owner hold**: personal POV, lesson and observation content, and the voice comparison, need the owner's material (see the LCE-051 report).
- Not verified yet: the first real freshness window (6 Oct post, opens 4 Oct 18:30 UTC).

## Update 17:00 UTC 4 Oct (owner decision: writer cadence)

- Owner voice interview completed (lce-data):
  - 10 Golden Voice Set samples;
  - 5 confirmed opinions and 1 confirmed disagreement;
  - voice.yaml v4 (no em dash, no guillemets, both registers);
  - work history kept as PRIVATE background only, by the owner's decision: never posted;
  - engine #105 adds the punctuation rules.
- Writer cadence stays hourly (:27). A 5-minute cadence was declined: the writer's persistent session already has a large context and the account is near its weekly usage warning, so frequent idle runs would burn usage for nothing.
- Fast path instead: when the owner wants a Refresh written now, they ask in this session and the writer Routine is fired once on demand.

## Update 19:10 UTC 4 Oct (first real freshness window; owner approvals; source images)

- Source images:
  - owner policy `visuals.source_image_policy: owner_accepts_copyright_risk`, engine #107 (10a2a2f);
  - the three awaiting posts got their cited source's own Open Graph image, with the credit line `Image: <publisher>`, via media-only packages (lce-data #106, run 37222127788).
- The owner approved the 6 Oct and 8 Oct posts in the Control Center at 18:28 UTC (cloud-access login). Both are READY_TO_PUBLISH and delegated to the cloud publisher.
- Auto-publish is OFF and neither post is scheduled, so nothing publishes until the owner schedules a post with auto-publish on, or runs a controlled test publish.
- **Verified:** the first real freshness window (6 Oct post). The window opened at 18:30 UTC, and freshness run (18:48 UTC schedule) checked it at 18:49 UTC:
  - status `current`, decision `unchanged`, no material change;
  - the source returned HTTP 200 and all 3 recorded claims were found;
  - the media was re-evaluated;
  - plan date (2026-10-06) and approval are unchanged; no replacement was requested.
- Not verified: a fresh production dashboard check after these changes. Dispatching it was refused by the permission classifier; the last cloud-sync of the mirror succeeded.

## Update 5 Oct (voice correction: polished editorial voice)

Owner review of the 29 Sep agentic AI / ITSM post: factually fine, but "too polished, editorial,
consultant-like". Engine #109/#110: editorial phrases and labels, borrowed beliefs
(`pov.unbacked_belief`), repeated symmetric constructions (`style.symmetry`) and `angle_origin` on
packages; these codes fail the pov, voice and aloud humanity criteria. Eval set 16/16 with the
fictional `itsm-bad-editorial` regression. lce-data #109: voice v5 (both flags false, the owner's
avoided phrases and counter examples), WRITER.md and skills rules, pin c9fef44.

Rescore with v5: 29 Sep 3/10, FAIL (four QA errors). The approved 6 Oct post now shows
`style.symmetry` and the 10 Oct post (AWAITING_APPROVAL) shows `pov.unbacked_belief`; approved posts
are not re-checked unless their text changes. Nothing was approved, rewritten or published.

## Update 5 Oct (humanity v2: sound real, not impressive)

Owner standard: 10/10 only when the text sounds like a real person and like the owner, could be
spoken, varies its sentences, has no stiffness, symmetry, manufactured opinion, over-explaining or
manufactured takeaway, and has real reasoning or a concrete observation. Engine #112 (criteria,
ruleset, QA warnings, verdict PASS/PARTIAL/FAIL, eval set 23/23) and a follow-up excluding number
lists ("12, 24 or 36") from three-part lists. lce-data: voice v6, WRITER.md and skills target
10/10 PASS. Rescore: 29 Sep FAIL 4/10, 6 Oct FAIL 6/10, 8 Oct PARTIAL 7/10, 10 Oct FAIL 6/10
(QA error pov.unbacked_belief). 6, 8 and 10 Oct are READY_TO_PUBLISH and keep their approvals until
their text changes. Nothing was rewritten, approved or published.

## Update 5 Oct (owner Voice Gate)

Permanent owner rule: a post can pass the mechanical humanity checks and still fail the owner's
Voice Gate (natural English, owner-grounded opinion, owner-recognizable phrasing). Engine #114
(credit-line closings, borrowed beliefs, spoken patterns), #115 (`lce.voice_gate`, hash-bound
`voice_gate_review`, dashboard block), #116 (sentence split after quotes). lce-data: WRITER.md,
skills and voice.yaml carry the rule; pin 52ba9b2.

Controlled candidates (scratch copy only): 6, 8 and 10 Oct each humanity 10/10 PASS and Voice Gate
SOURCE_HEAVY (owner share 15 to 23%, every first-person sentence from a confirmed opinion). The
production versions are Voice Gate FAIL (invented first-person views). Production texts, approvals
(approved hash = content hash) and schedules are unchanged; a Refresh with owner re-approval is
the owner's call.
