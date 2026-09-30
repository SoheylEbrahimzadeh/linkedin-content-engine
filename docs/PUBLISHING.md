# Publishing (Phase 3)

Approved posts can be published to **your personal LinkedIn profile** through
the **official LinkedIn Posts API**. Publishing is always started by you, in an
interactive terminal. Nothing else can publish: not the scheduler, not the
dashboard, not a job, not CI.

```
READY_TO_PUBLISH ──lce publish (you)──► PUBLISHING ──► PUBLISHED
                                                  ├──► PUBLISH_FAILED  (not created; retry or reject)
                                                  └──► NEEDS_RECONCILE (outcome unknown; you decide)
```

## What the official API allows (facts used by the adapter)

| Topic | Fact | Source |
|---|---|---|
| Permission | `w_member_social` from the self-serve **Share on LinkedIn** product | [Getting access](https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access) |
| Endpoint | `POST https://api.linkedin.com/rest/posts`, headers `Linkedin-Version: YYYYMM`, `X-Restli-Protocol-Version: 2.0.0` | [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api) |
| Result | `201` with the post URN in `x-restli-id` | Posts API |
| Scheduling | `lifecycleState` must be `PUBLISHED` on create: no scheduling on LinkedIn | [Post schema](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/post-api-schema) |
| Reading posts | needs `r_member_social`, **restricted** — this app cannot look posts up | Posts API |
| Text format | `commentary` uses *little*: `\| { } @ [ ] ( ) < > # \ * _ ~` must be escaped | [little format](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/little-text-format) |
| Author | `urn:li:person:{sub}`; `sub` from `GET /v2/userinfo` (OpenID Connect) | [OIDC](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2) |
| Token | access tokens last 60 days; programmatic refresh only for approved partners | [Refresh tokens](https://learn.microsoft.com/en-us/linkedin/shared/authentication/programmatic-refresh-tokens) |
| Rate limits | Share on LinkedIn: 150 requests/member/day; reset at midnight UTC; `429` when exceeded | [Share on LinkedIn](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin) |
| Versions | monthly; each supported ≥ 12 months; the header is mandatory | [Versioning](https://learn.microsoft.com/en-us/linkedin/marketing/versioning) |
| Automation | bots/automated posting outside the API are prohibited (User Agreement 8.2) | [User Agreement](https://www.linkedin.com/legal/user-agreement) |

Not yet verified against a live account (checked in the first controlled test):
that `/rest/posts` accepts `w_member_social` alone for a person author, and the
exact character limit (the adapter uses LinkedIn's 3,000).

## Outcome handling

| LinkedIn answer | Outcome | Post state | Automatic retry |
|---|---|---|---|
| `201` + URN | published | `PUBLISHED` | — |
| `201` without URN | ambiguous | `NEEDS_RECONCILE` | never |
| `400 401 403 404 422` | not created | `PUBLISH_FAILED` | never (fix, then `lce publish` again) |
| `429` | not created (rate limited) | `PUBLISH_FAILED` | never; you may retry later |
| `409`, `5xx`, other codes | ambiguous | `NEEDS_RECONCILE` | never |
| timeout / connection lost after sending | ambiguous | `NEEDS_RECONCILE` | never |
| DNS failure / connection refused (not sent) | not created | `PUBLISH_FAILED` | never |
| crash mid-request | — | stays `PUBLISHING` | never; reconcile |

LinkedIn offers **no idempotency key** and this app **cannot read posts back**.
So the only protection against duplicates is: one request per confirmed
command, an intent record written **before** the request, and refusing to send
again while an attempt is `publishing` or `needs_reconcile`.

## Commands

```bash
lce linkedin status                       # config, token presence (never the value), expiry
lce linkedin whoami                       # GET /v2/userinfo → your person URN (network call)
lce publish <post> --dry-run              # exact request; reads no token, sends and writes nothing
lce publish <post>                        # interactive: type "PUBLISH <post>"
lce publish reconcile <post> --published-url https://www.linkedin.com/feed/update/urn:li:share:…/
lce publish reconcile <post> --not-published
```

`lce publish` refuses to run without an interactive terminal, when the text's
hash differs from the approved hash, when the provider is not enabled, or when
a previous attempt is unresolved. `reconcile` also requires an interactive
terminal and the phrase `RECONCILE <post>`.

## Records

`posts/<id>/publication.json` (private data): provider, local idempotency key,
approved hash, hash of the exact commentary sent, API version, author, every
attempt (intent time, outcome, HTTP status, reason), the LinkedIn URN and URL,
and who verified it (`api_response` or `owner`). Events: `publish.intent`,
`publish.published`, `publish.failed`, `publish.ambiguous`,
`publish.reconciled`. The token appears in none of them.

## Setup (done by you, later, in the first controlled test)

1. Create a LinkedIn developer app; add **Share on LinkedIn** and **Sign In with
   LinkedIn using OpenID Connect**.
2. Generate a token with `openid profile w_member_social` (Developer Portal
   token generator).
3. `security add-generic-password -s lce-linkedin -a default -w` (paste when prompted).
4. `lce linkedin whoami`, then create `config/linkedin.yaml` from
   `templates/private-data/config/linkedin.example.yaml` and set
   `publisher.provider: linkedin_api` in `config/settings.yaml`.
5. `lce publish <post> --dry-run`, then `lce publish <post>`.

Posts in the new states require an engine that knows them: raise `ENGINE_REF`
in the private repository before publishing.

## Out of scope

Company pages, images/video/documents/polls, publishing at scheduled times
(Phase 4), reading posts or statistics, refresh tokens, third-party providers,
browser automation.
