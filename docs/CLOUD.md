# Cloud publisher (Phase 4B)

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

## Free-plan budget

One Cron Trigger (`*/5`, 288 runs/day). A run with nothing due makes one D1
read and no writes. CPU per run is hashing and small queries; the LinkedIn
request is wall time, not CPU. Expected cost: €0/month.

## Deploying (not done in 4B)

`.github/workflows/cloud-deploy.yml` runs only manually and only when the
repository variable `CLOUD_DEPLOY_ENABLED` is `true`. It runs the tests,
fills non-secret identifiers from repository variables, applies D1
migrations and deploys.

## Tests

`cd cloud && npm ci && npx vitest run` — runs in the local Workers runtime
(Miniflare) with a fake LinkedIn, a fixed clock and blocked network.
`scripts/make_cloud_vectors.py` generates the Python/TypeScript parity
vectors; `tests/test_cloud_vectors.py` fails if they are stale.
