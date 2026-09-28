# Language rulesets

Humanizer and audit logic is language-aware: the detection engine is generic and
each language supplies its own rules in `rules/<lang>/ruleset.yaml`.

| Language | Status  | Phase |
|----------|---------|-------|
| en       | planned | 1     |
| de       | planned | 4     |
| fa       | planned | 4     |

A language is only publishable when its ruleset has `status: ready`.
