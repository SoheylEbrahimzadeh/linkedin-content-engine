"""Realism, point of view, networking signal and the humanity score (LCE-051).

Deterministic and local: regular expressions from the language ruleset plus simple text
statistics. No model, no network. The checks never decide that a post is "good"; they catch
what makes a post generic, invented or transactional, and they score ten observable
criteria so the owner sees why a draft does or does not sound like a person.

A criterion that cannot be judged from the data (for example "recognizably the owner's voice"
without real samples in the Golden Voice Set) is reported as `unknown`, never as a pass.
"""

from __future__ import annotations

import re
from statistics import mean, pstdev

from lce.textutil import HASHTAG_RE, paragraphs, sentences, words

FLAGS = re.I | re.M
FIRST_PERSON_RE = re.compile(r"\b(?:i|i'm|i've|i'd|i'll|my|me|mine)\b", re.I)
PROPER_RE = re.compile(r"(?<=[a-z,;:] )[A-Z][a-zA-Z0-9]+")
DIGIT_RE = re.compile(r"\d")


def _hits(patterns, text: str) -> int:
    return sum(1 for rx in patterns or [] if re.search(rx, text, FLAGS))


def _count(patterns, text: str) -> int:
    return sum(len(re.findall(rx, text, FLAGS)) for rx in patterns or [])


def _phrase_hits(phrases, text: str) -> list[str]:
    return [p for p in phrases or [] if re.search(rf"(?<!\w){re.escape(p)}(?!\w)", text, re.I)]


def _body(text: str) -> str:
    return HASHTAG_RE.sub("", text).strip()


def _parallel_openers(sents: list[str]) -> int:
    """Consecutive sentences that start with the same two words (a symmetric, list-like rhythm)."""
    heads = [tuple(words(s)[:2]) for s in sents]
    return sum(1 for a, b in zip(heads, heads[1:], strict=False) if len(a) == 2 and a == b)


def signals(text: str, rules: dict) -> dict:
    body = _body(text)
    sents = [s for s in sentences(body) if words(s)]
    toks = words(body)
    lens = [len(words(s)) for s in sents] or [0]
    fp = [s for s in sents if FIRST_PERSON_RE.search(s)]
    specific = [
        s
        for s in sents
        if FIRST_PERSON_RE.search(s) or DIGIT_RE.search(s) or PROPER_RE.search(s) or '"' in s or "“" in s
    ]
    buzz = _phrase_hits(rules.get("buzzwords"), body)
    return {
        "sentences": len(sents),
        "words": len(toks),
        "mean_sentence_words": round(mean(lens), 1),
        "max_sentence_words": max(lens),
        "first_person_share": round(len(fp) / len(sents), 2) if sents else 0.0,
        "i_opener_share": round(sum(1 for s in sents if s.strip().lower().startswith("i ")) / len(sents), 2)
        if sents
        else 0.0,
        "specific_share": round(len(specific) / len(sents), 2) if sents else 0.0,
        "stance": _hits(rules.get("stance_markers"), body),
        "reasoning": _count(rules.get("reasoning_markers"), body),
        "team": _hits(rules.get("team_signal_markers"), body),
        "experience_claims": _hits(rules.get("experience_markers"), body),
        "consultant": _phrase_hits(rules.get("consultant_language"), body),
        "buzzwords": buzz,
        "buzz_per_100": round(len(buzz) / max(len(toks), 1) * 100, 2),
        "generic_opening": _hits(rules.get("generic_openings"), body.split("\n", 1)[0]),
        "empty_leadership": _hits(rules.get("empty_leadership"), body),
        "transactional": _hits(rules.get("network_transactional"), body),
        "editorial": _phrase_hits(rules.get("editorial_phrases"), body),
        "editorial_labels": _hits(rules.get("editorial_labels"), body),
        "beliefs": _count(rules.get("belief_markers"), body),
        "parallel_openers": _parallel_openers(sents),
        "rhythm_stdev": round(pstdev(lens), 1) if len(lens) > 1 else 0.0,
        "fake_authority": _hits(rules.get("fake_authority"), body),
        "paragraphs": len(paragraphs(body)),
    }


def shape(text: str) -> tuple:
    """A coarse structure signature: paragraph band, bullets, closing question, hook form."""
    body = _body(text)
    paras = paragraphs(body)
    first = body.split("\n", 1)[0].strip()
    band = "short" if len(paras) <= 3 else "medium" if len(paras) <= 6 else "long"
    bullets = bool(re.search(r"^\s*(?:[-*•]|\d+[.)])\s+", body, re.M))
    hook = "question" if first.endswith("?") else "number" if DIGIT_RE.search(first[:30]) else "claim"
    return band, bullets, body.rstrip().endswith("?"), hook


def _content_words(text: str) -> set[str]:
    from lce.qa import STOPWORDS

    return {w for w in words(text) if w not in STOPWORDS and len(w) > 3}


def findings(
    text: str,
    *,
    rules: dict,
    post: dict,
    stories: dict,
    golden_items: dict,
    recent: list[str] | None = None,
    voice: dict | None = None,
) -> list[tuple]:
    """(code, severity, message) realism findings for QA."""
    from lce import persona

    out = list(persona.requirement_findings(None, post, stories, golden_items))
    sig = signals(text, rules)
    for p in sig["consultant"]:
        out.append(("voice.consultant_language", "warning", f"generic consultant wording: {p!r}"))
    if sig["generic_opening"]:
        out.append(("pattern.generic-opening", "warning", "the first line could open any generated post"))
    if sig["empty_leadership"]:
        out.append(
            ("pattern.empty-leadership", "warning", "thought-leadership filler without a concrete point")
        )
    if sig["buzz_per_100"] > rules.get("metrics", {}).get("max_buzzwords_per_100_words", 2.0):
        out.append(
            (
                "style.jargon",
                "warning",
                f"{len(sig['buzzwords'])} buzzwords ({', '.join(sig['buzzwords'][:4])})",
            )
        )
    public = [
        s for s in post.get("stories_used", []) if stories.get(s, {}).get("publication_status") == "PUBLIC"
    ]
    observed = [r for r in post.get("observations_used") or [] if r in golden_items]
    if sig["experience_claims"] and not public and not observed:
        out.append(
            (
                "claim.experience_unsupported",
                "error",
                "first-person experience ('in my experience', 'I've seen', 'a client') without a PUBLIC "
                "story or an owner-confirmed observation",
            )
        )
    fmt = (voice or {}).get("formatting") or {}
    editorial_sev = "error" if fmt.get("editorial_phrases_allowed") is False else "warning"
    for p in sig["editorial"]:
        out.append(("voice.editorial_phrase", editorial_sev,
                    f"analyst/editorial phrasing {p!r}: say it the way you would to a colleague"))
    if sig["editorial_labels"]:
        out.append(("pattern.editorial-label", editorial_sev,
                    "a label and a colon ('My reading: …') instead of a spoken sentence"))
    if fmt.get("symmetry_allowed") is False:
        from lce.qa import TRIAD_RE

        # One list can be how a person talks; a pattern of them is how a report reads.
        n = len(TRIAD_RE.findall(_body(text))) + sig["parallel_openers"]
        if n >= 2:
            out.append(("style.symmetry", "error",
                        f"{n} symmetric construction(s) (three-part lists, parallel sentence openers); "
                        "say what matters, unevenly, like a person thinking"))
    ct = post.get("content_type")
    owner_view = any(golden_items.get(r, {}).get("kind") in ("opinions", "disagreements", "approaches")
                     for r in post.get("opinions_used") or [])
    if post.get("angle_origin") == "owner" and not owner_view:
        out.append(("pov.unbacked_belief", "error",
                    "angle_origin: owner, but no owner-confirmed opinion is referenced"))
    elif sig["beliefs"] and not owner_view:
        out.append(("pov.unbacked_belief", "error",
                    "states a first-person belief ('I think', 'in my view', 'my reading') without an "
                    "owner-confirmed opinion; keep it to the source fact and a clearly proposed angle"))
    if ct == "personal_pov" and not sig["stance"]:
        out.append(("pov.no_stance", "error", "a point-of-view post that never takes a position"))
    if ct in ("external_insight", "observation") and not sig["stance"] and not sig["reasoning"] \
            and "?" not in text:
        out.append(
            (
                "insight.summary_only",
                "error",
                "only repeats the source; add the practical consequence or the question it raises",
            )
        )
    views = [golden_items[r] for r in post.get("opinions_used") or [] if r in golden_items]
    if views:
        overlap = max(
            len(_content_words(v["text"]) & _content_words(text)) / max(len(_content_words(v["text"])), 1)
            for v in views
        )
        if overlap < 0.15:
            out.append(
                (
                    "pov.opinion_not_expressed",
                    "warning",
                    "the referenced owner opinion is hardly visible in the text",
                )
            )
    if sig["transactional"]:
        out.append(
            (
                "network.transactional",
                "error",
                "transactional networking or self-promotion (DM me, I help companies, let's connect)",
            )
        )
    if sig["fake_authority"]:
        out.append(("network.fake_authority", "warning", "claims authority instead of showing the thinking"))
    if recent:
        mine = shape(text)
        same = sum(1 for r in recent[:5] if shape(r) == mine)
        if same >= 3:
            out.append(
                (
                    "repetition.structure",
                    "warning",
                    f"same structure as {same} of the last {min(5, len(recent))} posts",
                )
            )
    return out


CRITERIA = [
    ("pov", "Clear point of view"),
    ("evidence", "Real evidence"),
    ("voice", "Recognizably the owner's voice"),
    ("not_consultant", "Not generic consultant language"),
    ("not_interchangeable", "Not interchangeable with a vendor or analyst page"),
    ("thinking", "Shows how the owner thinks"),
    ("network", "Useful networking / team signal"),
    ("first_person", "Natural first person"),
    ("ending", "Natural ending"),
    ("aloud", "Something the owner could say aloud"),
]
EVIDENCE_CODES = (
    "claim.unsupported_number",
    "claim.personal_without_story",
    "claim.experience_unsupported",
    "brand.personal_evidence_missing",
    "source.missing",
    "pov.no_owner_opinion",
    "lesson.no_story",
    "observation.no_evidence",
    "evidence.none",
    "golden.unconfirmed_ref",
)
CONSULTANT_CODES = (
    "voice.consultant_language",
    "phrase.ai_tell",
    "pattern.empty-leadership",
    "style.jargon",
    "voice.corporate_voice",
    "claim.hype",
    "voice.editorial_phrase",
    "pattern.editorial-label",
)
# Grammatically natural but not the owner: analyst labels, symmetric lists, borrowed beliefs.
EDITORIAL_CODES = (
    "voice.editorial_phrase",
    "pattern.editorial-label",
    "style.symmetry",
    "pov.unbacked_belief",
)
ENDING_CODES = (
    "structure.generic_close",
    "cta.not_allowed",
    "bait.engagement",
    "network.transactional",
    "repetition.closing_recent",
)


def voice_match(text: str, samples: list[str], rules: dict) -> tuple[str, str]:
    """Compare simple style statistics with the owner's real samples (≥3 needed)."""
    if len(samples) < 3:
        return "unknown", f"{len(samples)} owner-confirmed sample(s); at least 3 are needed to compare"
    s = signals(text, rules)
    ref = [signals(x, rules) for x in samples]
    ml = mean(r["mean_sentence_words"] for r in ref)
    fp = mean(r["first_person_share"] for r in ref)
    ok_len = abs(s["mean_sentence_words"] - ml) <= 0.35 * max(ml, 1)
    ok_fp = abs(s["first_person_share"] - fp) <= 0.25
    why = (
        f"sentence length {s['mean_sentence_words']} vs {ml:.1f} in your samples; "
        f"first person {s['first_person_share']:.0%} vs {fp:.0%}"
    )
    return ("pass" if ok_len and ok_fp else "fail"), why


def score(text: str, *, rules: dict, codes: set[str], post: dict, samples: list[str]) -> dict:
    """The ten humanity criteria for one text (codes = every QA finding code for it)."""
    s = signals(text, rules)
    ct = post.get("content_type")
    res = {}

    def put(key, ok, why, unknown=False):
        res[key] = {"result": "unknown" if unknown else ("pass" if ok else "fail"), "why": why}

    editorial = sorted(codes & set(EDITORIAL_CODES))
    # A point of view counts only when it is the owner's (a referenced owner opinion with a
    # stance) or, for sourced posts, a reasoned consequence that borrows no belief.
    blocked = {
        "pov.no_owner_opinion",
        "pov.no_stance",
        "insight.summary_only",
        "pov.unbacked_belief",
        "observation.no_evidence",
        "lesson.no_story",
        "claim.experience_unsupported",
    }
    owned = bool(post.get("opinions_used")) and s["stance"] >= 1
    reasoned = ct != "personal_pov" and s["reasoning"] >= 1
    pov_ok = (owned or reasoned) and not (blocked & codes) and not editorial
    put(
        "pov",
        pov_ok,
        f"{s['stance']} stance marker(s), {s['reasoning']} reasoning marker(s)"
        + ("; owner opinion referenced" if post.get("opinions_used") else "")
        + (f"; {', '.join(editorial)}" if editorial else ""),
    )
    hit = sorted(codes & set(EVIDENCE_CODES))
    has_basis = bool(
        post.get("sources")
        or post.get("stories_used")
        or post.get("opinions_used")
        or post.get("observations_used")
    )
    put(
        "evidence",
        has_basis and not hit,
        ", ".join(hit) if hit else ("backed" if has_basis else "nothing recorded"),
    )
    r, why = voice_match(text, samples, rules)
    if editorial:
        r, why = "fail", f"polished editorial voice ({', '.join(editorial)}); {why}"
    put("voice", r == "pass", why, unknown=r == "unknown")
    hit = sorted(codes & set(CONSULTANT_CODES))
    put("not_consultant", not hit, ", ".join(hit) or "no consultant or AI wording found")
    put(
        "not_interchangeable",
        s["specific_share"] >= 0.3 and s["stance"] >= 1,
        f"{s['specific_share']:.0%} of sentences are specific (first person, names, numbers)",
    )
    put(
        "thinking",
        s["reasoning"] >= 2,
        f"{s['reasoning']} reasoning marker(s) (because, trade-off, instead, I start with …)",
    )
    put(
        "network",
        s["team"] >= 1
        and s["reasoning"] >= 1
        and s["stance"] >= 1
        and not s["transactional"]
        and not s["fake_authority"],
        f"{s['team']} team/decision signal(s); transactional {s['transactional']}, "
        f"claimed authority {s['fake_authority']}",
    )
    fp_ok = (
        s["first_person_share"] > 0
        and s["i_opener_share"] <= 0.35
        and not codes & {"claim.experience_unsupported", "claim.personal_without_story"}
    )
    put(
        "first_person",
        fp_ok,
        f"first person in {s['first_person_share']:.0%} of sentences; "
        f"{s['i_opener_share']:.0%} start with 'I'",
    )
    hit = sorted(codes & set(ENDING_CODES))
    put("ending", not hit, ", ".join(hit) or "ends on a point, not a prompt")
    aloud = (
        s["mean_sentence_words"] <= 22
        and s["max_sentence_words"] <= 38
        and not s["generic_opening"]
        and s["buzz_per_100"] <= 2.0
        and "phrase.ai_tell" not in codes
        and not editorial
    )
    put(
        "aloud",
        aloud,
        f"average sentence {s['mean_sentence_words']} words, longest {s['max_sentence_words']}"
        + (f"; reads like a report ({', '.join(editorial)})" if editorial else ""),
    )
    passed = sum(1 for v in res.values() if v["result"] == "pass")
    unknown = sum(1 for v in res.values() if v["result"] == "unknown")
    # Whose view is it? Only a referenced owner-confirmed item makes it the owner's; any other
    # stance was proposed by the writer and must be confirmed by the owner at approval.
    origin = "owner" if post.get("opinions_used") else ("proposed" if s["stance"] else "none")
    return {
        "content_type": ct,
        "stance_origin": origin,
        "score": passed,
        "of": len(CRITERIA),
        "unknown": unknown,
        "criteria": [{"id": k, "label": label, **res[k]} for k, label in CRITERIA],
    }


def score_post(store, post_id: str) -> dict:
    """Score a post's current text with the full QA findings (read-only; nothing is written)."""
    from lce import persona
    from lce.posts import current_text
    from lce.privacy.scan import load_denylist
    from lce.qa import _recent_texts, run_checks
    from lce.rules import ready_ruleset

    post = store.load_post(post_id)
    text = current_text(store, post_id)
    rules = ready_ruleset(post["language"])
    golden_items = persona.confirmed(store)
    found = run_checks(
        text,
        rules=rules,
        voice=store.voice(),
        profile=store.profile(),
        post=post,
        stories=store.stories(),
        denylist=load_denylist(),
        brand=store.brand(),
        recent=_recent_texts(store, post, rules),
        golden_items=golden_items,
    )
    samples = [i["text"] for i in golden_items.values() if i["kind"] == "samples"]
    out = score(text, rules=rules, codes={f.code for f in found}, post=post, samples=samples)
    out["post_id"] = post_id
    out["errors"] = sorted({f.code for f in found if f.severity == "error"})
    return out


def evaluate(doc: dict, language: str = "en") -> list[dict]:
    """Score an evaluation set (scenarios + its own golden set and stories) against expectations."""
    from lce.qa import run_checks
    from lce.rules import ready_ruleset

    rules = ready_ruleset(language)
    golden_items = {
        i["id"]: {**i, "kind": kind, "status": "owner_confirmed", "source": "owner-2000-01-01"}
        for kind, items in (doc.get("golden") or {}).items()
        for i in items
    }
    samples = [i["text"] for i in golden_items.values() if i["kind"] == "samples"]
    stories = doc.get("stories") or {}
    voice = doc.get("voice") or {}
    out = []
    for sc in doc["scenarios"]:
        post = {
            "post_id": sc["id"],
            "content_type": sc.get("content_type"),
            "sources": sc.get("sources", []),
            "claims": sc.get("claims", []),
            "stories_used": sc.get("stories_used", []),
            "opinions_used": sc.get("opinions_used", []),
            "observations_used": sc.get("observations_used", []),
        }
        found = run_checks(
            sc["text"],
            rules=rules,
            voice=sc.get("voice", voice),
            profile={},
            post=post,
            stories=stories,
            denylist=[],
            golden_items=golden_items,
        )
        codes = {f.code for f in found}
        res = score(sc["text"], rules=rules, codes=codes, post=post, samples=samples)
        exp = sc.get("expect") or {}
        by = {c["id"]: c["result"] for c in res["criteria"]}
        problems = []
        if "min_score" in exp and res["score"] < exp["min_score"]:
            problems.append(f"score {res['score']} < {exp['min_score']}")
        if "max_score" in exp and res["score"] > exp["max_score"]:
            problems.append(f"score {res['score']} > {exp['max_score']}")
        problems += [f"missing error {e}" for e in exp.get("errors", []) if e not in codes]
        problems += [f"{c} should fail" for c in exp.get("fail", []) if by.get(c) != "fail"]
        out.append(
            {
                "id": sc["id"],
                "area": sc.get("area"),
                "kind": sc.get("kind"),
                "score": res["score"],
                "of": res["of"],
                "errors": sorted(f.code for f in found if f.severity == "error"),
                "failed": [c["id"] for c in res["criteria"] if c["result"] == "fail"],
                "ok": not problems,
                "problems": problems,
            }
        )
    return out
