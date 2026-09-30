# First live publication (Phase 4D) — owner runbook

The only step left before this is real is the owner's: a LinkedIn token and an
explicit decision to publish one post. Everything below is prepared and tested
against fake transports. Nothing here runs without you typing it.

## 0. Preconditions (each must show ✓)

| Check | Command | Expected |
|---|---|---|
| Private data valid | `lce validate $LCE_DATA_DIR` | `✓ … valid` |
| A post is approved and ready | `lce status` / dashboard | one post `READY_TO_PUBLISH` |
| Engine pinned in the private repo | `ENGINE_REF` in `.github/workflows/validate.yml` | the current engine `main` |
| LinkedIn app + token | `lce linkedin status` | token present, days left > 7 |
| Person URN | `lce linkedin whoami` | `urn:li:person:…` in `config/linkedin.yaml` |
| Cloud path only | `lce cloud doctor` | every line ✓ except `kill switch: OFF` |

The token comes from your LinkedIn developer app (**Share on LinkedIn** +
**Sign In with LinkedIn using OpenID Connect**, scopes `openid profile
w_member_social`). It is stored only in the macOS Keychain (local) or as the
Worker secret `LINKEDIN_TOKEN` (cloud); see [PUBLISHING.md](PUBLISHING.md).

## 1. Rehearse (sends nothing)

```
lce publish <post> --dry-run        # exact request body; reads no token, writes nothing
lce cloud configure --dry-run       # cloud path: settings that would be sent
```

Read the commentary in the dry run: it is the approved text, byte for byte.

## 2a. Publish locally (you, now)

```
lce publish <post>                  # type: PUBLISH <post>
```

## 2b. Or publish from the cloud (at a slot, device-independent)

```
lce cloud configure                 # timezone, cadence, LinkedIn config; never the kill switch
lce cloud push <post>               # type: DELEGATE <post>
lce cloud consent <post>            # type: SCHEDULE <post>  (the post's planned slot)
```

Then in the Cloud Control Center turn auto-publish on (phrase
`ENABLE AUTO-PUBLISH`). The cron publishes at most one post per run, within
`max_lateness_minutes` after the slot.

## 3. Abort at any point before the slot

- Kill switch off in the Cloud Control Center: nothing is sent, nothing is written.
- `lce cloud revoke <consent>` or **Withdraw** in the dashboard.
- Local: simply do not type the phrase.

## 4. Verify

- Success: the post shows `PUBLISHED` with a LinkedIn URN (`urn:li:share:…`),
  the approved hash and a timestamp (`lce cloud pull` mirrors the cloud record).
- Unclear outcome (timeout, 5xx after sending): `NEEDS_RECONCILE`. Nothing is
  retried. Check your LinkedIn profile, then
  `lce publish reconcile <post> --published-url <URL>` or `--not-published`
  (or Reconcile in the dashboard).
- Rejection (4xx before creation): `PUBLISH_FAILED` with the reason; fix and
  publish again yourself.

## 5. Afterwards

- 3–7 days later: `lce analytics record <post> --impressions … --reactions … --comments … --reposts …`.
- Update `docs/PROJECT_STATE.md`: 4D VERIFIED with the URN and date (no private text).
