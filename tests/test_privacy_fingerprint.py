import subprocess

from conftest import write

from lce.privacy import fingerprint
from lce.privacy.scan import load_denylist, main, scan_history


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


def test_terms_come_from_the_private_data_directory(store):
    s = store.settings()
    s["cadence"] = {"posts_per_week": 2, "slots": [{"day": "mon", "time": "07:05"},
                                                   {"day": "fri", "time": "18:40"}]}
    store.write_doc(store.settings_path, "settings", s)
    terms = fingerprint.derive_terms(store)
    assert {"mon-0705", "fri-1840"} <= set(terms)
    for story_id, story in store.stories().items():
        assert story_id in terms and story["title"] in terms
    assert all(len(t) >= fingerprint.MIN_LEN for t in terms)
    assert len({t.lower() for t in terms}) == len(terms)


def test_post_ids_and_opening_lines(store):
    from conftest import selected_post

    from lce.posts import save_draft

    pid = selected_post(store)
    save_draft(store, pid, "An opening line that is clearly distinctive enough.\n\nBody.\n")
    terms = fingerprint.derive_terms(store)
    assert pid in terms and "An opening line that is clearly distinctive enough." in terms


def test_generated_file_is_private_and_prints_only_counts(store, tmp_path, monkeypatch, capsys):
    out = tmp_path / "priv" / "gen.txt"
    monkeypatch.setenv("LCE_DENYLIST_GENERATED_PATH", str(out))
    from lce.cli import main as cli

    assert cli(["--data-dir", str(store.root), "privacy-denylist"]) == 0
    printed = capsys.readouterr().out
    terms = fingerprint.derive_terms(store)
    assert f"{len(terms)} term(s)" in printed
    assert not any(t in printed for t in terms)
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    assert load_denylist(str(tmp_path / "none.txt"), include_generated=True) == terms


def test_post_qa_never_uses_the_generated_list(tmp_path, monkeypatch):
    gen = tmp_path / "gen.txt"
    gen.write_text("derived-term\n")
    monkeypatch.setenv("LCE_DENYLIST_GENERATED_PATH", str(gen))
    manual = tmp_path / "manual.txt"
    manual.write_text("manual-term\n")
    assert load_denylist(str(manual)) == ["manual-term"]
    assert load_denylist(str(manual), include_generated=True) == ["manual-term", "derived-term"]


def test_history_scan_finds_terms_in_old_commits_and_messages(git_repo, tmp_path, monkeypatch, capsys):
    write(git_repo, "a.txt", "contains wed-1405 here\n")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "first")
    write(git_repo, "a.txt", "clean now\n")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "mention sat-2210 in message")
    hits = scan_history(git_repo, ["wed-1405", "sat-2210"])
    assert sorted((p, n) for _, p, n in hits) == [("<message>", 1), ("a.txt", 1)]
    assert scan_history(git_repo, []) == []

    gen = git_repo / ".git" / "gen.txt"          # outside the scanned working tree
    gen.write_text("wed-1405\n")
    monkeypatch.setenv("LCE_DENYLIST_GENERATED_PATH", str(gen))
    monkeypatch.setenv("LCE_DENYLIST_PATH", str(git_repo / ".git" / "none.txt"))
    assert main(["--root", str(git_repo)]) == 0            # working tree is clean
    assert main(["--root", str(git_repo), "--history"]) == 1
    out = capsys.readouterr().out
    assert "wed-1405" not in out and "history scan" in out
