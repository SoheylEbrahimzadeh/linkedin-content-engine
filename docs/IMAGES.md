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

## Charts from recorded evidence (`lce image chart`)

`lce image chart <post> [--claim N ...]` draws the post's recorded, sourced
claims that contain a number as a square 1200×1200 PNG: percentages as bars on
a 0–100 scale, other figures as number cards, each labelled with the claim's
verbatim text and the source domain. It records the decision itself (kind
`chart`, origin `own_creation`, generation method with the matplotlib version,
relation and alt text), so the image passes the same checks and is bound to
approval. With no numeric claim it refuses: there would be nothing true to
show. Needs the optional extra: `pip install 'linkedin-content-engine[visuals]'`.
The accent colour is configurable (`visuals.accent` in `config/settings.yaml`).

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

## Publishing (Phase 6B, local publisher)

`lce publish <post>` uploads the approved image through LinkedIn's official
Images API, then creates the post referencing it:

1. `POST /rest/images?action=initializeUpload` with the person as owner →
   `uploadUrl` and `urn:li:image:…`;
2. `PUT <uploadUrl>` with the image bytes and the OAuth token (201);
3. `POST /rest/posts` with `content.media = {id: <image urn>, altText}`.

The token is only sent to `api.linkedin.com` and to LinkedIn's
`https://www.linkedin.com/dms-uploads/` upload URLs; any other upload URL is
refused before anything is sent. A failure in steps 1–2 creates no post and is
a plain, retryable `PUBLISH_FAILED`; step 3 keeps the existing rules
(ambiguous → `NEEDS_RECONCILE`, never retried). The bytes sent are re-hashed
against the approved image hash. `publication.json` records the image URN and
hash. `lce publish <post> --dry-run` lists the three steps and sends nothing.
LinkedIn accepts JPG, PNG and GIF under 36,152,320 pixels; `lce image check`
reads the dimensions from the file header.

The cloud Worker (PR #5) is still text-only: `lce cloud push` refuses posts
with an image until the Worker gains the same upload (6B, cloud part).
Approvals from before the image stage are text-only.

Sources: LinkedIn Images API and Posts API documentation (Microsoft Learn).
