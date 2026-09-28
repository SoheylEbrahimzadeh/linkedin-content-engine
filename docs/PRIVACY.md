# Privacy and security model

## What is public (this repository)

Reusable code, JSON schemas, language rulesets, generic prompts, the private
repo template, a **fictional** demo persona, and documentation.

## What is private (never in this repository)

Personal brand profile, voice profile, story bank, content calendar, research,
drafts, published-post history, analytics, personal settings, any credentials,
OAuth tokens and API keys. When in doubt, data is treated as private.

## Where private data lives

- A private data directory (`$LCE_DATA_DIR`), normally a clone of a **private**
  repository created from `templates/private-data/`.
- A local-only folder `~/.lce-private/` for material that should not even be in
  the private repository (interview notes, the denylist).

## Layers of protection

1. **`.gitignore`** blocks `.env*` and every private-data directory name.
2. **Runtime guard** (`lce.config.paths`): the engine refuses a data directory
   located inside this repository (detected via `.lce-engine-root`).
3. **Pre-commit hooks**: gitleaks on staged changes, the project privacy
   scanner (`lce privacy-scan`), demo-persona validation.
4. **Pre-push hooks**: gitleaks over the full history plus the privacy scan.
5. **CI**: tests, privacy scan and gitleaks over the full history on every push
   and pull request.
6. **GitHub**: secret scanning and push protection enabled on the repository.
7. **Commit identity**: commits use the GitHub `noreply` address.

## Runtime privacy checks on posts

QA blocks any post containing denylist terms, contact details, sensitive
terms from any story, or content from stories that are not `PUBLIC`.

## The privacy scanner

`src/lce/privacy/scan.py` flags private paths, `.env` files, story-bank data
outside `examples/`/`templates/`/`tests/`, example data without `demo: true`,
e-mail addresses outside documentation/noreply domains, phone numbers, common
credential shapes, any use of the Anthropic API, and terms from a local
denylist (`~/.lce-private/denylist.txt`, one term per line, never committed).
It reports file, line and rule only, never the matched value.

## Secrets

No secrets exist in Phase 0. Later phases add exactly the credential they need,
only after explicit owner approval, stored in the private repository's secret
store and never in files.
