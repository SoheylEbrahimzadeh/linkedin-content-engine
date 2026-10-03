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
   `lce select pick <candidate> --pillar <id> --theme <theme> [--chapter <id>] [--evidence personal|external] --objective <objective_id> --angle "<angle>" --format text --date YYYY-MM-DD [--story <story_id>]`.
   The objective is one id from `objectives` in `profile/voice.yaml` (never a
   new one); set it later with `lce post objective <post_id> <objective_id>`.
   Humanize save refuses a post without an objective when objectives exist.
   If the post lands in `NEEDS_INPUT` for personal evidence, do not write it
   from imagination: ask the owner for a story (story bank) or re-select with
   external evidence.
2. **Draft**: write the post to a temp file, `lce draft save <post_id> --file <file>`.
3. **Humanize** against the voice profile, not just "less AI-like". Read
   `profile/voice.yaml` completely and apply every section: tone, formality,
   point_of_view (first person only for observations and opinions; experience
   only from PUBLIC stories), individual_voice (one person, never a company
   page or "we at …"), technical_depth, sentence_length, structure, opinions
   vs facts, evidence, cta, hashtag and emoji policy, avoid_phrases,
   avoid_patterns; write for the audience and positioning in
   `profile/profile.yaml` and the post's objective. Fields listed under
   `review` are derived and await the owner's confirmation: follow them, and
   mention any conflict with the owner's instructions. Check with
   `lce humanize check <post_id> --file <file>` (it prints the voice
   checklist: every machine-checkable rule must pass; tone and positioning are
   "owner review") and iterate, then `lce humanize save <post_id> --file <file>`,
   which records the voice/profile/brand versions and the checklist on the
   post. The Control Center shows exactly that record.
4. **QA**: `lce qa <post_id>`. On failure, fix the text, save again (step 3).
   At most two revision rounds; after that stop and tell the owner what input
   is missing.
5. **Duplicate check**: `lce dupcheck <post_id>`. On failure, change the angle
   or topic; do not paraphrase around it.
6. **Media decision (explicit; text-only is never a silent default).** LCE-043:
   the default is a **real, relevant, legally reusable image**, never a generated
   diagram and never the post's sentences in boxes. Decide in this order:
   1. What is the post's central subject, and the key visual concept? What kind
      of real image would show it (a photo of the industry, place, artefact,
      event or organisation; a source chart whose licence allows reuse)?
      If no real image would add anything: text-only (below).
   2. **Real image from Wikimedia Commons** (the only external source with
      machine-readable licences): name candidate files and/or search queries,
      the subject terms the file's own metadata must contain, and why it fits.
      The engine accepts a file only if its licence is PD, CC0, CC BY or CC BY-SA
      (never NC/ND/fair use/unknown) AND its title, description or categories
      name at least one subject term; it records creator, licence, licence URL,
      source URL, attribution, the retrieved file and its hashes, and every
      candidate it refused with the reason. Licences that require credit put the
      credit line into the post. Commons is reachable from GitHub Actions, not
      from this container: for a refresh, use `media: {commons: …}` in the
      package (section A of `lce-refresh`); for a new post,
      `lce image commons <post_id> --title "File:…" …` where the network allows.
      Never Google Images, never a page whose rights are unclear, never invented.
   3. **The owner's own screenshot/photo**: `lce image decide … --origin
      owner_screenshot|owner_photo --concept … --visual-type screenshot|photo
      --relevance-reason …`.
   4. **Recorded figures the post rests on**: `lce image chart <post_id>` (a
      chart of sourced claims, source shown).
   5. **Otherwise text-only**, with the reason:
      `lce image decide <post_id> --kind none --text-only-reason no_suitable_licensed_image|text_carries_point|no_relevant_visual|no_rights_safe_source|personal_story_without_owner_photo|would_be_decorative --rationale "..."`.
   A drawn conceptual diagram (`lce image diagram --spec`) is used **only when the
   owner asked for one**; it must still pass the relevance check (short labels of
   your own, no text dump, every number a sourced claim). Never a random
   stock/AI image, generated people, logos or fake screenshots.
   `lce image check <post_id>` must pass; `lce image show <post_id>` prints the
   media record with its `media_relevance`.
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
