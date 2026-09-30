"""Deterministic quality checks. Errors block the post; warnings inform the reviewer.

The LLM (Claude Code) writes and rewrites; these checks decide. None of them
calls an LLM or the network.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass

from lce.posts import current_text, set_state
from lce.privacy.scan import load_denylist
from lce.rules import RulesetNotReady, ready_ruleset
from lce.state import PostState
from lce.store import DataStore, StoreError, now_iso
from lce.textutil import (
    EMAIL_RE,
    HASHTAG_RE,
    PHONE_RE,
    URL_RE,
    claim_numbers,
    content_hash,
    count_emojis,
    hashtags,
    paragraphs,
    sentences,
    words,
)

ERROR, WARNING = "error", "warning"
STOPWORDS = set(
    "a an and are as at be been but by can do for from has have how i if in into is it its "
    "just me more most my no not of on one or our out so that the their them then there these "
    "they this to up us was we what when which who why will with you your".split()
)
MARKDOWN_RE = re.compile(r"^#{1,6}\s|\*\*[^*\n]+\*\*|__[^_\n]+__|\[[^\]\n]+\]\(https?://", re.M)
BULLET_RE = re.compile(r"^\s*(?:[-*•▪◦]|\d+[.)])\s+", re.M)
TRIAD_RE = re.compile(r"\b[\w'-]+(?: [\w'-]+)?, [\w'-]+(?: [\w'-]+)?,? (?:and|or) [\w'-]+", re.I)
FLAGS = re.I | re.M


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str

    def render(self) -> str:
        return f"  [{self.severity}] {self.code}: {self.message}"


def _phrase_re(phrase: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.I)


def _allowed_numbers(post: dict, stories: dict[str, dict]) -> set[str]:
    texts = [c["text"] for c in post.get("claims", [])]
    for sid in post.get("stories_used", []):
        story = stories.get(sid, {})
        if story.get("publication_status") == "PUBLIC":
            texts.extend(story.get("allowed_claims", []))
    allowed: set[str] = set()
    for t in texts:
        allowed.update(claim_numbers(t))
    return allowed


def run_checks(text: str, *, rules: dict, voice: dict, profile: dict, post: dict,
               stories: dict[str, dict], denylist: list[str],
               brand: dict | None = None) -> list[Finding]:
    f: list[Finding] = []
    add = lambda code, sev, msg: f.append(Finding(code, sev, msg))  # noqa: E731
    body = text.strip()
    if not body:
        return [Finding("text.missing", ERROR, "the post has no text")]
    limits, metrics = rules["limits"], rules["metrics"]
    defaults = rules.get("defaults", {})

    # ── length & structure ────────────────────────────────────────────
    n = len(body)
    if n > limits["hard_max_chars"]:
        add("length.hard_max", ERROR, f"{n} characters; the limit is {limits['hard_max_chars']}")
    voice_max = voice.get("formatting", {}).get("max_chars")
    if voice_max and n > voice_max:
        add("length.voice_max", WARNING, f"{n} characters; your preference is ≤ {voice_max}")
    if n < limits["min_chars"]:
        add("length.too_short", WARNING, f"only {n} characters")
    paras = paragraphs(body)
    if n > limits["wall_of_text_chars"] and len(paras) == 1:
        add("structure.wall_of_text", ERROR, "long post without paragraph breaks")
    first_line = body.split("\n", 1)[0]
    if len(first_line) > limits["hook_max_chars"]:
        add("structure.hook_too_long", WARNING,
            f"first line is {len(first_line)} characters (≤ {limits['hook_max_chars']} recommended)")
    for i, p in enumerate(paras, 1):
        if len(p) > limits["max_paragraph_chars"]:
            add("structure.long_paragraph", WARNING, f"paragraph {i} has {len(p)} characters")
    if MARKDOWN_RE.search(body):
        add("structure.markdown", WARNING, "Markdown syntax is not rendered by LinkedIn")
    if voice.get("formatting", {}).get("bullets_allowed") is False and BULLET_RE.search(body):
        add("structure.bullets", WARNING, "bullet lists are disabled in your voice profile")

    # ── phrases ───────────────────────────────────────────────────────
    avoid = list(voice.get("avoid_phrases", [])) + list(voice.get("vocabulary", {}).get("avoid", []))
    for phrase in avoid:
        if _phrase_re(phrase).search(body):
            add("phrase.forbidden", ERROR, f"uses a phrase you asked to avoid: {phrase!r}")
    for term in profile.get("topics", {}).get("avoid", []) + profile.get("forbidden", {}).get(
        "claims", []
    ):
        if _phrase_re(term).search(body):
            add("topic.forbidden", ERROR, "mentions a topic/claim on your avoid list")
    for phrase in rules.get("lexicon", []):
        if _phrase_re(phrase).search(body):
            add("phrase.ai_tell", WARNING, f"AI-typical wording: {phrase!r}")
    for pat in rules.get("patterns", []):
        if re.search(pat["regex"], body, FLAGS):
            add(f"pattern.{pat['id']}", WARNING, pat["message"])
    for rx in rules.get("engagement_bait", []):
        if re.search(rx, body, FLAGS):
            add("bait.engagement", ERROR, "engagement-bait wording")
            break
    for rx in rules.get("hype", []):
        if re.search(rx, body, FLAGS):
            add("claim.hype", WARNING, "hype phrasing; back it with a concrete argument or remove")
            break
    for rx in rules.get("placeholders", []):
        if re.search(rx, body, re.M):
            add("placeholder", ERROR, "placeholder text left in the post")
            break

    # ── closing & CTA ─────────────────────────────────────────────────
    closing = next((c for c in (HASHTAG_RE.sub("", p).strip() for p in reversed(paras)) if c), "")
    last_sentence = (sentences(closing) or [""])[-1]
    if any(re.search(rx, last_sentence, re.I) for rx in rules.get("generic_close", [])):
        add("structure.generic_close", WARNING,
            "generic closing question; end on a specific point or a specific question")
    if voice.get("cta", {}).get("allowed") is False and (
        last_sentence.endswith("?")
        or any(re.search(rx, closing, re.I) for rx in rules.get("cta_markers", []))
    ):
        add("cta.not_allowed", WARNING, "the closing asks readers to act; your voice profile disables CTAs")

    # ── hashtags & emoji ──────────────────────────────────────────────
    tags = hashtags(body)
    policy = voice.get("hashtag_policy", {})
    max_tags = policy.get("max", defaults.get("max_hashtags", 3))
    if policy.get("placement") == "none" and tags:
        add("hashtags.not_allowed", ERROR, "your voice profile disallows hashtags")
    elif len(tags) > max_tags:
        add("hashtags.excess", ERROR, f"{len(tags)} hashtags (max {max_tags})")
    if tags and policy.get("placement") == "end" and len(hashtags(paras[-1])) != len(tags):
        add("hashtags.placement", WARNING, "hashtags should be at the end")
    emojis = count_emojis(body)
    max_emoji = voice.get("emoji_policy", {}).get("max_per_post", defaults.get("max_emojis", 1))
    if emojis > max_emoji:
        add("emoji.excess", ERROR, f"{emojis} emojis (max {max_emoji})")

    # ── repetition & style ────────────────────────────────────────────
    sents = sentences(body)
    norm = [" ".join(words(s)) for s in sents]
    dup = [s for s, c in Counter(x for x in norm if len(x) >= 20).items() if c > 1]
    if dup:
        add("repetition.sentence", ERROR, f"{len(dup)} sentence(s) repeated verbatim")
    toks = words(body)
    k = metrics["repeated_ngram_size"]
    grams = Counter(tuple(toks[i : i + k]) for i in range(len(toks) - k + 1))
    rep = [g for g, c in grams.items() if c > metrics["repeated_ngram_max"]
           and not set(g) <= STOPWORDS]
    if rep:
        add("repetition.ngram", WARNING, f"{len(rep)} phrase(s) repeated more than twice")
    openers = [w[0] for w in (words(s) for s in sents) if w]
    run, longest = 1, 1
    for a, b in zip(openers, openers[1:], strict=False):
        run = run + 1 if a == b else 1
        longest = max(longest, run)
    if longest > metrics["max_same_opener_run"]:
        add("repetition.opener", WARNING, f"{longest} consecutive sentences start the same way")
    content = [w for w in toks if w not in STOPWORDS and len(w) > 2]
    for w, c in Counter(content).most_common(3):
        if c >= metrics["overused_word_min_count"] and c / max(len(toks), 1) > metrics[
            "overused_word_share"
        ]:
            add("repetition.word", WARNING, f"{w!r} appears {c} times")
    per100 = body.count("—") / max(len(toks), 1) * 100
    if per100 > metrics["max_em_dashes_per_100_words"]:
        add("style.em_dash", WARNING, f"{body.count('—')} em dashes")
    triads = len(TRIAD_RE.findall(body))
    if triads > metrics["max_triads"]:
        add("style.triads", WARNING, f"{triads} three-part lists")
    if body.count("!") > metrics["max_exclamations"]:
        add("style.exclamations", WARNING, f"{body.count('!')} exclamation marks")

    # ── sources & claims ──────────────────────────────────────────────
    sources = post.get("sources", [])
    if not sources and any(re.search(rx, body, FLAGS) for rx in rules.get("source_markers", [])):
        add("source.missing", ERROR, "refers to research/data but the post has no source")
    allowed = _allowed_numbers(post, stories)
    for num in sorted(set(claim_numbers(body)) - allowed):
        add("claim.unsupported_number", ERROR,
            f"number {num} is not backed by a PUBLIC story claim or a recorded source claim")
    public_used = [s for s in post.get("stories_used", [])
                   if stories.get(s, {}).get("publication_status") == "PUBLIC"]
    if not public_used and any(re.search(rx, body, FLAGS) for rx in rules.get("personal_claims", [])):
        add("claim.personal_without_story", ERROR,
            "first-person achievement without a PUBLIC story in the story bank")

    # ── personal brand ────────────────────────────────────────────────
    placement = post.get("brand") or {}
    if placement.get("evidence") == "personal" and not public_used:
        add("brand.personal_evidence_missing", ERROR,
            "the post is planned on personal evidence but uses no PUBLIC story")
    for market in ((brand or {}).get("target") or {}).get("markets", []):
        if _phrase_re(market).search(body):
            add("brand.market_as_subject", WARNING,
                "mentions a target market; target markets are direction, not post subjects")
            break

    # ── privacy ───────────────────────────────────────────────────────
    for term in denylist:
        if _phrase_re(term).search(body):
            add("privacy.denylist", ERROR, "contains a term from your private denylist")
            break
    if EMAIL_RE.search(body) or PHONE_RE.search(body):
        add("privacy.contact", ERROR, "contains an e-mail address or phone number")
    for sid in post.get("stories_used", []):
        if stories.get(sid, {}).get("publication_status") != "PUBLIC":
            add("privacy.private_story", ERROR, f"story {sid} is not PUBLIC")
    for story in stories.values():
        for term in story.get("sensitive_terms", []):
            if _phrase_re(term).search(body):
                add("privacy.sensitive_term", ERROR,
                    f"contains a sensitive term from story {story['story_id']}")
                break
    known = {s["url"].rstrip("/") for s in sources}
    for url in URL_RE.findall(body):
        if url.rstrip("/.,") not in known:
            add("privacy.unknown_url", WARNING, "links to a URL that is not a recorded source")
    return f


def run_qa(store: DataStore, post_id: str, denylist: list[str] | None = None) -> dict:
    post = store.load_post(post_id)
    if PostState(post["state"]) != PostState.HUMANIZED:
        raise StoreError(f"QA runs on HUMANIZED posts; this one is {post['state']}")
    text = current_text(store, post_id)
    try:
        rules = ready_ruleset(post["language"])
        findings = run_checks(
            text, rules=rules, voice=store.voice(), profile=store.profile(), post=post,
            stories=store.stories(),
            denylist=load_denylist() if denylist is None else denylist,
            brand=store.brand(),
        )
    except RulesetNotReady as exc:
        findings = [Finding("language.not_ready", ERROR, str(exc))]
    errors = [x for x in findings if x.severity == ERROR]
    warnings = [x for x in findings if x.severity == WARNING]
    report = {
        "post_id": post_id,
        "content_hash": content_hash(text),
        "at": now_iso(),
        "status": "failed" if errors else "passed",
        "errors": [asdict(x) for x in errors],
        "warnings": [asdict(x) for x in warnings],
    }
    store.write_text(store.post_dir(post_id) / "qa.json",
                     json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    post["qa"] = {"status": report["status"], "content_hash": report["content_hash"],
                  "at": report["at"], "errors": len(errors), "warnings": len(warnings)}
    store.save_post(post)
    store.log_event("qa", post_id=post_id, status=report["status"], errors=len(errors),
                    warnings=len(warnings), codes=sorted({x.code for x in findings}))
    new = PostState.NEEDS_REVISION if errors else PostState.QA_PASSED
    set_state(store, post, new, f"QA {report['status']}")
    return report
