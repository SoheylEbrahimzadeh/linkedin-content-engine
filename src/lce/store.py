"""File-based storage for the private data directory.

Every write is validated against its schema and written atomically. All paths
are derived from validated identifiers, so nothing is written outside the data
directory.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

from lce.config.paths import DATA_MARKER, check_location, resolve_data_dir
from lce.validate import jsonable, validate_doc

POST_ID_RE = re.compile(r"^\d{8}-[a-z0-9-]{1,56}$")
STORY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
CANDIDATE_ID_RE = re.compile(r"^c-[a-z0-9-]{4,64}$")

DEFAULT_SETTINGS: dict = {
    "approval": {"mode": "local", "expire_unapproved": True},
    "publisher": {"provider": "none"},
    "llm": {"runtime": "claude_code"},
}
DEFAULT_TUNING: dict = {
    "research": {"feeds": [], "max_items_per_feed": 10, "timeout_seconds": 10},
    "duplicates": {
        "shingle_size": 3,
        "near_duplicate_jaccard": 0.5,
        "near_duplicate_containment": 0.7,
        "similar_warn_jaccard": 0.3,
        "story_reuse_window_days": 30,
        "story_max_uses": 3,
        "angle_window_days": 21,
        "topic_window_days": 14,
        "topic_similarity": 0.6,
    },
}
SKELETON_DIRS = (
    "config", "profile", "story_bank/stories", "research/candidates", "plan", "posts",
    "runs", "history/external", "interview",
)


class StoreError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def dump_yaml(doc: dict) -> str:
    return yaml.safe_dump(jsonable(doc), sort_keys=False, allow_unicode=True, width=100)


class DataStore:
    def __init__(self, root: Path):
        self.root = root

    @classmethod
    def open(cls, value: str | None = None) -> DataStore:
        return cls(resolve_data_dir(value))

    @classmethod
    def init(cls, path: Path) -> DataStore:
        """Create the data-directory skeleton (idempotent, never overwrites files)."""
        path = path.expanduser()
        if not path.is_absolute():
            raise StoreError("the data directory must be an absolute path")
        existing = next((a for a in (path, *path.parents) if a.exists()), path)
        check_location(existing)  # refuse before creating anything inside the engine repo
        path.mkdir(parents=True, exist_ok=True)
        root = check_location(path)
        for d in SKELETON_DIRS:
            (root / d).mkdir(parents=True, exist_ok=True)
            keep = root / d / ".gitkeep"
            if not any((root / d).iterdir()):
                keep.touch()
        marker = root / DATA_MARKER
        if not marker.exists():
            marker.write_text("# Marker: root of a private lce data directory.\n", "utf-8")
        store = cls(root)
        if not store.settings_path.exists():
            store.write_doc(store.settings_path, "settings", DEFAULT_SETTINGS)
        return store

    # ── paths ──────────────────────────────────────────────────────────
    @property
    def settings_path(self) -> Path:
        return self.root / "config" / "settings.yaml"

    @property
    def profile_path(self) -> Path:
        return self.root / "profile" / "profile.yaml"

    @property
    def voice_path(self) -> Path:
        return self.root / "profile" / "voice.yaml"

    @property
    def plan_path(self) -> Path:
        return self.root / "plan" / "calendar.yaml"

    def story_path(self, story_id: str) -> Path:
        if not STORY_ID_RE.match(story_id):
            raise StoreError(f"invalid story id {story_id!r}")
        return self.root / "story_bank" / "stories" / f"{story_id}.yaml"

    def candidate_path(self, candidate_id: str) -> Path:
        if not CANDIDATE_ID_RE.match(candidate_id):
            raise StoreError(f"invalid candidate id {candidate_id!r}")
        return self.root / "research" / "candidates" / f"{candidate_id}.yaml"

    def post_dir(self, post_id: str) -> Path:
        if not POST_ID_RE.match(post_id):
            raise StoreError(f"invalid post id {post_id!r}")
        return self.root / "posts" / post_id

    # ── generic documents ──────────────────────────────────────────────
    def read_doc(self, path: Path, default: dict | None = None) -> dict:
        if not path.exists():
            return copy.deepcopy(default) if default is not None else {}
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            raise StoreError(f"{path.relative_to(self.root)} is not a mapping")
        return jsonable(doc)

    def write_doc(self, path: Path, kind: str, doc: dict) -> None:
        errors = validate_doc(kind, doc)
        if errors:
            raise StoreError(f"{kind} is invalid: " + "; ".join(errors))
        _atomic_write(path, dump_yaml(doc))

    def write_text(self, path: Path, text: str) -> None:
        _atomic_write(path, text)

    # ── typed accessors ────────────────────────────────────────────────
    def settings(self) -> dict:
        return self.read_doc(self.settings_path, DEFAULT_SETTINGS)

    def tuning(self) -> dict:
        """Settings merged over built-in defaults for research and duplicate checks."""
        return _merge(DEFAULT_TUNING, self.settings())

    def profile(self) -> dict:
        return self.read_doc(self.profile_path)

    def voice(self) -> dict:
        return self.read_doc(self.voice_path)

    def plan(self) -> dict:
        return self.read_doc(self.plan_path, {"entries": []})

    def stories(self) -> dict[str, dict]:
        out = {}
        for p in sorted((self.root / "story_bank" / "stories").glob("*.yaml")):
            doc = self.read_doc(p)
            out[doc.get("story_id", p.stem)] = doc
        return out

    def candidates(self) -> dict[str, dict]:
        out = {}
        for p in sorted((self.root / "research" / "candidates").glob("*.yaml")):
            doc = self.read_doc(p)
            out[doc.get("candidate_id", p.stem)] = doc
        return out

    def external_posts(self) -> dict[str, str]:
        """Previously published posts imported as plain text (for duplicate checks)."""
        folder = self.root / "history" / "external"
        return {p.stem: p.read_text("utf-8") for p in sorted(folder.glob("*.md"))}

    # ── posts ──────────────────────────────────────────────────────────
    def post_ids(self) -> list[str]:
        folder = self.root / "posts"
        return sorted(p.parent.name for p in folder.glob("*/post.yaml"))

    def load_post(self, post_id: str) -> dict:
        path = self.post_dir(post_id) / "post.yaml"
        if not path.exists():
            raise StoreError(f"post {post_id} does not exist")
        return self.read_doc(path)

    def save_post(self, post: dict) -> None:
        self.write_doc(self.post_dir(post["post_id"]) / "post.yaml", "post", post)

    def post_text(self, post_id: str, name: str = "post.md") -> str | None:
        path = self.post_dir(post_id) / name
        return path.read_text("utf-8") if path.exists() else None

    # ── run history ────────────────────────────────────────────────────
    def log_event(self, event: str, **fields: object) -> dict:
        record = {"at": now_iso(), "event": event, **fields}
        path = self.root / "runs" / f"{record['at'][:7]}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(jsonable(record), ensure_ascii=False) + "\n")
        return record
