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

## LCE-046: source first

A post backed by a specific article or report should carry a visual that **belongs
to that source** whenever the law allows it. "A picture generally about the topic"
is not the same thing.

1. `lce image search` with `source_urls` (the post's own sources) inspects each
   page first. It records every visual on it (Open Graph/Twitter image, `<img>`,
   `<figure>` with alt text and caption) and its rights signals (copyright notice,
   "may not be reproduced / without permission", Creative Commons links,
   public-domain statements). Copies are kept for inspection only, never attached.
2. The media package records the decision as `media.source_check`:
   - `source_visual_used`: only with an explicit licence or permission.
   - `source_visual_unavailable_or_restricted`: a visual exists but reuse is not
     permitted, or the page could not be inspected.
   - `no_source_visual`.

   It also carries the source URL and the evidence. The decision is required for
   every real-image choice and for text-only `no_suitable_licensed_image`.
3. Next: the original creator's licensed asset, or another licensed asset
   directly tied to the same subject. The `reviewed` selection must state
   `association` (`source_visual` | `original_source_asset` |
   `same_subject_licensed`), `why_belongs_to_source` and `why_legal`.
4. Otherwise text-only `no_suitable_licensed_image`. Never a generated diagram,
   and never a visual copied from a page whose rights do not allow it.

### Owner policy: the source's own image without a licence

`config/settings.yaml` `visuals.source_image_policy: owner_accepts_copyright_risk` is the owner's
decision to post the cited source's own image (Open Graph / hero image or a figure on that page)
without an explicit licence. The engine then accepts `reviewed: {source: source_page, page_url,
image_url, publisher}` only when the image is published on that page; provenance is
`source_publication` / `owner_accepted_risk`, and the attribution `Image: <publisher>` is recorded
in the image record (LCE-052: never as a line in the post). Default `licensed_only` keeps the
licence requirement.

## LCE-044: real images are chosen by looking at them

Matching words in metadata is not relevance: in production it picked a 1997
post-office photo and then a generic accounting screenshot. The selection is
now two steps:

1. `lce image search --file spec.yaml --out DIR` (in the private repo: commit
   `media/requests/<post_id>.yaml`; the `media-search` workflow runs it where
   the sources are reachable). It searches **Wikimedia Commons** and
   **Openverse** (openly licensed images from Flickr, museums, StockSnap,
   Rawpixel and more) through their public APIs. It keeps only PD, CC0, CC BY
   and CC BY-SA (no NC/ND), and saves a preview and the licence record of each
   candidate. Nothing is attached. **Unsplash and Pexels are not searched**:
   their APIs need a key, which would be a new secret, and their sites may not
   be scraped.
2. A session **looks at every preview** and records what each one depicts and
   why it is or is not about the post. It then names one candidate in
   `media: {reviewed: {source, id, depicts, why_relevant, alt_text, relation,
   concept, reviewed_by, reviewed: [...]}}`, or chooses text-only
   `no_suitable_licensed_image`. `lce refresh media` (only the media changes)
   or `lce refresh package` fetches that exact file again, re-checks its
   licence at the source, hashes it and attaches it with the review. The
   attribution is recorded in the image record (LCE-052: not in the post).

## LCE-043: a real image first; generated diagrams only on request

The default for a post is a **real, relevant, legally reusable image**: a photo
of the industry, place, artefact, event or organisation the post is about, or a
source chart whose licence allows reuse. Generated diagrams are not the
fallback; "nothing suitable" means text-only (`no_suitable_licensed_image`).

`commons.select` (used by `media.commons` in a refresh package) records, per post:

| | stored in `image.yaml` |
|---|---|
| central subject, visual concept, subject terms | `selection.subject`, `selection.concept`, `selection.subject_terms` |
| every candidate file and why it was refused or chosen | `selection.tried[]` (licence, outcome, reason) |
| semantic match | `media_relevance.semantic.matched_terms`: subject terms found in the file's own title, description or categories (none → refused) |
| creator, licence, licence URL, source URL | `provenance.creator`, `.license`, `.license_url`, `.source_url`, `.title` |
| attribution | `provenance.attribution_required`, `provenance.attribution` (appended to the post text when required) |
| retrieved asset | `provenance.retrieved` (original, SHA-1 verified, or the Commons thumbnail of that file), `.retrieved_url`, `.retrieved_at`, `.original_sha1`, `sha256` |

Relevance gate (LCE-043b, after a 1997 post-office photo matched only "office
workers"): with three or more subject terms at least two must be named by the
file's metadata (`min_matches`), every `required_terms` entry must be, and files
marked with a personality-rights restriction (identifiable people) are refused.
A wrong image found after the fact is corrected with `lce refresh media` (text
kept, the wrong package kept in History as `replaced` with the reason).

Licences: PD, CC0, CC BY, CC BY-SA only; NC, ND, fair use and unknown are
refused. Sources: the Commons API only — never Google Images or pages with
unclear rights. A later version never reuses an earlier version's file (by
title and by hash). The Control Center's media card shows all of this, plus the
"Image search" list of candidates.

## Ways to get a real, rights-safe image (no paid API)

1. `lce image chart <post>` — recorded, sourced figures, drawn verbatim with the source.
2. (only when the owner asks for a drawn visual) `lce image diagram <post> --spec <file.yaml>` — a conceptual visual of the post's
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

## LCE-052: no visible image attribution in the post (owner rule 2026-10-06)

A selected image appears with the post on its own. The pipeline never writes a source label for it
into the text: no `Image: CIO.com`, `Photo: ...`, `Credit: ...`, `Image source: ...`.

- Provenance is unchanged and stays in `image.yaml` (`provenance.source_url`, `creator`, `credit`,
  `license`, `attribution`, `attribution_required`, `retrieved_url`, ...) for auditing, debugging,
  copyright tracking and compliance. The dashboard shows it as "kept in the image record, not shown
  in the post".
- `lce.credit` recognises visible image labels (a whole line `Image:` / `Photo:` / `Picture:` /
  `Illustration:` / `Credit:` / `Image source:` / `Image credit:` / `Header image by:` ...; a
  `Source:` or `Via` line only when it names the image's own credit or publisher, so text
  citations stay).
- Where it is enforced: `repackage._credit` (after an image is attached) no longer inserts the
  attribution and removes any label already in the text; `revise.autofix` (writing gate step 1:
  package, same-day refresh) removes labels; QA reports `media.visible_credit` as an error for
  pipeline text (a warning for the owner's own edit). The Worker publishes the approved text
  unchanged, so the published post carries no label.
- Posts approved before this rule keep their approved text (and its label) until the owner
  refreshes or edits them: an approval is bound to its exact text.
