import pytest

from lce.config.paths import ENGINE_MARKER, DataDirError, resolve_data_dir


def test_unset(monkeypatch):
    monkeypatch.delenv("LCE_DATA_DIR", raising=False)
    with pytest.raises(DataDirError):
        resolve_data_dir()


def test_relative_rejected():
    with pytest.raises(DataDirError):
        resolve_data_dir("relative/path")


def test_missing_rejected(tmp_path):
    with pytest.raises(DataDirError):
        resolve_data_dir(str(tmp_path / "nope"))


def test_inside_engine_repo_rejected(tmp_path):
    (tmp_path / ENGINE_MARKER).write_text("")
    inside = tmp_path / "private"
    inside.mkdir()
    with pytest.raises(DataDirError):
        resolve_data_dir(str(inside))


def test_outside_ok(tmp_path):
    engine = tmp_path / "engine"
    data = tmp_path / "data"
    engine.mkdir()
    data.mkdir()
    (engine / ENGINE_MARKER).write_text("")
    assert resolve_data_dir(str(data)) == data.resolve()
