# Attribution

This is an independent project. The MIT-licensed
[sergebulaev/linkedin-skills](https://github.com/sergebulaev/linkedin-skills)
repository was studied as a reference. No files are copied verbatim; where
later phases adapt a concept, it is listed here.

| Concept studied | Status here |
|---|---|
| Story Bank sections and "never invent" interviewing rule | re-implemented as a structured story schema (`story.schema.json`) and the `lce-interview` skill |
| Voice profile built from real past posts | re-implemented: `voice.schema.json`; past posts imported only as proposals the owner confirms |
| Pillar framework and weekly caps | re-implemented as per-pillar `max_share` and rotation checks |
| English AI-tell lists and post-audit blockers | independently written in `src/lce/rules/en/ruleset.yaml`, language-aware engine; reveal bridges, staccato stacks, performed sincerity and generic closers added as warnings after a re-audit (2026-09-30) |
| CTA check in post audit | re-implemented: the closing is checked against the voice profile's `cta.allowed` (warning) |
| Hook formula catalogue | not adopted; engagement bait is a QA error instead |
| Treating fetched content as untrusted | implemented: `untrusted: true` on web candidates, skill rules |
| Draft → approval → publish pattern | re-designed: approval is enforced (hash-bound), not a convention |

Designed from scratch: orchestration, state machine, storage layout, privacy
boundary, provider-agnostic publishing with reconciliation, duplicate
detection, scheduling.

Not used: LinkedIn scraping, comment/reply/engagement tooling, employee
advocacy, unverified algorithm claims as facts.

## Typefaces (Control Center)

The Control Center serves two typefaces from its own Worker (no third-party font
host). Both are under the SIL Open Font License 1.1; the licence texts sit next
to the files in `cloud/src/ui/fonts/`.

| Typeface | Copyright | File |
|---|---|---|
| Newsreader (variable, latin, wght) | 2020 The Newsreader Project Authors | `cloud/src/ui/fonts/newsreader.woff2` |
| Inter (variable, latin, wght) | 2016 The Inter Project Authors | `cloud/src/ui/fonts/inter.woff2` |

The `.woff2` subsets were taken unmodified from the `@fontsource-variable/newsreader`
and `@fontsource-variable/inter` npm packages.
