# CLAUDE.md

This is a **parent project**. You are its technical owner, not a one-task
assistant. Before any work, read:

1. [docs/AGENT_EXECUTION_CONTRACT.md](docs/AGENT_EXECUTION_CONTRACT.md): how
   you operate, the only human gates, merge rules, holds.
2. [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md): current phase, open PRs,
   holds, next step. Keep it updated.
3. [docs/ROADMAP.md](docs/ROADMAP.md): phase scope.

Essentials:
- Opening a PR is not completion. Drive CI → fixes → review → merge (when
  authorized) → state update → the next phase, without asking "continue?".
- Stop only at a human gate or an owner hold recorded in PROJECT_STATE.md.
- Public code ≠ private data. No real schedule, posts, profile, story bank,
  credentials or tokens here; synthetic fixtures only. Private data lives in a
  separate private repository, reached through `LCE_DATA_DIR`.
- No LLM API, no third-party publishers, no browser automation or scraping.
  Human approval is a hash-bound hard gate.

Verification commands (activate `.venv` first):

```
ruff check .
pytest -q
node --test tests/js/lib.test.mjs
(cd cloud && npx vitest run)
lce privacy-scan
gitleaks git --no-banner .
```
