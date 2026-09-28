# Security Policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting ("Report a vulnerability"
under the Security tab). Do not open a public issue for security problems.

## Design principles

- **Public code, private data.** This repository contains only reusable code,
  generic templates, and fictional demo data. Personal profiles, story banks,
  drafts, publishing history, analytics and all credentials live in a separate
  private data directory and are never committed here.
- **No Anthropic API usage.** The engine has no dependency on the Anthropic API
  and CI fails if one is introduced. LLM steps run inside Claude Code.
- **Defence in depth against leaks:** `.gitignore`, a runtime guard on the data
  directory location, pre-commit and pre-push hooks (gitleaks + privacy scan),
  CI scanning of the full history, and GitHub secret scanning with push
  protection.
- **Human approval before publishing.** No content is published without an
  explicit human approval recorded against the exact approved text.

See [docs/PRIVACY.md](docs/PRIVACY.md) for details.
