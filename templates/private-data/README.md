# Private data repository (template)

Skeleton for the **private** data repository used by `linkedin-content-engine`.
Keep it private. It holds personal configuration and content data, which must
never go into the public engine repository.

| Path | Holds |
|------|-------|
| `config/settings.yaml` | timezone, cadence, approval mode, research feeds |
| `profile/profile.yaml`, `profile/voice.yaml` | professional profile and voice |
| `story_bank/stories/*.yaml` | real experiences, each with a `publication_status` |
| `research/candidates/` | candidate topics with sources (web content is untrusted) |
| `plan/calendar.yaml` | content calendar |
| `posts/<post_id>/` | draft, candidate text, QA and duplicate reports, approval artifact |
| `history/external/` | previously published posts (for duplicate checks) |
| `runs/` | run history (JSON lines) |
| `interview/` | interview log (question ids and timestamps) |
| `.claude/skills/` | generic Claude Code skills (`lce skills sync`) |

Rules:

- No credentials in this repository. They belong in a secret store, added only
  when a phase requires them and the owner approves.
- Only `PUBLIC` stories, and only their `allowed_claims`, may appear in posts.
- Validate with `lce validate .`; scan with `gitleaks git`.
