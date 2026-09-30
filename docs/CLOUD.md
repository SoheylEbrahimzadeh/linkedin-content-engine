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
`POST /api/consents` · `DELETE /api/consents/:id` · `PUT /api/settings`.
Every mutation is written to `events` with the Access identity.

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

## Deploying (not done yet)

`.github/workflows/cloud-deploy.yml` runs only manually and only when the
repository variable `CLOUD_DEPLOY_ENABLED` is `true`. It runs the tests,
checks and fills non-secret identifiers from repository variables (a missing
or malformed value stops the job), applies D1 migrations and deploys.
Wrangler telemetry is off (`send_metrics = false`).

The bundle is about 37 KiB (11 KiB gzip), well inside the Free plan limit.

### Owner runbook

Every step below creates an account resource or a credential, so the owner
performs it; the agent never does. Nothing here costs money on the Free plans.
Menu names can change; follow the current Cloudflare dashboard.

1. **Cloudflare account** (Free plan). Note the account ID.
2. **D1 database** named `lce` (dashboard → Storage & Databases → D1, or
   `npx wrangler d1 create lce`). Note its database ID (a UUID).
3. **Deploy-only API token** (My Profile → API Tokens): start from the
   "Edit Cloudflare Workers" template, add Account → D1 → Edit, and restrict it
   to this one account. Never paste it anywhere except the GitHub secret.
4. **GitHub repository settings** (Settings → Secrets and variables → Actions):
   - secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`;
   - variables: `CLOUDFLARE_D1_DATABASE_ID`, `ACCESS_TEAM_DOMAIN`
     (`<team>.cloudflareaccess.com`), `ACCESS_AUD` (64 hex characters, step 5),
     and `CLOUD_DEPLOY_ENABLED=true` only when you want to deploy.
5. **Cloudflare Access** (Zero Trust, Free for up to 50 users): create a
   self-hosted application for the Worker's `workers.dev` hostname, with a
   policy that allows only your own identity. Copy its *Application Audience
   (AUD) tag* into `ACCESS_AUD`. Until this exists the API answers 503 to
   everything (fail-closed); after deploying, confirm that an unauthenticated
   request to `/api/snapshot` is refused.
6. **Deploy:** Actions → cloud-deploy → Run workflow.
7. **Local CLI:** put `api_base: https://lce-cloud.<subdomain>.workers.dev` in
   the private data repository's `config/cloud.yaml`, install `cloudflared` and
   run `cloudflared access login <api_base>` once. Open `<api_base>/` in the
   browser: the dashboard should load after the Access login.
8. **Settings:** `lce cloud configure --dry-run` shows what will be sent
   (timezone, cadence, and from `config/linkedin.yaml`: api_version,
   person_urn, visibility, token_expires_at); `lce cloud configure` sends it.
   It never sets the kill switch and never sends a token.
9. **LinkedIn token (credential gate):** set the Worker secret with
   `npx wrangler secret put LINKEDIN_TOKEN` or in the Cloudflare dashboard;
   never in a file, variable, issue or chat. The Cloud Control Center then
   shows "token present".
10. **First live publication (live gate, 4D):** only with the owner's explicit
    authorization: `lce cloud push <post>`, schedule it in a slot (dashboard or
    `lce cloud consent`), turn auto-publish on with its phrase, and afterwards
    `lce cloud pull` and `lce analytics record`.

After the credentials exist, steps 6–10 are the whole remaining path.

## Tests

`cd cloud && npm ci && npx vitest run` — runs in the local Workers runtime
(Miniflare) with a fake LinkedIn, a fixed clock and blocked network.
`scripts/make_cloud_vectors.py` generates the Python/TypeScript parity
vectors; `tests/test_cloud_vectors.py` fails if they are stale.
