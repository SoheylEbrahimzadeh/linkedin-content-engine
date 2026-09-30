---
name: lce-create-post
description: Turn a research candidate into an approval-ready LinkedIn post in the owner's voice - select, draft, humanize, QA, duplicate check, approval artifact. Never approves or publishes.
---

# LCE create post

Phase 1 ends at the approval boundary. **You never run `lce approve`,
`lce ready`, or anything that publishes.** There is no publisher.

## Inputs to read first

- `lce status` (must say "ready for drafting"; otherwise run the interview)
- `profile/profile.yaml`, `profile/voice.yaml`, `profile/brand.yaml`
  (objective, throughline, narrative chapters, themes, content mix,
  credibility rules; target markets are direction, never the post subject)
- `lce brand next`: the recommended pillar, theme and evidence mode
- `lce analytics insights`: what has worked, and saturated topics to avoid
- `story_bank/stories/*.yaml` — only `PUBLIC` stories may be used, and only
  their `allowed_claims`
- the candidate: `research/candidates/<id>.yaml`

## Steps

1. **Select**: start from `lce brand next`; `lce select list` ranks candidates
   (brand-target pillars rank higher, avoid-list topics are penalized). Then
   `lce select pick <candidate> --pillar <id> --theme <theme> [--chapter <id>] [--evidence personal|external] --angle "<angle>" --format text --date YYYY-MM-DD [--story <story_id>]`.
   If the post lands in `NEEDS_INPUT` for personal evidence, do not write it
   from imagination: ask the owner for a story (story bank) or re-select with
   external evidence.
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
6. **Image**: decide what, if anything, the post needs, then record it:
   `lce image decide <post_id> --kind none --rationale "..."`, or with a file:
   `lce image decide <post_id> --kind diagram|architecture|chart|screenshot|source_image|generated_concept --file <png/jpg/gif> --rationale "..." --relation "<what it shows and how it supports the point>" --alt "<alt text>" --origin <origin> --usage <usage> [--source-url --license --credit] [--method "<tool/code>" --prompt "..."]`.
   Prefer a diagram or chart built from the post's own recorded facts; a
   third-party image only with its licence; a screenshot only of the owner's
   own work or a cited source; a generated concept image only when it adds
   meaning. Never a random stock/AI image to fill space: choose `none`. Never
   generated people, logos or fake screenshots. `lce image check <post_id>`
   must pass; usage `needs_review` waits for the owner.
7. **Approval artifact**: `lce approval prepare <post_id>`, then show the owner
   `posts/<post_id>/APPROVAL.md` and the exact command they can run in their
   own terminal.

## Content rules

- No invented experiences, clients, numbers, results or quotes. A first-person
  achievement needs a PUBLIC story; every number needs a story claim or a
  recorded source claim — QA enforces this.
- No engagement bait, no hype without a concrete argument, no Markdown.
- Plain, specific, useful. One idea per post.
- Every post must be consistent with the brand throughline and serve the
  objective; follow the credibility rules in `brand.yaml`. Write for an
  international professional audience (no local jargon without explanation).
