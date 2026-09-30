from conftest import GOOD_POST, selected_post

from lce.qa import run_checks, run_qa
from lce.rules import load_ruleset

RULES = load_ruleset("en")
PROFILE = {"topics": {"avoid": ["celebrity gossip"]}, "forbidden": {"claims": ["revenue figures"]}}
VOICE = {"avoid_phrases": ["synergy"], "emoji_policy": {"max_per_post": 0},
         "hashtag_policy": {"max": 2, "placement": "end"}, "formatting": {"max_chars": 900}}
STORIES = {
    "pub": {"story_id": "pub", "publication_status": "PUBLIC",
            "allowed_claims": ["Cut handling time from 30 to 12 minutes."], "sensitive_terms": []},
    "priv": {"story_id": "priv", "publication_status": "PRIVATE", "sensitive_terms": ["Acme GmbH"]},
}


def codes(text, post=None, denylist=()):
    post = post or {"sources": [], "claims": [], "stories_used": []}
    return {f.code for f in run_checks(text, rules=RULES, voice=VOICE, profile=PROFILE, post=post,
                                       stories=STORIES, denylist=list(denylist))}


def test_missing_text():
    assert codes("   ") == {"text.missing"}


def test_length_and_structure():
    assert "length.hard_max" in codes("word " * 700)
    assert "structure.wall_of_text" in codes("x " * 400)
    assert "structure.markdown" in codes("## Heading\n\nSome **bold** text here.")


def test_forbidden_bait_hashtags_emoji_placeholders():
    c = codes("We need synergy 🚀\n\nComment YES below if [insert company] helps.\n\n#a #b #c")
    assert {"phrase.forbidden", "emoji.excess", "bait.engagement", "placeholder",
            "hashtags.excess"} <= c
    assert "topic.forbidden" in codes("A post about celebrity gossip today.")


def test_repetition():
    s = "This sentence is repeated for testing purposes. "
    assert "repetition.sentence" in codes(s + "\n\n" + s)


def test_sources_and_numbers():
    assert "source.missing" in codes("Studies show that teams struggle with tickets.")
    assert "claim.unsupported_number" in codes("We saw a 45% drop in tickets.")
    backed = {"sources": [], "claims": [], "stories_used": ["pub"]}
    assert "claim.unsupported_number" not in codes("Handling time went from 30 to 12 minutes.",
                                                    post=backed)
    sourced = {"sources": [{"url": "https://s.example"}],
               "claims": [{"text": "45% of teams drop tickets", "source_url": "https://s.example"}],
               "stories_used": []}
    assert "claim.unsupported_number" not in codes("A report found 45% of teams drop tickets.",
                                                    post=sourced)


def test_personal_claim_requires_public_story():
    assert "claim.personal_without_story" in codes("I led a migration of our service desk.")
    ok = {"sources": [], "claims": [], "stories_used": ["pub"]}
    assert "claim.personal_without_story" not in codes("I led a migration of our service desk.",
                                                       post=ok)


def test_privacy_leaks():
    assert "privacy.denylist" in codes("Worked with SecretCorp on this.", denylist=["SecretCorp"])
    assert "privacy.contact" in codes("Mail me: someone@" + "realmail.com")
    assert "privacy.sensitive_term" in codes("At Acme GmbH we tried this.")
    assert "privacy.private_story" in codes("x" * 300, post={"stories_used": ["priv"],
                                                             "sources": [], "claims": []})


def test_structural_ai_tells():
    assert "pattern.reveal-question" in codes("We rebuilt the queue. The result? Fewer escalations.")
    assert "pattern.reveal-bridge" in codes("Most rollouts stall.\n\nHere's why that happens.")
    assert "pattern.staccato-stack" in codes("No dashboards. No meetings. Just a checklist.")
    assert "pattern.performed-sincerity" in codes("Let me be honest about the rollout.")
    plain = codes("The rollout stalled because nobody owned the intake form.")
    assert not {c for c in plain if c.startswith("pattern.")}


def test_generic_close():
    body = "Routing rules beat a model when the categories are stable.\n\n"
    assert "structure.generic_close" in codes(body + "Thoughts?")
    assert "structure.generic_close" in codes(body + "What do you think?\n\n#itsm")
    assert "structure.generic_close" not in codes(
        body + "Which category in your queue changes most often?")


def test_cta_respects_voice_profile():
    body = "Routing rules beat a model when the categories are stable.\n\n"
    ask = body + "Which category in your queue changes most often?"
    dm = body + "If you are rebuilding intake, DM me."

    def cta_codes(text, allowed):
        voice = {**VOICE, "cta": {"allowed": allowed}}
        return {f.code for f in run_checks(text, rules=RULES, voice=voice, profile=PROFILE,
                                           post={"sources": [], "claims": [], "stories_used": []},
                                           stories=STORIES, denylist=[])}

    assert "cta.not_allowed" in cta_codes(ask, False)
    assert "cta.not_allowed" in cta_codes(dm, False)
    assert "cta.not_allowed" not in cta_codes(ask, True)
    assert "cta.not_allowed" not in cta_codes(body + "Stable categories are the precondition.", False)
    assert "cta.not_allowed" not in codes(ask)  # no CTA policy recorded


def test_repeated_hook_or_closing_against_recent_posts():
    body = ("Routing rules beat a model when the categories are stable.\n\n"
            "Write them down before you automate anything.\n\n"
            "Which category in your queue changes most often?")
    def run(text, recent):
        return {f.code for f in run_checks(text, rules=RULES, voice=VOICE, profile=PROFILE,
                                           post={"sources": [], "claims": [], "stories_used": []},
                                           stories=STORIES, denylist=[], recent=recent)}
    same_hook = "Routing rules beat a model for most teams.\n\nOther body.\n\nA different ending."
    same_close = "Another opening line entirely.\n\nBody.\n\nWhich category in your queue changes most often?"
    unrelated = "Service desks need owners, not tools.\n\nBody text here.\n\nThat is the whole lesson."
    assert "repetition.hook_recent" in run(body, [same_hook])
    assert "repetition.closing_recent" in run(body, [same_close])
    assert not {"repetition.hook_recent", "repetition.closing_recent"} & run(body, [unrelated])
    assert not {"repetition.hook_recent", "repetition.closing_recent"} & run(body, [])


def test_run_qa_compares_with_recent_posts_in_the_store(store):
    from datetime import date

    from lce.planning import select
    from lce.posts import save_draft, save_humanized
    from lce.research import add_candidate

    first = selected_post(store)
    save_draft(store, first, GOOD_POST)
    save_humanized(store, first, GOOD_POST)
    cand = add_candidate(store, title="Second topic", origin="manual")
    second = select(store, candidate_id=cand["candidate_id"], pillar="operations",
                    angle="another angle", fmt="text", plan_date=date(2025, 5, 8))["post_id"]
    copy = GOOD_POST.replace("twelve keyword rules", "a dozen keyword rules")
    save_draft(store, second, copy)
    save_humanized(store, second, copy)
    report = run_qa(store, second, denylist=[])
    codes_ = {w["code"] for w in report["warnings"]}
    assert {"repetition.hook_recent", "repetition.closing_recent"} <= codes_


def test_non_ready_language_fails(store):
    pid = selected_post(store)
    post = store.load_post(pid)
    post["language"] = "fa"
    store.save_post(post)
    from lce.posts import save_draft, save_humanized

    save_draft(store, pid, GOOD_POST)
    save_humanized(store, pid, GOOD_POST)
    report = run_qa(store, pid, denylist=[])
    assert report["status"] == "failed"
    assert report["errors"][0]["code"] == "language.not_ready"
    assert store.load_post(pid)["state"] == "NEEDS_REVISION"


def test_run_qa_passes_good_post_and_records(store):
    from lce.posts import save_draft, save_humanized

    pid = selected_post(store)
    save_draft(store, pid, GOOD_POST)
    save_humanized(store, pid, GOOD_POST)
    report = run_qa(store, pid, denylist=[])
    assert report["status"] == "passed", report
    post = store.load_post(pid)
    assert post["state"] == "QA_PASSED" and post["qa"]["content_hash"] == post["content_hash"]
    assert (store.post_dir(pid) / "qa.json").exists()
