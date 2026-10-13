"""LCE-062: a Cloudflare Preview deployment can never reach production data or publish.

`wrangler preview` takes bindings and variables only from the `[previews]` block of cloud/wrangler.toml
(Wrangler 4.x: nothing is inherited from the top level). These checks keep that block safe: no production
database, no credentials, and the explicit LCE_PREVIEW guard; and they keep the flag out of production.
"""

import re
import tomllib
from pathlib import Path

import pytest

CFG = tomllib.loads((Path(__file__).resolve().parents[1] / "cloud" / "wrangler.toml").read_text("utf-8"))
PREVIEWS = CFG.get("previews")
CREDENTIAL = re.compile(r"TOKEN|SECRET|PASSWORD|PRIVATE|CREDENTIAL|API_?KEY|CLIENT_ID", re.I)


def test_preview_block_exists_and_sets_the_explicit_guard():
    assert isinstance(PREVIEWS, dict), "a [previews] block must exist (Wrangler needs it for Previews)"
    assert PREVIEWS.get("vars", {}).get("LCE_PREVIEW") == "true"


def test_production_never_carries_the_preview_flag():
    assert "LCE_PREVIEW" not in CFG.get("vars", {})
    for name, env in (CFG.get("env") or {}).items():
        assert "LCE_PREVIEW" not in (env.get("vars") or {}), name


def test_preview_never_binds_the_production_database():
    prod = CFG["d1_databases"]
    prod_names = {d.get("database_name") for d in prod}
    prod_ids = {d.get("database_id") for d in prod if d.get("database_id")}
    for d in PREVIEWS.get("d1_databases", []):
        name = d.get("database_name")
        assert name not in prod_names, f"Preview binds the production database {name}"
        assert not d.get("database_id") or d["database_id"] not in prod_ids
        assert d.get("database_name") or d.get("database_id"), "a Preview database must be named explicitly"


@pytest.mark.parametrize(
    "key", ["secrets_store_secrets", "unsafe", "services", "dispatch_namespaces", "mtls_certificates"]
)
def test_preview_has_no_secret_or_cross_service_access(key):
    assert not PREVIEWS.get(key), f"[previews].{key} must stay empty"


def test_preview_declares_no_production_secret_and_no_credential_variable():
    required = (PREVIEWS.get("secrets") or {}).get("required") or []
    assert "LINKEDIN_TOKEN" not in required and not any(CREDENTIAL.search(n) for n in required)
    for name, value in (PREVIEWS.get("vars") or {}).items():
        assert not CREDENTIAL.search(name), f"[previews.vars] must not carry credentials ({name})"
        assert "linkedin" not in str(value).lower() or name == "LCE_PREVIEW"


def test_preview_runs_no_cron():
    assert "triggers" not in PREVIEWS and "crons" not in PREVIEWS
