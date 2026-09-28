# linkedin-content-engine

A reusable, privacy-first content engine for LinkedIn. It plans, drafts, audits
and — after explicit human approval — publishes posts, using only facts the
author has verified and approved.

> **Status: Phase 0 (scaffold).** Repository structure, schemas, privacy
> tooling and CI are in place. The content pipeline and publishing adapters are
> not implemented yet. See [docs/ROADMAP.md](docs/ROADMAP.md).

## Principles

- **Public code ≠ private data.** This repository holds code, templates and a
  fictional demo persona only. Personal profiles, story banks, drafts, history
  and credentials live in a separate private data directory
  ([docs/PRIVACY.md](docs/PRIVACY.md)).
- **No invented facts.** Drafts may only use facts marked `PUBLIC` in the story
  bank, and only the claims listed for them.
- **Human approval is mandatory.** Passing the audit never publishes anything.
- **No LLM API dependency.** LLM steps run inside Claude Code on a subscription;
  this code never calls an LLM API ([docs/COSTS.md](docs/COSTS.md)).
- **Provider-agnostic publishing** behind one interface
  ([docs/PUBLISHING.md](docs/PUBLISHING.md)).
- **Language-aware** humanizer and audit rules per language ([rules/](rules/)).

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start (development)

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"
brew install gitleaks          # or see https://github.com/gitleaks/gitleaks
pre-commit install --install-hooks
pytest -q
lce validate examples/demo-persona
lce privacy-scan
```

## Credits

Some concepts were studied in the MIT-licensed
[linkedin-skills](https://github.com/sergebulaev/linkedin-skills) project. This
is an independent implementation; see [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md).

## License

MIT — see [LICENSE](LICENSE).
