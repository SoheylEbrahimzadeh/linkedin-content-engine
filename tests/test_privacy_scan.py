from pathlib import Path

from conftest import write

from lce.privacy.scan import load_denylist, scan

# Sensitive-looking strings are assembled at runtime so this file stays clean.
A = "anthrop" + "ic"


def rules(root: Path, denylist=None) -> set[str]:
    return {f.rule for f in scan(root, denylist=denylist or [])}


def test_clean_repo(git_repo):
    write(git_repo, "src/app.py", "print('hello')\n")
    assert rules(git_repo) == set()


def test_private_paths_and_env(git_repo):
    write(git_repo, "story_bank/facts/x.yaml", "title: x\n")
    write(git_repo, ".env", "X=1\n")
    write(git_repo, ".env.example", "X=\n")
    found = scan(git_repo, denylist=[])
    assert {(f.path, f.rule) for f in found} >= {
        ("story_bank/facts/x.yaml", "private-path"),
        (".env", "env-file"),
    }
    assert not any(f.path == ".env.example" for f in found)


def test_personal_data_outside_examples(git_repo):
    write(git_repo, "config/me.yaml", "fact_id: my-fact\npublication_status: PRIVATE\n")
    assert "personal-data" in rules(git_repo)


def test_examples_require_demo_marker(git_repo):
    write(git_repo, "examples/p/story_bank/facts/a.yaml", "fact_id: a-fact\n")
    assert "demo-marker" in rules(git_repo)
    write(git_repo, "examples/p/story_bank/facts/a.yaml", "demo: true\nfact_id: a-fact\n")
    assert "demo-marker" not in rules(git_repo)


def test_email_rules(git_repo):
    write(git_repo, "a.md", "contact: someone@" + "realmail.com\n")
    assert "email" in rules(git_repo)
    write(git_repo, "a.md", "demo@example.com and 1+x@users.noreply.github.com\n")
    assert "email" not in rules(git_repo)


def test_phone(git_repo):
    write(git_repo, "a.md", "call +" + "49 170 1234567\n")
    assert "phone" in rules(git_repo)


def test_secret_shapes(git_repo):
    write(git_repo, "a.md", "token sk-ant-" + "abcdefghijklmnop\n")
    assert "secret-shape" in rules(git_repo)


def test_anthropic_api_blocked_in_code_and_config(git_repo):
    write(git_repo, "src/x.py", "import " + A + "\n")
    assert "anthropic-api" in rules(git_repo)
    write(git_repo, "src/x.py", "k = '" + A.upper() + "_API_KEY'\n")
    assert "anthropic-api" in rules(git_repo)
    write(git_repo, "src/x.py", "pass\n")
    write(git_repo, "pyproject.toml", 'dependencies = ["' + A + '>=1.0"]\n')
    assert "anthropic-api" in rules(git_repo)


def test_anthropic_mention_in_docs_is_fine(git_repo):
    write(git_repo, "README.md", "We do not use the " + A.upper() + "_API_KEY.\n")
    assert "anthropic-api" not in rules(git_repo)


def test_denylist_and_allow_marker(git_repo):
    write(git_repo, "a.md", "worked at Acme Private Corp\n")
    assert "denylist" in rules(git_repo, denylist=["Acme Private Corp"])
    write(git_repo, "a.md", "worked at acme private corp  <!-- lce-privacy: allow -->\n")
    assert "denylist" not in rules(git_repo, denylist=["Acme Private Corp"])


def test_denylist_file(tmp_path):
    p = tmp_path / "deny.txt"
    p.write_text("# comment\n\nab\nAcme Corp\n", encoding="utf-8")
    assert load_denylist(str(p)) == ["Acme Corp"]


def test_findings_do_not_leak_values(git_repo):
    secret = "Acme Private Corp"
    write(git_repo, "a.md", f"{secret}\n")
    for f in scan(git_repo, denylist=[secret]):
        assert secret not in f.render()


def test_this_repository_is_clean():
    root = Path(__file__).resolve().parents[1]
    self_rel = "src/lce/privacy/scan.py"
    found = scan(root, denylist=[], self_path=self_rel)
    assert found == [], [f.render() for f in found]
