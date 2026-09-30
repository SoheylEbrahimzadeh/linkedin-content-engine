"""End-to-end dry run of the local pipeline on the synthetic demo persona.

research candidate → select → draft → humanize → QA → duplicate check → image
decision → approval artifact → human approval (hash-bound) → ready → publish
through a fake LinkedIn transport → publication record → metrics → learning.

No network (conftest blocks it), no token store, no private data. The cloud
path (consent, cron claim, Worker publisher) is covered by cloud/test.
"""

from datetime import date

import pytest
from fakes import FAKE_TOKEN, FakeTransport, created

from lce import analytics, publishing
from lce.approval import approve, mark_ready, prepare
from lce.dupcheck import run_dupcheck
from lce.images import decide
from lce.planning import select
from lce.posts import save_draft, save_humanized
from lce.publish.credentials import MemoryTokenStore
from lce.qa import run_qa
from lce.research import add_candidate, add_claim
from lce.store import StoreError

SOURCE = "https://research.example/service-desk-report"
CLAIM = "In the survey, 37% of service desks still route tickets by hand."
TEXT = (
    "Most service desks still sort their tickets by hand.\n\n"
    "A recent survey found that 37% of service desks still route tickets by hand, "
    "which means the first automation win is often not a model at all.\n\n"
    "Writing down the routing rules the team already follows is cheaper, easier to "
    "explain and easier to audit than training anything.\n\n"
    "Which routing rule in your queue changes least often?\n"
)
URN = "urn:li:share:7000000000000000042"
TTY = lambda: True  # noqa: E731


def _states(store, pid):
    return [e["state"] for e in store.load_post(pid).get("history", [])]


def test_full_pipeline_dry_run(store):
    # research: a web candidate must carry a source; a claim must cite one of its sources
    with pytest.raises(StoreError):
        add_candidate(store, title="no source", origin="web_search")
    cand = add_candidate(store, title="Service desks still route tickets by hand",
                         origin="web_search", urls=[SOURCE], publisher="Example Research")
    assert cand["untrusted"] is True
    with pytest.raises(StoreError):
        add_claim(store, cand["candidate_id"], CLAIM, "https://elsewhere.example/")
    add_claim(store, cand["candidate_id"], CLAIM, SOURCE)

    # selection on external evidence
    post = select(store, candidate_id=cand["candidate_id"], pillar="automation",
                  angle="write the rules down before automating", fmt="text",
                  plan_date=date(2025, 5, 13), evidence="external")
    pid = post["post_id"]
    assert post["state"] == "SELECTED"
    assert [s["url"] for s in post["sources"]] == [SOURCE]

    # an invented number is blocked before any approval
    save_draft(store, pid, TEXT.replace("37%", "52%"))
    save_humanized(store, pid, TEXT.replace("37%", "52%"))
    report = run_qa(store, pid, denylist=[])
    assert report["status"] == "failed"
    assert "claim.unsupported_number" in {e["code"] for e in report["errors"]}

    # draft → humanize → QA → duplicate check → image decision → approval artifact
    save_draft(store, pid, TEXT)
    save_humanized(store, pid, TEXT)
    assert run_qa(store, pid, denylist=[])["status"] == "passed"
    assert run_dupcheck(store, pid)["status"] == "passed"
    decide(store, pid, kind="none", rationale="the claim carries the post; no visual adds meaning")
    prepare(store, pid)
    assert (store.post_dir(pid) / "APPROVAL.md").exists()
    h = store.load_post(pid)["content_hash"]

    # human approval: wrong hash and wrong phrase are refused, then the owner approves
    with pytest.raises(StoreError):
        approve(store, pid, "0" * 12, confirm=lambda _: f"APPROVE {pid}", is_tty=TTY)
    with pytest.raises(StoreError):
        approve(store, pid, h[:12], confirm=lambda _: "yes", is_tty=TTY)
    approve(store, pid, h[:12], confirm=lambda _: f"APPROVE {pid}", is_tty=TTY)
    mark_ready(store, pid)
    assert store.load_post(pid)["state"] == "READY_TO_PUBLISH"

    # publish through a fake transport: exactly one request with the approved text
    s = store.settings()
    s["publisher"] = {"provider": "linkedin_api"}
    store.write_doc(store.settings_path, "settings", s)
    (store.root / "config" / "linkedin.yaml").write_text(
        "api_version: '202609'\nperson_urn: urn:li:person:TestPerson1\n")
    transport = FakeTransport(created(URN))
    pub = publishing.make_publisher(store, transport=transport, tokens=MemoryTokenStore(FAKE_TOKEN))
    out = publishing.publish(store, pid, pub, confirm=lambda _: f"PUBLISH {pid}", is_tty=TTY)
    assert len(transport.calls) == 1
    assert "37% of service desks" in transport.calls[0]["body"]["commentary"]

    # verification: state, URN, approved hash, timestamp
    record = publishing.load_publication(store, pid)
    assert out["post"]["state"] == "PUBLISHED"
    assert record["state"] == "published" and record["remote_id"] == URN
    assert record["approved_hash"] == store.load_post(pid)["approval"]["approved_hash"] == h
    assert record["verified_by"] == "api_response" and record["published_at"]
    # a second publish is refused and sends nothing
    with pytest.raises(StoreError):
        publishing.publish(store, pid, pub, confirm=lambda _: f"PUBLISH {pid}", is_tty=TTY)
    assert len(transport.calls) == 1

    # analytics → learning input; one post is not enough data for any conclusion
    analytics.record(store, pid, {"impressions": 1000, "reactions": 30, "comments": 5,
                                  "reposts": 1}, at=record["published_at"])
    rows = {r["post_id"]: r for r in analytics.performance(store)}
    assert rows[pid]["rate"] == 0.036
    assert rows[pid]["features"]["pillar"] == "automation"
    assert rows[pid]["features"]["evidence"] == "external"
    ins = analytics.insights(store, date(2025, 5, 20))
    assert ins["overall"]["posts"] == 1
    assert not any(g["enough_data"] for gs in ins["groups"].values() for g in gs)
    assert analytics.suggest_mix(store)["suggested"] is None

    # the state machine walked the documented path
    walked = _states(store, pid)
    for expected in ("DRAFTED", "HUMANIZED", "QA_PASSED", "AWAITING_APPROVAL", "APPROVED",
                     "READY_TO_PUBLISH", "PUBLISHING", "PUBLISHED"):
        assert expected in walked, (expected, walked)
    assert walked.index("APPROVED") < walked.index("PUBLISHING") < walked.index("PUBLISHED")
