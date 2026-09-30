"""Copy the Web Control Center into the Worker bundle (cloud/src/pipeline/).

The Worker serves the same dashboard at /pipeline/ from the D1 mirror. JS files
are stored as .txt so Wrangler bundles them as text, not as Worker code.
tests/test_cloud_dashboard_sync.py fails when the copies are stale.

    python scripts/sync_cloud_dashboard.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "lce" / "dashboard" / "static"
DEST = ROOT / "cloud" / "src" / "pipeline"
FILES = {"index.html": "index.html", "styles.css": "styles.css", "app.js": "app.txt",
         "lib.js": "lib.txt"}


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    for src, dest in FILES.items():
        (DEST / dest).write_bytes((SRC / src).read_bytes())
    print(f"✓ {len(FILES)} dashboard file(s) copied to {DEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
