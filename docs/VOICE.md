# Voice system (LCE-051)

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
story or confirmed observation), `insight.summary_only` (external summary without a stance),
`lesson.no_story`, `observation.no_evidence`, `network.transactional` (DM me, let's connect,
I help companies, looking for projects, book a call).
Warnings: `voice.consultant_language`, `pattern.generic-opening`, `pattern.empty-leadership`,
`style.jargon`, `network.fake_authority`, `repetition.structure`, `content_type.missing`.

## Humanity test

`lce humanity score <post>` scores ten criteria deterministically: point of view, real evidence,
the owner's voice (compared with ≥3 real samples; `unknown` without them, never a pass), not
consultant language, not interchangeable, shows how the owner thinks, networking/team signal,
natural first person, natural ending, said aloud. `lce humanity eval` runs the bundled evaluation
set (fictional persona, 10 areas, good and bad versions); the test suite requires every
expectation to hold and every good text to outscore every bad one. The dashboard shows the score
on each post and the voice and content readiness on System.

## Content mix

`brand.yaml` `mix.content_types` (default: insight 30%, POV 20%, how-to 20%, observation 15%,
lesson 15%). `lce plan roll` gives each new open slot the producible type furthest below its
share; `lce brand mix` shows target, actual and what blocks each type.
