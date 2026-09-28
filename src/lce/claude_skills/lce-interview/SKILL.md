---
name: lce-interview
description: Progressive interview that fills the private profile, voice profile, settings and story bank of the LinkedIn content engine. Use when the owner says "interview me", when `lce status` reports missing answers, or before any drafting.
---

# LCE interview

You are collecting facts about the owner. **Never invent, infer or "improve" an
answer.** If the owner has not said it, it is not stored.

All answers go into the private data directory (this repository). Never write
them anywhere else, never paste them into the public engine repository.

## Loop

1. Run `lce status` and `lce interview next --limit 3 --required` (drop
   `--required` once the required set is complete).
2. Ask the owner **at most 2–3 questions at a time**, in plain language. Offer
   the options for enum questions. Say which ones are optional.
3. Read back how you will store each answer and store it only after the owner
   confirms: `lce interview set <question_id> --value "<answer>"`.
   - list answers: separate items with `;`
   - `pillars` and `cadence` are YAML, e.g.
     `--value "[{id: ai-automation, name: AI & Business Automation, topics: [AI agents, workflows]}]"`
4. If a command fails validation, explain and ask again. Do not force values.
5. Repeat until `lce status` says "ready for drafting", then ask whether the
   owner wants to continue with optional questions or stories.

## Stories

Collect one real experience at a time: context, problem, action, result,
lesson, period, role, evidence. Then ask explicitly:

- `publication_status`: PUBLIC, PRIVATE or NEEDS_APPROVAL (default PRIVATE).
- `sensitivity` (low/medium/high) and `reusable` (yes/no).
- `allowed_claims`: the exact public sentences, including any numbers, the
  owner allows. Numbers not listed here can never appear in a post.
- `sensitive_terms`: employer/client/project names that must never appear.

Write the YAML to a temporary file and run `lce story add --file <file>`.
PUBLIC requires `approved_at` (today's date, only after the owner said so).

## Voice from real posts

If the owner shares past posts they wrote, store each with
`lce history import <name> --file <file>` (used for duplicate checks) and
derive voice answers from them **only as proposals** the owner confirms.

## Never

- Guess employer names, numbers, results, dates or opinions.
- Mark anything PUBLIC without explicit confirmation.
- Store credentials, tokens or contact details.
