# Image stage (Phase 6A)

Images are part of the content, not decoration. Every post gets an explicit
image decision before its approval request can be prepared, and the approval
is bound to that decision.

```
… QA → duplicate check → image decision → approval (text hash + image hash) → publishing
```

## Decision

`lce image decide <post> --kind <kind> --rationale "..."` records
`posts/<post>/image.yaml` (private data directory). Kinds:

| Kind | Typical origin |
|---|---|
| `none` | an explicit, valid choice when an image would only decorate |
| `diagram`, `architecture`, `chart` | `own_creation` (drawn or scripted from the post's own facts), `generated`, or a cited `source_publication` |
| `screenshot` | `owner_screenshot` (own work) or a cited `source_publication` |
| `source_image` | `source_publication`, `licensed_stock` or `owner_photo` |
| `generated_concept` | `generated` only |

Every image records: file (copied into the post folder as `image.<ext>`,
PNG/JPG/GIF), SHA-256 and size, rationale, **relation** to the post, alt text,
and provenance: origin, usage (`owned`, `licensed`, `permitted`,
`public_domain`, `needs_review`), source URL, credit and licence for
third-party images, and generation method (and prompt) for generated or
scripted images.

## Checks (`lce image check`)

Errors block the approval request (and a scheduled job reports "image decision
needed" or the problem):

- no decision, or no rationale;
- missing or changed file, or content that does not match the extension;
- no relation to the post, no alt text;
- origin that does not fit the kind (e.g. a "screenshot" that was generated);
- third-party image without source URL and licence, or claimed as `owned`;
- generated image without a generation method;
- usage `needs_review`: the owner must resolve rights first.

A very short relation is a warning.

## Approval binding

`lce approval prepare` shows the decision in `APPROVAL.md` and records the
image hash (`none` for no image). `lce approve` refuses if the image changed
after the request was prepared; `lce ready` discards the approval if the image
changed after approval. Changing the decision of a post awaiting approval,
approved or ready reopens it (checks and approval are discarded); after
publishing starts it cannot change.

## Publishing

Image upload to LinkedIn is **Phase 6B**. Until then the publisher refuses a
post whose decision is not `none`, rather than publishing it without its
image. Approvals from before the image stage are text-only.
