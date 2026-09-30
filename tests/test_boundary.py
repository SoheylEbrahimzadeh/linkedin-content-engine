"""Guards for Phase 1 promises: no publishing, no LLM API, no private data in this repo."""

import re
import subprocess
from pathlib import Path

import pytest
from conftest import ROOT, awaiting_post

from lce.cli import build_parser, main

A = "anthrop" + "ic"


def test_cli_has_no_publish_command():
    sub = next(a for a in build_parser()._actions if a.dest == "command")
    names = set(sub.choices)
    assert not {n for n in names if re.search(r"publish|post-now|send|schedule", n)}


def test_no_provider_hosts_or_llm_sdk_in_source():
    src = "\n".join(p.read_text() for p in (ROOT / "src").rglob("*.py"))
    for needle in ("api.linkedin.com", "linkedin.com/oauth", "publora.com", "import " + A,
                   "from " + A, "api." + A + ".com"):
        assert needle not in src, needle


def test_no_llm_or_provider_dependencies():
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    deps = " ".join(project["dependencies"] + sum(project["optional-dependencies"].values(), []))
    for bad in (A, "openai", "publora", "linkedin", "oauth", "playwright", "selenium", "requests"):
        assert bad not in deps.lower(), bad


def test_publish_package_has_only_the_interface():
    files = {p.name for p in (ROOT / "src" / "lce" / "publish").glob("*.py")}
    assert files == {"__init__.py", "base.py"}


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
