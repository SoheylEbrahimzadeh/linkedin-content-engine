# Publishing

Publishing is provider-agnostic (`src/lce/publish/base.py`). **No adapter
exists yet.** The provider is chosen in a later phase.

## Candidate providers (assumptions, unverified)

Everything below is an architectural assumption. Before an adapter is built,
each point must be verified against the provider's official documentation.

| | Official LinkedIn API | Third-party scheduler (e.g. Publora) | Manual fallback |
|---|---|---|---|
| Setup | developer app + member OAuth | provider account + connected profile | none |
| Credential | member access token | provider API key | none |
| Scheduling | done by this engine | provider-side | human |
| Token lifecycle | assumed ~60 days, manual re-auth | managed by provider | n/a |
| Autonomous | yes | yes | no (temporary fallback only) |

## Excluded

Browser automation with session cookies and third-party LinkedIn scrapers are
out of scope in every phase.

## Adapter contract

See the module docstring in `src/lce/publish/base.py`: adapters must support
`find_existing` so ambiguous failures can be reconciled instead of retried.
