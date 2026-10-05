# Voice system (LCE-051)

> **Humanity score is a mechanical floor, not proof of owner voice.**
> **No stronger position than confirmed owner material.**
> Source-heavy posts stay source-heavy; any text change invalidates the approval.
> The final architecture is summarised in "Voice architecture (final, 5 Oct)" at the end.

The engine keeps three kinds of content apart and never lets one stand in for another:

| Content type | What it is | What it needs (never invented) |
|---|---|---|
| `external_insight` | A current development and the owner's reading of it | recorded sources and claims; a stance in the text |
| `personal_pov` | What the owner believes | an owner-confirmed opinion or disagreement (`opinions_used`) |
| `personal_lesson` | What happened and what the owner took from it | a PUBLIC story (`stories_used`) |
| `how_to` | How the owner approaches a kind of problem | an owner-confirmed approach, a PUBLIC story, or sources |
| `observation` | A pattern the owner has seen at work | an owner-confirmed observation or a PUBLIC story |

## Private material (data repository)

- `profile/voice.yaml`: tone, rhythm, vocabulary, first-person style, opinion and disagreement
  style, humour, openings, transitions, endings, CTA and networking behaviour, technical/business
  balance, how problems are explained, how uncertainty is expressed, how assumptions are challenged,
  signature patterns, anti-generic rules. A trait the owner has not described is
  `{owner_input_required: true, question: "…"}`, never a guess. `lce voice status` lists the gaps.
- `profile/golden/{samples,opinions,disagreements,approaches,observations}.yaml` (Golden Voice Set):
  the owner's own words. An item counts only with `status: owner_confirmed` and
  `source: owner-YYYY-MM-DD`. `lce golden init` creates the empty templates.

## Opinion engine

`lce select pick … --content-type personal_pov --opinion <id>`: without an owner-confirmed opinion the
post goes to `NEEDS_INPUT` ("point of view required"). QA backs it up:
`pov.no_owner_opinion`, `golden.unconfirmed_ref` (errors), `pov.no_stance` (error),
`pov.opinion_not_expressed` (warning). `lce opinion for <post>` answers "what does the owner
actually believe about this?" from the recorded material only.

## Realism and networking checks (QA, English ruleset)

Errors: `claim.experience_unsupported` (in my experience / I've seen / a client … without a PUBLIC
story or confirmed observation), `insight.summary_only` (external summary with no consequence, reasoning or question),
`lesson.no_story`, `observation.no_evidence`, `network.transactional` (DM me, let's connect,
I help companies, looking for projects, book a call).
Warnings: `voice.consultant_language`, `pattern.generic-opening`, `pattern.empty-leadership`,
`style.jargon`, `network.fake_authority`, `repetition.structure`, `content_type.missing`.

## Humanity test (owner standard, 2026-10-05)

`lce humanity score <post>` scores the owner's ten criteria deterministically. Optimized for
sounding real, not impressive:

| id | criterion | fails on |
|---|---|---|
| real_person | Sounds like a real person | AI tells, generic/LinkedIn hooks, filler, hype, `style.impress`, claimed authority, transactional networking, bait |
| owner_voice | Sounds like the owner | longer sentences than the owner's samples, more first person than they use, more formal (uncontracted) than they write, `avoided_vocabulary`, near-copies of `counter_examples`, consultant/editorial codes; `unknown` with fewer than 3 samples |
| spoken | Could be spoken naturally | average sentence > 16 words, a sentence > 28, `style.uncontracted`, `style.stiff_phrase`, editorial phrases/labels |
| variation | Natural sentence variation | no short (≤8) or no medium (≥12) sentence, spread < 3 words, every paragraph the same size |
| no_stiffness | No corporate stiffness | consultant language, jargon, `style.stiff_phrase` (in order to, utilize, furthermore …), `style.polished_transition` |
| no_symmetry | No AI-style symmetry | more than one three-part list, parallel sentence openers, `style.contrast_frame` ("It's not X. It's Y."), staccato stacks |
| no_manufactured_opinion | No manufactured opinion | `pov.unbacked_belief` (incl. "I'd look at / I'd ask" without a confirmed approach), `pov.unbacked_emotion`, invented experience, missing owner material |
| substance | Real reasoning or a concrete observation | no basis, `insight.summary_only`, unsupported numbers, no reasoning/concrete sentence/question |
| no_over_explaining | Doesn't over-explain | `style.over_explaining` (in other words, put simply, sentences repeating each other), `style.over_long` (> 230 words) |
| genuine_ending | A genuine thought, not a takeaway | `ending.takeaway` (the lesson, at the end of the day, decides whether …), generic closes, CTAs, bait, repeated closings |

Verdict: PASS only at 10/10. FAIL with any QA error, when "real person" or "no manufactured
opinion" fails, or below 7. Otherwise PARTIAL. An unknown criterion can never reach PASS. The new
style codes are QA warnings (they do not block the pipeline); the writer must clear them.
Credit lines (Source:, Image:) and hashtags are not scored as prose.

`lce humanity eval` runs the bundled evaluation set (fictional persona): every good text must
reach 10/10 PASS and every bad one (generic, stiff, symmetric, invented, editorial,
over-explained, forced takeaway, manufactured emotion, unbacked "I'd ask") must FAIL or miss its
named criteria. The dashboard shows the score and verdict on each post.

## Content mix

`brand.yaml` `mix.content_types` (default: insight 30%, POV 20%, how-to 20%, observation 15%,
lesson 15%). `lce plan roll` gives each new open slot the producible type furthest below its
share; `lce brand mix` shows target, actual and what blocks each type.

## Whose view is it?

Every scored post carries `stance_origin`: `owner` when the text expresses an owner-confirmed item
(`opinions_used`), `proposed` when the writer proposed the stance (typical for an external insight,
where QA requires a reading of the source), `none` without a stance. The post page says so at
approval time: a proposed view is the owner's only once the owner approves it as theirs.

## Owner punctuation rules

`voice.yaml` `formatting.em_dash_allowed: false` and `formatting.guillemets_allowed: false` make an em dash
(`style.em_dash_forbidden`) or guillemets (`style.guillemets_forbidden`) a QA error. `registers` records which
registers posts use and in what balance.

## Editorial voice (polished, natural, not the owner)

The failure mode: a text that is factually careful and grammatically natural but reads like an
analyst summarizing a report ("My reading: …", "In my view, …", "This suggests that …", a tidy
three-part reading). The ruleset carries `editorial_phrases`, `editorial_labels` and
`belief_markers`; "my reading" and "in my view" are no longer stance markers.

- `voice.editorial_phrase`, `pattern.editorial-label`: warnings, errors when `voice.yaml`
  `formatting.editorial_phrases_allowed: false`.
- `style.symmetry`: error when `formatting.symmetry_allowed: false` and the text has two or more
  three-part lists or consecutive sentences with the same opening (one list is allowed).
- `pov.unbacked_belief` (error): a first-person belief (I think, in my view, my reading is) without
  an owner-confirmed opinion, disagreement or approach in `opinions_used`. The source fact stays the
  source's; an angle the writer proposes is written as a consequence or a question, not as the
  owner's belief (`angle_origin: proposed` on the post); only a Golden Set item becomes "I think".

Any of these codes fails the humanity criteria point of view, voice and said aloud. Point of view
now passes only for an owner opinion with a stance, or (outside `personal_pov`) a reasoned
consequence that borrows no belief. The evaluation set has a fictional `itsm-bad-editorial`
scenario for this regression, and `voice.yaml` `counter_examples` holds owner-flagged sentences.

## Voice validation fixes (5 Oct, controlled rewrite of two approved posts)

- Credit lines (`Image:`, `Source:`) are no longer read as the closing: `repetition.closing_recent`,
  `structure.generic_close` and `cta.not_allowed` look at the last prose sentence. Earlier
  "ends like a recent post" results on posts ending with the same credit line were false positives.
- Contractions count as a stance ("I'd keep", "I'm wary").
- A source's framework voiced as the owner's requirement is a borrowed belief (`pov.unbacked_belief`):
  "I want three answers", "Before I would support …", "my first question would be …",
  "the one I would put in front of …", "I'd keep/insist/recommend" without an owner-confirmed item.
- `pattern.written-inversion` ("Age, he says, …") and `pattern.setup-line` ("He asks a simple
  question.") fail "could be spoken"; "puts it clearly", "worth a read" are editorial phrases.

## Owner Voice Gate (permanent rule, 5 Oct)

**A post can pass the mechanical humanity checks and still fail the owner's Voice Gate.** A 10/10
humanity score is never treated as proof that a post sounds like the owner. The gate
(`lce.voice_gate`, shown under `lce humanity score` and on the post page) judges three things apart:

1. **Natural English**: mechanical failures (real person, spoken, variation, stiffness, symmetry,
   over-explaining) fail it; with none, the result is `review` (read it aloud), never an automatic pass.
2. **Owner-grounded opinion**: every first-person sentence must come from referenced owner material;
   personal language outside it ("For me", "my approach", "I would", "I want") fails it. With no owner
   opinion at all the result is `insufficient`, not a failure.
3. **Owner-recognizable phrasing / reasoning**: the owner's sentences are shown with the word pairs
   they share with the owner's samples and confirmed items; only a person decides (`review`).

Each prose sentence is classed `source`, `owner`, `writer` or `unbacked_personal`. Under 25% owner
sentences, the post is classified **Source-heavy — insufficient owner voice**
(`source_heavy_insufficient_owner_voice`, verdict `SOURCE_HEAVY`): an honest label to show the
owner, never a reason to invent first-person language. Verdicts: `FAIL`, `SOURCE_HEAVY`,
`REVIEW_NEEDED`, `PASS` (only with a manual review saying pass on all three).

A manual review (`voice_gate_review` on the post, or in a refresh package) is bound to the text's
content hash: a new text drops it, a stale one is ignored, and a review never overrides a
mechanical failure. Approval stays the owner's and is hash-bound as before.

## No stronger position than the owner's material (permanent rule, 5 Oct)

When owner material supports a general principle but does not contain the specific statement, the
writer must not paraphrase it into a stronger or more specific first-person position. Confirmed
"AI needs human oversight" allows "AI doesn't always get it right, and a person needs to stay in
control"; it does not allow "I wouldn't cut that checking", "I would always keep a human in the
loop" or "I'd never automate this without …". QA: `pov.stronger_than_owner` (error) on any
first-person stance or belief sentence whose content words are not mostly found in the referenced
owner item (text, why, instead); it fails "no manufactured opinion" and the Voice Gate's
owner-grounded opinion. The Golden Voice Set has a sixth kind, `principles` (practical rules and
decision principles, target 5).

## Voice architecture (final, 5 Oct)

Two separate systems, shown separately everywhere (CLI, post page, quality strip):

| System | What it checks | What it never means |
|---|---|---|
| Humanity (`lce.humanity`, 10 criteria) | spoken English, rhythm, contractions, long sentences, stiffness, over-explaining, AI symmetry, corporate wording, artificial endings, repeated structures | that the post sounds like the owner |
| Soheyl Voice Gate (`lce.voice_gate`) | 1. natural English, 2. owner-grounded meaning, 3. owner-recognizable phrasing and reasoning | a pass without a manual review on the current text hash |

**Sentence attribution.** Every prose sentence is `source` (attributed, or matching a recorded claim at
least as well as owner material), `owner` (a faithful paraphrase of a referenced confirmed item),
`writer` (connective framing that adds no personal meaning) or `invented_personal` (a personal
position, or a writer judgement such as "should", "the right way", "is a mistake", that neither the
owner material nor the source states). Any invented personal sentence fails the gate.

**No stronger position than confirmed owner material** (`lce.owner_scope`). A faithful paraphrase
is allowed. Not allowed, unless the owner confirmed exactly that: a stronger position (absolutes
such as always, never, every, must that the item lacks), a narrower operational position (a
first-person commitment to an action the item does not contain: "I'd keep approval on", "I'd
require approval for every action", "I wouldn't cut that checking"), a broader one ("everything",
"in general"), a different one (flipped polarity, an antonym, "I agree with him" without a
recorded agreement). The verdict is semantic: synonym groups, polarity, absolutes, commitments and
scope, with word overlap as one signal only. An item's owner-confirmed `scope`,
`allowed_paraphrases` and `forbidden_interpretations` come first. QA codes:
`pov.stronger_than_owner`, `pov.narrower_than_owner`, `pov.broader_than_owner`,
`pov.different_from_owner`, `pov.unsupported_by_owner` (errors); `voice.never_say` for anything
close to an owner never-say item.

**Source-heavy.** Under 25% owner sentences: `source_heavy_insufficient_owner_voice`, verdict
SOURCE_HEAVY, flagged "needs owner input". Acceptable when the source is strong, attribution is
clear, the writing is natural and no owner position is invented. Never fixed by adding first-person
language.

**Owner material** (`profile/golden/<kind>.yaml`): samples, opinions, disagreements, approaches,
observations, principles, noticings, explanations, never_say, priorities. Each item keeps the
owner's raw words (`original`, never overwritten), the confirmed normalized wording (`text`),
type (the file's kind), `confidence`, `provenance` (channel, question, proposal hash, dates),
`public_use` (true, false, or null = not asked), semantic `scope` (meaning, strength, actions,
topics, agrees_with, excludes), `allowed_paraphrases` and `forbidden_interpretations`. The voice
profile also has `natural_phrases`, `communication_habits`, `reasoning_patterns` and
`professional_priorities`.

**Voice-learning workflow** (`lce intake`, file `interview/intake.yaml`):

```
raw owner answer            lce intake answer <id> --raw "..."      (verbatim, appended)
→ normalized proposal       lce intake propose <id> --file p.yaml   (text, scope, paraphrases,
                                                                     forbidden, ambiguities)
→ owner review              lce intake review                       (shows the proposal hash)
→ owner confirmation        lce intake confirm <id> --hash <hash>   (only that exact proposal)
→ private voice profile     profile/golden/<kind>.yaml or a voice trait
```

A proposal with ambiguities is `unresolved` and cannot be confirmed; no answer (or owner draft)
means nothing can be proposed; confirmed material is never overwritten; `lce intake show` prints
the consolidated questionnaire with already-confirmed material listed and not asked again.

**Approval safety.** Approval is bound to the text hash. Any change (edit, same-day refresh,
Refresh replacement) invalidates it: local publish and cloud delegation refuse a stale approval,
the decisions path refuses a decision bound to an old hash, `mark_ready` discards a changed
approval, and a Voice Gate review of an older text is stale. Tested end to end in
`tests/test_approval_hash_safety.py`. The dashboard shows "Approval invalid (text changed)".

## Writing gate: weak drafts never reach the dashboard (5 Oct)

Root cause of 5/10 texts on the dashboard: style findings (long sentences, uncontracted forms,
editorial phrasing) were QA warnings, so a refresh package could pass QA and reach approval with a
failing humanity score; the score was only displayed. Now every package (`lce refresh package`,
slot `mode: new`) and every same-day update (`lce refresh update`) goes through `lce.revise`:

1. `autofix`: safe contractions (do not → don't, it is → it's before a word, I would → I'd, ...),
   never inside quotes, credit lines or hashtags;
2. `gate`: QA errors, or any failing mechanical humanity criterion (real person, spoken, variation,
   stiffness, symmetry, manufactured opinion, over-explaining, ending) refuse the package; a failing
   owner_voice or substance criterion needs `humanity_limitation` in the package (an honest reason,
   e.g. source-heavy) instead of an invented opinion;
3. the refusal lists every revision at once (sentences to split with their word count, phrases to
   drop, first-person positions without owner material, contrast frames, flat rhythm, ...). Nothing
   is stored; the writer rewrites and resubmits.

`lce humanity revise <post> --file draft.md [--write]` runs the same loop on a draft before
packaging (exit 1 while revisions remain). The engine never writes new prose itself. A media-only
refresh does not touch the text and is not gated. Settings `writing.gate: off` exists for
emergencies only; the default is on.

