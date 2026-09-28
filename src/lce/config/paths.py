"""Resolve the private data directory and enforce the public/private boundary.

Personal data (profile, story bank, drafts, history) must live in a directory
outside the public engine repository. This module refuses to hand out a data
directory that sits inside the engine repository, which is detected by the
`.lce-engine-root` marker file at the repository root.
"""

from __future__ import annotations

import os
from pathlib import Path

ENGINE_MARKER = ".lce-engine-root"
DATA_DIR_ENV = "LCE_DATA_DIR"


class DataDirError(RuntimeError):
    pass


def find_engine_root(start: Path) -> Path | None:
    """Return the nearest ancestor of `start` (inclusive) holding the engine marker."""
    start = start.resolve()
    for candidate in (start, *start.parents):
        if (candidate / ENGINE_MARKER).is_file():
            return candidate
    return None


def resolve_data_dir(value: str | None = None) -> Path:
    """Return the validated private data directory.

    `value` defaults to the LCE_DATA_DIR environment variable.
    """
    raw = value if value is not None else os.environ.get(DATA_DIR_ENV, "")
    if not raw.strip():
        raise DataDirError(f"{DATA_DIR_ENV} is not set; point it at your private data directory")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise DataDirError(f"{DATA_DIR_ENV} must be an absolute path")
    if not path.is_dir():
        raise DataDirError(f"{DATA_DIR_ENV} does not exist or is not a directory")
    engine_root = find_engine_root(path)
    if engine_root is not None:
        raise DataDirError(
            f"{DATA_DIR_ENV} points inside the public engine repository ({engine_root}); "
            "private data must live outside it"
        )
    return path.resolve()
