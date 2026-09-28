import pytest

from lce.config.paths import DATA_MARKER, ENGINE_MARKER, DataDirError, resolve_data_dir


def _data(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / DATA_MARKER).write_text("")
    return path


def test_unset_and_no_marker_in_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("LCE_DATA_DIR", raising=False)
    with pytest.raises(DataDirError):
        resolve_data_dir(cwd=tmp_path)


def test_cwd_with_marker(monkeypatch, tmp_path):
    monkeypatch.delenv("LCE_DATA_DIR", raising=False)
    d = _data(tmp_path / "d")
    assert resolve_data_dir(cwd=d) == d.resolve()


def test_relative_rejected():
    with pytest.raises(DataDirError):
        resolve_data_dir("relative/path")


def test_missing_rejected(tmp_path):
    with pytest.raises(DataDirError):
        resolve_data_dir(str(tmp_path / "nope"))


def test_marker_required(tmp_path):
    (tmp_path / "plain").mkdir()
    with pytest.raises(DataDirError):
        resolve_data_dir(str(tmp_path / "plain"))


def test_inside_engine_repo_rejected(tmp_path):
    (tmp_path / ENGINE_MARKER).write_text("")
    inside = _data(tmp_path / "private")
    with pytest.raises(DataDirError):
        resolve_data_dir(str(inside))


def test_outside_ok(tmp_path):
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / ENGINE_MARKER).write_text("")
    data = _data(tmp_path / "data")
    assert resolve_data_dir(str(data)) == data.resolve()


def test_demo_persona_cannot_be_used_in_place():
    from pathlib import Path

    demo = Path(__file__).resolve().parents[1] / "examples" / "demo-persona"
    with pytest.raises(DataDirError):
        resolve_data_dir(str(demo))
