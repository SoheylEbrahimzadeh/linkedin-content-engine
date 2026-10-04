# 04: Executive briefing (visual and UX redesign)

Skills used, in order: `ui-ux-pro-max` (direction), `frontend-design` (plan review and
implementation), `baseline-ui` (polish; `better-ui` is not published in any skills registry).
Visual and UX changes only; no business logic changed.

## Direction

- Ground: porcelain `#F2F3F0`; surfaces white; ink midnight `#131A26`.
- One accent for decisions: lapis `#2E3A8C` (Approve, the current step, links).
- One bold element: the midnight briefing plate on Overview, with the approval count as a large
  champagne (`#D9C394`) numeral. Champagne appears nowhere else.
- Type: Newsreader (serif) for what is read: titles, the post itself, numerals, dates. Inter for
  controls and data. Both are self-hosted by the Worker (SIL OFL 1.1, `cloud/src/ui/fonts/`), so the
  CSP gains only `font-src 'self'`.
- Status is a dot and a word; one dot per row (the state), everything after it is plain words.

Plan review against the frontend-design calibration list: the first draft (warm cream ground with a
serif display) was the skill's first "generated look" tell, so the ground moved to cool porcelain and
the warmth was confined to the plate.

## Structure

- Overview: briefing plate (approval count, first title, Review), the work in motion underneath it;
  then the next post as a hero next to "Needs your attention"; the week as a run-sheet; standing
  facts (publishing as, auto-publish, emergency stop, writer start, LinkedIn access) as one quiet line.
- Upcoming: a run-sheet. Each day is a typeset date block; rows sit on hairlines with a status spine
  (lapis awaiting, green approved/published, ink scheduled, dashed amber being written, dotted open
  slot or planned, struck-through skipped, red failed).
- Post: the preview on a recessed stage with the only lifted shadow; a sticky operations rail.
  Controls in three tiers: Approve full width; Refresh / Edit / Reschedule; Skip, Reject, Duplicate
  and the log as text actions. On phones the first two tiers are a fixed bar above the navigation.
- Radar: an editorial feed (source and age, serif title, excerpt, "why it matters" from the matched
  pillar terms, relevance in words, used/planned state).
- Refresh progress: a vertical timeline of the reported events with times.

## Critique loop (desktop 1280, phone 390)

1. Seven equal buttons on the post rail; secondary status dots in every row; mobile brand truncated.
2. Tier divider shown with no first tier; quiet days overlapped their text; phone stepper wrapped.
3. Tier-3 actions stretched (selector specificity); progress panel kept the tinted notice background.

Also found: an entrance animation on `transform` made each section the containing block of the fixed
phone action bar; the entrance is opacity only.

baseline-ui pass: no looping paint animation; interaction motion at most 200 ms with ease-out;
tracking only on display sizes; tabular numerals for data; a fixed z-index scale.
