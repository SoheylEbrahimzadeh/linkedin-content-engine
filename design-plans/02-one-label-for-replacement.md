# One label for "refresh requested, replacement being written"

Written against: 0ae8c1b

## Evidence chain

- Surface: planRow meta line, post page stepper and Plan card
- Problem: the same state shows "Rejected · replacement being written" (status pill) and "Refresh requested · writing a replacement" (refresh pill) side by side
- Design evidence: direct contradiction in user-facing copy within one row (production: 29 Sept row; Gartner post page)
- Owner: `cloud/src/ui/lib.txt` STATUSES; `cloud/src/ui/app.txt` planRow
- Uncertainty: none

## Design decision

REPLACEMENT_PENDING uses the Refresh wording ("Refresh requested · writing replacement"); a row whose status is REPLACEMENT_PENDING does not repeat it with the refresh pill.

## Changes

1. `cloud/src/ui/lib.txt`: STATUSES.REPLACEMENT_PENDING.label → "Refresh requested · writing replacement"
2. `cloud/src/ui/app.txt` planRow: omit `refreshPill(p)` when `i.status === "REPLACEMENT_PENDING"`
   - Preserve: the refresh pill for other states (e.g. "Refreshed · Awaiting approval", "Sources changed · writing an updated version" appears only in the post banner)
   - Verify: harness Upcoming row shows one replacement label

## Validation

- Repository: `node --test tests/js/*.mjs`, `node tests/ui/render.mjs` → pass
