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
           provenance: dict | None = None, decided_by: str = "agent") -> dict:
    """Record the decision; copies the image into the post folder as image.<ext>."""
    from lce.posts import reopen
    from lce.state import PostState as S

    state = S(store.load_post(post_id)["state"])
    if kind not in KINDS:
        raise StoreError(f"kind must be one of {', '.join(KINDS)}")
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
                    "relation": relation.strip(), "alt_text": alt_text.strip(),
                    "provenance": provenance or {}})
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
    return errors, warnings


def approval_hash(store: DataStore, post_id: str) -> str:
    """What approval binds: the image file's SHA-256, or 'none'."""
    doc = load(store, post_id)
    if doc is None:
        raise StoreError("no image decision yet")
    if doc["kind"] == NO_IMAGE:
        return NO_IMAGE
    return file_sha256(store.post_dir(post_id) / doc["file"])
