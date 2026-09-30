"""Manual publication record, metrics, insights, saturation, mix suggestion (fictional data)."""

import json
from datetime import date

import pytest
from conftest import awaiting_post

from lce import analytics, brand, publishing
from lce.approval import approve, mark_ready
from lce.store import StoreError

TTY = lambda: True  # noqa: E731
URL = "https://www.linkedin.com/feed/update/urn:li:share:7000000000000000042/"


def ready_post(store):
    pid = awaiting_post(store)
    h = store.load_post(pid)["content_hash"]
    approve(store, pid, h[:12], confirm=lambda _: f"APPROVE {pid}", is_tty=TTY)
    mark_ready(store, pid)
    return pid


# ── manual publication ───────────────────────────────────────────────
def test_manual_publication_is_human_only_and_bound_to_the_approval(store):
    pid = ready_post(store)
    with pytest.raises(StoreError, match="interactive terminal"):
        publishing.record_manual(store, pid, url=URL, confirm=lambda _: f"PUBLISHED {pid}",
                                 is_tty=lambda: False)
    with pytest.raises(StoreError, match="LinkedIn post URL"):
        publishing.record_manual(store, pid, url="https://example.com/x",
                                 confirm=lambda _: f"PUBLISHED {pid}", is_tty=TTY)
    with pytest.raises(StoreError, match="phrase"):
        publishing.record_manual(store, pid, url=URL, confirm=lambda _: "yes", is_tty=TTY)
    out = publishing.record_manual(store, pid, url=URL, published_at="2025-05-06T09:20:00-05:00",
                                   confirm=lambda _: f"PUBLISHED {pid}", is_tty=TTY)
    pub = out["publication"]
    assert out["post"]["state"] == "PUBLISHED"
    assert pub["provider"] == "manual" and pub["verified_by"] == "owner"
    assert pub["remote_id"] == "urn:li:share:7000000000000000042"
    assert pub["published_at"] == "2025-05-06T14:20:00+00:00"
    with pytest.raises(StoreError, match="READY_TO_PUBLISH"):
        publishing.record_manual(store, pid, url=URL, confirm=lambda _: f"PUBLISHED {pid}",
                                 is_tty=TTY)


def test_manual_publication_refuses_changed_text(store):
    pid = ready_post(store)
    (store.post_dir(pid) / "post.md").write_text("changed after approval\n")
    with pytest.raises(StoreError, match="no longer matches"):
        publishing.record_manual(store, pid, url=URL, confirm=lambda _: f"PUBLISHED {pid}",
                                 is_tty=TTY)


# ── metrics ─────────────────────────────────────────────────────────
def fake_published(store, n, *, pillar, theme, topic, impressions, reactions, day=6, hour=14):
    pid = f"202505{day:02d}-post-{n}"
    store.save_post({"post_id": pid, "created_at": "2025-05-01T00:00:00+00:00", "language": "en",
                     "plan_date": f"2025-05-{day:02d}", "topic": topic, "pillar": pillar,
                     "format": "text", "brand": {"theme": theme, "evidence": "external"},
                     "state": "PUBLISHED", "history": []})
    (store.post_dir(pid) / "post.md").write_text(f"Short hook {n}\n\nBody text.\n")
    at = f"2025-05-{day:02d}T{hour:02d}:00:00+00:00"
    (store.post_dir(pid) / "publication.json").write_text(json.dumps({
        "post_id": pid, "provider": "manual", "idempotency_key": "0" * 64,
        "approved_hash": "0" * 64, "commentary_hash": "0" * 64, "state": "published",
        "url": f"https://www.linkedin.com/feed/update/urn:li:share:{n}/", "published_at": at,
        "verified_by": "owner", "attempts": []}))
    analytics.record(store, pid, {"impressions": impressions, "reactions": reactions},
                     at=f"2025-05-{day + 3:02d}T12:00:00+00:00")
    return pid


def test_metrics_only_for_published_posts_and_validated(store):
    pid = ready_post(store)
    with pytest.raises(StoreError, match="PUBLISHED"):
        analytics.record(store, pid, {"impressions": 10})
    pid = fake_published(store, 1, pillar="automation", theme="observations", topic="a",
                         impressions=100, reactions=5)
    with pytest.raises(StoreError, match="at least one"):
        analytics.record(store, pid, {"clicks": 3})
    with pytest.raises(StoreError, match="negative"):
        analytics.record(store, pid, {"impressions": -1})
    assert analytics.load(store, pid)["snapshots"][0]["source"] == "manual"


def test_csv_import_matches_by_url_or_post_id_and_reports_unmatched(store, tmp_path):
    pid = fake_published(store, 7, pillar="automation", theme="observations", topic="a",
                         impressions=100, reactions=5)
    f = tmp_path / "m.csv"
    f.write_text("url,at,impressions,reactions,comments\n"
                 "https://www.linkedin.com/feed/update/urn:li:share:7/,2025-05-20T08:00:00+00:00,\"1,200\",9,2\n"
                 "https://www.linkedin.com/feed/update/urn:li:share:999/,,5,1,0\n")
    out = analytics.import_csv(store, str(f))
    assert out == {"recorded": 1, "unmatched_rows": [3]}
    last = analytics.load(store, pid)["snapshots"][-1]
    assert last["impressions"] == 1200 and last["source"] == "csv"


def test_insights_need_enough_data_and_find_saturation(store):
    for n in range(3):
        fake_published(store, 10 + n, pillar="automation", theme="observations",
                       topic="Routing rules for service desks", impressions=1000, reactions=50,
                       day=6 + n)
    fake_published(store, 20, pillar="operations", theme="practical-how",
                   topic="Incident review template", impressions=1000, reactions=10, day=10)
    ins = analytics.insights(store, date(2025, 5, 12))
    pillar = {g["value"]: g for g in ins["groups"]["pillar"]}
    assert pillar["automation"]["enough_data"] and pillar["automation"]["median_rate"] == 0.05
    assert not pillar["operations"]["enough_data"]
    assert ins["overall"]["posts"] == 4
    assert ins["saturated_topics"][0]["topic"] == "Routing rules for service desks"
    assert {g["value"] for g in ins["groups"]["weekday"]} >= {"tue"}


def test_suggest_mix_is_bounded_and_needs_data(store):
    assert analytics.suggest_mix(store)["suggested"] is None
    for n in range(3):
        fake_published(store, 30 + n, pillar="automation", theme="observations", topic=f"t{n}",
                       impressions=1000, reactions=80, day=6 + n)
        fake_published(store, 40 + n, pillar="operations", theme="practical-how", topic=f"u{n}",
                       impressions=1000, reactions=20, day=6 + n)
    out = analytics.suggest_mix(store)
    cur, sug = out["current"], out["suggested"]
    assert sug["automation"] > cur["automation"] and sug["operations"] < cur["operations"]
    assert all(abs(sug[p] - cur[p]) <= 0.15 for p in cur)
    assert store.brand()["mix"]["pillars"] == cur        # nothing applied


def test_theme_performance_breaks_ties_in_recommendations(store):
    for n in range(3):
        fake_published(store, 50 + n, pillar="operations", theme="practical-how", topic=f"v{n}",
                       impressions=1000, reactions=90, day=6 + n)
    assert analytics.theme_rates(store) == {"practical-how": 0.09}
    rec = next(r for r in brand.recommend(store, date(2025, 5, 12), count=3)
               if r["theme"] == "practical-how")
    assert any("engagement rate" in reason for reason in rec["reasons"])


def test_cli_analytics(store, tmp_path, capsys):
    from lce.cli import main

    pid = fake_published(store, 60, pillar="automation", theme="observations", topic="a",
                         impressions=100, reactions=5)
    args = ["--data-dir", str(store.root), "analytics"]
    assert main([*args, "record", pid, "--impressions", "150", "--comments", "2"]) == 0
    assert main([*args, "insights"]) == 0
    assert main([*args, "suggest-mix"]) == 0
    out = capsys.readouterr().out
    assert "metrics recorded" in out and "1 post(s) with metrics" in out and "no suggestion" in out
