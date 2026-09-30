# Automation & scheduling (Phase 2)

Phase 2 turns the posting cadence into scheduled, persisted, inspectable jobs.
It prepares posts **up to human approval** and stops there. It never approves,
never marks a post ready, never publishes. Publishing is Phase 3.

## Concepts

```
config (private: timezone + cadence)
   ↓
Schedule → Slot (one per posting time)        lce/schedule.py
   ↓
Job (one per slot)                              lce/jobs.py
   ↓
Pipeline steps                                  lce/scheduler.py
   ├─ LLM steps: research, select, draft, humanize → Claude Code (skill lce-run-jobs)
   └─ deterministic: QA → duplicate check → approval artifact
   ↓
Post state: AWAITING_APPROVAL
   ↓
Human approval (lce approve, interactive terminal only)
   ↓
APPROVED → READY_TO_PUBLISH (lce ready, human)
   ↓
STOP — publishing is only `lce publish`, run by a human (Phase 3)
```

The job lifecycle and the post lifecycle are separate. A job only records the
`post_id` it drives; the post keeps its own state and history. A job has done
its part when its post reaches `AWAITING_APPROVAL` (or later).

## Time and timezone

- One clock (`lce/clock.py`). Nothing else reads the system time; tests and
  dry runs inject a fixed clock.
- Every persisted timestamp is UTC ISO 8601 with `+00:00`.
- Slots store `local` (wall time with offset), `timezone` (IANA) and `utc`.
- Naive timestamps are rejected everywhere.
- DST: a slot in the spring-forward gap moves forward by the gap
  (02:30 → 03:30) and is marked `dst_adjusted`; a slot in the fall-back overlap
  uses the first occurrence.
- Times in `cadence.slots` must be quoted strings (`"10:00"`); unquoted YAML
  times are read as numbers and are rejected with an explicit error.

## Identity and idempotency

- `slot_id = <local date>-<day>-<HHMM>` in the configured timezone, e.g.
  `2026-10-01-thu-0830`; `job_id = job-<slot_id>`. Stable across machines and DST.
- Jobs are created with exclusive file creation: a second pass creates nothing.
- A scheduler lock (`automation/scheduler.lock`, exclusive create, expiry)
  rejects concurrent passes; an expired lock is taken over atomically and logged.
- A running job holds a lease; the job file is checkpointed after every step.
  Nothing lives only in memory.

## Job states

| State | Meaning |
|---|---|
| `SCHEDULED` | slot in the horizon; prepare window (`slot − lead_hours`) not open yet |
| `READY` | eligible; the next pass runs it |
| `RUNNING` | leased by a scheduler pass or claimed by an agent |
| `BLOCKED` | waiting: `awaiting_agent` (LLM work), `awaiting_revision` (QA/duplicate failed), `needs_input` (owner) |
| `SUCCEEDED` | post reached `AWAITING_APPROVAL` or later |
| `FAILED` | error recorded; `retryable` says whether an automatic retry is allowed |
| `SKIPPED` | `missed_slot`, `rejected_by_human`, or manual |
| `NEEDS_RECONCILE` | recorded state and files disagree; needs a decision |

A slot that passes before its post reached approval is `SKIPPED (missed_slot)`.
No content is generated late for a past slot. A succeeded job re-opens only if
its post is sent back for editing before the slot.

## Retries

- Automatic: only retryable failures (filesystem errors, unexpected internal
  errors in deterministic steps), at most `max_attempts` (default 3), after a
  fixed `retry_delay_minutes` (default 15). No exponential backoff.
- Never automatic: invalid configuration, validation errors, QA/duplicate
  failures beyond `max_revisions`, human rejection, ambiguous state.
- `lce jobs retry <job>` is the owner's explicit decision for any FAILED job.

## Reconciliation

- A `RUNNING` job whose lease expired is reconciled on the next pass. If the
  post's files and recorded state agree, the job returns to `READY` and the
  interrupted step re-runs (all steps are local and idempotent). Otherwise it
  becomes `NEEDS_RECONCILE`.
- `lce jobs reconcile <job>` re-checks; if still ambiguous it requires
  `--decision retry|skip|fail`. Retry is refused while the data is inconsistent.

## Commands

```bash
lce cadence show [--days 14]                    # slots (local + UTC) and job states
lce automation run-once --dry-run [--now ISO]   # what would happen; writes nothing
lce automation run-once                         # one real pass
lce jobs list [--state BLOCKED]
lce jobs show <job>
lce jobs agent-tasks                            # handed to Claude Code
lce jobs claim <job> [--as NAME] / lce jobs release <job> --note "..."
lce select pick <candidate> --pillar P --angle A --job <job>
lce jobs retry <job> | skip <job> --reason R | reconcile <job> [--decision D]
```

Command groups are named `cadence` and `automation` (not "schedule") because the
Phase 1 boundary test forbids any command name suggesting publication
scheduling.

`--now` is accepted only together with `--dry-run`, so simulated time can never
be written into your data.

## Triggering

There is no daemon and the engine configures no trigger. Automation runs when
something calls `lce automation run-once` (you, a local scheduler you set up, or
a Claude Code routine using the `lce-run-jobs` skill). Setting up any trigger is
a separate, explicit decision. The dashboard shows the last pass, never
"automation active".

## Files (private data directory)

| Path | Content |
|---|---|
| `config/automation.yaml` | optional tuning (see `templates/private-data/config/automation.example.yaml`) |
| `automation/jobs/<job_id>.yaml` | one job: slot, state, lease, attempts, steps, history |
| `automation/scheduler.lock` | only while a pass runs (gitignored) |
| `runs/*.jsonl` | `scheduler.*` and `job.*` events with invocation ids |

Existing schemas (settings, post, plan) are unchanged, so a data repository
validated by an older engine (`ENGINE_REF`) stays valid; the new files are only
validated by an engine that knows them.

## Events

Each pass has an `invocation_id`. Events: `scheduler.start`, `scheduler.finish`,
`scheduler.locked`, `scheduler.lock_takeover`, `scheduler.config_invalid`,
`job.created` (slot, prepare window, invocation), `job.transition`
(from, to, reason, invocation, post), `job.step` (qa, duplicate_check,
approval_artifact, agent_work), `job.error` (kind, retryable), `job.linked`.
They answer: why and when a job was created, by which pass, which post it
drives, which step ran, what happened and why it stopped. No post text and no
secrets are logged.
