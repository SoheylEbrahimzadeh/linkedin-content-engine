# Media pipeline (LCE-038)

Every post gets an explicit media decision before approval (`posts/<id>/image.yaml`,
schema `src/lce/schemas/image.schema.json`). Text-only is a decision with a
reason, never a silent default.

| Field (dashboard name) | Stored as |
|---|---|
| media_required | `kind` ≠ `none` |
| media_type | `kind` (`none`, `chart`, `diagram`, `architecture`, `screenshot`, `source_image`, `generated_concept`) + `mime` |
| media_status | derived by `images.media_view`: attached, text_only, needs_review, invalid, undecided |
| media_source / source_url | `provenance.origin`, `provenance.source_url`, `provenance.credit`, `provenance.title` |
| usage / licence | `provenance.usage`, `provenance.license`, `provenance.license_url` |
| asset hash, size | `sha256`, `bytes`, `width`, `height` (read from the file) |
| alt text | `alt_text` |
| text-only reason | `text_only_reason` + `rationale` |

## Ways to get a real, rights-safe image (no paid API)

1. `lce image chart <post>` — recorded, sourced figures, drawn verbatim with the source.
2. `lce image diagram <post> --spec <file.yaml>` — a conceptual visual of the post's
   idea (flow, process, decision tree, framework/relationship map) with short labels
   of its own; attached only if the media relevance check accepts it (below).
   Origin `own_creation`, usage `owned`. (LCE-041: the old verbatim checklist mode
   restated the post's text and is now rejected.)
3. `lce image commons <post> --title "File:…" --relation … --alt … --rationale …
   --concept … --visual-type photo --relevance-reason …` —
   Wikimedia Commons through its API only; licence must be PD, CC0, CC BY or CC BY-SA
   (NC/ND/fair use/unknown refused); credit, licence and licence URL come from the API;
   the download must match the SHA-1 Commons reports.
4. `lce image decide … --origin owner_screenshot|owner_photo` — the owner's own material.
5. Otherwise `lce image decide <post> --kind none --text-only-reason … --rationale …`.

The asset is copied into the post folder (`image.png|jpg|gif`); approval binds its
SHA-256, and any change reopens the post (approval discarded, QA/duplicate check and a
new approval artifact needed). `lce cloud sync` uploads the asset to `preview_media`
(sha-checked) so the Control Center shows the real thumbnail, source, rights, size and
alt text. Publishing uses the approved image only.

## Semantic relevance (LCE-041)

The Gartner post showed the gap: a diagram that repeated the post's checklist
was technically valid but added nothing. The decision is now
`post concept → does a visual add value → choose the form → generate → verify
relevance → verify facts → attach`, never `checklist detected → copy it`.

Every image carries `media_relevance` in `image.yaml`, re-evaluated against the
current text whenever the image is checked:

| field | meaning |
|---|---|
| `concept` | the idea the visual communicates (required) |
| `visual_type` | flow, process, decision_tree, framework, relationship_map, comparison, matrix, chart, photo, screenshot, illustration |
| `relevance_reason` | what the visual adds beyond the text (required) |
| `copied_post_text_ratio` | share of the image's non-factual words inside a 4-word sequence also in the post; `null` when the image's text cannot be read (photo, Commons, owner screenshot: declared) |
| `factual_claims` | every string with a number, whether it is a recorded claim, and its source |
| `source_requirements` | the source line the image must show |
| `media_decision` | `accepted` or `rejected` (with `problems`) |

Rejected (nothing attached, approval impossible): copied post text above 35%,
a label longer than 6 words or a note longer than 8, a question copied from the
post, more than 60 words of text, a number that is not a recorded claim, a
figure without a source line, missing concept/reason, alt text that repeats the
post or is shorter than 40 characters. Images from before LCE-041 without a
record are re-evaluated from what they draw; a verbatim checklist fails and must
be regenerated or replaced by text-only. The Control Center shows the concept,
type, reason, copied ratio, facts, decision and image hash next to the image.
