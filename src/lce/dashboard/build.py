"""Static demo build for GitHub Pages: fictional data only.

The build never consults $LCE_DATA_DIR or any private directory. It reads only
`examples/demo-dashboard/` from the engine repository and refuses to write its
output inside a data directory.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from lce.config.paths import DATA_MARKER
from lce.dashboard.server import STATIC_FILES, config_js, demo_store, static_file
from lce.dashboard.snapshot import build_snapshot, to_json


def build_demo(engine_root: Path, out: Path) -> list[str]:
    out = out.resolve()
    for parent in (out, *out.parents):
        if (parent / DATA_MARKER).exists():
            raise RuntimeError("refusing to build into a private data directory")
    store = demo_store(engine_root)
    snapshot = build_snapshot(store, mode="demo", data_label="fictional demo data")
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)
    for name in STATIC_FILES:
        (out / name).write_bytes(static_file(name))
    (out / "config.js").write_bytes(config_js("demo", "data/snapshot.json"))
    (out / "data" / "snapshot.json").write_text(to_json(snapshot), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
