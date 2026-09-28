"""Command-line entry point: `lce <command>`.

There is intentionally no publish command in Phase 1.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from importlib import resources
from pathlib import Path

import yaml

from lce import __version__
from lce.config.paths import DataDirError
from lce.rules import RulesetNotReady
from lce.store import DataStore, StoreError


def _store(args: argparse.Namespace) -> DataStore:
    return DataStore.open(args.data_dir)


def _read_file(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def _print_findings(findings) -> None:
    for f in findings:
        print(f.render())


# ── setup & validation ────────────────────────────────────────────────
def cmd_init_data(args):
    store = DataStore.init(Path(args.path))
    print(f"✓ data directory initialized: {store.root}")
    return 0


def cmd_validate(args):
    from lce.validate import validate_dir

    root = Path(args.path)
    if not root.is_dir():
        print(f"✗ {root} is not a directory")
        return 2
    checked, errors = validate_dir(root)
    if errors:
        print(f"✗ {len(errors)} validation error(s) in {checked} file(s)")
        for err in errors:
            print(err.render())
        return 1
    print(f"✓ {checked} file(s) valid")
    return 0


def cmd_privacy_scan(args):
    from lce.privacy.scan import main as scan_main

    return scan_main(["--root", args.root])


def cmd_check_data_dir(args):
    from lce.config.paths import resolve_data_dir

    print(f"✓ data directory OK: {resolve_data_dir(args.path)}")
    return 0


def cmd_status(args):
    from lce import interview

    store = _store(args)
    missing = interview.ready_for_drafting(store)
    answered = sum(1 for _, done in interview.status(store) if done)
    total = len(interview.questions())
    print(f"data directory: {store.root}")
    print(f"interview: {answered}/{total} answered; "
          + ("ready for drafting" if not missing else f"{len(missing)} required missing"))
    stories = store.stories()
    public = sum(1 for s in stories.values() if s["publication_status"] == "PUBLIC")
    print(f"story bank: {len(stories)} stories ({public} PUBLIC)")
    print(f"research candidates: {sum(1 for c in store.candidates().values() if c['status'] == 'new')} new")
    print(f"publisher: {store.settings()['publisher']['provider']} (Phase 1: publishing disabled)")
    for pid in store.post_ids():
        print(f"  {pid}: {store.load_post(pid)['state']}")
    return 0


# ── interview ─────────────────────────────────────────────────────────
def cmd_interview_next(args):
    from lce import interview

    store = _store(args)
    todo = interview.missing(store, required_only=args.required)
    if not todo:
        print("✓ nothing missing")
        return 0
    for q in todo[: args.limit]:
        opts = f" [{'/'.join(q.options)}]" if q.options else ""
        req = "required" if q.required else "optional"
        print(f"- {q.id} ({q.group}, {req}, {q.type}){opts}: {q.prompt}")
    print(f"({len(todo)} unanswered)")
    return 0


def cmd_interview_set(args):
    from lce import interview

    store = _store(args)
    value = interview.answer(store, args.question, args.value, source=args.source)
    print(f"✓ stored {args.question} = {json.dumps(value, ensure_ascii=False)}")
    return 0


# ── story bank ────────────────────────────────────────────────────────
def cmd_story_add(args):
    store = _store(args)
    doc = yaml.safe_load(_read_file(args.file))
    if not isinstance(doc, dict) or "story_id" not in doc:
        raise StoreError("story file must be a mapping with a story_id")
    path = store.story_path(doc["story_id"])
    if path.exists() and not args.replace:
        raise StoreError(f"story {doc['story_id']} exists; use --replace")
    store.write_doc(path, "story", doc)
    store.log_event("story.save", story_id=doc["story_id"], status=doc["publication_status"])
    print(f"✓ story {doc['story_id']} saved ({doc['publication_status']})")
    return 0


def cmd_story_list(args):
    for sid, s in _store(args).stories().items():
        print(f"- {sid} [{s['publication_status']}, reusable={s['reusable']}] {s['title']}")
    return 0


# ── research ──────────────────────────────────────────────────────────
def cmd_research_fetch(args):
    from lce.research import fetch_feeds

    result = fetch_feeds(_store(args))
    print(f"✓ {len(result['added'])} new candidate(s)")
    for err in result["errors"]:
        print(f"  ! {err['feed']}: {err['error']}")
    return 0


def cmd_research_add(args):
    from lce.research import add_candidate

    doc = add_candidate(_store(args), title=args.title, summary=args.summary, origin=args.origin,
                        urls=args.url, publisher=args.publisher, pillar=args.pillar)
    print(f"✓ candidate {doc['candidate_id']}")
    return 0


def cmd_research_claim(args):
    from lce.research import add_claim

    add_claim(_store(args), args.candidate, args.text, args.url)
    print("✓ claim recorded")
    return 0


def cmd_research_list(args):
    for cid, c in _store(args).candidates().items():
        if args.all or c["status"] == "new":
            print(f"- {cid} [{c['status']}, {c['origin']}] {c['title']}")
    return 0


# ── planning & selection ──────────────────────────────────────────────
def cmd_plan_add(args):
    from lce.planning import add_plan_entry

    add_plan_entry(_store(args), plan_date=date.fromisoformat(args.date), topic=args.topic,
                   pillar=args.pillar, fmt=args.format, angle=args.angle)
    print("✓ plan entry added")
    return 0


def cmd_plan_list(args):
    for e in _store(args).plan().get("entries", []):
        print(f"- {e['date']} [{e['status']}] {e['pillar']}: {e['topic']}"
              + (f" → {e['draft_ref']}" if e.get("draft_ref") else ""))
    return 0


def cmd_select_list(args):
    from lce.planning import rank_candidates

    day = date.fromisoformat(args.date) if args.date else date.today()
    for r in rank_candidates(_store(args), day)[: args.limit]:
        why = f" ({'; '.join(r['reasons'])})" if r["reasons"] else ""
        print(f"- {r['candidate_id']} score={r['score']} pillar={r['pillar']}: {r['title']}{why}")
    return 0


def cmd_select_pick(args):
    from lce.planning import select

    post = select(_store(args), candidate_id=args.candidate, pillar=args.pillar, angle=args.angle,
                  fmt=args.format, plan_date=date.fromisoformat(args.date), topic=args.topic,
                  stories=args.story)
    print(f"✓ {post['post_id']} → {post['state']}")
    return 0


# ── writing ───────────────────────────────────────────────────────────
def cmd_draft_save(args):
    from lce.posts import save_draft

    post = save_draft(_store(args), args.post, _read_file(args.file))
    print(f"✓ {post['post_id']} → {post['state']}")
    return 0


def cmd_humanize_check(args):
    """Preview QA findings for a text without changing any state."""
    from lce.privacy.scan import load_denylist
    from lce.qa import ERROR, run_checks
    from lce.rules import ready_ruleset

    store = _store(args)
    post = store.load_post(args.post)
    text = _read_file(args.file) if args.file else (store.post_text(args.post, "post.md")
                                                     or store.post_text(args.post, "draft.md") or "")
    findings = run_checks(text, rules=ready_ruleset(post["language"]), voice=store.voice(),
                          profile=store.profile(), post=post, stories=store.stories(),
                          denylist=load_denylist())
    _print_findings(findings)
    errors = sum(1 for f in findings if f.severity == ERROR)
    print(f"{errors} error(s), {len(findings) - errors} warning(s) — preview only, state unchanged")
    return 1 if errors else 0


def cmd_humanize_save(args):
    from lce.posts import save_humanized

    post = save_humanized(_store(args), args.post, _read_file(args.file))
    print(f"✓ {post['post_id']} → {post['state']} (hash {post['content_hash'][:12]})")
    return 0


def cmd_qa(args):
    from lce.qa import run_qa

    report = run_qa(_store(args), args.post)
    for f in report["errors"] + report["warnings"]:
        print(f"  [{f['severity']}] {f['code']}: {f['message']}")
    print(f"{'✓' if report['status'] == 'passed' else '✗'} QA {report['status']}")
    return 0 if report["status"] == "passed" else 1


def cmd_dupcheck(args):
    from lce.dupcheck import run_dupcheck

    r = run_dupcheck(_store(args), args.post)
    print(f"compared against {r['compared_against']} post(s)")
    for key in ("exact", "near", "similar", "story_reuse", "angle_reuse", "topic_reuse"):
        if r[key]:
            print(f"  {key}: {json.dumps(r[key], ensure_ascii=False)}")
    print(f"{'✓' if r['status'] == 'passed' else '✗'} duplicate check {r['status']}")
    return 0 if r["status"] == "passed" else 1


# ── approval ──────────────────────────────────────────────────────────
def cmd_approval_prepare(args):
    from lce.approval import prepare

    post, path = prepare(_store(args), args.post)
    print(f"✓ {post['post_id']} → {post['state']}")
    print(f"  review: {path}")
    return 0


def cmd_approve(args):
    from lce.approval import approve

    post = approve(_store(args), args.post, args.hash)
    print(f"✓ {post['post_id']} APPROVED (hash {post['approval']['approved_hash'][:12]}). "
          "Nothing was published.")
    return 0


def cmd_reject(args):
    from lce.approval import reject

    post = reject(_store(args), args.post, args.reason)
    print(f"✓ {post['post_id']} → {post['state']}")
    return 0


def cmd_ready(args):
    from lce.approval import mark_ready

    post = mark_ready(_store(args), args.post)
    print(f"✓ {post['post_id']} → {post['state']}. No publisher exists in Phase 1; "
          "nothing was published.")
    return 0


# ── posts & history ───────────────────────────────────────────────────
def cmd_post_list(args):
    store = _store(args)
    for pid in store.post_ids():
        p = store.load_post(pid)
        print(f"- {pid} [{p['state']}] {p.get('pillar', '')}: {p.get('topic', '')}")
    return 0


def cmd_post_show(args):
    store = _store(args)
    print(yaml.safe_dump(store.load_post(args.post), sort_keys=False, allow_unicode=True))
    text = store.post_text(args.post, "post.md") or store.post_text(args.post, "draft.md")
    if text:
        print("---\n" + text)
    return 0


def cmd_post_reopen(args):
    from lce.posts import reopen

    post = reopen(_store(args), args.post, args.reason)
    print(f"✓ {post['post_id']} → {post['state']} (approval discarded)")
    return 0


def cmd_history_import(args):
    from lce.dupcheck import import_external

    slug = import_external(_store(args), args.name, _read_file(args.file))
    print(f"✓ imported history/external/{slug}.md")
    return 0


def cmd_skills_sync(args):
    """Copy the generic Claude Code skills into <data-dir>/.claude/skills/."""
    store = _store(args)
    src = resources.files("lce.claude_skills")
    count = 0
    for skill in src.iterdir():
        if not skill.is_dir() or skill.name.startswith("_"):
            continue
        dest = store.root / ".claude" / "skills" / skill.name
        dest.mkdir(parents=True, exist_ok=True)
        with resources.as_file(skill.joinpath("SKILL.md")) as f:
            shutil.copyfile(f, dest / "SKILL.md")
        count += 1
    print(f"✓ {count} skill(s) synced to {store.root / '.claude' / 'skills'}")
    return 0


# ── dashboard ─────────────────────────────────────────────────────────
def _engine_root() -> Path:
    from lce.config.paths import find_engine_root

    root = find_engine_root(Path(__file__).resolve().parent)
    if root is None:
        raise StoreError("demo data is only available from a source checkout of the engine")
    return root


def cmd_dashboard_serve(args):
    from lce.dashboard.server import demo_store, make_server

    if args.demo:
        store, mode = demo_store(_engine_root()), "demo"
    else:
        store, mode = _store(args), "real"
    server = make_server(store, mode=mode, port=args.port)
    port = server.server_address[1]
    label = "DEMO MODE — NO PRIVATE DATA" if mode == "demo" else f"REAL DATA: {store.root}"
    print(f"LCE Control Center ({label})")
    print(f"  http://127.0.0.1:{port}/   (read-only, local only; Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_dashboard_build_demo(args):
    from lce.dashboard.build import build_demo

    files = build_demo(_engine_root(), Path(args.out))
    print(f"✓ demo site ({len(files)} files) in {Path(args.out).resolve()} — fictional data only")
    return 0


# ── parser ────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="lce", description="LinkedIn content engine (Phase 1)")
    ap.add_argument("--version", action="version", version=f"lce {__version__}")
    ap.add_argument("--data-dir", default=None,
                    help="private data directory (default: $LCE_DATA_DIR or current directory)")
    sub = ap.add_subparsers(dest="command", required=True)

    def cmd(name, func, help_):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(func=func)
        return p

    def group(name, help_):
        return sub.add_parser(name, help=help_).add_subparsers(dest="sub", required=True)

    def gcmd(g, name, func, help_):
        p = g.add_parser(name, help=help_)
        p.set_defaults(func=func)
        return p

    cmd("init-data", cmd_init_data, "create a private data directory skeleton").add_argument("path")
    cmd("validate", cmd_validate, "validate a data directory").add_argument("path")
    cmd("privacy-scan", cmd_privacy_scan, "scan this repository for private data").add_argument(
        "--root", default=".")
    cmd("check-data-dir", cmd_check_data_dir, "verify the data directory").add_argument(
        "path", nargs="?", default=None)
    cmd("status", cmd_status, "readiness and post overview")

    g = group("interview", "progressive profile/voice interview")
    p = gcmd(g, "next", cmd_interview_next, "show the next unanswered questions")
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--required", action="store_true")
    p = gcmd(g, "set", cmd_interview_set, "store one answer")
    p.add_argument("question")
    p.add_argument("--value", required=True)
    p.add_argument("--source", default="interview")

    g = group("story", "story bank")
    p = gcmd(g, "add", cmd_story_add, "add a story from a YAML file ('-' = stdin)")
    p.add_argument("--file", required=True)
    p.add_argument("--replace", action="store_true")
    gcmd(g, "list", cmd_story_list, "list stories")

    g = group("research", "research candidates")
    gcmd(g, "fetch", cmd_research_fetch, "fetch configured RSS/Atom feeds")
    p = gcmd(g, "add", cmd_research_add, "add a candidate topic")
    p.add_argument("--title", required=True)
    p.add_argument("--summary", default="")
    p.add_argument("--url", action="append", default=[])
    p.add_argument("--origin", choices=["web_search", "manual", "story"], default="manual")
    p.add_argument("--publisher", default="")
    p.add_argument("--pillar", default=None)
    p = gcmd(g, "claim", cmd_research_claim, "attach a sourced claim to a candidate")
    p.add_argument("candidate")
    p.add_argument("--text", required=True)
    p.add_argument("--url", required=True)
    p = gcmd(g, "list", cmd_research_list, "list candidates")
    p.add_argument("--all", action="store_true")

    g = group("plan", "content calendar")
    p = gcmd(g, "add", cmd_plan_add, "add a planned slot")
    p.add_argument("--date", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--pillar", required=True)
    p.add_argument("--format", default="")
    p.add_argument("--angle", default="")
    gcmd(g, "list", cmd_plan_list, "list plan entries")

    g = group("select", "topic selection")
    p = gcmd(g, "list", cmd_select_list, "rank new candidates")
    p.add_argument("--date", default=None)
    p.add_argument("--limit", type=int, default=10)
    p = gcmd(g, "pick", cmd_select_pick, "create a post from a candidate")
    p.add_argument("candidate")
    p.add_argument("--pillar", required=True)
    p.add_argument("--angle", required=True)
    p.add_argument("--format", default="text")
    p.add_argument("--date", required=True)
    p.add_argument("--topic", default=None)
    p.add_argument("--story", action="append", default=[])

    g = group("draft", "first drafts")
    p = gcmd(g, "save", cmd_draft_save, "store a draft ('-' = stdin)")
    p.add_argument("post")
    p.add_argument("--file", required=True)

    g = group("humanize", "humanized candidate text")
    p = gcmd(g, "check", cmd_humanize_check, "preview findings without changing state")
    p.add_argument("post")
    p.add_argument("--file", default=None)
    p = gcmd(g, "save", cmd_humanize_save, "store the humanized text")
    p.add_argument("post")
    p.add_argument("--file", required=True)

    cmd("qa", cmd_qa, "run quality checks").add_argument("post")
    cmd("dupcheck", cmd_dupcheck, "run the duplicate check").add_argument("post")

    g = group("approval", "approval artifacts")
    gcmd(g, "prepare", cmd_approval_prepare, "write APPROVAL.md").add_argument("post")
    p = cmd("approve", cmd_approve, "approve a post (interactive terminal only)")
    p.add_argument("post")
    p.add_argument("--hash", required=True, help="first 12+ characters of the content hash")
    p = cmd("reject", cmd_reject, "reject a post")
    p.add_argument("post")
    p.add_argument("--reason", required=True)
    cmd("ready", cmd_ready, "mark an approved post ready (does not publish)").add_argument("post")

    g = group("post", "posts")
    gcmd(g, "list", cmd_post_list, "list posts")
    gcmd(g, "show", cmd_post_show, "show a post").add_argument("post")
    p = gcmd(g, "reopen", cmd_post_reopen, "reopen for editing (discards approval)")
    p.add_argument("post")
    p.add_argument("--reason", required=True)

    g = group("history", "previously published posts")
    p = gcmd(g, "import", cmd_history_import, "import a past post for duplicate checks")
    p.add_argument("name")
    p.add_argument("--file", required=True)

    g = group("dashboard", "Web Control Center (read-only)")
    p = gcmd(g, "serve", cmd_dashboard_serve, "serve the dashboard on 127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--demo", action="store_true", help="fictional demo data instead of your data")
    p = gcmd(g, "build-demo", cmd_dashboard_build_demo, "static demo site (fictional data only)")
    p.add_argument("--out", default="_site")

    g = group("skills", "Claude Code skills")
    gcmd(g, "sync", cmd_skills_sync, "copy skills into the data directory")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (StoreError, DataDirError, RulesetNotReady) as exc:
        print(f"✗ {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
