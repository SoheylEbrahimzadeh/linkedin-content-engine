import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_committed_vectors_match_the_python_reference(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mk", ROOT / "scripts" / "make_cloud_vectors.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    monkeypatch.setattr(mk, "OUT", tmp_path / "vectors.json")
    mk.main()
    fresh = json.loads((tmp_path / "vectors.json").read_text())
    committed = json.loads((ROOT / "cloud" / "test" / "vectors.json").read_text())
    assert fresh == committed, "run `python scripts/make_cloud_vectors.py`"


def test_worker_never_contains_credentials_or_llm_or_scrapers():
    src = "\\n".join(p.read_text() for p in (ROOT / "cloud" / "src").glob("*.ts"))
    for needle in ("anthrop" + "ic", "openai", "playwright", "puppeteer", "selenium", "li_at",
                   "publora", "buffer.com", "zapier"):
        assert needle not in src.lower(), needle
    toml = (ROOT / "cloud" / "wrangler.toml").read_text()
    config = "\n".join(line.split("#", 1)[0] for line in toml.splitlines())
    assert "database_id" not in config  # resolved by name; never an account identifier
    assert "LINKEDIN_TOKEN =" not in toml and "api_token" not in toml.lower()


def test_kill_switch_defaults_off_in_the_migration():
    sql = (ROOT / "cloud" / "migrations" / "0001_init.sql").read_text()
    assert "('auto_publish', 'false'" in sql and "('provider', 'none'" in sql
