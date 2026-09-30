"""The Worker's copy of the Web Control Center must match the source."""

import importlib.util

from conftest import ROOT


def test_worker_dashboard_copy_is_current():
    spec = importlib.util.spec_from_file_location("sync", ROOT / "scripts" / "sync_cloud_dashboard.py")
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    for src, dest in sync.FILES.items():
        assert (sync.DEST / dest).read_bytes() == (sync.SRC / src).read_bytes(), (
            f"{dest} is stale: run python scripts/sync_cloud_dashboard.py")
