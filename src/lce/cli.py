"""Command-line entry point: `lce <command>`.

There is intentionally no publish command in Phase 1.
"""

from __future__ import annotations

import argparse
import json
import os
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

    return scan_main(["--root", args.root, *(["--history"] if args.history else [])])


def cmd_privacy_denylist(args):
    from lce.privacy.fingerprint import write_generated

    path, count = write_generated(_store(args))
    print(f"✓ {count} term(s) derived from the private data directory → {path} (local only)")
    return 0


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


def cmd_brand_status(args):
    from lce.brand import status

    store = _store(args)
    st = status(store, date.fromisoformat(args.date) if args.date else _today_local(store))
    if args.json:
        print(json.dumps(st, indent=2, ensure_ascii=False))
        return 0
    if not st["configured"]:
        print("! no profile/brand.yaml yet — run `lce interview next` (group: brand)")
    print(f"window: {st['window_days']} days, {st['posts_in_window']} post(s); "
          f"personal-evidence share {st['personal_share']:.2f}"
          + (f" (min {st['min_personal_share']:.2f})" if st["min_personal_share"] is not None else ""))
    for r in st["pillars"]:
        target = f"{r['target']:.2f}" if r["target"] is not None else "—"
        print(f"  pillar {r['id']:<32} {r['posts']:>3} post(s)  actual {r['actual']:.2f}  "
              f"target {target}  stories {r['evidence_stories']}")
    for t in st["themes"]:
        flag = "  ← needs personal input" if t["needs_personal_input"] else ""
        print(f"  theme  {t['id']:<32} {t['posts']:>3} post(s)  evidence {t['evidence']}{flag}")
    for p in st["problems"]:
        print(f"  ✗ {p}")
    return 1 if st["problems"] else 0


def cmd_brand_next(args):
    from lce.brand import recommend

    store = _store(args)
    recs = recommend(store, date.fromisoformat(args.date) if args.date else _today_local(store),
                     args.count)
    if args.json:
        print(json.dumps(recs, indent=2, ensure_ascii=False))
        return 0
    for i, r in enumerate(recs, 1):
        head = f"{i}. pillar {r['pillar']} · theme {r['theme'] or '—'} · evidence {r['evidence']}"
        print(head + ("  (NEEDS PERSONAL INPUT: no PUBLIC story)" if r["needs_personal_input"] else ""))
        for reason in r["reasons"]:
            print(f"   - {reason}")
        if r["stories"]:
            print(f"   stories: {', '.join(r['stories'])}")
        if r["candidates"]:
            print(f"   candidates: {', '.join(r['candidates'])}")
    return 0


def cmd_image_decide(args):
    from lce.images import decide

    prov = None
    if args.kind != "none":
        prov = {"origin": args.origin, "usage": args.usage}
        for key in ("source_url", "credit", "license"):
            if getattr(args, key):
                prov[key] = getattr(args, key)
        if args.method:
            prov["generation"] = {"method": args.method, **({"prompt": args.prompt}
                                                           if args.prompt else {})}
    if args.kind == "none" and not args.text_only_reason:
        raise StoreError("--kind none needs --text-only-reason (why no visual serves this post)")
    rel = None
    if args.kind != "none" and (args.concept or args.visual_type or args.relevance_reason):
        rel = {"concept": args.concept or "", "visual_type": args.visual_type or "",
               "reason": args.relevance_reason or ""}
    doc = decide(_store(args), args.post, kind=args.kind, rationale=args.rationale,
                 source_file=args.file, relation=args.relation or "", alt_text=args.alt or "",
                 provenance=prov, decided_by=args.by,
                 text_only_reason=args.text_only_reason if args.kind == "none" else None,
                 relevance=rel)
    print(f"✓ image decision for {args.post}: {doc['kind']}"
          + (f" ({doc['file']}, sha256 {doc['sha256'][:12]}…)" if doc["kind"] != "none" else ""))
    return cmd_image_check(args)


# ── same-day refresh (LCE-040) ───────────────────────────────────────
def _as_of(store, value):
    from lce import refresh

    return date.fromisoformat(value) if value else refresh.today_local(store)


def _print_refresh(pid, rec):
    mark = {"current": "✓", "needs_review": "?", "update_required": "!", "update_awaiting_approval": "↻",
            "update_in_progress": "…"}[rec["status"]]
    print(f"{mark} {pid}: {rec['decision']} → {rec['status']} ({rec['reason']})")
    for src in rec.get("sources", []):
        http = f" (HTTP {src['http']})" if src.get("http") else ""
        print(f"    source {src.get('status')}: {src['url']}{http}")
    for c in rec.get("claims", []):
        print(f"    claim {c['status']}: {c['text'][:90]}")
    m = rec.get("media") or {}
    print(f"    media {m.get('status')}: {m.get('note', '')}")
    print(f"    content {rec['content_hash'][:12]}…  approval {rec.get('approval')} "
          f"({rec.get('approval_effect')})")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::notice title=refresh {pid}::{rec['decision']} → {rec['status']}: {rec['reason']} | media "
              f"{m.get('status')} | approval {rec.get('approval')} ({rec.get('approval_effect')})")


def _push_freshness(client, rows):
    for row in rows:
        client.call("PUT", f"/freshness/{row['post_id']}", row)
    print(f"✓ {len(rows)} same-day freshness record(s) sent to the cloud (test-mode records are never sent)")


def cmd_refresh_manual(args):
    """LCE-041/042: manual Refresh — pending requests, and a new replacement package."""
    from lce import repackage

    store = _store(args)
    if args.sub == "unskip":
        out = repackage.unskip_as_refresh(store, args.post, by=args.by, note=args.note or "")
        print(f"✓ {args.post}: skip undone; v{out['rejected_version']} archived as rejected; "
              "replacement requested (the refresh worker writes it; nothing approved or published)")
        return 0
    if args.sub == "pending":
        rows = repackage.pending(store)
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        elif not rows:
            print("no refresh requested")
        for r in [] if args.json else rows:
            print(f"{r['post_id']}  {r['state']}  planned {r.get('plan_date')}  requested {r['requested_at']}"
                  f" by {r.get('requested_by')}" + (f"  note: {r['note']}" if r.get("note") else ""))
        return 0
    pkg_path = Path(args.file)
    pkg = yaml.safe_load(pkg_path.read_text(encoding="utf-8")) or {}
    if "text_file" in pkg:
        pkg["text"] = (pkg_path.parent / pkg.pop("text_file")).read_text(encoding="utf-8")
    spec = (pkg.get("media") or {}).get("spec")
    if isinstance(spec, str):
        pkg["media"]["spec"] = yaml.safe_load((pkg_path.parent / spec).read_text(encoding="utf-8"))
    rec = repackage.package(store, args.post, pkg, by=args.by, as_of=_as_of(store, args.as_of))
    print(f"✓ {args.post} refreshed → AWAITING_APPROVAL (previous version kept as v{rec['version_before']})")
    print(f"    text  {rec['content_hash_before'][:12]}… → {rec['content_hash'][:12]}…")
    print(f"    image {(rec['image_sha256_before'] or 'none')[:12]} → {(rec['image_sha256'] or 'none')[:12]}"
          f"  ({rec['media']['note']})")
    rel = rec["media"].get("relevance") or {}
    if rel:
        print(f"    media relevance {rel.get('media_decision')}: {rel.get('visual_type')} · "
              f"{rel.get('concept')}")
    dup = rec["steps"]["duplicate"]
    print(f"    QA passed · duplicate check {dup['status']} against {dup['compared_against']} archived "
          f"post(s) · approval {rec['approval_effect']}")
    print("    nothing was approved or published; the owner approves the new version in the Control Center")
    return 0


def cmd_versions(args):
    from lce import versions

    store = _store(args)
    if args.sub == "list":
        for v in versions.listing(store, args.post):
            print(f"v{v['version']}  {v['created_at']}  {v['state']}  text {v['content_hash'][:12]}…  "
                  f"image {(v.get('image_sha256') or 'none')[:12]}  {v['reason']}")
        return 0
    m = versions.restore(store, args.post, args.version, by=args.by)
    print(f"✓ v{m['version']} restored as the current candidate (HUMANIZED); the version it replaced is "
          "kept. Run QA, the duplicate check and prepare approval again.")
    return 0


def cmd_refresh(args):
    from lce import refresh

    store = _store(args)
    as_of = _as_of(store, getattr(args, "as_of", None))
    if args.sub == "run":
        extra = set(args.post or [])
        client = None
        if args.push:
            from lce import cloud

            client = cloud.make_client(store)
            for c in client.call("GET", "/snapshot").get("consents", []):
                if c.get("status") == "active" and refresh.localdate(store, c["slot_utc"]) == as_of:
                    extra.add(c["post_id"])
        pids = sorted(set(args.post)) if args.post and args.only else refresh.due_posts(store, as_of, extra)
        if not pids:
            print(f"no post planned or scheduled for {as_of}")
        for pid in pids:
            _print_refresh(pid, refresh.check(store, pid, as_of=as_of, dry_run=args.dry_run, by=args.by))
        if client and pids and not args.dry_run:
            _push_freshness(client, refresh.cloud_rows(store, pids))
        return 0
    if args.sub == "push":
        from lce import cloud

        client = cloud.make_client(store)
        extra = {c["post_id"] for c in client.call("GET", "/snapshot").get("consents", [])
                 if c.get("status") == "active" and refresh.localdate(store, c["slot_utc"]) == as_of}
        _push_freshness(client, refresh.cloud_rows(store, refresh.due_posts(store, as_of, extra)))
        return 0
    if args.sub == "research":
        rec = refresh.research(store, args.post, as_of=as_of, sources=args.source, note=args.note,
                               material=args.material == "yes", by=args.by)
    elif args.sub == "apply":
        rec = refresh.apply_update(store, args.post, as_of=as_of, text=_read_file(args.file),
                                   reason=args.reason, sources=args.source, by=args.by)
    elif args.sub == "finish":
        rec = refresh.finish(store, args.post, as_of=as_of, by=args.by)
    else:
        print(json.dumps(refresh.history(store, args.post), indent=2, ensure_ascii=False))
        return 0
    _print_refresh(args.post, rec)
    return 0


def cmd_image_diagram(args):
    from lce.visuals import concept, diagram

    store = _store(args)
    if args.spec:
        spec = yaml.safe_load(Path(args.spec).read_text(encoding="utf-8")) or {}
        doc = concept(store, args.post, spec)
    elif args.title and args.item:
        doc = diagram(store, args.post, title=args.title, items=args.item, footer=args.footer)
    else:
        raise StoreError("give --spec <file.yaml> (a conceptual visual of the post's idea)")
    rel = doc["media_relevance"]
    print(f"✓ diagram generated for {args.post} ({doc['file']}, {doc.get('width')}x{doc.get('height')}, "
          f"sha256 {doc['sha256'][:12]}…)")
    print(f"  relevance {rel['media_decision']}: {rel['visual_type']} · copied post text "
          f"{rel['copied_post_text_ratio']:.0%} · {rel['image_words']} words in the image")
    return cmd_image_check(args)


def cmd_image_commons(args):
    from lce.commons import attach

    doc = attach(_store(args), args.post, args.title, relation=args.relation, alt_text=args.alt,
                 rationale=args.rationale,
                 relevance={"concept": args.concept, "visual_type": args.visual_type,
                            "reason": args.relevance_reason})
    prov = doc["provenance"]
    print(f"✓ {args.title} attached to {args.post}: {prov['license']} ({prov['usage']}), "
          f"credit {prov['credit']}")
    return cmd_image_check(args)


def cmd_image_show(args):
    from lce.images import media_view

    print(json.dumps(media_view(_store(args), args.post), indent=2, ensure_ascii=False))
    return 0


def cmd_image_chart(args):
    from lce.visuals import chart

    doc = chart(_store(args), args.post, claims=args.claim or None)
    print(f"✓ chart generated for {args.post} ({doc['file']}, sha256 {doc['sha256'][:12]}…)")
    return cmd_image_check(args)


def cmd_image_check(args):
    from lce.images import check

    errors, warnings = check(_store(args), args.post)
    for e in errors:
        print(f"  ✗ {e}")
    for w in warnings:
        print(f"  ! {w}")
    print("✓ image decision is complete" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


def cmd_analytics_record(args):
    from lce.analytics import METRICS, record

    values = {k: getattr(args, k) for k in METRICS}
    snap = record(_store(args), args.post, values, at=args.at, note=args.note or "")
    print(f"✓ metrics recorded for {args.post} at {snap['at']}")
    return 0


def cmd_analytics_import(args):
    from lce.analytics import import_csv

    out = import_csv(_store(args), args.file)
    print(f"✓ {out['recorded']} row(s) recorded")
    if out["unmatched_rows"]:
        print(f"! rows without a matching published post: {out['unmatched_rows']}")
    return 0


def cmd_analytics_insights(args):
    from lce.analytics import insights

    store = _store(args)
    ins = insights(store, _today_local(store), args.min_sample)
    if args.json:
        print(json.dumps(ins, indent=2, ensure_ascii=False))
        return 0
    o = ins["overall"]
    if not o["posts"]:
        print("no metrics yet — record them with `lce analytics record` or `lce analytics import`")
        return 0
    rate = f"{o['median_rate']:.2%}" if o["median_rate"] is not None else "—"
    print(f"{o['posts']} post(s) with metrics; median impressions {o['median_impressions']}, "
          f"median engagement rate {rate}")
    for feat, groups in ins["groups"].items():
        shown = [g for g in groups if g["enough_data"]]
        if shown:
            print(f"  {feat}: " + ", ".join(
                f"{g['value']} ({g['posts']}): {g['median_rate']:.2%}" for g in shown
                if g["median_rate"] is not None))
    for s in ins["saturated_topics"]:
        print(f"  ! saturated topic: {s['topic']} ({len(s['posts'])} posts in 60 days)")
    if not any(g["enough_data"] for gs in ins["groups"].values() for g in gs):
        print(f"  (no group has ≥{ins['min_sample']} posts yet; keep publishing)")
    return 0


def cmd_analytics_suggest_mix(args):
    from lce.analytics import suggest_mix

    out = suggest_mix(_store(args), args.min_sample)
    if out["suggested"] is None:
        print(f"no suggestion: {out['reason']}")
        return 0
    print("suggested pillar mix (apply yourself with `lce interview set brand_mix`):")
    for p, v in out["suggested"].items():
        print(f"  {p}: {out['current'][p]:.2f} → {v:.2f}")
    print(f"  ({out['reason']})")
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
                  fmt=args.format, plan_date=plan_date, topic=args.topic, stories=args.story,
                  theme=args.theme, evidence=args.evidence, chapter=args.chapter,
                  objective=args.objective)
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
                          denylist=load_denylist(), brand=store.brand())
    _print_findings(findings)
    from lce import voice

    items = voice.checklist(store, post, findings)
    print("Voice profile checklist (profile/voice.yaml, profile.yaml, brand.yaml):")
    marks = {"passed": "✓", "failed": "✗", "review": "?"}
    for i in items:
        print(f"  {marks[i['status']]} {i['label']}" + (f" — {i['detail']}" if i["detail"] else ""))
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


def cmd_post_objective(args):
    from lce.posts import set_objective

    post = set_objective(_store(args), args.post, args.objective)
    print(f"✓ {post['post_id']} objective: {post['objective']}")
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


def cmd_jobs_brief(args):
    from lce.brief import briefs
    from lce.jobs import load_job
    from lce.scheduler import pending_agent_tasks

    store = _store(args)
    jobs = [load_job(store, args.job)] if args.job else pending_agent_tasks(store)
    out = briefs(store, _today_local(store), jobs)
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    if not out:
        print("no agent work pending")
    for b in out:
        print(f"- {b['job_id']} · slot {b['slot_local']} · task {b['task']}")
        for key in ("post_id", "pillar", "theme", "evidence", "candidates", "stories",
                    "avoid_saturated_topics"):
            if b.get(key):
                print(f"    {key}: {b[key]}")
        print(f"    → {b['instruction']}")
    return 0


def cmd_readiness(args):
    from lce import publishing
    from lce.publish.credentials import DEFAULT_ACCOUNT, DEFAULT_SERVICE, KeychainTokenStore
    from lce.readiness import check

    store = _store(args)
    try:
        doc = publishing.load_linkedin_config(store)
        config_ok = True
    except StoreError:
        doc, config_ok = {}, False
    token = None
    if not args.no_keychain:
        token = KeychainTokenStore(doc.get("keychain_service", DEFAULT_SERVICE),
                                   doc.get("keychain_account", DEFAULT_ACCOUNT)).exists()
    try:
        from lce.cloud import load_cloud_config

        load_cloud_config(store)
        cloud_configured = True
    except StoreError:
        cloud_configured = False
    rows = check(store, _today_local(store), token_present=token, linkedin_config_ok=config_ok,
                 cloud_available=cloud_configured)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    mark = {"ok": "✓", "todo": "•", "gate": "⛔"}
    for r in rows:
        print(f"{mark[r['status']]} {r['step']}: {r['detail']}")
        if r["action"]:
            print(f"    → {r['action']}")
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
    if args.target == "manual":
        if not args.post or not args.published_url:
            raise StoreError("usage: lce publish manual <post> --published-url URL "
                             "[--published-at ISO]")
        out = publishing.record_manual(store, args.post, url=args.published_url,
                                       published_at=args.published_at)
        print(f"✓ {args.post} → {out['post']['state']} (manual publication recorded)")
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


# ── cloud (Phase 4B) ──────────────────────────────────────────────────
def _annotate(tool: str, checks: list[dict]) -> None:
    """In GitHub Actions, one notice line with every result (readable via the checks API)."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        summary = " | ".join(f"{c['status']}: {c['check']} ({c['detail']})"
                             + (f" → {c['action']}" if c["status"] != "ok" and c["action"] else "")
                             for c in checks)
        print(f"::notice title={tool}::{summary}")


def cmd_cloud(args):
    from lce import cloud

    if args.sub == "smoke":
        base = args.api_base or os.environ.get("LCE_API_BASE", "").strip()
        if not base:
            base = cloud.load_cloud_config(_store(args))["api_base"]
        checks = cloud.smoke(base, wait_seconds=args.wait)
        _annotate("lce cloud smoke", checks)
        marks = {cloud.OK: "✓", cloud.ACTION: "→", cloud.FAIL: "✗"}
        for c in checks:
            print(f"{marks[c['status']]} {c['check']}: {c['detail']}"
                  + (f"\n    {c['action']}" if c["action"] else ""))
        return 2 if any(c["status"] == cloud.FAIL for c in checks) else 0
    store = _store(args)
    if args.sub == "configure" and args.dry_run:
        print(json.dumps(cloud.configure_payload(store), indent=2, ensure_ascii=False))
        print("(dry run: nothing sent; auto_publish is never set here)")
        return 0
    if args.sub == "doctor":
        marks = {cloud.OK: "✓", cloud.ACTION: "→", cloud.FAIL: "✗"}
        checks = cloud.doctor(store)
        _annotate("lce cloud doctor", checks)
        for c in checks:
            print(f"{marks[c['status']]} {c['check']}: {c['detail']}"
                  + (f"\n    {c['action']}" if c["action"] else ""))
        # 0 all ok · 1 only owner actions open · 2 something is broken
        statuses = {c["status"] for c in checks}
        return 2 if cloud.FAIL in statuses else 1 if cloud.ACTION in statuses else 0
    if args.sub == "identity":
        base = cloud.load_cloud_config(store)["api_base"].rstrip("/")
        r = cloud.linkedin_identity(cloud.UrllibCloudTransport(), base, cloud.default_access(base))
        b = r.body
        if r.status != 200 or b.get("ok") is not True:
            why = b.get("reason") or b.get("error") or b.get("_raw") or ""
            what = b.get("status") or f"HTTP {r.status}"
            print(f"✗ LinkedIn identity not verified: {what} {why}".strip())
            fix = cloud.IDENTITY_FIX.get(str(b.get("status", "")))
            if fix:
                print(f"    {fix}")
            return 2
        print(f"✓ token verified with LinkedIn (read-only userinfo); person_urn: {b['person_urn']}")
        print(f"  settings person_urn: {b.get('configured_person_urn') or 'not set'}"
              + {True: " (matches)", False: " (DIFFERENT)", None: ""}[b.get("person_urn_matches")])
        if args.write:
            doc = cloud.write_person_urn(store, b["person_urn"], args.api_version)
            print(f"✓ config/linkedin.yaml: person_urn recorded (api_version {doc['api_version']}); "
                  "next: commit it, then lce cloud configure")
        return 0
    client = cloud.make_client(store)
    if args.sub == "configure":
        out = cloud.configure(store, client)
        print(f"✓ cloud settings updated: {', '.join(out.get('updated', []))} "
              "(auto-publish unchanged; enable it in the dashboard with its typed phrase)")
        return 0
    if args.sub == "decisions":
        from lce import decisions

        if not args.apply:
            items = decisions.pending(client)
            for d in items:
                print(f"- {d['decision_id']} {d['action']} {d.get('post_id') or d.get('plan_date')} "
                      f"by {d['created_by']} at {d['created_at']}")
            print(f"{len(items)} pending decision(s)" + (" (apply with --apply)" if items else ""))
            return 0
        results = decisions.apply_all(store, client)
        marks = {"applied": "✓", "refused": "✗", "pending": "…"}
        for r in results:
            print(f"{marks[r['status']]} {r['action']} {r.get('post_id') or ''}: {r['result']}")
        print(f"{len(results)} decision(s) processed")
        if os.environ.get("GITHUB_ACTIONS") == "true" and results:
            for r in results:
                print(f"::notice title=lce cloud decisions::{r['status']}: {r['action']} "
                      f"{r.get('post_id') or ''} — {r['result']}")
        return 0
    if args.sub == "push":
        out = cloud.push(store, args.post, client)
        print(f"✓ {args.post} delegated to the cloud ({out.get('state')}); local publish is now refused")
    elif args.sub == "consent":
        slot = args.slot or cloud.slot_for_post(store, args.post)
        out = cloud.consent(store, args.post, slot, client)
        print(f"✓ consent {out['consent_id']}: {args.post} at {out['slot']['local']} "
              "(only if auto_publish is on)")
    elif args.sub == "revoke":
        client.call("DELETE", f"/consents/{args.consent}")
        print(f"✓ consent {args.consent} revoked")
    elif args.sub == "status":
        snap = client.call("GET", "/snapshot")
        s = snap["settings"]
        print(f"auto_publish: {s['auto_publish']}   provider: {s['provider']}   "
              f"token present: {s['token_present']}   token expires: {s.get('token_expires_at')}")
        nxt = snap.get("next_scheduled_publication")
        print(f"next scheduled: {nxt['post_id'] + ' at ' + nxt['slot_utc'] if nxt else 'none'}")
        for p in snap.get("posts", []):
            print(f"- {p['post_id']} [{p['state']}]")
    elif args.sub == "sync":
        out = cloud.sync(store, client, verify=args.verify)
        print(f"✓ pipeline mirrored to the cloud dashboard ({out.get('bytes')} bytes, "
              f"sha256 {str(out.get('sha256'))[:12]}) — open <api_base>/pipeline/")
        if args.verify:
            checks = out["checks"]
            _annotate("lce cloud sync --verify", checks)
            for c in checks:
                print(f"{'✓' if c['status'] == cloud.OK else '✗'} {c['check']}: {c['detail']}")
            if any(c["status"] != cloud.OK for c in checks):
                return 2
    elif args.sub == "migrate":
        out = cloud.migrate(client, apply=args.apply)
        if args.apply:
            print(f"✓ applied now: {', '.join(out.get('applied_now', [])) or 'nothing pending'}")
        print(f"applied: {', '.join(out.get('applied', [])) or 'none'}")
        print(f"pending: {', '.join(out.get('pending', [])) or 'none'}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::notice title=lce cloud migrate::applied now: {out.get('applied_now', [])} | "
                  f"applied: {out.get('applied', [])} | pending: {out.get('pending', [])}")
        return 1 if out.get("pending") else 0
    elif args.sub == "pull":
        for c in cloud.pull(store, client):
            print(f"✓ {c['post_id']} → {c['state']} (mirrored)")
        print("✓ pulled")
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
    p = cmd("privacy-scan", cmd_privacy_scan, "scan this repository for private data")
    p.add_argument("--root", default=".")
    p.add_argument("--history", action="store_true", help="also search all commits")
    cmd("privacy-denylist", cmd_privacy_denylist,
        "derive a local denylist from the private data directory")
    cmd("check-data-dir", cmd_check_data_dir, "verify the data directory").add_argument(
        "path", nargs="?", default=None)
    cmd("status", cmd_status, "readiness and post overview")
    p = cmd("readiness", cmd_readiness, "end-to-end readiness of your real setup")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-keychain", action="store_true", help="do not query the Keychain")

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
    p.add_argument("--theme", default=None, help="brand theme id (profile/brand.yaml)")
    p.add_argument("--chapter", default=None, help="narrative chapter id")
    p.add_argument("--evidence", choices=["personal", "external"], default=None,
                   help="personal needs a PUBLIC story; default follows the theme")
    p.add_argument("--objective", default=None, help="content objective id (voice.yaml objectives)")

    g = group("brand", "personal brand strategy")
    p = gcmd(g, "status", cmd_brand_status, "pillar balance, themes, evidence, problems")
    p.add_argument("--date", default=None)
    p.add_argument("--json", action="store_true")
    p = gcmd(g, "next", cmd_brand_next, "recommended next content moves")
    p.add_argument("--date", default=None)
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--json", action="store_true")

    g = group("image", "image decision before approval (none is allowed)")
    p = gcmd(g, "decide", cmd_image_decide, "record the image decision for a post")
    p.add_argument("post")
    p.add_argument("--kind", required=True, choices=["none", "source_image", "diagram",
                                                     "architecture", "screenshot", "chart",
                                                     "generated_concept"])
    p.add_argument("--rationale", required=True, help="why this image (or none) serves the post")
    p.add_argument("--file", default=None, help="PNG/JPG/GIF; copied into the post folder")
    p.add_argument("--relation", default=None, help="what it shows and how it supports the post")
    p.add_argument("--alt", default=None, help="alt text")
    p.add_argument("--origin", choices=["own_creation", "owner_screenshot", "owner_photo",
                                        "source_publication", "licensed_stock", "generated"])
    p.add_argument("--usage", choices=["owned", "licensed", "permitted", "public_domain",
                                       "needs_review"])
    p.add_argument("--source-url", dest="source_url", default=None)
    p.add_argument("--credit", default=None)
    p.add_argument("--license", default=None)
    p.add_argument("--method", default=None, help="generation tool/code for generated images")
    p.add_argument("--prompt", default=None)
    p.add_argument("--by", default="agent", help="who decided (agent or owner)")
    p.add_argument("--text-only-reason", dest="text_only_reason", default=None,
                   choices=["text_carries_point", "no_relevant_visual", "no_rights_safe_source",
                            "personal_story_without_owner_photo", "would_be_decorative",
                            "no_suitable_licensed_image"],
                   help="required with --kind none: why the post stays text-only")
    p.add_argument("--concept", default=None, help="the idea the image communicates (LCE-041)")
    p.add_argument("--visual-type", dest="visual_type", default=None,
                   help="flow, process, decision_tree, framework, relationship_map, comparison, matrix, "
                        "chart, photo, screenshot, illustration")
    p.add_argument("--relevance-reason", dest="relevance_reason", default=None,
                   help="what the image adds beyond the text")
    p = gcmd(g, "diagram", cmd_image_diagram,
             "conceptual diagram of the post's idea from a spec file (visuals extra); "
             "attached only if the media relevance check accepts it")
    p.add_argument("post")
    p.add_argument("--spec", default=None, help="YAML spec: visual_type, concept, relevance_reason, "
                   "title, nodes [{label, note}], outcomes, footer_claim, alt_text (see docs/MEDIA.md)")
    p.add_argument("--title", default=None, help="(legacy) title; goes through the same relevance check")
    p.add_argument("--item", action="append", default=[], help="(legacy) item")
    p.add_argument("--footer", default="")
    p = gcmd(g, "commons", cmd_image_commons,
             "attach a Wikimedia Commons file with a reuse licence (PD, CC0, CC BY, CC BY-SA)")
    p.add_argument("post")
    p.add_argument("--title", required=True, help="Commons file title, e.g. File:Example.jpg")
    p.add_argument("--relation", required=True)
    p.add_argument("--alt", required=True)
    p.add_argument("--rationale", required=True)
    p.add_argument("--concept", required=True, help="the idea the image communicates")
    p.add_argument("--visual-type", dest="visual_type", required=True, help="e.g. photo, illustration")
    p.add_argument("--relevance-reason", dest="relevance_reason", required=True,
                   help="what the image adds beyond the text")
    p = gcmd(g, "show", cmd_image_show, "the post's media decision (type, status, source, rights)")
    p.add_argument("post")
    p = gcmd(g, "chart", cmd_image_chart, "chart from the post's recorded claims (visuals extra)")
    p.add_argument("post")
    p.add_argument("--claim", type=int, action="append", default=[],
                   help="0-based index of a recorded claim to include (repeatable)")
    p = gcmd(g, "check", cmd_image_check, "verify the image decision")
    p.add_argument("post")

    g = group("refresh", "same-day freshness check before publication (never publishes)")
    p = gcmd(g, "run", cmd_refresh, "check every post planned/scheduled for the day (sources, claims, media)")
    p.add_argument("--as-of", dest="as_of", default=None,
                   help="publication day (default: today, local time zone)")
    p.add_argument("--post", action="append", default=[], help="also check this post (repeatable)")
    p.add_argument("--only", action="store_true", help="check only the --post ids")
    p.add_argument("--dry-run", action="store_true", help="print the record, write nothing")
    p.add_argument("--push", action="store_true", help="include cloud-scheduled posts and send the results")
    p.add_argument("--by", default="workflow")
    p = gcmd(g, "research", cmd_refresh, "record a session's fresh research result")
    p.add_argument("post")
    p.add_argument("--source", action="append", default=[], required=True)
    p.add_argument("--note", required=True)
    p.add_argument("--material", choices=["yes", "no"], required=True)
    p.add_argument("--as-of", dest="as_of", default=None)
    p.add_argument("--by", default="session")
    p = gcmd(g, "apply", cmd_refresh,
             "material change: new text through the full pipeline (approval discarded)")
    p.add_argument("post")
    p.add_argument("--file", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--source", action="append", default=[], required=True)
    p.add_argument("--as-of", dest="as_of", default=None)
    p.add_argument("--by", default="session")
    p = gcmd(g, "finish", cmd_refresh, "after a new media decision: QA, duplicate check, approval artifact")
    p.add_argument("post")
    p.add_argument("--as-of", dest="as_of", default=None)
    p.add_argument("--by", default="session")
    p = gcmd(g, "pending", cmd_refresh_manual, "posts whose owner asked for a refresh (LCE-041)")
    p.add_argument("--json", action="store_true")
    p = gcmd(g, "package", cmd_refresh_manual,
             "manual refresh: a new post package (text, sources, claims, media) through every check")
    p.add_argument("post")
    p.add_argument("--file", required=True, help="YAML: text|text_file, reason, sources [{url, title}], "
                   "claims [{text, source_url}], media {spec: <file|dict> | "
                   "text_only: {reason, rationale}}")
    p.add_argument("--as-of", dest="as_of", default=None)
    p.add_argument("--by", default="session")
    p = gcmd(g, "unskip", cmd_refresh_manual,
             "the owner skipped a post but meant Refresh: reopen the slot and request a replacement")
    p.add_argument("post")
    p.add_argument("--note", default="")
    p.add_argument("--by", required=True, help="who asked (e.g. owner via chat)")
    p = gcmd(g, "push", cmd_refresh, "send today's latest freshness records to the cloud gate")
    p.add_argument("--as-of", dest="as_of", default=None)
    p = gcmd(g, "show", cmd_refresh, "the post's freshness history")
    p.add_argument("post")

    g = group("versions", "earlier versions of a post package (kept by every refresh)")
    p = gcmd(g, "list", cmd_versions, "the post's preserved versions")
    p.add_argument("post")
    p = gcmd(g, "restore", cmd_versions, "make version N the current candidate again (approval discarded)")
    p.add_argument("post")
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--by", default="session")

    g = group("analytics", "metrics of published posts and what they teach")
    p = gcmd(g, "record", cmd_analytics_record, "record metrics you can see for a published post")
    p.add_argument("post")
    for m in ("impressions", "members-reached", "reactions", "comments", "reposts", "clicks",
              "followers-gained"):
        p.add_argument(f"--{m}", dest=m.replace("-", "_"), type=int, default=None)
    p.add_argument("--at", default=None, help="when you read the numbers (ISO; default now)")
    p.add_argument("--note", default=None)
    p = gcmd(g, "import", cmd_analytics_import, "import metrics from a CSV you exported or typed")
    p.add_argument("file")
    p = gcmd(g, "insights", cmd_analytics_insights, "what performs, saturation")
    p.add_argument("--min-sample", type=int, default=3)
    p.add_argument("--json", action="store_true")
    p = gcmd(g, "suggest-mix", cmd_analytics_suggest_mix, "suggested pillar mix (not applied)")
    p.add_argument("--min-sample", type=int, default=3)

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
    p = gcmd(g, "objective", cmd_post_objective, "record the content objective (voice.yaml objectives)")
    p.add_argument("post")
    p.add_argument("objective")
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
    p = gcmd(g, "brief", cmd_jobs_brief, "brand-aware content brief for pending agent work")
    p.add_argument("job", nargs="?", default=None)
    p.add_argument("--json", action="store_true")
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
    p.add_argument("target", help="post id, 'reconcile' or 'manual'")
    p.add_argument("post", nargs="?", default=None, help="post id (with 'reconcile')")
    p.add_argument("--dry-run", action="store_true", help="show the request; send nothing")
    p.add_argument("--published-url", default=None)
    p.add_argument("--not-published", action="store_true")
    p.add_argument("--published-at", default=None, help="with 'manual': when you posted it (ISO)")

    g = group("linkedin", "LinkedIn account settings (no secrets are ever printed)")
    gcmd(g, "status", cmd_linkedin_status, "config, token presence and expiry")
    gcmd(g, "whoami", cmd_linkedin_whoami, "look up your person URN (network call)")

    g = group("cloud", "cloud publisher (Cloudflare Worker)")
    gcmd(g, "push", cmd_cloud, "delegate an approved post to the cloud (interactive)").add_argument("post")
    p = gcmd(g, "consent", cmd_cloud, "schedule a delegated post for one slot (interactive)")
    p.add_argument("post")
    p.add_argument("--slot", default=None,
                   help="slot id, e.g. 2026-10-07-wed-0030 (default: the post's scheduled job slot)")
    gcmd(g, "revoke", cmd_cloud, "revoke a consent").add_argument("consent")
    gcmd(g, "status", cmd_cloud, "kill switch, token presence, next publication")
    gcmd(g, "doctor", cmd_cloud, "read-only production preflight: which owner gate is still open")
    p = gcmd(g, "smoke", cmd_cloud, "unauthenticated production check: reachable and fail-closed")
    p.add_argument("--api-base", help="Worker URL (default: $LCE_API_BASE, then config/cloud.yaml)")
    p.add_argument("--wait", type=int, default=0, help="seconds to wait for /api/health (deploys)")
    gcmd(g, "pull", cmd_cloud, "mirror cloud outcomes into local posts")
    p = gcmd(g, "migrate", cmd_cloud, "D1 migration status; --apply applies pending migrations")
    p.add_argument("--apply", action="store_true",
                   help="apply pending migrations through the Worker (sends the typed phrase)")
    p = gcmd(g, "sync", cmd_cloud, "mirror the private pipeline to the cloud dashboard (read-only view)")
    p.add_argument("--verify", action="store_true",
                   help="read the mirror back: same sha256 and posts, no leaks")
    p = gcmd(g, "decisions", cmd_cloud,
             "owner decisions from the cloud Control Center; --apply applies them to this repository")
    p.add_argument("--apply", action="store_true",
                   help="apply pending decisions (hash-checked) and resolve them in the cloud")
    p = gcmd(g, "identity", cmd_cloud,
             "verify LINKEDIN_TOKEN with LinkedIn through the Worker (read-only) and show the person URN")
    p.add_argument("--write", action="store_true", help="record the person URN in config/linkedin.yaml")
    p.add_argument("--api-version", default=None,
                   help="Linkedin-Version (YYYYMM) when config/linkedin.yaml has none yet")
    p = gcmd(g, "configure", cmd_cloud, "send timezone, cadence and LinkedIn settings (no secrets)")
    p.add_argument("--dry-run", action="store_true")

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
