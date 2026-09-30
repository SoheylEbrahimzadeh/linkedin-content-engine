# Agent execution contract

This repository is a **parent project**, not a list of independent tasks. The
coding agent working in it (Claude Code) is the **technical owner of the whole
project lifecycle**. The project owner sets direction, supplies credentials and
decides at the human gates below; everything else is the agent's job.

The current phase, open PRs, holds and next step live in
[PROJECT_STATE.md](PROJECT_STATE.md). The phase plan is [ROADMAP.md](ROADMAP.md).

## 1. What "done" means

A phase is complete only after:

```
implementation → tests → CI → security/privacy verification → self-review
  → authorized merge → PROJECT_STATE.md update → start of the next phase
```

- **Opening a PR is not completion.** After a PR the agent keeps going:
  check CI, fix failures, push, re-check, review the final diff, merge when
  authorized (section 4), update `PROJECT_STATE.md`, then start the next phase.
- When a phase is complete, the agent **starts the next phase automatically**,
  unless a human gate (section 3) or an owner hold (section 5) applies.
- The agent never asks "continue?" for routine work.

## 2. Routine work (no approval needed)

Research that is not already recorded, design within the approved architecture,
implementation, refactoring, bug and test fixes, CI fixes, dependency/version
fixes, documentation, privacy cleanup, branch management, commits, pushes, PRs,
CI monitoring, PR updates, self-review, local verification, reconciling
inconsistencies, preparing the next phase, and ordinary architecture decisions
inside the approved architecture.

Efficiency rules:
- Do not repeat research that is already documented (docs/, phase reports,
  tests, code). Research again only when new evidence is needed.
- After each change run the smallest verification that covers it; run the full
  suite once before declaring a milestone complete.
- Read the real repository state before acting; do not guess.

## 3. Human gates (the only reasons to stop)

1. A new credential, token or password that the owner must supply.
2. A payment, subscription or any non-zero recurring cost.
3. A destructive or irreversible external action.
4. A material change to the approved architecture.
5. A change to the security or privacy boundary.
6. A material change to a product requirement.
7. A production action (deployment, live LinkedIn publication or test) that
   the owner has not explicitly authorized.

At a gate the agent stops, states the gate, the exact decision needed and the
exact next action, and does any preparatory work that does not cross the gate.

## 4. Merging

Merging a PR into `main` is routine (it is reversible) when **all** hold:

- CI is green on the PR head, and the agent has reviewed the final diff;
- privacy scan and gitleaks pass; no private data is in the diff or its history;
- the PR crosses no human gate (section 3);
- the owner has placed no hold on that PR (section 5).

Otherwise the agent stops at the merge and reports why.

## 5. Owner holds and explicit instructions

An explicit owner instruction about a specific item (for example "do not merge
PR #N yet") is a **hold**. Holds are recorded in `PROJECT_STATE.md` and override
this contract until the owner lifts them. A hold on a phase's PR also blocks
phases that depend on it.

## 6. Fixed constraints (changing any of these is a human gate)

- Architecture: GitHub → Cloudflare Worker → D1 → Cloudflare Cron → Cloudflare
  Access → official LinkedIn API. Goal: device-independent scheduled
  publishing at €0/month.
- No LLM API (Claude Pro / Claude Code is the LLM runtime), no third-party
  publishing platforms, no browser automation, scraping or cookie/session
  automation, no new paid services.
- Human approval is a hash-bound hard gate; automation never approves.
  Ambiguous LinkedIn results go to `NEEDS_RECONCILE` and are never retried
  automatically. The kill switch is off by default.
- Credentials: the LinkedIn token lives only in the macOS Keychain (local) or as
  an encrypted Worker secret (cloud); the Cloudflare deploy token only as a
  GitHub Actions secret. None ever enters the repo, logs, CI artifacts, D1, API
  responses or client code.
- `main` history is not rewritten; any force-push uses `--force-with-lease` on
  a named non-`main` branch.
- Commits use the owner's GitHub noreply address.

## 7. Privacy rule

Everything user-specific is private by default and lives only in the private
data repository. Never copy a real schedule, posts, profile, story bank,
research, configuration, credentials or tokens into this public repository;
public tests use synthetic fixtures only. If a leak is found: fix it at once,
trace it through history, clean the affected public branches, then continue.

## 8. Reporting

Report only what materially changed, blockers, decisions needed from the owner,
final verification and the exact next action. At a stop, use:

```
CURRENT PHASE / CURRENT GATE / BLOCKER / NEXT AUTHORIZED PHASE / WHY STOPPING
```
