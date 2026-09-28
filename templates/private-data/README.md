# Private data repository (template)

This is the skeleton for the **private** data repository used by
`linkedin-content-engine`. Create it as a *private* repository. It holds your
personal configuration and content data, which must never go into the public
engine repository:

| Path | Holds |
|------|-------|
| `config/settings.yaml` | timezone, cadence, approval mode, publisher choice |
| `profile/brand.yaml`, `profile/voice.yaml` | personal brand and voice profile |
| `story_bank/facts/*.yaml` | verified facts, each with a `publication_status` |
| `plan/` | content calendars |
| `research/` | research notes and sources |
| `posts/` | drafts and published-post records (one file per post) |
| `runs/` | run summaries |

Rules:

- Credentials never go in this repository either. They belong in the secret
  store of the runtime (for example GitHub Actions secrets), added only when a
  phase requires them.
- Only facts with `publication_status: PUBLIC` may ever be used in a draft.
- Validate with `lce validate .` and scan with `gitleaks git`.
