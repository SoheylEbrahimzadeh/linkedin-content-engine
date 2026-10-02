"""Post versions (LCE-041): every refresh keeps the version it replaces.

`posts/<id>/versions/vN/` holds an exact copy of the post package as it was
(text, metadata, image file and decision, approval artifact, QA and duplicate
reports) plus `version.yaml` (hashes, hook, media concept, approval state,
reason). Versions are never overwritten or deleted by the engine; git keeps
them too. A version can be restored; restoring discards any approval (the
owner approves the restored text and image again).
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from lce import images
from lce.store import DataStore, StoreError, now_iso
from lce.textutil import content_hash

FILES = ("post.md", "draft.md", "post.yaml", "image.yaml", "APPROVAL.md", "qa.json", "duplicate.json")


def folder(store: DataStore, post_id: str) -> Path:
    return store.post_dir(post_id) / "versions"


def listing(store: DataStore, post_id: str) -> list[dict]:
    root = folder(store, post_id)
    if not root.exists():
        return []
    out = [store.read_doc(p) for p in root.glob("v*/version.yaml")]
    return sorted(out, key=lambda v: v["version"])


def _hook(text: str) -> str | None:
    line = next((x.strip() for x in text.splitlines() if x.strip()), None)
    return line[:200] if line else None


def snapshot(store: DataStore, post_id: str, *, reason: str, by: str) -> dict:
    """Copy the current package into versions/vN and return its manifest."""
    src = store.post_dir(post_id)
    text_path = src / "post.md"
    if not text_path.exists():
        raise StoreError("the post has no text yet; nothing to keep as a version")
    n = (listing(store, post_id)[-1]["version"] if listing(store, post_id) else 0) + 1
    dest = folder(store, post_id) / f"v{n}"
    dest.mkdir(parents=True, exist_ok=False)
    copied = []
    for name in FILES:
        if (src / name).exists():
            shutil.copy2(src / name, dest / name)
            copied.append(name)
    doc = images.load(store, post_id)
    image_file = None
    if doc and doc.get("file") and (src / doc["file"]).exists():
        shutil.copy2(src / doc["file"], dest / doc["file"])
        copied.append(doc["file"])
        image_file = doc["file"]
    post = store.load_post(post_id)
    text = text_path.read_text(encoding="utf-8")
    rel = (doc or {}).get("media_relevance") or {}
    approval = src / "APPROVAL.md"
    manifest = {
        "version": n,
        "created_at": now_iso(),
        "by": by,
        "reason": reason,
        "state": post["state"],
        "content_hash": content_hash(text),
        "hook": _hook(text),
        "image_sha256": (doc or {}).get("sha256"),
        "image_file": image_file,
        "media": (
            {
                "kind": doc["kind"],
                "concept": rel.get("concept"),
                "visual_type": rel.get("visual_type"),
                "media_decision": rel.get("media_decision"),
                "alt_text": doc.get("alt_text"),
                "text_only_reason": doc.get("text_only_reason"),
            }
            if doc
            else None
        ),
        "approval_state": (post.get("approval") or {}).get("state"),
        "approval_artifact_sha256": hashlib.sha256(approval.read_bytes()).hexdigest()
        if approval.exists()
        else None,
        "sources": [
            {"url": s["url"], **({"title": s["title"]} if s.get("title") else {})}
            for s in post.get("sources") or []
        ],
        "files": copied,
    }
    store.write_doc(dest / "version.yaml", "version", manifest)
    store.log_event("post.version_saved", post_id=post_id, version=n, reason=reason)
    return manifest


def _clear_package(store: DataStore, post_id: str) -> None:
    src = store.post_dir(post_id)
    for name in FILES:
        if name != "post.yaml" and (src / name).exists():
            (src / name).unlink()
    for old in src.glob("image.*"):
        old.unlink()


def rollback(store: DataStore, post_id: str, version: int) -> None:
    """Exact revert to version N (used when a refresh fails half-way); removes vN."""
    from lce.posts import sync_plan

    src = folder(store, post_id) / f"v{version}"
    manifest = store.read_doc(src / "version.yaml")
    _clear_package(store, post_id)
    for name in manifest["files"]:
        shutil.copy2(src / name, store.post_dir(post_id) / name)
    shutil.rmtree(src)
    sync_plan(store, store.load_post(post_id))
    store.log_event("post.refresh_rolled_back", post_id=post_id, version=version)


def restore(store: DataStore, post_id: str, version: int, *, by: str) -> dict:
    """Bring back the text and image of version N as the current candidate. The
    current package is kept as a new version first; the approval is discarded and
    the restored version goes through QA, duplicate check and approval again."""
    from lce.posts import reopen, save_humanized
    from lce.state import PostState as S

    src = folder(store, post_id) / f"v{version}"
    if not (src / "version.yaml").exists():
        raise StoreError(f"no version {version} for {post_id}")
    manifest = store.read_doc(src / "version.yaml")
    snapshot(store, post_id, reason=f"before restoring v{version}", by=by)
    post = store.load_post(post_id)
    if S(post["state"]) in {S.AWAITING_APPROVAL, S.APPROVED, S.READY_TO_PUBLISH}:
        reopen(store, post_id, f"restoring v{version}")
    dst = store.post_dir(post_id)
    for old in dst.glob("image.*"):
        old.unlink()
    for name in manifest["files"]:
        if name.startswith("image."):
            shutil.copy2(src / name, dst / name)
    text = (src / "post.md").read_text(encoding="utf-8")
    save_humanized(store, post_id, text, source="session", by=by)
    store.log_event("post.version_restored", post_id=post_id, version=version)
    return manifest
