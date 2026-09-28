"""Resolve the private data directory and enforce the public/private boundary.

Personal data (profile, story bank, drafts, history) must live in a directory
outside the public engine repository. The engine refuses a data directory that
sits inside the engine repository (detected by the `.lce-engine-root` marker)
and requires the `.lce-data-root` marker, so it never writes into an arbitrary
folder.
"""

from __future__ import annotations

import os
from pathlib import Path

ENGINE_MARKER = ".lce-engine-root"
DATA_MARKER = ".lce-data-root"
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


def check_location(path: Path) -> Path:
    """Validate a directory that is about to hold private data (marker not required)."""
    if not path.is_absolute():
        raise DataDirError("the data directory must be an absolute path")
    if not path.is_dir():
        raise DataDirError("the data directory does not exist or is not a directory")
    engine_root = find_engine_root(path)
    if engine_root is not None:
        raise DataDirError(
            f"the data directory points inside the public engine repository ({engine_root}); "
            "private data must live outside it"
        )
    return path.resolve()


def resolve_data_dir(value: str | None = None, cwd: Path | None = None) -> Path:
    """Return the validated private data directory.

    Resolution order: explicit `value`, then $LCE_DATA_DIR, then the current
    directory if it carries the data marker.
    """
    raw = value if value is not None else os.environ.get(DATA_DIR_ENV, "")
    if raw.strip():
        path = Path(raw).expanduser()
    else:
        here = (cwd or Path.cwd()).resolve()
        if not (here / DATA_MARKER).is_file():
            raise DataDirError(
                f"{DATA_DIR_ENV} is not set and the current directory is not a data directory"
            )
        path = here
    path = check_location(path)
    if not (path / DATA_MARKER).is_file():
        raise DataDirError(f"{path} is not an initialized data directory (missing {DATA_MARKER})")
    return path
