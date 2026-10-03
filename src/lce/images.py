"""Image stage: decide, record and verify the image (or explicit "no image") for a post.

Rules:
- Every post gets an explicit decision before approval; `none` is valid.
- An image is never decoration: it needs a rationale, a stated relation to the
  post, alt text and a provenance record (origin, usage rights, source/licence
  for third-party images, generation method for generated or scripted images).
- Usage `needs_review` blocks approval until the owner resolves it.
- The file's SHA-256 is recorded; approval binds it, and any later change to
  the image discards the approval exactly like a text change.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from lce.store import DataStore, StoreError, now_iso

KINDS = ("none", "source_image", "diagram", "architecture", "screenshot", "chart",
         "generated_concept")
ALLOWED_EXT = (".png", ".jpg", ".jpeg", ".gif")
MAGIC = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff",
         ".gif": b"GIF8"}
# Which origins make sense for which kind (a generated image cannot be a "screenshot").
ORIGINS_FOR_KIND = {
    "source_image": {"source_publication", "licensed_stock", "owner_photo"},
    "diagram": {"own_creation", "generated", "source_publication"},
    "architecture": {"own_creation", "generated", "source_publication"},
    "screenshot": {"owner_screenshot", "source_publication"},
    "chart": {"own_creation", "generated", "source_publication"},
    "generated_concept": {"generated"},
}
THIRD_PARTY = {"source_publication", "licensed_stock"}
# LCE-038: why a post stays text-only (recorded, never a silent default).
TEXT_ONLY_REASONS = ("text_carries_point", "no_relevant_visual", "no_rights_safe_source",
                     "personal_story_without_owner_photo", "would_be_decorative",
                     "no_suitable_licensed_image")
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
MIN_RELATION = 20
NO_IMAGE = "none"


MAX_PIXELS = 36_152_320  # LinkedIn Images API: fewer than this many pixels


def dimensions(data: bytes, ext: str) -> tuple[int, int] | None:
    """(width, height) from the file header, or None if it cannot be read."""
    import struct

    try:
        if ext == ".png" and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if ext == ".gif":
            return struct.unpack("<HH", data[6:10])
        if ext in (".jpg", ".jpeg"):
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    return None
                marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + length
    except struct.error:
        return None
    return None


def path(store: DataStore, post_id: str) -> Path:
    return store.post_dir(post_id) / "image.yaml"


def load(store: DataStore, post_id: str) -> dict | None:
    p = path(store, post_id)
    return store.read_doc(p) if p.exists() else None


def file_sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def decide(store: DataStore, post_id: str, *, kind: str, rationale: str,
           source_file: str | None = None, relation: str = "", alt_text: str = "",
           provenance: dict | None = None, decided_by: str = "agent",
           text_only_reason: str | None = None, relevance: dict | None = None) -> dict:
    """Record the decision; copies the image into the post folder as image.<ext>."""
    from lce.posts import reopen
    from lce.state import PostState as S

    state = S(store.load_post(post_id)["state"])
    if kind not in KINDS:
        raise StoreError(f"kind must be one of {', '.join(KINDS)}")
    if text_only_reason is not None and (kind != NO_IMAGE or text_only_reason not in TEXT_ONLY_REASONS):
        raise StoreError("text_only_reason applies to kind none and must be one of "
                         + ", ".join(TEXT_ONLY_REASONS))
    if state in {S.PUBLISHING, S.PUBLISHED, S.PUBLISH_FAILED, S.NEEDS_RECONCILE}:
        raise StoreError(f"the image of a {state.value} post cannot change")
    if state in {S.AWAITING_APPROVAL, S.APPROVED, S.READY_TO_PUBLISH}:
        reopen(store, post_id, "image decision changed")  # approval and checks discarded
    folder = store.post_dir(post_id)
    for old in folder.glob("image.*"):
        if old.suffix != ".yaml":
            old.unlink()
    doc: dict = {"kind": kind, "rationale": rationale.strip(), "decided_at": now_iso(),
                 "decided_by": decided_by}
    if kind == NO_IMAGE and text_only_reason:
        doc["text_only_reason"] = text_only_reason
    if kind != NO_IMAGE:
        if not source_file:
            raise StoreError("an image decision needs --file")
        src = Path(source_file).expanduser()
        ext = src.suffix.lower()
        if ext not in ALLOWED_EXT or not src.is_file():
            raise StoreError(f"image must be an existing {'/'.join(ALLOWED_EXT)} file")
        dest = folder / f"image{ext}"
        shutil.copyfile(src, dest)
        doc.update({"file": dest.name, "sha256": file_sha256(dest), "bytes": dest.stat().st_size,
                    "mime": MIME[ext], "relation": relation.strip(), "alt_text": alt_text.strip(),
                    "provenance": provenance or {}})
        size = dimensions(dest.read_bytes(), ext)
        if size:
            doc["width"], doc["height"] = int(size[0]), int(size[1])
        if relevance:
            from lce import relevance as rel
            from lce.posts import current_text

            doc["media_relevance"] = rel.declared(
                concept=relevance.get("concept", ""), visual_type=relevance.get("visual_type", ""),
                reason=relevance.get("reason", ""), alt_text=doc["alt_text"],
                post=store.load_post(post_id), text=current_text(store, post_id))
    store.write_doc(path(store, post_id), "image", doc)
    store.log_event("image", post_id=post_id, kind=kind)
    return doc


def check(store: DataStore, post_id: str) -> tuple[list[str], list[str]]:
    """(errors, warnings) for the post's image decision."""
    doc = load(store, post_id)
    if doc is None:
        return ["no image decision yet (use `lce image decide`; 'none' is allowed)"], []
    errors, warnings = [], []
    if not doc.get("rationale", "").strip():
        errors.append("the decision needs a rationale")
    if doc["kind"] == NO_IMAGE:
        if not doc.get("text_only_reason"):
            warnings.append("text-only without a reason category (text_only_reason)")
        if _has_figures(store, post_id):
            warnings.append("the post cites recorded figures: `lce image chart` could show them; "
                            "keep text-only only if the text carries the point")
        return errors, warnings
    f = store.post_dir(post_id) / doc.get("file", "")
    if not doc.get("file") or not f.is_file():
        return errors + ["the image file is missing"], warnings
    if file_sha256(f) != doc.get("sha256"):
        errors.append("the image file changed after the decision; decide again")
    data = f.read_bytes()
    if not data.startswith(MAGIC[f.suffix.lower()]):
        errors.append("the file content does not match its extension")
    else:
        size = dimensions(data, f.suffix.lower())
        if size is None:
            errors.append("cannot read the image dimensions")
        elif size[0] * size[1] >= MAX_PIXELS:
            errors.append(f"{size[0]}x{size[1]} is too large for LinkedIn "
                          f"(< {MAX_PIXELS:,} pixels)")
    relation = doc.get("relation", "").strip()
    if not relation:
        errors.append("state how the image relates to the post (no decorative images)")
    elif len(relation) < MIN_RELATION:
        warnings.append("the relation to the post is very short; be specific")
    if not doc.get("alt_text", "").strip():
        errors.append("alt text is required")
    prov = doc.get("provenance") or {}
    origin, usage = prov.get("origin"), prov.get("usage")
    if not origin or not usage:
        errors.append("provenance needs origin and usage")
        return errors, warnings
    if origin not in ORIGINS_FOR_KIND[doc["kind"]]:
        errors.append(f"origin {origin} does not fit kind {doc['kind']}")
    if usage == "needs_review":
        errors.append("usage rights need the owner's review before approval")
    if origin in THIRD_PARTY:
        if not prov.get("source_url") or not prov.get("license"):
            errors.append("third-party images need source_url and license")
        if usage == "owned":
            errors.append("a third-party image cannot be 'owned'")
    if origin == "generated" and not (prov.get("generation") or {}).get("method"):
        errors.append("generated images need generation.method")
    rel = relevance_now(store, post_id, doc)
    if rel is None:
        errors.append("no media relevance record: state the concept, visual type and relevance reason "
                      "(`lce image decide --concept --visual-type --relevance-reason`)")
    elif rel.get("legacy"):
        errors.append("this visual predates LCE-041 and restates the post's text; regenerate it as a "
                      "conceptual visual (`lce image diagram --spec`) or choose text-only: "
                      + "; ".join(rel["problems"]))
    elif rel["media_decision"] != "accepted":
        errors.append("media relevance rejected: " + "; ".join(rel["problems"]))
    return errors, warnings


# LCE-041: visuals the engine draws itself must pass the semantic relevance check.
GENERATED_KINDS = {"diagram", "chart"}


def relevance_now(store: DataStore, post_id: str, doc: dict | None = None) -> dict | None:
    """The media_relevance record re-evaluated against the CURRENT post text (the text
    can change after the image was made). None when nothing can be evaluated."""
    from lce import relevance
    from lce.posts import current_text

    doc = doc if doc is not None else load(store, post_id)
    if not doc or doc.get("kind") == NO_IMAGE:
        return None
    spec = relevance.legacy_spec(doc)
    if spec is None:
        return doc.get("media_relevance")
    try:
        text = current_text(store, post_id)
    except StoreError:
        return doc.get("media_relevance")
    rec = relevance.evaluate(spec, store.load_post(post_id), text, alt_text=doc.get("alt_text", ""),
                             method=(doc.get("media_relevance") or {}).get("method", ""))
    if doc.get("media_relevance") is None and doc.get("kind") in GENERATED_KINDS:
        rec["legacy"] = True
    return rec


def approval_hash(store: DataStore, post_id: str) -> str:
    """What approval binds: the image file's SHA-256, or 'none'."""
    doc = load(store, post_id)
    if doc is None:
        raise StoreError("no image decision yet")
    if doc["kind"] == NO_IMAGE:
        return NO_IMAGE
    return file_sha256(store.post_dir(post_id) / doc["file"])


def _has_figures(store: DataStore, post_id: str) -> bool:
    from lce.textutil import claim_numbers

    return any(claim_numbers(c.get("text", "")) for c in store.load_post(post_id).get("claims") or [])


def media_view(store: DataStore, post_id: str) -> dict:
    """The post's media decision as the dashboard and reports show it (LCE-038).
    Derived from image.yaml and the actual file; never invented."""
    doc = load(store, post_id)
    if doc is None:
        return {"media_required": None, "media_type": "text", "media_status": "undecided",
                "note": "no media decision recorded"}
    errors, warnings = check(store, post_id)
    if doc["kind"] == NO_IMAGE:
        return {"media_required": False, "media_type": "text", "media_status": "text_only",
                "text_only_reason": doc.get("text_only_reason"), "rationale": doc.get("rationale"),
                "decided_by": doc.get("decided_by"), "decided_at": doc.get("decided_at"),
                "warnings": warnings}
    prov = doc.get("provenance") or {}
    status = "attached" if not errors else (
        "needs_review" if prov.get("usage") == "needs_review" else "invalid")
    return {"media_required": True, "media_type": "image", "kind": doc["kind"], "media_status": status,
            "media_source": prov.get("origin"), "media_source_url": prov.get("source_url"),
            "credit": prov.get("credit"), "license": prov.get("license"), "usage": prov.get("usage"),
            "generation": (prov.get("generation") or {}).get("method"),
            "sha256": doc.get("sha256"), "bytes": doc.get("bytes"), "mime": doc.get("mime"),
            "width": doc.get("width"), "height": doc.get("height"), "alt_text": doc.get("alt_text"),
            "relation": doc.get("relation"), "rationale": doc.get("rationale"),
            "decided_by": doc.get("decided_by"), "decided_at": doc.get("decided_at"),
            "media_relevance": relevance_now(store, post_id, doc),
            "errors": errors, "warnings": warnings}
