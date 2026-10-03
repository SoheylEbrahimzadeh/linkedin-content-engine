# The progress list's current step matches the phase the panel states

Written against: 0ae8c1b

## Evidence chain

- Surface: post page Refresh progress panel
- Problem: phase pill "Waiting for writing session" while the list marks "Starting writer" as current
- Design evidence: direct contradiction inside one panel (production, Gartner post, 20:25 UTC)
- Owner: `cloud/src/ui/lib.txt` refreshProgress
- Uncertainty: none

## Design decision

When the phase is "waiting" (request in the repository, no writer event), "Starting writer" is shown done without a time and "Writer session started" is the current step.

## Changes

1. `cloud/src/ui/lib.txt` refreshProgress: before choosing the current step, if phase is waiting and the dispatched step has no event, mark it `done_untimed`
   - Preserve: queued/starting phases and every recorded timestamp
   - Verify: tests/js/control.test.mjs LCE-048 case: applied, no worker → current step "waiting"

## Validation

- Repository: `node --test tests/js/lib.test.mjs tests/js/control.test.mjs` → pass
