import pytest

from lce.config.paths import DataDirError
from lce.store import DataStore, StoreError


def test_init_creates_skeleton_and_defaults(tmp_path):
    store = DataStore.init(tmp_path / "d")
    assert (store.root / ".lce-data-root").exists()
    assert store.settings()["publisher"]["provider"] == "none"
    assert (store.root / "story_bank" / "stories").is_dir()
    DataStore.init(tmp_path / "d")  # idempotent


def test_init_refuses_engine_repo(tmp_path):
    (tmp_path / ".lce-engine-root").write_text("")
    (tmp_path / "x").mkdir()
    with pytest.raises(DataDirError):
        DataStore.init(tmp_path / "x" / "new")
    assert not (tmp_path / "x" / "new").exists()


def test_invalid_write_leaves_file_untouched(store):
    before = store.settings_path.read_text()
    with pytest.raises(StoreError):
        store.write_doc(store.settings_path, "settings", {"publisher": {"provider": "x"}})
    assert store.settings_path.read_text() == before


def test_ids_cannot_escape_data_dir(store):
    for bad in ("../x", "20250101-../../etc", "a/b"):
        with pytest.raises(StoreError):
            store.post_dir(bad)
        with pytest.raises(StoreError):
            store.story_path(bad)


def test_run_log(store):
    rec = store.log_event("test", x=1)
    files = list((store.root / "runs").glob("*.jsonl"))
    assert files and rec["event"] == "test"
