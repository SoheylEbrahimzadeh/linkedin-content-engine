# Pipeline (Phase 1)

```
Research → Planning → Generation → Humanization → QA → Duplicate check → Approval
        → [Publisher interface → Verification → Reconciliation]   (later phases)
```

| Stage | Command(s) | Who | State after |
|---|---|---|---|
| Research | `lce research fetch`, `lce research add`, `lce research claim` | Claude Code + free feeds | candidate `new` |
| Selection | `lce select list`, `lce select pick` | Claude Code proposes, rules check | `SELECTED` (or `NEEDS_INPUT`) |
| Draft | `lce draft save` | Claude Code | `DRAFTED` |
| Humanize | `lce humanize check`, `lce humanize save` | Claude Code | `HUMANIZED` |
| QA | `lce qa` | deterministic | `QA_PASSED` / `NEEDS_REVISION` |
| Duplicate check | `lce dupcheck` | deterministic | `DUPLICATE_CHECKED` / `NEEDS_REVISION` |
| Approval artifact | `lce approval prepare` | deterministic | `AWAITING_APPROVAL` |
| Approval | `lce approve --hash` (interactive terminal only) | **the owner** | `APPROVED` |
| Ready | `lce ready` | owner | `READY_TO_PUBLISH` (end of Phase 1) |

Every text change clears QA, duplicate and approval results. Approved or
ready posts cannot be edited without `lce post reopen`, which discards the
approval. `lce ready` re-verifies the approved hash.

## Automation

`lce automation run-once` runs QA, the duplicate check and the approval
artifact automatically for posts linked to scheduled jobs, and hands research,
selection, drafting and humanizing to Claude Code. It never approves or
publishes. See [AUTOMATION.md](AUTOMATION.md).

## QA checks (deterministic)

Errors block: missing text, over 3000 characters, wall of text, phrases from
your avoid list, avoided topics/claims, engagement bait, placeholders, too many
hashtags or emojis, repeated sentences, research wording without a source,
numbers not backed by a PUBLIC story claim or a recorded source claim,
first-person achievements without a PUBLIC story, denylist terms, contact
details, non-public stories, story sensitive terms, a language whose ruleset
is not ready.

Warnings inform: length vs. your preference, long hook or paragraphs,
Markdown, AI-typical wording and structures, hype, hashtag placement,
repeated phrases/openers/words, em dashes, three-part lists, exclamation
marks, URLs that are not recorded sources.

## Duplicate check

Compared against all other non-rejected posts and imported past posts
(`lce history import`): exact (canonical text), near (3-word shingles,
Jaccard ≥ 0.5 or containment ≥ 0.7), similar (warning, Jaccard ≥ 0.3), story
reuse (non-reusable story, within 30 days, or more than 3 uses), same pillar +
angle within 21 days, similar topic within 14 days. All thresholds are
configurable in `settings.yaml` under `duplicates`. Results go to
`posts/<id>/duplicate.json` and `runs/*.jsonl`.

## Approval

`APPROVAL.md` shows the post, topic, pillar, sources, stories, QA and
duplicate results, generation time, content hash and approval state.
`lce approve` requires an interactive terminal, the hash prefix (12+
characters) and the typed phrase `APPROVE <post_id>`; it refuses if the text
or the artifact changed. Automated agents therefore cannot approve.
