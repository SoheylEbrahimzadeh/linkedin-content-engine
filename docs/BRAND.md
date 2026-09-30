# Personal Brand Engine (Phase 5)

The engine builds a long-term professional identity rather than a stream of
unrelated posts. Every post is placed in a deliberate strategy: a pillar, a
recurring theme, optionally a career chapter, and an explicit evidence mode.

```
Personal brand → Research → Topic selection → Content strategy → Writing
→ Humanization → QA → Image ([IMAGES.md](IMAGES.md)) → Approval → Publishing
→ Analytics (Phase 7) → Learning → brand refinement
```

## Where the brand lives

All real brand data is private and lives in the private data directory:

| File | Holds |
|---|---|
| `profile/profile.yaml` | identity, expertise, audience, goals, pillars, public/avoid topics, forbidden claims |
| `profile/brand.yaml` | objective, throughline, career chapters, target markets/roles/industries, themes, content mix, credibility rules |
| `story_bank/stories/*.yaml` | personal experiences; only `PUBLIC` stories are evidence |

This repository holds only the schema (`src/lce/schemas/brand.schema.json`),
the engine and a fictional demo (`examples/demo-persona/profile/brand.yaml`).
`lce privacy-denylist` turns private brand/profile data into a local scanner
denylist so it cannot slip into public code.

**Target markets are direction, not subjects.** They inform audience and
wording; QA warns when a post mentions one. Topics that must never become
content go on the profile's avoid list (QA error, ranking penalty).

## Evidence rule

Each post has an evidence mode:

- `personal`: backed by at least one PUBLIC story. Without one, `lce select
  pick` puts the post into `NEEDS_INPUT` ("personal evidence required") and QA
  fails `brand.personal_evidence_missing`. The engine never invents
  experience, employers, projects, results, opinions or stories.
- `external`: backed by recorded research sources and claims.

A theme declares `evidence: personal | external | either`. With `either`, a
story is used when one is given (or when the personal share is below
`mix.min_personal_share` and a matching PUBLIC story exists).

## Strategy (deterministic)

`lce brand status` shows, for the last `mix.window_days` (default 28):
pillar counts and shares against the target mix, theme use, career-chapter
coverage, the personal-evidence share, PUBLIC stories available per pillar and
theme, and cross-reference problems (unknown pillars/stories, mix > 1).

`lce brand next` recommends the next moves: the most under-served pillar,
its least-used theme, and the evidence mode, flagging themes that need the
owner's personal input. `lce select list` adds a bonus for pillars below
target and a penalty for avoid-list topics.

The LLM (Claude Code, skills `lce-research` and `lce-create-post`) proposes
candidates and writes; these rules decide placement and block unsupported
claims.

## Collecting the brand

`lce interview next` asks the brand questions (group `brand`). Required before
drafting: `brand_objective` and `brand_themes`. Optional: throughline,
chapters, target markets/roles/industries, mix and credibility rules.

## Cadence

Publishing frequency is configuration (`config/settings.yaml`: `posts_per_week`
1–7 and slots), not architecture. Human approval stays mandatory.
