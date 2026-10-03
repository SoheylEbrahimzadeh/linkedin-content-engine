# Replacement rows carry the warn spine their status already declares

Written against: 0ae8c1b

## Evidence chain

- Surface: Overview / Upcoming / "Earlier, not yet published" rows (`planRow`)
- Problem: rows with status REPLACEMENT_PENDING render a grey spine while their status pill is warn
- Design evidence: `cloud/src/ui/app.css` header ("status ... shown as a coloured spine"); `lib.txt` STATUSES.REPLACEMENT_PENDING tone "warn"
- Owner: `cloud/src/ui/app.css` (.row.status-*)
- Scope and affected surfaces: every planRow with status replacement_pending
- Uncertainty: none

## Design decision

Give `.row.status-replacement_pending` the same `var(--warn)` spine as `.row.status-needs_regeneration`.

## Reuse

- `var(--warn)`
- Exemplar: `.row.status-needs_regeneration` in `cloud/src/ui/app.css`

## Changes

1. `cloud/src/ui/app.css`
   - Change: add `.row.status-replacement_pending` to the needs_regeneration spine rule
   - Preserve: all other spines
   - Verify: render harness, Upcoming row "An approved fictional post whose date passed" has an amber spine

## Validation

- Repository: `node tests/ui/render.mjs` → OK no errors
