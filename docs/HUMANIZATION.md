# Humanization: audit and design (LCE-037)

## Audit (before LCE-037)

Traced through the code: research → selection → draft → humanize → QA →
duplicate check → approval.

| Question | Finding |
|---|---|
| Where is humanization implemented? | Not in the engine. A Claude Code session follows step 3 of the `lce-create-post` skill ("rewrite in the owner's voice") and saves the text with `lce humanize save`. There is no LLM API (free-first rule). |
| What does `HUMANIZED` mean? | Only that `posts.save_humanized` stored `post.md` and its hash. It recorded nothing about which profile, rules or objective the text was written against. |
| What inputs does the session get? | The skill tells it to read `profile/profile.yaml`, `voice.yaml`, `brand.yaml`, `lce brand next`, analytics and PUBLIC stories. Nothing verified that it did. |
| Which rules are enforced? | Only negative, machine-checkable ones, by QA (`lce.qa.run_checks`): avoided phrases and topics, hashtag/emoji limits, CTA switch, bullets, length, generic AI patterns from the language ruleset, unsourced numbers, first-person claims without a PUBLIC story, brand evidence. |
| Positive personalization (positioning, audience, objective, point of view, structure)? | Not encoded. The owner's private `voice.yaml` filled 5 of the schema's 14 fields (tone list, formality, emoji, hashtags, an empty avoid list); posts had no objective field. |
| Where do the rules live? | Voice/profile/brand in the private repo (`profile/*.yaml`); generic patterns in the engine (`src/lce/rules/en/ruleset.yaml`); the writing procedure in the skill. |
| Verdict | "Humanized" proved a saved text that passed generic QA. It did not prove the text was written for the owner's positioning, audience and goals. |

## Design (LCE-037)

- **Voice profile v2** (private `profile/voice.yaml`, schema
  `src/lce/schemas/voice.schema.json`): point of view and first-person rules,
  individual (not company-page) voice, technical depth, sentence length,
  structure and hook style, opinions vs facts, evidence, CTA style, hashtag
  and emoji policy, avoided phrases and patterns, content objectives. Every
  field carries its source (`sources`: owner answer, or `derived: …` from
  profile/brand/ruleset); derived fields are listed under `review` until the
  owner confirms them.
- **Objective**: each post records one objective id from `voice.yaml`
  (`lce select pick --objective`, `lce post objective`). When objectives are
  defined, `lce humanize save` refuses a post without one.
- **Humanization record**: `lce humanize save` stores on the post the SHA-256
  of `voice.yaml`, `profile.yaml` and `brand.yaml`, the voice version, the
  objective, who saved it (session or owner edit) and the voice checklist
  result.
- **Voice checklist** (`lce.voice.checklist`, printed by `lce humanize
  check`): objective, pillar, hashtags, emoji, avoided phrases, generic AI
  patterns, evidence, individual voice (new QA check `voice.corporate_voice`),
  CTA, format — each passed/failed with the QA codes behind it. Tone and
  positioning fit are listed as "owner review": no program can verify them.
- **Dashboard**: per post, "Voice profile: applied (vN)" only when the record
  exists; a warning when the profile files changed since; "not recorded" for
  older posts; machine-checked rules with their count; objective; tone and
  positioning as "your review"; the voice profile used, with its review state.

What this proves: the exact profile versions the text was saved against and
that every machine-checkable voice rule passed. What it cannot prove: that the
tone and argument fit the owner — that stays the owner's judgement at approval.
