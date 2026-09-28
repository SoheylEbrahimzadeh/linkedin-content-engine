"""Privacy scanner for the public engine repository.

Runs in pre-commit, pre-push and CI. It complements gitleaks (which looks for
credentials) with project-specific rules:

- private-path:    files under directories reserved for private runtime data
- env-file:        real .env files
- personal-data:   story-bank / profile data outside examples/, templates/, tests/
- demo-marker:     data files under examples/ that are not marked `demo: true`
- email:           e-mail addresses other than documentation/noreply domains
- phone:           international phone numbers
- secret-shape:    common credential prefixes (defence in depth for gitleaks)
- anthropic-api:   any use of the Anthropic API SDK or API-key configuration
- denylist:        terms from a local, never-committed denylist file

Findings never print the matched value, only the file, line and rule.
Suppress a single line with the comment `lce-privacy: allow` (reviewed in PRs).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ALLOW_MARKER = "lce-privacy: allow"

PRIVATE_DIRS = (
    "private",
    "data",
    "profile",
    "story_bank",
    "plan",
    "research",
    "posts",
    "drafts",
    "runs",
    "history",
    "analytics",
)
DATA_ALLOWED_PREFIXES = ("examples/", "templates/", "tests/")
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".jsonl", ".cfg",
    ".ini", ".sh", ".example", ".gitignore", "",
}
ALLOWED_EMAIL_DOMAINS = re.compile(
    r"@(?:[\w.-]+\.)?(?:example\.(?:com|org|net)|users\.noreply\.github\.com)$"
    r"|@[\w.-]+\.(?:invalid|test|example)$",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w.])\+\d{1,3}[\s-]?\(?\d{1,4}\)?(?:[\s-]?\d{2,4}){2,4}(?![\w.])")
SECRET_SHAPES = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{10,}"
    r"|\bghp_[A-Za-z0-9]{30,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{30,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
# Built from fragments so this file does not flag itself.
_A = "anthrop" + "ic"
ANTHROPIC_CODE_RE = re.compile(
    rf"^\s*(?:import\s+{_A}\b|from\s+{_A}\b)"
    rf"|\bAsync{_A.capitalize()}\(|\b{_A.capitalize()}\(",
    re.MULTILINE,
)
ANTHROPIC_CONFIG_RE = re.compile(
    rf"[\"']{_A}(?:[<>=!~\[\s\"']|$)"  # dependency entry, e.g. "anthropic>=1.0"
    rf"|{_A.upper()}_API_KEY"
    rf"|{_A}_api_key"
    r"|claude_code_oauth_" + r"token",
    re.IGNORECASE,
)
DATA_KEYS = {"fact_id", "publication_status", "allowed_claims", "story_bank"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    message: str

    def render(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        return f"  [{self.rule}] {loc} — {self.message}"


def repo_root(start: Path) -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start, capture_output=True, text=True, check=True,
    )
    return Path(out.stdout.strip())


def candidate_files(root: Path) -> list[str]:
    """Tracked files plus untracked files that are not ignored."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, capture_output=True, check=True,
    )
    return sorted({p for p in out.stdout.decode().split("\0") if p})


def load_denylist(path: str | None = None) -> list[str]:
    p = Path(path or os.environ.get("LCE_DENYLIST_PATH") or "~/.lce-private/denylist.txt")
    p = p.expanduser()
    if not p.is_file():
        return []
    terms = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        term = raw.strip()
        if term and not term.startswith("#") and len(term) >= 3:
            terms.append(term)
    return terms


def _is_text(rel: str) -> bool:
    name = Path(rel).name
    return Path(rel).suffix.lower() in TEXT_SUFFIXES or name.startswith(".")


def _check_path(rel: str) -> list[Finding]:
    parts = Path(rel).parts
    findings = []
    if parts and parts[0] in PRIVATE_DIRS:
        findings.append(Finding(rel, 0, "private-path", "path is reserved for private data"))
    name = Path(rel).name
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        findings.append(Finding(rel, 0, "env-file", "environment files must not be committed"))
    return findings


def _check_data_file(rel: str, text: str) -> list[Finding]:
    if Path(rel).suffix.lower() not in {".yaml", ".yml", ".json"}:
        return []
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if not DATA_KEYS & set(doc):
            if rel.startswith("examples/") and doc and doc.get("demo") is not True:
                return [Finding(rel, 0, "demo-marker", "example data must set `demo: true`")]
            continue
        if not rel.startswith(DATA_ALLOWED_PREFIXES):
            return [Finding(rel, 0, "personal-data",
                            "story-bank/profile data belongs in the private data directory")]
        if rel.startswith("examples/") and doc.get("demo") is not True:
            return [Finding(rel, 0, "demo-marker", "example data must set `demo: true`")]
    return []


def _check_lines(rel: str, text: str, denylist: list[str]) -> list[Finding]:
    findings = []
    is_code = rel.endswith(".py")
    is_config = (
        rel == "pyproject.toml"
        or Path(rel).name.startswith("requirements")
        or rel.startswith(".github/workflows/")
    )
    deny_res = [re.compile(rf"(?<!\w){re.escape(t)}(?!\w)", re.IGNORECASE) for t in denylist]
    for no, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for email in EMAIL_RE.findall(line):
            if not ALLOWED_EMAIL_DOMAINS.search(email):
                findings.append(Finding(rel, no, "email", "e-mail address outside allowed domains"))
                break
        if PHONE_RE.search(line):
            findings.append(Finding(rel, no, "phone", "looks like a phone number"))
        if SECRET_SHAPES.search(line):
            findings.append(Finding(rel, no, "secret-shape", "looks like a credential"))
        if (is_code and ANTHROPIC_CODE_RE.search(line)) or (
            (is_code or is_config) and ANTHROPIC_CONFIG_RE.search(line)
        ):
            findings.append(Finding(rel, no, "anthropic-api",
                                    "Anthropic API usage is not allowed in this project"))
        for rx in deny_res:
            if rx.search(line):
                findings.append(Finding(rel, no, "denylist", "matches a private denylist term"))
                break
    return findings


def scan(root: Path, files: list[str] | None = None, denylist: list[str] | None = None,
         self_path: str | None = None) -> list[Finding]:
    files = candidate_files(root) if files is None else files
    denylist = load_denylist() if denylist is None else denylist
    findings: list[Finding] = []
    for rel in files:
        findings.extend(_check_path(rel))
        path = root / rel
        if not path.is_file() or not _is_text(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(_check_data_file(rel, text))
        if rel == self_path:
            # The scanner's own patterns would match themselves; still check denylist.
            findings.extend(f for f in _check_lines(rel, text, denylist) if f.rule == "denylist")
            continue
        findings.extend(_check_lines(rel, text, denylist))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lce privacy-scan", description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="path inside the repository to scan")
    args = ap.parse_args(argv)

    root = repo_root(Path(args.root))
    denylist = load_denylist()
    self_rel = None
    try:
        self_rel = str(Path(__file__).resolve().relative_to(root))
    except ValueError:
        pass
    findings = scan(root, denylist=denylist, self_path=self_rel)
    note = f"{len(denylist)} denylist term(s) loaded" if denylist else "no local denylist found"
    if findings:
        print(f"✗ privacy scan: {len(findings)} finding(s) ({note})")
        for f in findings:
            print(f.render())
        return 1
    print(f"✓ privacy scan clean ({note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
