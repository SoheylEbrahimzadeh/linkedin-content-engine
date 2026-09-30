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
from lce.schedule import ScheduleError
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

    day = date.fromisoformat(args.date) if args.date else _today_local(_store(args))
    for r in rank_candidates(_store(args), day)[: args.limit]:
        why = f" ({'; '.join(r['reasons'])})" if r["reasons"] else ""
        print(f"- {r['candidate_id']} score={r['score']} pillar={r['pillar']}: {r['title']}{why}")
    return 0


def cmd_select_pick(args):
    from lce.planning import select

    store = _store(args)
    if args.job:
        from lce.jobs import load_job

        slot_day = date.fromisoformat(load_job(store, args.job)["slot"]["slot_id"][:10])
        if args.date and date.fromisoformat(args.date) != slot_day:
            raise StoreError(f"--date must match the job's slot date {slot_day}")
        plan_date = slot_day
    elif args.date:
        plan_date = date.fromisoformat(args.date)
    else:
        raise StoreError("give --date or --job")
    post = select(store, candidate_id=args.candidate, pillar=args.pillar, angle=args.angle,
                  fmt=args.format, plan_date=plan_date, topic=args.topic, stories=args.story)
    print(f"✓ {post['post_id']} → {post['state']}")
    if args.job:
        from lce.scheduler import link_post

        link_post(store, args.job, post["post_id"])
        print(f"  linked to {args.job}")
    return 0


def _today_local(store: DataStore) -> date:
    """Today's date in the configured timezone (UTC when none is configured)."""
    from zoneinfo import ZoneInfo

    from lce import clock

    tz = store.settings().get("timezone") or "UTC"
    return clock.now().astimezone(ZoneInfo(tz)).date()


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


# ── schedule, scheduler & jobs ────────────────────────────────────────
def cmd_schedule_show(args):
    from datetime import timedelta

    from lce import clock
    from lce.jobs import job_id_for, list_jobs
    from lce.schedule import load_schedule, slots_between

    store = _store(args)
    schedule = load_schedule(store.settings())
    now = clock.now()
    jobs = {j["job_id"]: j for j in list_jobs(store)}
    print(f"timezone {schedule.timezone}, {schedule.posts_per_week} posts/week")
    for slot in slots_between(schedule, now, now + timedelta(days=args.days)):
        job = jobs.get(job_id_for(slot.slot_id))
        state = job["state"] if job else "no job yet"
        extra = f" ({job['blocked_reason']})" if job and job.get("blocked_reason") else ""
        dst = " [DST-adjusted]" if slot.dst_adjusted else ""
        print(f"- {slot.local.strftime('%a %Y-%m-%d %H:%M %z')}  (UTC {slot.utc:%H:%M})"
              f"  {job_id_for(slot.slot_id)}: {state}{extra}{dst}")
    return 0


def cmd_scheduler_run_once(args):
    from lce.clock import parse_iso
    from lce.scheduler import run_once

    now = parse_iso(args.now) if args.now else None
    report = run_once(_store(args), dry_run=args.dry_run, now=now)
    head = "DRY RUN — nothing is written" if report.dry_run else f"run {report.invocation_id}"
    print(f"{head} at {report.now}")
    if report.status != "ok":
        print(f"✗ {report.status}: {report.message}")
        return 1
    if not report.actions:
        print("  nothing due")
    for a in report.actions:
        print(f"  {a.slot_local}  {a.job_id}  → {a.action}" + (f": {a.detail}" if a.detail else ""))
    for r in report.results:
        why = f" ({r['blocked_reason']})" if r.get("blocked_reason") else ""
        print(f"  ✓ {r['job_id']}: {r['action']} → {r['state']}{why}")
    print("Nothing is approved or published by the scheduler.")
    return 0


def cmd_jobs_list(args):
    from lce.jobs import list_jobs

    for j in list_jobs(_store(args)):
        if args.state and j["state"] != args.state:
            continue
        extra = f" ({j['blocked_reason']})" if j.get("blocked_reason") else ""
        post = f" post={j['post_id']}" if j.get("post_id") else ""
        print(f"- {j['job_id']} [{j['state']}{extra}] slot {j['slot']['local']}{post}")
    return 0


def cmd_jobs_show(args):
    from lce.jobs import load_job

    print(yaml.safe_dump(load_job(_store(args), args.job), sort_keys=False, allow_unicode=True))
    return 0


def cmd_jobs_agent_tasks(args):
    from lce.scheduler import pending_agent_tasks

    tasks = pending_agent_tasks(_store(args))
    for j in tasks:
        post = j.get("post_id") or "no post yet"
        print(f"- {j['job_id']} ({j['blocked_reason']}) slot {j['slot']['local']}: {post}")
    if not tasks:
        print("no agent work pending")
    return 0


def _job_cmd(func_name: str, *extra):
    def run(args):
        from lce import scheduler

        job = getattr(scheduler, func_name)(_store(args), args.job, *[getattr(args, e) for e in extra])
        reason = f" ({job['blocked_reason']})" if job.get("blocked_reason") else ""
        print(f"✓ {job['job_id']} → {job['state']}{reason}")
        return 0
    return run


# ── publishing (human-triggered only) ─────────────────────────────────
def cmd_publish(args):
    from lce import publishing

    store = _store(args)
    if args.target == "reconcile":
        if not args.post:
            raise StoreError("usage: lce publish reconcile <post> --published-url URL | --not-published")
        out = publishing.reconcile(store, args.post, published_url=args.published_url,
                                   not_published=args.not_published)
        print(f"✓ {args.post} → {out['post']['state']} (recorded as the owner's decision)")
        return 0
    if args.post or args.published_url or args.not_published:
        raise StoreError("usage: lce publish <post> [--dry-run]")
    publisher = publishing.make_publisher(store)
    if args.dry_run:
        plan = publishing.dry_run(store, args.target, publisher)
        print("DRY RUN — no token is read, nothing is sent or written")
        print(f"POST {plan['url']}")
        for k, v in plan["headers"].items():
            print(f"  {k}: {v}")
        print(json.dumps(plan["body"], indent=2, ensure_ascii=False))
        print(f"approved hash {plan['approved_hash'][:12]}, {plan['characters']} characters")
        return 0
    out = publishing.publish(store, args.target, publisher)
    post, pub, result = out["post"], out["publication"], out["result"]
    if post["state"] == "PUBLISHED":
        print(f"✓ PUBLISHED {pub['remote_id']}\n  {pub['url']}")
        return 0
    if post["state"] == "PUBLISH_FAILED":
        d = result.detail or {}
        print(f"✗ PUBLISH_FAILED: not created on LinkedIn ({d.get('reason')}"
              f"{', HTTP ' + str(d['http_status']) if d.get('http_status') else ''})"
              f"{' — may be retried later with lce publish' if d.get('retryable') else ''}")
        return 1
    print("⚠ NEEDS_RECONCILE: the request may have reached LinkedIn, outcome unknown.\n"
          "  Check your profile, then run:\n"
          f"  lce publish reconcile {args.target} --published-url <url>   or   --not-published")
    return 1


def cmd_linkedin_status(args):
    from lce import publishing
    from lce.publish.credentials import DEFAULT_ACCOUNT, DEFAULT_SERVICE, KeychainTokenStore

    store = _store(args)
    provider = store.settings().get("publisher", {}).get("provider")
    print(f"publisher.provider: {provider}")
    try:
        doc = publishing.load_linkedin_config(store)
    except StoreError as exc:
        print(f"config/linkedin.yaml: ✗ {exc}")
        return 1
    print(f"api_version: {doc['api_version']}   person_urn: {doc['person_urn']}   "
          f"visibility: {doc.get('visibility', 'PUBLIC')}")
    tokens = KeychainTokenStore(doc.get("keychain_service", DEFAULT_SERVICE),
                                doc.get("keychain_account", DEFAULT_ACCOUNT))
    print(f"token in Keychain: {'present' if tokens.exists() else 'absent'} "
          f"(service {tokens.service!r}, account {tokens.account!r}; value never shown)")
    if doc.get("token_expires_at"):
        from lce.clock import now, parse_iso

        days = (parse_iso(doc["token_expires_at"]) - now()).days
        print(f"token expires: {doc['token_expires_at']} ({days} days)"
              + ("  ✗ EXPIRED" if days < 0 else "  ⚠ renew soon" if days < 14 else ""))
    else:
        print("token expiry: unknown (record token_expires_at in config/linkedin.yaml)")
    return 0


def cmd_linkedin_whoami(args):
    from lce import publishing
    from lce.publish.credentials import CredentialError

    store = _store(args)
    try:
        info = publishing.make_publisher(store).whoami()
    except CredentialError as exc:
        raise StoreError(str(exc)) from exc
    print(f"sub: {info['sub']}   name: {info['name']}\nperson_urn: {info['person_urn']}")
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
    p.add_argument("--date", default=None, help="planned date (or use --job)")
    p.add_argument("--job", default=None, help="scheduled job this post fulfils")
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

    g = group("cadence", "posting slots from your private settings")
    p = gcmd(g, "show", cmd_schedule_show, "upcoming slots and their jobs")
    p.add_argument("--days", type=int, default=14)

    g = group("automation", "one deterministic scheduler pass (no daemon)")
    p = gcmd(g, "run-once", cmd_scheduler_run_once, "create due jobs and run deterministic steps")
    p.add_argument("--dry-run", action="store_true", help="show what would happen; write nothing")
    p.add_argument("--now", default=None, help="simulated time with offset (dry-run only)")

    g = group("jobs", "scheduled jobs")
    p = gcmd(g, "list", cmd_jobs_list, "list jobs")
    p.add_argument("--state", default=None)
    gcmd(g, "show", cmd_jobs_show, "show one job with history").add_argument("job")
    gcmd(g, "agent-tasks", cmd_jobs_agent_tasks, "jobs waiting for Claude Code work")
    p = gcmd(g, "claim", _job_cmd("claim", "holder"), "lease a job for agent work")
    p.add_argument("job")
    p.add_argument("--as", dest="holder", default="claude-code")
    p = gcmd(g, "release", _job_cmd("release", "note"), "end agent work on a job")
    p.add_argument("job")
    p.add_argument("--note", default="")
    gcmd(g, "retry", _job_cmd("retry"), "retry a FAILED job").add_argument("job")
    p = gcmd(g, "skip", _job_cmd("skip", "reason"), "skip a job")
    p.add_argument("job")
    p.add_argument("--reason", required=True)
    p = gcmd(g, "reconcile", _job_cmd("reconcile", "decision"), "resolve NEEDS_RECONCILE")
    p.add_argument("job")
    p.add_argument("--decision", choices=["retry", "skip", "fail"], default=None)

    p = cmd("publish", cmd_publish, "publish an approved post to LinkedIn (interactive only)")
    p.add_argument("target", help="post id, or 'reconcile'")
    p.add_argument("post", nargs="?", default=None, help="post id (with 'reconcile')")
    p.add_argument("--dry-run", action="store_true", help="show the request; send nothing")
    p.add_argument("--published-url", default=None)
    p.add_argument("--not-published", action="store_true")

    g = group("linkedin", "LinkedIn account settings (no secrets are ever printed)")
    gcmd(g, "status", cmd_linkedin_status, "config, token presence and expiry")
    gcmd(g, "whoami", cmd_linkedin_whoami, "look up your person URN (network call)")

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
    except (StoreError, DataDirError, RulesetNotReady, ScheduleError, ValueError) as exc:
        print(f"✗ {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
