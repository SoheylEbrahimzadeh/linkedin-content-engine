# Attribution

This is an independent project. The MIT-licensed
[sergebulaev/linkedin-skills](https://github.com/sergebulaev/linkedin-skills)
repository was studied as a reference. No files are copied verbatim; where
later phases adapt a concept, it is listed here.

| Concept studied | Status here |
|---|---|
| Story Bank sections and "never invent" interviewing rule | adapted into a structured, metadata-rich fact schema (`story_fact.schema.json`) |
| Voice profile built from real past posts | planned (Phase 1) |
| Pillar framework and weekly caps | planned (Phase 1), extended to a 30-day plan |
| English AI-tell lists and post-audit blockers | planned (Phase 1) as `rules/en/`, rewritten, language-aware |
| Hook formula catalogue | planned (Phase 1), curated to exclude engagement-bait formulas |
| Treating fetched content as untrusted | planned (Phase 1) |
| Draft → approval → publish pattern | re-designed: approval is enforced (hash-bound), not a convention |

Designed from scratch: orchestration, state machine, storage layout, privacy
boundary, provider-agnostic publishing with reconciliation, duplicate
detection, scheduling.

Not used: LinkedIn scraping, comment/reply/engagement tooling, employee
advocacy, unverified algorithm claims as facts.
