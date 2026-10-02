# Cloud publisher and remote dashboard (Phases 4B, 4C)

Scheduled publishing that needs **no Mac, PC or phone**: a Cloudflare Worker
with a Cron Trigger publishes an already human-approved post at a slot the
owner explicitly consented to, through the official LinkedIn Posts API.

```
local (Mac)                          cloud (Cloudflare, Free plan)
lce approve (human, terminal)
lce cloud push <post>  ─────────►  D1: post READY_TO_PUBLISH (hash verified)
lce cloud consent <post> --slot ►  D1: consent (one post, one slot)
                                    Cron */5 → gates → atomic claim → 1 request
                                    → PUBLISHED | PUBLISH_FAILED | NEEDS_RECONCILE
lce cloud pull  ◄─────────────────  outcomes mirrored locally
```

## Guarantees

- **Human approval stays local.** The cloud accepts a post only if the hash of
  its text equals the approved hash; the API cannot approve anything.
- **Explicit consent per slot.** One active consent per post and per slot; only
  configured, future slots (≤ 60 days) are accepted; revocable until used.
- **Kill switch OFF by default** (`auto_publish=false`, provider `none`). With
  it off, the cron run sends nothing and writes nothing.
- **Gates at run time:** kill switch on, provider `linkedin_api`, valid
  `api_version`/`person_urn`, token secret present and not past
  `token_expires_at`, consent due within `max_lateness_minutes` (default 180),
  post `READY_TO_PUBLISH`, hash(text) = approved hash = consented hash.
- **One request, never repeated:** one D1 transaction consumes the consent,
  moves the post to `PUBLISHING` and records the intent before the single
  LinkedIn request; a concurrent run cannot win the same claim.
- **Outcomes** match the local publisher (shared test vectors). Every
  transport error in the Worker is treated as ambiguous → `NEEDS_RECONCILE`
  (a Worker cannot tell whether a failed request reached LinkedIn).
- **One publisher per post:** `lce cloud push` writes `delegation.json`
  locally and local `lce publish` refuses delegated posts.
- **A missed window** (slot + lateness passed) expires the consent; nothing is
  published late.
- **Same-day freshness (LCE-040):** a scheduled publication also needs a
  freshness row received on the publishing day, dated that day, status
  `current`, for the approved text hash; otherwise the run reports
  `freshness_pending` and sends nothing. `PUT /api/freshness/:id` is CLI-only
  and accepts only today's date. See [REFRESH.md](REFRESH.md).
- **Refresh (LCE-041):** `refresh` is a recorded decision like the others
  (person only, refused for queued or published posts); earlier versions'
  images live in D1 `version_media` (migration 0007, which also rebuilds
  `decisions` for the new action, keeping every row), uploaded by `lce cloud
  sync` (`PUT /api/version-media/:id/:n`, CLI only) and served inline at
  `GET /api/posts/:id/versions/:n/image`.

## Images

A post with an approved image is pushed with it (`lce cloud push`: the image is
re-hashed against the approval and sent base64 with its alt text). The Worker
verifies the bytes against the approved hash and the PNG/JPEG/GIF signature,
stores them in `post_images` (never in listings or the snapshot) and, at the
slot, re-verifies the hash, uploads through the Images API
(`initializeUpload` → `PUT` to LinkedIn's `dms-uploads` URL → post with
`content.media`), exactly like the local publisher (shared request vector).
A failure before the post creates nothing and becomes `PUBLISH_FAILED`
(owner rearms). Images over 1.5 MB (D1 row limit) are refused at push; publish
those locally.

## Credentials

| Credential | Where | Who sets it |
|---|---|---|
| LinkedIn access token | Worker secret `LINKEDIN_TOKEN` (encrypted) | owner, Cloudflare dashboard or `wrangler secret put` |
| Cloudflare deploy token | GitHub Actions secret `CLOUDFLARE_API_TOKEN` | owner |
| API access | Cloudflare Access login; the CLI uses a short-lived token from `cloudflared access token` | not stored |

The dashboard/API exposes only `token_present` and `token_expires_at`.

## API (Cloudflare Access protected, fail-closed)

`GET /api/health` (public, no data) · `GET /api/snapshot` ·
`PUT /api/posts/:id` · `POST /api/posts/:id/withdraw|rearm|reconcile` ·
`POST /api/consents` · `DELETE /api/consents/:id` · `PUT /api/settings` ·
`GET /api/linkedin/identity` · `GET|POST /api/decisions` ·
`POST /api/decisions/:id/resolve` (CLI) · `DELETE /api/decisions/:id` ·
`POST /api/posts/:id/publish-now` · `GET /api/posts/:id/image` ·
`GET /api/preview-media` · `PUT /api/preview-media/:id` (CLI).
Every mutation is written to `events` with the Access identity.

### LinkedIn identity (LCE-035)

`GET /api/linkedin/identity` proves that the runtime `LINKEDIN_TOKEN` secret
works and derives the person URN, without the macOS Keychain. It sends exactly
one `GET https://api.linkedin.com/v2/userinfo` (OpenID Connect; sources in
[PUBLISHING.md](PUBLISHING.md)) and writes nothing, at LinkedIn or in D1. The
answer never contains the token or the profile (name, picture, email); only:

| field | meaning |
|---|---|
| `ok`, `status` | `verified`, `token_missing` (503), `token_rejected` (LinkedIn 401), `forbidden` (403, missing `openid`), `rate_limited`, `malformed_response`, `linkedin_error` (502), `timeout` (504), `network_error` |
| `http_status` | LinkedIn's status, when it answered |
| `person_urn` | `urn:li:person:{sub}`, only when verified |
| `configured_person_urn`, `person_urn_matches` | the D1 setting and whether it is the token's member (`null` when unset) |
| `api_version`, `api_version_valid` | the configured `Linkedin-Version` and whether it is `YYYYMM` |

`lce cloud doctor` runs it whenever the token is present (`linkedin identity`:
ok when the URN matches, action when person_urn is unset, fail on a different
member or a refused token). `lce cloud identity --write [--api-version 202609]`
records the URN in the private `config/linkedin.yaml`; commit that file and run
`lce cloud configure`. The route is behind Access like every other API route
(`lce cloud smoke` checks that it is refused without credentials).

### Personal LinkedIn Control Center (LCE-036)

`<api_base>/` is the owner's Control Center (Access-protected, phone and
desktop): Overview, Upcoming (7/14-day calendar of planned, scheduled and free
slots), Posts with a feed-style preview, Controlled test publish, History and
System. It publishes as the **personal profile** only: the author is the
configured `person_urn`, which the read-only identity check matches against
the token's member; company pages are not implemented. `/pipeline/` stays as
the detailed pipeline view.

Decisions (`POST /api/decisions`, D1 table `decisions`, migration 0004):
approve, reject, edit, regenerate, reschedule, skip, duplicate. Only a person
signed in through Access may decide (service tokens get 403). Approve needs
`APPROVE <post>` and is bound to the full hash of the mirrored text the owner
reviewed (and its image); reject needs `REJECT <post>`. The cloud only records
them. The private workflow runs `lce cloud decisions --apply`, which re-checks
every hash against the git files, applies the decision (approve → APPROVED →
READY_TO_PUBLISH → pushed to the cloud queue; edit → text replaced, then QA,
duplicate check and a new approval artifact; regenerate → flagged
NEEDS_REVISION for the next drafting session; reschedule/skip/duplicate →
plan), commits, and resolves each decision as `applied` or `refused`. A
decision on a post already in the cloud queue is refused (withdraw it first).

Controlled test publish (`POST /api/posts/:id/publish-now`): a person, the
phrase `PUBLISH NOW <post>`, the approved hash of the exact text shown, a post
READY_TO_PUBLISH in the cloud queue without a scheduled consent, and a fresh
identity check whose member equals `person_urn` — otherwise nothing is sent.
It does not need or change auto-publish; it reuses the scheduled publisher's
claim transaction and records the LinkedIn post URN.

Emergency stop (`emergency_stop` setting): blocks every publication,
scheduled and manual. Turning it on needs nothing; releasing it, like enabling
auto-publish, needs a person and a typed phrase (`RELEASE EMERGENCY STOP`,
`ENABLE AUTO-PUBLISH`). Optional display settings `display_name` and
`profile_url` come from the private `config/linkedin.yaml` via
`lce cloud configure`.

Content plan (LCE-037): Overview and Upcoming read the private content plan
(`/api/pipeline`, the git source of truth) and add cloud facts (scheduled
consents, publications, free slots up to 31 days). A planned post is visible
before it is approved or scheduled; a free slot never hides it. Images recorded
for posts are uploaded by `lce cloud sync` into `preview_media` (CLI only,
sha-checked, ≤ 1.5 MB, migration 0005) so previews show the real image; the
publish queue's approved image always wins. Humanization status per post: see
[HUMANIZATION.md](HUMANIZATION.md).

Media: text-only and text + one image (PNG/JPEG/GIF, ≤ 1.5 MB in the cloud)
are implemented. Video, document/PDF, article/link, multi-image and polls are
shown as "not implemented" and cannot be test-published.

Mutation safety: every non-GET request needs the header `x-lce-client`
(`cli` or `dashboard`), which forces a CORS preflight that is never granted,
and a foreign `Origin` is refused, so another site cannot ride on the Access
cookie. Actions that can lead to a publication need the owner's typed phrase in
`confirm` (HTTP 428 otherwise): `SCHEDULE <post>` (consent), `ENABLE
AUTO-PUBLISH` (turning the kill switch on), `WITHDRAW <post>`, `REARM <post>`,
`RECONCILE <post>`. Revoking a consent and turning auto-publish off need no
phrase (they only stop things).

## Remote dashboard (Phase 4C)

The Worker serves the Cloud Control Center at `/` (plus `/app.js`,
`/app.css`), behind the same fail-closed Access check: without Access it
answers 503, without a valid login 401. It works from a phone and shows the
kill switch with its gates (provider, token present, token expiry, LinkedIn
config, schedule), the next scheduled publication, upcoming slots with their
consent, posts in the cloud (state, approved hash, image), publications with
their LinkedIn links, jobs and the audit log.

From it the owner can schedule a ready post into a slot, revoke a consent,
withdraw, rearm or reconcile a post, and switch auto-publish off or (with the
phrase) on. It cannot approve content, edit text, or see or enter the token:
approval stays local and hash-bound; the token is a Worker secret. The page
holds no data (it reads `/api/snapshot`), renders everything as text, and is
served with a strict CSP (`script-src 'self'`, no inline code, no framing).

## Free-plan budget

One Cron Trigger (`*/5`, 288 runs/day). A run with nothing due makes one D1
read and no writes. CPU per run is hashing and small queries; the LinkedIn
request is wall time, not CPU. Expected cost: €0/month.

## Deploying

### Current production state (owner-reported, 2026-09-30; not verified by the agent)

- The owner connected the repository to Cloudflare **Workers Builds**. Each
  push to `main` builds and deploys the production Worker
  `linkedin-content-engine` (binding `DB` → D1 `lce`). `wrangler.toml` uses the
  same name. Workers Builds would override it anyway (`WRANGLER_CI_OVERRIDE_NAME`),
  but the manual workflow below would otherwise create a second Worker.
- Workers Builds runs `npx wrangler deploy` by default, which applies **no**
  D1 migrations. If they have not been applied, the cron reports
  `{"cron":"schema_missing"}` in the Worker logs and the API answers 503
  "database schema missing". The Worker can also apply them itself (LCE-032):
  `lce cloud migrate` shows applied/pending by name and `lce cloud migrate
  --apply` sends `POST /api/migrations` (Access + CLI header + typed phrase
  `APPLY MIGRATIONS`). It applies only pending files, each with its log row in
  one D1 batch, records them in Wrangler's own `d1_migrations` table (so
  `wrangler d1 migrations apply` later sees them as applied) and refuses with
  409 when a table it would create already exists unrecorded. The private
  repository's `cloud-sync` workflow runs it when doctor reports pending
  migrations. `wrangler.toml` has no `database_id`: Wrangler
  resolves the existing database **by its name `lce`** in the account you are
  logged into (a placeholder id used to make `--remote` target a database that
  does not exist). Two ways to fix it (owner):
  - once, from an up-to-date checkout with authenticated Wrangler
    (`npx wrangler login`):
    `cd cloud && npx wrangler d1 migrations list lce --remote` (shows what is
    pending), then `npx wrangler d1 migrations apply lce --remote`;
  - or permanently: set the Builds **Deploy command** to `npm run deploy`
    (`cloud/package.json`: applies pending migrations, then deploys). If the
    Builds token lacks D1 permission, that build fails visibly and nothing is
    deployed; fall back to the one-off command.
- Cloudflare Access identifiers: the production application's team domain and
  AUD are carried by every deploy as `[vars]` `LCE_ACCESS_TEAM_DOMAIN` /
  `LCE_ACCESS_AUD` (LCE-031). Both are public: Cloudflare sends them to every
  unauthenticated visitor in the login redirect. Dashboard variables were lost
  twice, so the deploy now configures the Worker itself. The Worker still
  verifies every request's Access JWT against them and fails closed (503) when
  they are missing or malformed. To point the Worker at another application
  without editing the file, set **both** Worker secrets (they take precedence;
  Wrangler never deletes secrets on deploy):
  `npx wrangler secret put ACCESS_TEAM_DOMAIN` (`<team>.cloudflareaccess.com`)
  and `npx wrangler secret put ACCESS_AUD` (64-hex Application Audience tag),
  or add them as *Secret* variables in the Worker's dashboard settings. Missing
  or malformed values keep the API and dashboard fail-closed (503); the Worker
  never fetches signing keys from a host that is not `*.cloudflareaccess.com`.
- Use one deploy path. Keep `CLOUD_DEPLOY_ENABLED` unset while Workers Builds
  deploys production.

### Public privacy policy (LCE-034)

`<api_base>/privacy` is the only page the Worker serves without its own Access
check: static HTML (no scripts, no data), required by the LinkedIn Developer
Portal. It describes exactly what the project processes (`cloud/src/public/privacy.html`).
Cloudflare Access must not cover that path: a separate Access application for
`<hostname>/privacy` with a **Bypass** policy (Include: Everyone); a path can
belong to only one Access application, so it is not a policy on the main one. `lce cloud
smoke` reports the page as ok, behind Access, or missing.

### Full pipeline on any device (LCE-013)

The Cloud Control Center (`/`) shows what the Worker owns: delegated posts,
consents, publications, kill switch, audit log. The **Web Control Center** at
`<api_base>/pipeline/` shows the whole private pipeline (research, calendar,
drafts, QA, duplicate checks, approval queue, brand, analytics) from any
browser. It is the same app as `lce dashboard serve`, reading a read-only
mirror in D1 (`pipeline_snapshot`, migration 0003) behind the same Access
check. `lce cloud sync` builds the dashboard snapshot from the private data
(allowlisted settings, secrets redacted, story counts only, no local paths, no
repository remote) and uploads it; the private repository can run the same
command from GitHub Actions with an Access service token after every push.
Approving stays terminal-only: the mirror cannot approve, schedule or publish.

### Verifying production from GitHub (no local machine)

`.github/workflows/production-smoke.yml` runs after every push to `main`, daily
and on demand. With the repository **variable** `LCE_API_BASE`
(`https://<worker>.<subdomain>.workers.dev`, not a secret) it waits for the
Worker, then runs `lce cloud smoke`: `/`, `/pipeline/`, `/api/snapshot`,
`/api/pipeline` and three mutations must never answer 2xx without credentials
(redirects are not followed, so an Access login redirect counts as refused).
The private repository's `cloud-sync` workflow runs the same command against
its `config/cloud.yaml`. With an **Access
service token** (Zero Trust → Access → Service Auth → create token; add a
policy with action *Service Auth* for it on the Worker's Access application)
stored as the repository secrets `LCE_CF_ACCESS_CLIENT_ID` and
`LCE_CF_ACCESS_CLIENT_SECRET`, it also runs the read-only `lce cloud doctor`
(schema, settings, provider, LinkedIn token presence and expiry). Exit codes:
0 all ok, 1 owner steps still open (the job passes with a notice), 2 broken
(the job fails).

The CLI uses the same two environment variables when they are set, so any
non-interactive client (CI, the private repository's workflows) authenticates
without `cloudflared`; otherwise it uses `cloudflared access token`.

### Manual workflow

`.github/workflows/cloud-deploy.yml` runs only manually and only when the
repository variable `CLOUD_DEPLOY_ENABLED` is `true`. It runs the tests,
checks and fills non-secret identifiers from repository variables (a missing
or malformed value stops the job), applies D1 migrations and deploys.
Wrangler telemetry is off (`send_metrics = false`).

The bundle is about 60 KiB (18 KiB gzip), well inside the Free plan limit.

### Owner runbook

Every step below creates an account resource or a credential, so the owner
performs it; the agent never does. Nothing here costs money on the Free plans.
Menu names can change; follow the current Cloudflare dashboard.

1. **Cloudflare account** (Free plan). Note the account ID.
2. **D1 database** named `lce` (dashboard → Storage & Databases → D1, or
   `npx wrangler d1 create lce`). It is found by name; no id is needed.
3. **Deploy-only API token** (My Profile → API Tokens): start from the
   "Edit Cloudflare Workers" template, add Account → D1 → Edit, and restrict it
   to this one account. Never paste it anywhere except the GitHub secret.
4. **GitHub repository settings** (Settings → Secrets and variables → Actions):
   - secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`;
   - variables: `ACCESS_TEAM_DOMAIN`
     (`<team>.cloudflareaccess.com`), `ACCESS_AUD` (64 hex characters, step 5),
     and `CLOUD_DEPLOY_ENABLED=true` only when you want to deploy.
5. **Cloudflare Access** (Zero Trust, Free for up to 50 users): create a
   self-hosted application for the Worker's `workers.dev` hostname, with a
   policy that allows only your own identity. Copy its *Application Audience
   (AUD) tag* into `ACCESS_AUD`; the workflow stores both as Worker secrets.
   On the Workers Builds path set the secrets yourself (see "Current production
   state"). Until they exist the API answers 503 to
   everything (fail-closed); after deploying, confirm that an unauthenticated
   request to `/api/snapshot` is refused.
6. **Deploy:** Actions → cloud-deploy → Run workflow.
7. **Local CLI:** put `api_base: https://linkedin-content-engine.<subdomain>.workers.dev` in
   the private data repository's `config/cloud.yaml`, install `cloudflared` and
   run `cloudflared access login <api_base>` once. Open `<api_base>/` in the
   browser: the dashboard should load after the Access login.
   From here on, `lce cloud doctor` checks every remaining gate read-only
   (Worker reachable, Access login, Access secrets, D1 schema, settings,
   provider, LinkedIn token and its expiry, kill switch) and prints the exact
   next command for the first one that is still open. Exit code 0 = all ✓.
8. **Settings:** `lce cloud configure --dry-run` shows what will be sent
   (timezone, cadence, and from `config/linkedin.yaml`: api_version,
   person_urn, visibility, token_expires_at); `lce cloud configure` sends it.
   It never sets the kill switch and never sends a token.
9. **LinkedIn token (credential gate):** set the Worker secret with
   `npx wrangler secret put LINKEDIN_TOKEN` or in the Cloudflare dashboard;
   never in a file, variable, issue or chat. The Cloud Control Center then
   shows "token present". `lce cloud doctor` then verifies it with LinkedIn
   (`linkedin identity`); `lce cloud identity --write` records the person URN
   in `config/linkedin.yaml`, then repeat step 8.
10. **First live publication (live gate, 4D):** only with the owner's explicit
    authorization: `lce cloud push <post>`, schedule it in a slot (dashboard or
    `lce cloud consent`), turn auto-publish on with its phrase, and afterwards
    `lce cloud pull` and `lce analytics record`. Step by step, with abort
    paths: [LIVE_TEST.md](LIVE_TEST.md).

After the credentials exist, steps 6–10 are the whole remaining path.

## Tests

`cd cloud && npm ci && npx vitest run` — runs in the local Workers runtime
(Miniflare) with a fake LinkedIn, a fixed clock and blocked network.
`scripts/make_cloud_vectors.py` generates the Python/TypeScript parity
vectors; `tests/test_cloud_vectors.py` fails if they are stale.
