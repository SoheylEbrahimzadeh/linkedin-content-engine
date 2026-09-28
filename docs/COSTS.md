# Cost and credential model

## Decision

The engine **does not use the Anthropic API**. There is no `anthropic`
dependency and no API-key configuration; CI fails if either is introduced.

LLM work runs inside **Claude Code** on the owner's Claude subscription:
interactively on a workstation, or unattended as a Claude Code *routine*
(scheduled cloud run). Routines draw on subscription usage and have a daily run
cap; they are in research preview and may change.

## Guards against hidden usage-based charges

- The engine never calls an LLM API.
- An `ANTHROPIC_API_KEY` in the environment takes precedence over a
  subscription login in Claude Code, so it must not be set in any environment
  that runs this pipeline.
- Keep metered "usage credits" disabled in the subscription settings if runs
  should stop, rather than bill, when limits are reached.

## Component matrix

| Component | LLM | Runtime | Credential |
|---|---|---|---|
| Interview, voice profile | yes | Claude Code (interactive) | subscription login |
| Research, topics, planning, writing, humanizing, audit rubric | yes | Claude Code routine | subscription login |
| Rule-based audit, privacy guard, duplicates, validation | no | CI | none |
| Approval | no | pull request | GitHub account |
| Publishing, reconciliation | no | CI | provider credential (later phase) |
