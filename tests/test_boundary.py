"""Guards for Phase 1 promises: no publishing, no LLM API, no private data in this repo."""

import re
import subprocess
from pathlib import Path

import pytest
from conftest import ROOT, awaiting_post

from lce.cli import build_parser, main

A = "anthrop" + "ic"


def test_cli_publishing_commands_are_explicit_and_nothing_else():
    sub = next(a for a in build_parser()._actions if a.dest == "command")
    names = set(sub.choices)
    assert {"publish", "linkedin"} <= names
    assert not {n for n in names if re.search(r"schedule|post-now|send|auto-?publish", n)}


def test_no_provider_hosts_or_llm_sdk_in_source():
    files = {p: p.read_text() for p in (ROOT / "src").rglob("*.py")}
    adapter = ROOT / "src" / "lce" / "publish" / "linkedin.py"
    for path, src in files.items():
        if path != adapter:
            assert "api.linkedin.com" not in src, path  # only the official adapter talks to LinkedIn
        for needle in ("linkedin.com/oauth", "publora.com", "buffer.com", "hootsuite", "zapier",
                       "import " + A, "from " + A, "api." + A + ".com", "openai",
                       "playwright", "selenium", "li_at"):
            assert needle not in src, (needle, path)


def test_no_llm_or_provider_dependencies():
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    deps = " ".join(project["dependencies"] + sum(project["optional-dependencies"].values(), []))
    for bad in (A, "openai", "publora", "linkedin", "oauth", "playwright", "selenium", "requests"):
        assert bad not in deps.lower(), bad


def test_publish_package_has_only_the_linkedin_provider():
    files = {p.name for p in (ROOT / "src" / "lce" / "publish").glob("*.py")}
    assert files == {"__init__.py", "base.py", "credentials.py", "linkedin.py", "little.py"}


def _imports(path):
    import ast

    tree = ast.parse(path.read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_only_the_cli_imports_the_publishing_path():
    """Scheduler, jobs, dashboard, research and every other module cannot publish."""
    allowed = {ROOT / "src" / "lce" / "cli.py", ROOT / "src" / "lce" / "publishing.py"}
    for path in (ROOT / "src" / "lce").rglob("*.py"):
        if path in allowed or path.parent.name == "publish":
            continue
        mods = _imports(path)
        assert not {"lce.publishing", "lce.publish.linkedin", "lce.publish.credentials"} & mods, path


def test_no_real_data_files_outside_examples_and_templates():
    tracked = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for rel in tracked:
        if rel.endswith((".yaml", ".yml")) and "story" in rel:
            assert rel.startswith(("examples/", "templates/")), rel


def test_cli_refuses_data_dir_inside_engine(capsys):
    assert main(["--data-dir", str(ROOT / "examples" / "demo-persona"), "status"]) == 1
    assert "inside the public engine repository" in capsys.readouterr().out


def test_cli_approve_refuses_without_tty(store, capsys, monkeypatch):
    pid = awaiting_post(store)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert main(["--data-dir", str(store.root), "approve", pid, "--hash", "0" * 12]) == 1
    assert "interactive terminal" in capsys.readouterr().out


def test_ready_output_says_nothing_published(store, capsys):
    from lce import approval
    from lce.textutil import content_hash

    pid = awaiting_post(store)
    h = content_hash((store.post_dir(pid) / "post.md").read_text())
    approval.approve(store, pid, h[:12], confirm=lambda _: f"APPROVE {pid}", is_tty=lambda: True)
    assert main(["--data-dir", str(store.root), "ready", pid]) == 0
    assert "nothing was published" in capsys.readouterr().out


def test_skills_sync_copies_generic_skills_only(store):
    assert main(["--data-dir", str(store.root), "skills", "sync"]) == 0
    skills = sorted(p.parent.name for p in (store.root / ".claude" / "skills").glob("*/SKILL.md"))
    assert skills == ["lce-create-post", "lce-interview", "lce-research", "lce-run-jobs"]
    for p in (store.root / ".claude" / "skills").glob("*/SKILL.md"):
        text = p.read_text()
        assert "never" in text.lower()


@pytest.mark.parametrize("skill", ["lce-create-post", "lce-interview", "lce-research",
                                   "lce-run-jobs"])
def test_skills_never_instruct_approval_or_publishing(skill):
    text = (ROOT / "src" / "lce" / "claude_skills" / skill / "SKILL.md").read_text()
    assert "lce approve " not in text.replace("never run `lce approve`", "")
    assert not re.search(r"\blce publish\b", text)


def test_demo_data_is_marked_demo():
    for p in (ROOT / "examples").rglob("*.yaml"):
        assert "demo: true" in p.read_text(), p
    assert Path(ROOT / "examples" / "demo-persona" / "README.md").exists()


def test_wrangler_config_has_no_account_identifiers_or_placeholders():
    """The D1 database is resolved by name; Access identifiers are Worker secrets."""
    toml = (ROOT / "cloud" / "wrangler.toml").read_text()
    body = "\n".join(line.split("#", 1)[0] for line in toml.splitlines())
    assert "database_id" not in body and "account_id" not in body
    assert 'database_name = "lce"' in body
    assert "keep_vars = true" in body   # dashboard variables survive Workers Builds deploys
    assert "ACCESS_TEAM_DOMAIN" not in body and "ACCESS_AUD" not in body
    assert not re.search(r"\b[0-9a-f]{32}\b|[0-9a-f]{8}-[0-9a-f]{4}-", body)
