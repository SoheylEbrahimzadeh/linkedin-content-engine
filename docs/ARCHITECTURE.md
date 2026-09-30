# Architecture

## Two repositories

| | Public engine (this repo) | Private data repo |
|---|---|---|
| Contains | code, schemas, rulesets, generic prompts, templates, fictional demo data, docs | settings, brand & voice profile, story bank, plans, research, posts, run logs |
| Automation | CI only (tests, lint, privacy + secret scans) | scheduled pipeline, approval PRs, publishing |
| Secrets | none | only what an enabled phase requires |

The pipeline runs **in the private repository**, which installs this engine at a
pinned commit. Workflow logs of public repositories are public, so nothing
content-related ever runs here.

## Pipeline

```
Research → Topic selection → Plan → Draft → Humanize → Audit → Verify
        → Human approval → Schedule/Publish → Reconcile
```

| Stage | Runs in | Needs an LLM |
|---|---|---|
| Research, topic selection, planning, drafting, humanizing, audit rubric, semantic verification | Claude Code (interactive locally, or a scheduled routine) | yes |
| Rule-based audit, privacy guard, duplicate detection, schema validation, state transitions | this engine, in CI of the private repo | no |
| Approval | pull request review + merge in the private repo | no |
| Publishing, reconciliation | this engine, in CI of the private repo | no |

## Post lifecycle

Defined in `src/lce/state.py`:

```
RESEARCHED → SELECTED → DRAFTED → HUMANIZED → QA_PASSED → DUPLICATE_CHECKED
          → AWAITING_APPROVAL → APPROVED → READY_TO_PUBLISH
side states: NEEDS_INPUT, NEEDS_REVISION, REJECTED
```

Publishing (Phase 3): `READY_TO_PUBLISH → PUBLISHING → PUBLISHED |
PUBLISH_FAILED | NEEDS_RECONCILE`, started only by `lce publish` (interactive,
human). See [PUBLISHING.md](PUBLISHING.md) and [PIPELINE.md](PIPELINE.md).

## Scheduling (Phase 2)

One job per posting slot, created and advanced by `lce automation run-once`.
Jobs have their own lifecycle (SCHEDULED, READY, RUNNING, BLOCKED, SUCCEEDED,
FAILED, SKIPPED, NEEDS_RECONCILE) and drive a post up to AWAITING_APPROVAL.
Deterministic steps run in the engine; LLM steps are handed to Claude Code.
See [AUTOMATION.md](AUTOMATION.md).

## Approval

Phase 1: local approval in an interactive terminal, bound to the SHA-256 of
the exact text (`approved_hash`). Phase 2 adds pull-request approval in the
private repo with the same hash binding. Any publisher must refuse text whose
hash differs from the approved hash.

## Duplicate prevention and idempotency

- Content: normalized-text hash, n-gram similarity against published posts and
  open drafts, and rotation rules (pillar share, hook formula, fact reuse).
- Publishing: deterministic `idempotency_key` per post; the `PUBLISHING` intent
  is committed before the provider call; one publisher at a time (CI
  concurrency group).

## Failure handling

- Errors that definitely happened before creation (connection refused, rate
  limit, validation) are retried with exponential backoff and jitter.
- Ambiguous errors (timeout or server error after sending) are **never retried
  blindly**: the post moves to `NEEDS_RECONCILE` and the adapter's
  `find_existing` decides.
- A slot that cannot be met within `max_lateness_minutes` becomes `MISSED`.

## Language awareness

The humanizer/audit engine is generic; each language supplies
`rules/<lang>/ruleset.yaml`. Only languages with `status: ready` are
publishable. English is first.

## Scheduling

Timezone and slots come from private settings (IANA timezone, e.g. with DST
handled by `zoneinfo`). CI schedulers run in UTC; the engine decides locally
whether a slot is due.
