"""Personal voice system (LCE-051): Golden Voice Set, opinion bank, content types, voice gaps.

Three kinds of content are kept apart, never collapsed into one generic style:

- external insight: what a source says, plus the owner's reading of it;
- personal point of view: what the owner believes, which must come from the owner;
- personal experience / lesson: what happened to the owner, which must come from a
  PUBLIC story (or an owner-confirmed observation).

The private Golden Voice Set (`profile/golden/<kind>.yaml`) holds only material the owner
provided: real posts/messages, opinions, disagreements, how they approach problems, and
professional observations. An item counts as evidence only with status `owner_confirmed`
and an owner source (`owner-YYYY-MM-DD`). Nothing here writes or invents an item; empty
files stay empty and the gap is reported as owner input.
"""

from __future__ import annotations

from lce.store import DataStore

KINDS = ("samples", "opinions", "disagreements", "approaches", "observations")
TARGETS = {
    "samples": (10, 20),
    "opinions": (5, 10),
    "disagreements": (5, 5),
    "approaches": (5, 5),
    "observations": (5, 5),
}
GOLDEN_DIR = "profile/golden"

# What each content type needs before a writer may draft it (never invented).
CONTENT_TYPES = {
    "external_insight": {
        "label": "External insight",
        "needs": "sources",
        "rule": "a recorded source, and the owner's reading of it (a stance), not a summary",
    },
    "personal_pov": {
        "label": "Personal point of view",
        "needs": "opinion",
        "rule": "an owner-confirmed opinion or disagreement from the Golden Voice Set",
    },
    "personal_lesson": {
        "label": "Personal lesson",
        "needs": "story",
        "rule": "a PUBLIC story from the story bank",
    },
    "how_to": {
        "label": "Practical how-to",
        "needs": "approach_or_sources",
        "rule": "an owner-confirmed approach (or PUBLIC story); otherwise recorded sources",
    },
    "observation": {
        "label": "Experience-based observation",
        "needs": "observation",
        "rule": "an owner-confirmed observation or a PUBLIC story",
    },
}
POV_KINDS = ("opinions", "disagreements")
VIEW_KINDS = ("opinions", "disagreements", "approaches")

# The voice dimensions the owner asked for (voice.yaml); each is a trait, or an explicit
# owner-input request. `derived` fields came from documented answers and still await review.
VOICE_TRAITS = [
    ("tone", "Tone"),
    ("rhythm", "Sentence rhythm"),
    ("formality", "Formality"),
    ("preferred_vocabulary", "Preferred vocabulary"),
    ("avoided_vocabulary", "Avoided vocabulary"),
    ("first_person_style", "First-person style"),
    ("opinion_style", "Opinion style"),
    ("disagreement_style", "Disagreement style"),
    ("humor", "Humour"),
    ("openings", "Openings"),
    ("transitions", "Transitions"),
    ("endings", "Endings"),
    ("cta", "CTA behaviour"),
    ("networking", "Networking behaviour"),
    ("technical_business_balance", "Technical vs business balance"),
    ("explanation_style", "How problems are explained"),
    ("uncertainty_style", "How uncertainty is expressed"),
    ("challenge_style", "How common assumptions are challenged"),
    ("signature_patterns", "Recognizable writing patterns"),
    ("anti_generic", "Anti-corporate / anti-generic rules"),
]


def golden(store: DataStore) -> dict[str, list[dict]]:
    out = {}
    for kind in KINDS:
        doc = store.read_doc(store.root / GOLDEN_DIR / f"{kind}.yaml")
        out[kind] = list(doc.get("items") or [])
    return out


def is_confirmed(item: dict) -> bool:
    return item.get("status") == "owner_confirmed" and str(item.get("source", "")).startswith("owner-")


def confirmed(store: DataStore, kinds=KINDS) -> dict[str, dict]:
    """Owner-confirmed items of the given kinds, by id (with their kind)."""
    g = golden(store)
    return {i["id"]: {**i, "kind": k} for k in kinds for i in g[k] if is_confirmed(i)}


def _matches(item: dict, pillar: str | None) -> bool:
    scope = item.get("applies_to") or []
    return not scope or not pillar or pillar in scope


def evidence_for(store: DataStore, content_type: str, pillar: str | None = None) -> dict:
    """What the owner has provided that could back this content type for this pillar."""
    from lce.brand import public_stories_for

    stories = public_stories_for(store, pillar=pillar) if pillar else public_stories_for(store)
    pov = [i for i in confirmed(store, POV_KINDS).values() if _matches(i, pillar)]
    approaches = [i for i in confirmed(store, ("approaches",)).values() if _matches(i, pillar)]
    observations = [i for i in confirmed(store, ("observations",)).values() if _matches(i, pillar)]
    needs = CONTENT_TYPES[content_type]["needs"]
    ok = {
        "sources": True,
        "opinion": bool(pov),
        "story": bool(stories),
        "approach_or_sources": True,
        "observation": bool(observations or stories),
    }[needs]
    return {
        "content_type": content_type,
        "available": ok,
        "stories": stories,
        "opinions": [i["id"] for i in pov],
        "approaches": [i["id"] for i in approaches],
        "observations": [i["id"] for i in observations],
    }


def producible(store: DataStore) -> dict[str, bool]:
    """Which content types the engine can draft right now without inventing anything."""
    return {ct: evidence_for(store, ct)["available"] for ct in CONTENT_TYPES}


def requirement_findings(
    store_or_none, post: dict, stories: dict, golden_items: dict[str, dict]
) -> list[tuple]:
    """(code, severity, message) for the post's content-type requirements (used by QA)."""
    out = []
    ct = post.get("content_type")
    if not ct:
        return [
            (
                "content_type.missing",
                "warning",
                "no content type (external_insight, personal_pov, personal_lesson, how_to, observation)",
            )
        ]
    public = [
        s for s in post.get("stories_used", []) if stories.get(s, {}).get("publication_status") == "PUBLIC"
    ]
    refs = post.get("opinions_used") or []
    unknown = [r for r in refs + (post.get("observations_used") or []) if r not in golden_items]
    for r in unknown:
        out.append(
            (
                "golden.unconfirmed_ref",
                "error",
                f"{r!r} is not an owner-confirmed item of the Golden Voice Set",
            )
        )
    views = [golden_items[r] for r in refs if r in golden_items]
    if ct == "personal_pov" and not any(v["kind"] in POV_KINDS for v in views):
        out.append(
            (
                "pov.no_owner_opinion",
                "error",
                "a personal point of view needs an owner-confirmed opinion or disagreement "
                "(profile/golden); none is referenced, so the view would be invented",
            )
        )
    if ct == "personal_lesson" and not public:
        out.append(("lesson.no_story", "error", "a personal lesson needs a PUBLIC story"))
    obs = [golden_items[r] for r in post.get("observations_used") or [] if r in golden_items]
    if ct == "observation" and not (public or any(o["kind"] == "observations" for o in obs)):
        out.append(
            (
                "observation.no_evidence",
                "error",
                "an experience-based observation needs an owner-confirmed observation or a PUBLIC story",
            )
        )
    if ct in ("external_insight", "how_to") and not post.get("sources") and not views and not public:
        out.append(
            (
                "evidence.none",
                "error",
                f"a {CONTENT_TYPES[ct]['label'].lower()} needs sources or owner-provided material",
            )
        )
    return out


def voice_gaps(store: DataStore) -> list[dict]:
    """Voice traits that are missing or explicitly waiting for the owner."""
    v = store.voice()
    out = []
    for key, label in VOICE_TRAITS:
        val = v.get(key)
        if val in (None, "", [], {}):
            out.append({"trait": key, "label": label, "status": "missing"})
        elif isinstance(val, dict) and val.get("owner_input_required"):
            out.append(
                {"trait": key, "label": label, "status": "owner_input", "question": val.get("question", "")}
            )
    return out


def status(store: DataStore) -> dict:
    """Voice and personal-brand readiness (counts only; never item text)."""
    g = golden(store)
    kinds = {}
    for k in KINDS:
        lo, hi = TARGETS[k]
        n = sum(1 for i in g[k] if is_confirmed(i))
        kinds[k] = {
            "confirmed": n,
            "drafts": len(g[k]) - n,
            "target_min": lo,
            "target_max": hi,
            "ready": n >= lo,
        }
    v = store.voice()
    gaps = voice_gaps(store)
    return {
        "golden": kinds,
        "voice_version": v.get("version"),
        "voice_review": (v.get("review") or {}).get("status"),
        "voice_gaps": gaps,
        "voice_traits_total": len(VOICE_TRAITS),
        "public_stories": sum(1 for s in store.stories().values() if s.get("publication_status") == "PUBLIC"),
        "producible": producible(store),
    }


INSTRUCTIONS = {
    "samples": "10-20 real posts or messages you wrote (LinkedIn, e-mail, chat, talk notes), "
    "copied verbatim. "
    "They teach the writer your rhythm and wording; they are never republished unless publishable: true. "
    "Remove client, employer and colleague names first.",
    "opinions": "5-10 things you genuinely believe about your field, one per item, in your own words "
    "(text), with why. A personal point-of-view post can only express an opinion from this list.",
    "disagreements": "5 common ideas you reject (text) and what you do or believe instead "
    "(instead), with why.",
    "approaches": "5 explanations of how you approach a kind of problem (text): what you look at first, "
    "what you ask, what you refuse to skip.",
    "observations": "5 professional observations or short stories from your own work (text), "
    "non-confidential "
    "(context without client/employer names). First-person observations in posts need one of these "
    "or a PUBLIC story.",
}
ITEM_TEMPLATE = (
    "# Add items like this (status owner_confirmed + source owner-YYYY-MM-DD make it usable):\n"
    '# - id: short-kebab-id\n#   text: "...your own words..."\n#   why: "..."\n'
    "#   applies_to: [pillar-id]\n#   status: owner_confirmed\n#   source: owner-2026-10-04\n"
)


def init_templates(store: DataStore) -> list[str]:
    """Create empty Golden Voice Set files (instructions only, no items). Existing files are kept."""
    from lce.store import dump_yaml

    made = []
    base = store.root / GOLDEN_DIR
    base.mkdir(parents=True, exist_ok=True)
    for kind in KINDS:
        path = base / f"{kind}.yaml"
        if path.exists():
            continue
        lo, hi = TARGETS[kind]
        doc = {
            "kind": kind,
            "instructions": INSTRUCTIONS[kind],
            "target": {"min": lo, "max": hi},
            "items": [],
        }
        store.write_text(path, ITEM_TEMPLATE + dump_yaml(doc))
        made.append(str(path.relative_to(store.root)))
    return made


def opinion_for(store: DataStore, post: dict) -> dict:
    """'What does the owner actually believe about this?' — answered only from owner material."""
    items = confirmed(store)
    views = [items[r] for r in post.get("opinions_used") or [] if r in items]
    if views:
        return {
            "answer": "recorded",
            "views": [
                {
                    "id": v["id"],
                    "kind": v["kind"],
                    "text": v["text"],
                    "why": v.get("why"),
                    "source": v["source"],
                }
                for v in views
            ],
        }
    candidates = [i for i in confirmed(store, VIEW_KINDS).values() if _matches(i, post.get("pillar"))]
    return {
        "answer": "not recorded",
        "note": "no owner opinion is referenced; a personal point of view stays NEEDS_INPUT",
        "candidates": [{"id": c["id"], "kind": c["kind"], "text": c["text"]} for c in candidates],
    }
