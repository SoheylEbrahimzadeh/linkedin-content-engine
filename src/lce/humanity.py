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
from statistics import mean, median, pstdev

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


CREDIT_LINE_RE = re.compile(
    r"^\s*(?:sources?|image|photo|credit|via)\s*:.*$|^\s*https?://\S+\s*$", re.I | re.M
)


def _body(text: str) -> str:
    """The prose a reader hears: no hashtags, no source/image credit lines."""
    return CREDIT_LINE_RE.sub("", HASHTAG_RE.sub("", text)).strip()


def _redundant(sents: list[str]) -> int:
    """Consecutive sentences that mostly repeat each other (saying it twice)."""
    out = 0
    for a, b in zip(sents, sents[1:], strict=False):
        wa, wb = _content_words(a), _content_words(b)
        if len(wa) >= 4 and len(wb) >= 4 and len(wa & wb) / len(wa | wb) >= 0.5:
            out += 1
    return out


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
    paras = paragraphs(body)
    from lce.qa import count_triads

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
        "paragraphs": len(paras),
        # humanity v2: sounds real, not impressive
        "stiff": _phrase_hits(rules.get("stiff_phrases"), body),
        "bookish": _phrase_hits(rules.get("bookish_words"), body),
        "polished_transitions": sum(1 for p in paras[1:] if _hits(rules.get("polished_transitions"), p)),
        "contrast_frames": _count(rules.get("contrast_frames"), body),
        "over_explaining": _phrase_hits(rules.get("over_explaining"), body),
        "takeaway_ending": _hits(rules.get("takeaway_endings"), paras[-1]) if paras else 0,
        "impress": _phrase_hits(rules.get("impress_words"), body),
        "emotions": _count(rules.get("emotion_markers"), body),
        "uncontracted": _count(rules.get("uncontracted"), body),
        "contractions": _count(rules.get("contractions"), body),
        "short_sentences": sum(1 for n in lens if 0 < n <= 8),
        "medium_plus_sentences": sum(1 for n in lens if n >= 12),
        "triads": count_triads(body),
        "even_paragraphs": len(paras) >= 4 and len({len(sentences(p)) for p in paras}) == 1,
        "redundant": _redundant(sents),
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
        # One list can be how a person talks; a pattern of them is how a report reads.
        n = sig["triads"] + sig["parallel_openers"]
        if n >= 2:
            out.append(("style.symmetry", "error",
                        f"{n} symmetric construction(s) (three-part lists, parallel sentence openers); "
                        "say what matters, unevenly, like a person thinking"))
    ct = post.get("content_type")
    view_kinds = ("opinions", "disagreements", "approaches", "principles")
    owner_view = any(
        golden_items.get(r, {}).get("kind") in view_kinds for r in post.get("opinions_used") or []
    )
    # ── humanity v2: sounds real, not impressive (owner rules 2026-10-05) ──
    if sig["stiff"]:
        out.append(("style.stiff_phrase", "warning",
                    f"written-report wording ({', '.join(sig['stiff'][:4])}): say it the plain way"))
    if sig["bookish"]:
        out.append(("style.bookish", "warning",
                    f"bookish words ({', '.join(sorted(set(sig['bookish']))[:5])}): use the word you'd say "
                    "out loud (but, about, many, people, buy, help, enough, ...)"))
    if sig["polished_transitions"]:
        out.append(("style.polished_transition", "warning",
                    f"{sig['polished_transitions']} paragraph(s) open with a polished transition "
                    "(Moreover, That said, Ultimately …); people just start the next thought"))
    if sig["contrast_frames"]:
        out.append(("style.contrast_frame", "warning",
                    "set-piece contrast ('It's not X. It's Y.', 'less X, more Y')"))
    if sig["over_explaining"]:
        out.append(("style.over_explaining", "warning",
                    f"explains what it just said ({', '.join(sig['over_explaining'][:3])})"))
    if sig["redundant"]:
        out.append(("style.over_explaining", "warning",
                    f"{sig['redundant']} sentence(s) repeat the one before"))
    if sig["words"] > rules.get("metrics", {}).get("max_body_words", 230):
        out.append(("style.over_long", "warning", f"{sig['words']} words; say less"))
    if sig["takeaway_ending"]:
        out.append(("ending.takeaway", "warning",
                    "the last paragraph hands the reader a lesson or takeaway; stop on the thought itself"))
    if sig["impress"]:
        out.append(("style.impress", "warning",
                    f"trying to impress ({', '.join(sig['impress'][:3])}); say what it does"))
    if sig["uncontracted"] >= 2 and (
        sig["contractions"] == 0 or sig["uncontracted"] > 2 * sig["contractions"] + 1
    ):
        out.append(("style.uncontracted", "warning",
                    f"{sig['uncontracted']} uncontracted forms (do not, it is …) and "
                    f"{sig['contractions']} contraction(s): reads written, not spoken"))
    if sig["max_sentence_words"] > 28:
        out.append(("style.long_sentence", "warning",
                    f"a {sig['max_sentence_words']}-word sentence; nobody says that in one breath"))
    if sig["sentences"] >= 5 and (sig["rhythm_stdev"] < 3 or not sig["short_sentences"]):
        out.append(("style.flat_rhythm", "warning",
                    f"sentences are all about the same length (spread {sig['rhythm_stdev']} words); "
                    "mix short and medium ones"))
    backed = owner_view or public or observed
    if sig["emotions"] and not backed:
        out.append(("pov.unbacked_emotion", "error",
                    "a felt reaction (I was surprised, I love …) the owner never recorded"))
    # Owner rule 2026-10-05: no stronger position than the owner's confirmed material. A faithful
    # paraphrase is allowed; a stronger, narrower, broader or different position is not
    # (lce.owner_scope judges meaning, not just shared words).
    refs = (post.get("opinions_used") or []) + (post.get("observations_used") or [])
    items = [golden_items[r] for r in refs if r in golden_items]
    if items:
        from lce import owner_scope
        from lce.voice_gate import ATTRIBUTION_RE

        claims = [c.get("text", "") for c in post.get("claims") or []]
        seen = set()
        for sent in sentences(_body(text)):
            personal = bool(FIRST_PERSON_RE.search(sent)) and bool(
                _hits(rules.get("stance_markers"), sent) or _count(rules.get("belief_markers"), sent)
                or owner_scope.COMMIT_RE.search(sent) or owner_scope.AGREE_RE.search(sent))
            res = owner_scope.check(sent, items)
            src = max((owner_scope.similarity(sent, c) for c in claims), default=0.0)
            owner_meaning = (
                res["similarity"] >= 0.34 and not ATTRIBUTION_RE.search(sent) and src < res["similarity"]
            )
            if not (personal or owner_meaning) or res["verdict"] == "faithful":
                continue
            if res["verdict"] == "unsupported" and not personal:
                continue
            code = owner_scope.CODES[res["verdict"]]
            if (code, sent) in seen:
                continue
            seen.add((code, sent))
            why = "; ".join(res["reasons"])
            out.append((code, "error",
                        f"{sent[:90]!r} {owner_scope.MESSAGES[res['verdict']]} ({why}); "
                        "keep to what the owner said, or attribute it to the source"))
    never = [i for i in golden_items.values() if i.get("kind") == "never_say"]
    if never:
        from lce import owner_scope

        for sent in sentences(_body(text)):
            for i in never:
                said = i.get("text", "")
                if owner_scope.similarity(sent, said) >= 0.7 or said.lower() in sent.lower():
                    out.append(("voice.never_say", "error",
                                f"{sent[:80]!r} is close to something the owner would never say: "
                                f"{said[:60]!r}"))
    if post.get("angle_origin") == "owner" and not owner_view:
        out.append(("pov.unbacked_belief", "error",
                    "angle_origin: owner, but no owner-confirmed opinion is referenced"))
    elif sig["beliefs"] and not owner_view:
        hits = [x for x in sentences(_body(text)) if _count(rules.get("belief_markers"), x)]
        for what in [f"{h[:110]!r}" for h in hits] or ["a first-person belief"]:
            out.append(("pov.unbacked_belief", "error",
                        f"first-person position without owner-confirmed material: {what}; "
                        "drop it or attribute the idea to the source"))
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


# The owner's standard (2026-10-05): 10/10 only when every one of these holds. Optimized for
# sounding real, not impressive.
CRITERIA = [
    ("real_person", "Sounds like a real person"),
    ("owner_voice", "Sounds like the owner, not a generic consultant"),
    ("spoken", "Could be spoken naturally"),
    ("variation", "Natural sentence variation"),
    ("no_stiffness", "No corporate stiffness"),
    ("no_symmetry", "No AI-style symmetry"),
    ("no_manufactured_opinion", "No manufactured opinion"),
    ("substance", "Real reasoning or a concrete observation"),
    ("no_over_explaining", "Doesn't over-explain"),
    ("genuine_ending", "Leaves a genuine thought, not a manufactured takeaway"),
]
EVIDENCE_CODES = (
    "claim.unsupported_number",
    "source.missing",
    "evidence.none",
)
MANUFACTURED_CODES = (
    "pov.unbacked_belief",
    "pov.stronger_than_owner",
    "pov.narrower_than_owner",
    "pov.broader_than_owner",
    "pov.different_from_owner",
    "pov.unsupported_by_owner",
    "pov.unbacked_emotion",
    "pov.no_owner_opinion",
    "golden.unconfirmed_ref",
    "claim.experience_unsupported",
    "claim.personal_without_story",
    "brand.personal_evidence_missing",
    "lesson.no_story",
    "observation.no_evidence",
)
REAL_PERSON_CODES = (
    "phrase.ai_tell",
    "pattern.generic-opening",
    "pattern.empty-leadership",
    "pattern.rhetorical-opener",
    "pattern.reveal-question",
    "pattern.reveal-bridge",
    "pattern.performed-sincerity",
    "network.fake_authority",
    "network.transactional",
    "bait.engagement",
    "claim.hype",
    "style.impress",
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
STIFF_CODES = (
    "style.bookish",
    "voice.consultant_language",
    "voice.corporate_voice",
    "style.jargon",
    "style.stiff_phrase",
    "style.polished_transition",
    "voice.editorial_phrase",
    "pattern.editorial-label",
)
SYMMETRY_CODES = (
    "style.symmetry",
    "style.triads",
    "style.contrast_frame",
    "pattern.not-x-but-y",
    "pattern.staccato-stack",
    "pattern.stacked-questions",
)
SPOKEN_CODES = (
    "style.bookish",
    "pattern.written-inversion",
    "pattern.setup-line",
    "voice.editorial_phrase",
    "pattern.editorial-label",
    "style.uncontracted",
    "style.long_sentence",
    "style.stiff_phrase",
)
OVER_CODES = ("style.over_explaining", "style.over_long")
ENDING_CODES = (
    "structure.generic_close",
    "cta.not_allowed",
    "bait.engagement",
    "network.transactional",
    "repetition.closing_recent",
    "ending.takeaway",
)


def _contraction_share(sig: dict) -> float:
    n = sig["contractions"] + sig["uncontracted"]
    return sig["contractions"] / n if n else 0.5


def voice_match(text: str, samples: list[str], rules: dict, voice: dict | None = None) -> tuple[str, str]:
    """Compare simple style statistics with the owner's real samples (≥3 needed).

    The samples are e-mails and chats, so a post may be shorter-sentenced than they are (the
    owner asked for shorter sentences) but not longer, not more first-person (no personal-brand
    performance), and not more formal (contractions where the owner uses them). Owner-flagged
    counter examples and avoided vocabulary fail it outright.
    """
    if len(samples) < 3:
        return "unknown", f"{len(samples)} owner-confirmed sample(s); at least 3 are needed to compare"
    s = signals(text, rules)
    ref = [signals(x, rules) for x in samples]
    ml = median(r["mean_sentence_words"] for r in ref)
    fp = max(r["first_person_share"] for r in ref)
    cs = mean(_contraction_share(r) for r in ref)
    problems = []
    if s["mean_sentence_words"] > 1.15 * ml:
        problems.append(f"sentences longer than yours ({s['mean_sentence_words']} vs {ml:.1f} words)")
    if s["first_person_share"] > fp + 0.1:
        problems.append(f"more first person than you use ({s['first_person_share']:.0%})")
    if cs >= 0.5 and s["uncontracted"] >= 2 and _contraction_share(s) < 0.34:
        problems.append("more formal than you write (you use contractions)")
    body = _body(text).lower()
    v = voice or {}
    avoided = v.get("avoided_vocabulary") if isinstance(v.get("avoided_vocabulary"), list) else []
    hits = [a for a in avoided if a.lower() in body]
    counter = v.get("counter_examples") if isinstance(v.get("counter_examples"), list) else []
    near = [c for c in counter if _content_words(c) and
            len(_content_words(c) & _content_words(body)) / len(_content_words(c)) >= 0.7]
    if hits:
        problems.append(f"words you avoid: {', '.join(hits)}")
    if near:
        problems.append(f"{len(near)} sentence(s) close to ones you marked as not your voice")
    if problems:
        return "fail", "; ".join(problems)
    return "pass", (f"sentence length {s['mean_sentence_words']} (yours {ml:.1f}), first person "
                    f"{s['first_person_share']:.0%}, contractions like yours")


def verdict(score_: int, res: dict, errors: set[str] | None = None) -> str:
    """PASS only at 10/10. FAIL: a QA error, a generic/generated feel, a manufactured opinion,
    or fewer than 7 criteria. Anything else is PARTIAL."""
    gate = ("real_person", "no_manufactured_opinion")
    if errors or any(res[k]["result"] == "fail" for k in gate) or score_ < 7:
        return "FAIL"
    return "PASS" if score_ == len(CRITERIA) else "PARTIAL"


def score(
    text: str,
    *,
    rules: dict,
    codes: set[str],
    post: dict,
    samples: list[str],
    voice: dict | None = None,
    errors: set[str] | None = None,
) -> dict:
    """The owner's ten humanity criteria for one text (codes = every QA finding code for it)."""
    s = signals(text, rules)
    ct = post.get("content_type")
    res = {}

    def put(key, ok, why, unknown=False):
        res[key] = {"result": "unknown" if unknown else ("pass" if ok else "fail"), "why": why}

    def hit(group):
        return sorted(codes & set(group))

    h = hit(REAL_PERSON_CODES)
    put("real_person", not h, ", ".join(h) or "no generated-post tells, hype or claimed authority")

    r, why = voice_match(text, samples, rules, voice)
    h = hit(CONSULTANT_CODES + EDITORIAL_CODES)
    if h:
        r, why = "fail", f"consultant/editorial voice ({', '.join(h)}); {why}"
    put("owner_voice", r == "pass", why, unknown=r == "unknown")

    h = hit(SPOKEN_CODES)
    spoken = not h and s["mean_sentence_words"] <= 16 and s["max_sentence_words"] <= 28
    put(
        "spoken",
        spoken,
        f"average sentence {s['mean_sentence_words']} words (≤16), longest {s['max_sentence_words']} (≤28)"
        + (f"; {', '.join(h)}" if h else ""),
    )

    varied = (
        s["short_sentences"] >= 1
        and s["medium_plus_sentences"] >= 1
        and (s["sentences"] < 5 or s["rhythm_stdev"] >= 3)
        and not s["even_paragraphs"]
        and "style.flat_rhythm" not in codes
    )
    put(
        "variation",
        varied,
        f"{s['short_sentences']} short (≤8 words), {s['medium_plus_sentences']} medium/long (≥12), "
        f"spread {s['rhythm_stdev']} words"
        + ("; every paragraph the same size" if s["even_paragraphs"] else ""),
    )

    h = hit(STIFF_CODES)
    put("no_stiffness", not h, ", ".join(h) or "plain wording")

    h = hit(SYMMETRY_CODES)
    sym_ok = not h and s["parallel_openers"] == 0 and s["triads"] <= 1
    put(
        "no_symmetry",
        sym_ok,
        ", ".join(h) or f"{s['triads']} three-part list(s), {s['parallel_openers']} parallel opener(s)",
    )

    h = hit(MANUFACTURED_CODES)
    origin = "owner" if post.get("opinions_used") else ("proposed" if s["stance"] else "none")
    put(
        "no_manufactured_opinion",
        not h,
        ", ".join(h) or ("owner-confirmed view" if origin == "owner" else "no first-person view claimed"),
    )

    has_basis = bool(
        post.get("sources") or post.get("stories_used") or post.get("opinions_used")
        or post.get("observations_used")
    )
    concrete = sum(1 for x in sentences(_body(text)) if DIGIT_RE.search(x) or PROPER_RE.search(x))
    h = hit(EVIDENCE_CODES + ("insight.summary_only",))
    substance = has_basis and not h and (s["reasoning"] >= 1 or concrete >= 1 or "?" in _body(text))
    put(
        "substance",
        substance,
        ", ".join(h) if h else (
            f"{s['reasoning']} reasoning marker(s), {concrete} concrete sentence(s)"
            if has_basis else "nothing recorded behind it"
        ),
    )

    h = hit(OVER_CODES)
    put("no_over_explaining", not h, ", ".join(h) or f"{s['words']} words, nothing said twice")

    h = hit(ENDING_CODES)
    put("genuine_ending", not h, ", ".join(h) or "stops on a thought, no lesson or prompt")

    passed = sum(1 for v in res.values() if v["result"] == "pass")
    unknown = sum(1 for v in res.values() if v["result"] == "unknown")
    return {
        "content_type": ct,
        "stance_origin": origin,
        "score": passed,
        "of": len(CRITERIA),
        "unknown": unknown,
        "verdict": verdict(passed, res, errors),
        "criteria": [{"id": k, "label": label, **res[k]} for k, label in CRITERIA],
    }


def _recent_findings(store, post_id: str) -> list:
    """Every QA finding for a post's current text (read-only)."""
    from lce import persona
    from lce.posts import current_text
    from lce.privacy.scan import load_denylist
    from lce.qa import _recent_texts, run_checks
    from lce.rules import ready_ruleset

    post = store.load_post(post_id)
    rules = ready_ruleset(post["language"])
    return run_checks(
        current_text(store, post_id), rules=rules, voice=store.voice(), profile=store.profile(), post=post,
        stories=store.stories(), denylist=load_denylist(), brand=store.brand(),
        recent=_recent_texts(store, post, rules), golden_items=persona.confirmed(store),
    )


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
    errors = {f.code for f in found if f.severity == "error"}
    out = score(text, rules=rules, codes={f.code for f in found}, post=post, samples=samples,
                voice=store.voice(), errors=errors)
    out["post_id"] = post_id
    out["errors"] = sorted(errors)
    # The owner's Voice Gate is a separate judgement: 10/10 here is never a voice pass.
    from lce.voice_gate import assess

    out["voice_gate"] = assess(text, post=post, golden_items=golden_items, stories=store.stories(),
                               samples=samples, humanity=out, codes={f.code for f in found})
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
        errors = {f.code for f in found if f.severity == "error"}
        res = score(sc["text"], rules=rules, codes=codes, post=post, samples=samples,
                    voice=sc.get("voice", voice), errors=errors)
        exp = sc.get("expect") or {}
        by = {c["id"]: c["result"] for c in res["criteria"]}
        problems = []
        if "min_score" in exp and res["score"] < exp["min_score"]:
            problems.append(f"score {res['score']} < {exp['min_score']}")
        if "max_score" in exp and res["score"] > exp["max_score"]:
            problems.append(f"score {res['score']} > {exp['max_score']}")
        if "verdict" in exp and res["verdict"] != exp["verdict"]:
            problems.append(f"verdict {res['verdict']} ≠ {exp['verdict']}")
        problems += [f"missing error {e}" for e in exp.get("errors", []) if e not in codes]
        problems += [f"{c} should fail" for c in exp.get("fail", []) if by.get(c) != "fail"]
        out.append(
            {
                "id": sc["id"],
                "area": sc.get("area"),
                "kind": sc.get("kind"),
                "score": res["score"],
                "of": res["of"],
                "verdict": res["verdict"],
                "errors": sorted(f.code for f in found if f.severity == "error"),
                "failed": [c["id"] for c in res["criteria"] if c["result"] == "fail"],
                "ok": not problems,
                "problems": problems,
            }
        )
    return out
