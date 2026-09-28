# linkedin-content-engine

A reusable, privacy-first content engine for LinkedIn. It plans, drafts, audits
and — after explicit human approval — publishes posts, using only facts the
author has verified and approved.

> **Status: Phase 1.** A local pipeline runs from research to an explicit,
> hash-bound human approval and stops at `READY_TO_PUBLISH`. **There is no
> publisher yet; nothing is ever sent to LinkedIn.** See
> [docs/ROADMAP.md](docs/ROADMAP.md) and [docs/PIPELINE.md](docs/PIPELINE.md).

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
- **Language-aware** humanizer and audit rules per language
  ([src/lce/rules/](src/lce/rules/)).

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

## Using it with your own data

```bash
lce init-data /path/outside/this/repo/my-data   # your private data directory
export LCE_DATA_DIR=/path/outside/this/repo/my-data
lce skills sync                                  # Claude Code skills into the data dir
lce status                                       # what the interview still needs
```

See the state of everything in the browser (read-only, local only):

```bash
lce dashboard serve        # http://127.0.0.1:8765/ — REAL DATA
lce dashboard serve --demo # fictional data
```

More in [docs/DASHBOARD.md](docs/DASHBOARD.md). Then open Claude Code in the data directory and use the `lce-interview`,
`lce-research` and `lce-create-post` skills. Approve posts yourself with
`lce approve <post_id> --hash <prefix>` in an interactive terminal.

## Credits

Some concepts were studied in the MIT-licensed
[linkedin-skills](https://github.com/sergebulaev/linkedin-skills) project. This
is an independent implementation; see [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md).

## License

MIT — see [LICENSE](LICENSE).
