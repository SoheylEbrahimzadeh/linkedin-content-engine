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
2. `lce image diagram <post> --title … --item … [--footer …]` — a checklist diagram;
   every string must be the post's own words or a recorded claim (refused
   otherwise); the source publisher is added. Origin `own_creation`, usage `owned`.
3. `lce image commons <post> --title "File:…" --relation … --alt … --rationale …` —
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
