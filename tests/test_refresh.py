"""LCE-040: same-day refresh — check, research, update; approval/hash safety."""

from datetime import date

import pytest
from conftest import GOOD_POST, awaiting_post
from test_media_pipeline import SPEC

from lce import cloud, images, refresh
from lce.posts import current_text
from lce.store import StoreError
from lce.textutil import content_hash
from lce.visuals import concept

DAY = date(2026, 10, 8)
URL = "https://example.com/report"
CLAIM = "Manual triage dropped from about 40 minutes a day to about 10"


def with_source(store, pid, claim=CLAIM):
    post = store.load_post(pid)
    post["sources"] = [{"url": URL, "title": "Fictional report"}]
    post["claims"] = [{"text": claim, "source_url": URL}]
    post["plan_date"] = DAY.isoformat()
    store.save_post(post)
    return pid


def page(*sentences):
    body = "<html><body><script>var x=1</script><p>" + "</p><p>".join(sentences) + "</p></body></html>"
    return lambda url: (200, url, body.encode())


def snapshot(store, pid):
    post = store.load_post(pid)
    d = store.post_dir(pid)
    return (
        post["state"],
        post.get("approval"),
        current_text(store, pid),
        (d / "APPROVAL.md").read_text(),
        (images.load(store, pid) or {}).get("sha256"),
    )


def test_unchanged_sources_preserve_text_image_and_approval(store):
    pid = with_source(store, awaiting_post(store))
    before = snapshot(store, pid)
    text = "In the study, manual triage dropped from about 40 minutes a day to about 10."
    rec = refresh.check(store, pid, as_of=DAY, fetch=page(text))
    assert rec["decision"] == "unchanged" and rec["status"] == "current" and rec["material_change"] is False
    assert rec["claims"][0]["status"] == "found" and rec["sources"][0]["status"] == "ok"
    assert len(rec["sources"][0]["content_sha256"]) == 64
    assert rec["approval"] == "pending" and rec["approval_effect"] == "preserved"
    assert snapshot(store, pid) == before  # nothing invalidated
    assert refresh.latest(store, pid)["checked_at"] == rec["checked_at"]
    assert "session" in rec["new_developments"]


def test_source_no_longer_supporting_a_claim_requires_an_update(store):
    pid = with_source(store, awaiting_post(store))
    before = snapshot(store, pid)
    rec = refresh.check(store, pid, as_of=DAY, fetch=page("The report was withdrawn."))
    assert rec["decision"] == "update_required" and rec["material_change"] is True
    assert rec["claims"][0]["status"] == "missing"
    assert snapshot(store, pid) == before  # a check never rewrites


@pytest.mark.parametrize(
    "fetch,status", [(lambda u: (403, u, b""), "blocked"), (lambda u: (500, u, b""), "unreachable")]
)
def test_unreadable_sources_need_a_sessions_review(store, fetch, status):
    pid = with_source(store, awaiting_post(store))
    rec = refresh.check(store, pid, as_of=DAY, fetch=fetch)
    assert rec["decision"] == "unverifiable" and rec["status"] == "needs_review"
    assert rec["sources"][0]["status"] == status and rec["claims"][0]["status"] == "unverified"


def test_session_research_confirms_or_flags(store):
    pid = with_source(store, awaiting_post(store))
    images.decide(store, pid, kind="none", rationale="text", text_only_reason="text_carries_point")
    with pytest.raises(StoreError):
        refresh.research(store, pid, as_of=DAY, sources=[], note="x", material=False)
    rec = refresh.research(
        store,
        pid,
        as_of=DAY,
        sources=[URL, "https://example.org/news"],
        note="No newer figures published since the report.",
        material=False,
    )
    assert rec["decision"] == "confirmed" and rec["status"] == "current" and len(rec["sources"]) == 2


def test_material_change_runs_the_full_pipeline_and_invalidates_approval(store):
    from lce.dupcheck import import_external

    pid = with_source(store, awaiting_post(store))
    import_external(
        store, "older-post", "Kanban limits work in progress so teams finish more than they start."
    )
    old_hash = content_hash(current_text(store, pid))
    old_artifact = store.load_post(pid)["approval"]["artifact_hash"]
    new = GOOD_POST.replace("Start with the boring rules.", "Start with the boring rules first.")
    rec = refresh.apply_update(
        store, pid, as_of=DAY, text=new, reason="source updated its figure", sources=[URL]
    )
    post = store.load_post(pid)
    assert rec["decision"] == "updated" and rec["approval_before"] == "pending"
    assert rec["approval_effect"] == "invalidated"
    from lce.revise import autofix

    new = autofix(new)[0]   # the writing gate's safe contractions are applied before storing
    assert rec["content_hash_before"] == old_hash and rec["content_hash"] == content_hash(new) != old_hash
    assert rec["steps"]["qa"] == "passed" and rec["steps"]["duplicate"]["status"] == "passed"
    assert rec["steps"]["duplicate"]["compared_against"] >= 1  # archive checked again
    assert post["state"] == "AWAITING_APPROVAL" and post["approval"]["artifact_hash"] != old_artifact
    assert rec["status"] == "update_awaiting_approval" and post["humanization"]["source"] == "session"


def test_update_that_duplicates_the_archive_is_stopped(store):
    from lce.dupcheck import import_external

    archived = GOOD_POST.replace("twelve", "eleven")
    pid = with_source(store, awaiting_post(store))
    import_external(store, "published-earlier", archived)  # already in the archive
    rec = refresh.apply_update(store, pid, as_of=DAY, text=archived, reason="r", sources=[URL])
    dup = rec["steps"]["duplicate"]
    assert dup["status"] == "failed" and dup["exact"] + dup["near"] >= 1
    assert store.load_post(pid)["state"] == "NEEDS_REVISION" and rec["status"] == "update_in_progress"


def test_unchanged_text_is_refused_and_cloud_posts_need_withdrawal(store):
    pid = with_source(store, awaiting_post(store))
    with pytest.raises(StoreError, match="unchanged"):
        refresh.apply_update(store, pid, as_of=DAY, text=current_text(store, pid), reason="r", sources=[URL])
    (store.post_dir(pid) / "delegation.json").write_text('{"approved_hash": "x"}')
    assert cloud.load_delegation(store, pid)
    with pytest.raises(StoreError, match="withdraw"):
        refresh.apply_update(store, pid, as_of=DAY, text="New text.", reason="r", sources=[URL])


TITLE = "Most small service teams do not need a model to sort tickets"
ITEMS = [
    "Start with the boring rules",
    "They cover more than you expect, and they are easy to explain to the team",
]


def legacy_text_dump(store, pid):
    """An image.yaml as LCE-038 wrote it: a checklist diagram restating the post verbatim."""
    from test_images import png

    images.decide(
        store, pid, kind="diagram", rationale="checklist made scannable",
        source_file=str(png(store.root / "legacy.png")),
        relation=f"restates the post's checklist verbatim: {TITLE} — " + "; ".join(ITEMS),
        alt_text="Checklist diagram of the post's two points, written out in full",
        provenance={"origin": "own_creation", "usage": "owned",
                    "generation": {"method": "lce image diagram"}})
    return images.load(store, pid)


def test_text_dump_image_is_stale_and_blocks_approval_until_replaced(store):
    """The Gartner case: a pre-LCE-041 verbatim diagram is rejected on refresh; a conceptual
    visual replaces it and the new image hash is bound to the new approval."""
    from lce.approval import prepare
    from lce.dupcheck import run_dupcheck
    from lce.qa import run_qa

    pid = with_source(store, awaiting_post(store))
    doc = legacy_text_dump(store, pid)
    rec = refresh.check(store, pid, as_of=DAY, fetch=page(CLAIM + "."))
    assert rec["media"]["status"] == "stale" and "text dump" in rec["media"]["note"]
    assert rec["status"] == "update_required"
    run_qa(store, pid, denylist=[])
    run_dupcheck(store, pid)
    with pytest.raises(StoreError, match="predates LCE-041"):
        prepare(store, pid)  # a text dump never reaches approval
    new_doc = concept(store, pid, SPEC)
    rec = refresh.finish(store, pid, as_of=DAY)
    post = store.load_post(pid)
    assert rec["status"] == "update_awaiting_approval" and post["state"] == "AWAITING_APPROVAL"
    assert post["approval"]["image_hash"] == new_doc["sha256"] != doc["sha256"]
    assert rec["image_sha256_before"] == doc["sha256"] and rec["image_sha256"] == new_doc["sha256"]
    assert rec["steps"]["media"] == "still_relevant"


def test_media_change_on_an_approved_post_discards_the_approval(store):
    pid = with_source(store, awaiting_post(store))
    post = store.load_post(pid)
    post["approval"] = {**post["approval"], "state": "approved", "approved_hash": post["content_hash"]}
    post["state"] = "APPROVED"
    store.save_post(post)
    concept(store, pid, SPEC)
    assert store.load_post(pid)["state"] == "HUMANIZED" and "approval" not in store.load_post(pid)


def test_due_posts_and_cloud_rows(store, monkeypatch):
    pid = with_source(store, awaiting_post(store))
    assert refresh.due_posts(store, DAY) == [pid]
    assert refresh.due_posts(store, date(2026, 10, 9)) == []
    # a run "as of" another day is a test: recorded as such and never sent to the Worker
    monkeypatch.setattr(refresh, "today_local", lambda _s: date(2026, 10, 2))
    rec = refresh.check(store, pid, as_of=DAY, fetch=page(CLAIM))
    assert rec["test_mode"] is True and refresh.cloud_rows(store, [pid]) == []
    monkeypatch.setattr(refresh, "today_local", lambda _s: DAY)
    assert refresh.check(store, pid, as_of=DAY, fetch=page(CLAIM))["test_mode"] is False
    [row] = refresh.cloud_rows(store, [pid])
    assert row["status"] == "current" and row["check_date"] == "2026-10-08"
    assert row["content_hash"] == content_hash(current_text(store, pid))


def test_freshness_file_validates_and_dry_run_writes_nothing(store, monkeypatch, capsys):
    from lce import cli
    from lce.validate import validate_dir

    pid = with_source(store, awaiting_post(store))
    monkeypatch.setattr(refresh, "http_fetch", page(CLAIM))
    defaults = {**refresh.check.__kwdefaults__, "fetch": page(CLAIM)}
    monkeypatch.setattr(refresh.check, "__kwdefaults__", defaults)
    run = ["--data-dir", str(store.root), "refresh", "run", "--as-of", "2026-10-08"]
    assert cli.main([*run, "--dry-run"]) == 0
    assert "unchanged → current" in capsys.readouterr().out and refresh.history(store, pid) == []
    assert cli.main(run) == 0
    assert len(refresh.history(store, pid)) == 1
    _, errors = validate_dir(store.root)
    assert errors == []


def test_snapshot_exposes_freshness(store):
    from lce.dashboard.snapshot import build_snapshot

    pid = with_source(store, awaiting_post(store))
    refresh.check(store, pid, as_of=DAY, fetch=page(CLAIM))
    view = next(p for p in build_snapshot(store, mode="real")["posts"] if p["post_id"] == pid)
    assert view["freshness"]["latest"]["status"] == "current" and len(view["freshness"]["history"]) == 1
