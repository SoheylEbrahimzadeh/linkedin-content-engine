---
name: lce-create-post
description: Turn a research candidate into an approval-ready LinkedIn post in the owner's voice - select, draft, humanize, QA, duplicate check, approval artifact. Never approves or publishes.
---

# LCE create post

Phase 1 ends at the approval boundary. **You never run `lce approve`,
`lce ready`, or anything that publishes.** There is no publisher.

## Inputs to read first

- `lce status` (must say "ready for drafting"; otherwise run the interview)
- `profile/profile.yaml`, `profile/voice.yaml`
- `story_bank/stories/*.yaml` — only `PUBLIC` stories may be used, and only
  their `allowed_claims`
- the candidate: `research/candidates/<id>.yaml`

## Steps

1. **Select**: `lce select list`, pick a candidate that fits a pillar, then
   `lce select pick <candidate> --pillar <id> --angle "<angle>" --format text --date YYYY-MM-DD [--story <story_id>]`
2. **Draft**: write the post to a temp file, `lce draft save <post_id> --file <file>`.
3. **Humanize**: rewrite in the owner's voice (tone, formality, sentence
   length, avoid list, emoji/hashtag policy). Check with
   `lce humanize check <post_id> --file <file>` and iterate, then
   `lce humanize save <post_id> --file <file>`.
4. **QA**: `lce qa <post_id>`. On failure, fix the text, save again (step 3).
   At most two revision rounds; after that stop and tell the owner what input
   is missing.
5. **Duplicate check**: `lce dupcheck <post_id>`. On failure, change the angle
   or topic; do not paraphrase around it.
6. **Approval artifact**: `lce approval prepare <post_id>`, then show the owner
   `posts/<post_id>/APPROVAL.md` and the exact command they can run in their
   own terminal.

## Content rules

- No invented experiences, clients, numbers, results or quotes. A first-person
  achievement needs a PUBLIC story; every number needs a story claim or a
  recorded source claim — QA enforces this.
- No engagement bait, no hype without a concrete argument, no Markdown.
- Plain, specific, useful. One idea per post.
