"""Voice profile and the humanization record (LCE-037).

Humanization is done by a Claude Code session (no LLM API). What the engine can
do, and does here, is make that step explicit and inspectable:

- `profile_hashes` pins the exact versions of `profile/voice.yaml`,
  `profile/profile.yaml` and `profile/brand.yaml` a text was written against;
- `checklist` evaluates every voice rule that a program can verify (hashtags,
  emoji, avoided phrases, generic AI patterns, sourced numbers, first-person
  claims, corporate voice, objective, pillar) and lists what it cannot verify
  (tone, positioning fit) as "owner review" instead of claiming it;
- `record` is stored on the post when its humanized text is saved.

The dashboard shows "applied" only for what this record proves.
"""

from __future__ import annotations

import hashlib

from lce.store import DataStore, now_iso

FILES = {"voice": "profile/voice.yaml", "profile": "profile/profile.yaml", "brand": "profile/brand.yaml"}

# QA finding codes behind each machine-checkable voice rule.
RULES = [
    ("hashtags", "Hashtag policy (count, placement)",
     ("hashtags.excess", "hashtags.not_allowed", "hashtags.placement")),
    ("emoji", "Emoji policy", ("emoji.excess",)),
    ("avoid_phrases", "Avoided phrases and forbidden topics", ("phrase.forbidden", "topic.forbidden")),
    ("ai_patterns", "Generic AI / corporate patterns",
     ("phrase.ai_tell", "structure.generic_close", "style.triads", "style.em_dash", "bait.engagement",
      "claim.hype")),
    ("evidence", "Evidence: numbers sourced, no invented experience",
     ("claim.unsupported_number", "claim.personal_without_story", "brand.personal_evidence_missing",
      "source.missing")),
    ("individual_voice", "Individual expert voice (no company-page 'we')", ("voice.corporate_voice",)),
    ("cta", "CTA style", ("cta.not_allowed",)),
    ("format", "Format (length, paragraphs, no Markdown)",
     ("length.voice_max", "length.hard_max", "structure.markdown", "structure.wall_of_text",
      "structure.long_paragraph", "structure.bullets")),
]
NOT_MACHINE_CHECKABLE = [
    ("tone", "Tone matches the tone list in voice.yaml"),
    ("positioning", "Fits the positioning and target audience in profile.yaml"),
]


def file_sha256(store: DataStore, rel: str) -> str | None:
    path = store.root / rel
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def profile_hashes(store: DataStore) -> dict:
    return {f"{k}_sha256": file_sha256(store, rel) for k, rel in FILES.items()}


def objectives(store: DataStore) -> dict[str, dict]:
    return {o["id"]: o for o in store.voice().get("objectives", []) if o.get("id")}


def checklist(store: DataStore, post: dict, findings: list) -> list[dict]:
    """One line per voice rule: passed / failed (with codes) / review."""
    codes = {getattr(f, "code", None) or f.get("code") for f in findings}
    out = []
    objs = objectives(store)
    if objs:
        obj = post.get("objective")
        out.append({"rule": "objective", "label": "Content objective set (voice.yaml objectives)",
                    "status": "passed" if obj in objs else "failed",
                    "detail": objs[obj]["label"] if obj in objs else "no objective recorded for this post"})
    pillars = {p.get("id") for p in store.profile().get("pillars", [])}
    out.append({"rule": "pillar", "label": "Content pillar from profile.yaml",
                "status": "passed" if post.get("pillar") in pillars else "failed",
                "detail": post.get("pillar")})
    for rule, label, rule_codes in RULES:
        hit = sorted(c for c in codes
                     if c in rule_codes or (rule == "ai_patterns" and str(c).startswith("pattern.")))
        out.append({"rule": rule, "label": label, "status": "failed" if hit else "passed",
                    "detail": ", ".join(hit) if hit else ""})
    for rule, label in NOT_MACHINE_CHECKABLE:
        out.append({"rule": rule, "label": label, "status": "review",
                    "detail": "not machine-verifiable: owner review at approval"})
    return out


def summary(items: list[dict]) -> dict:
    return {s: sum(1 for i in items if i["status"] == s) for s in ("passed", "failed", "review")}


def record(store: DataStore, post: dict, findings: list, *, source: str, by: str) -> dict:
    items = checklist(store, post, findings)
    voice = store.voice()
    return {"at": now_iso(), "by": by, "source": source, "voice_version": voice.get("version"),
            **profile_hashes(store), "objective": post.get("objective"), "checklist": summary(items)}


def view(store: DataStore) -> dict:
    """What the dashboard shows about the voice profile (non-secret, allowlisted)."""
    v = store.voice()
    return {
        "version": v.get("version"),
        **profile_hashes(store),
        "review": v.get("review"),
        "sources": v.get("sources"),
        "tone": v.get("tone"),
        "formality": v.get("formality"),
        "point_of_view": v.get("point_of_view"),
        "technical_depth": v.get("technical_depth"),
        "sentence_length": v.get("sentence_length"),
        "structure": v.get("structure"),
        "opinions_vs_facts": v.get("opinions_vs_facts"),
        "evidence": v.get("evidence"),
        "individual_voice": v.get("individual_voice"),
        "cta": v.get("cta"),
        "hashtag_policy": v.get("hashtag_policy"),
        "emoji_policy": v.get("emoji_policy"),
        "avoid_phrases": v.get("avoid_phrases"),
        "avoid_patterns": v.get("avoid_patterns"),
        "objectives": v.get("objectives"),
        "rules": [{"rule": r, "label": label} for r, label, _ in RULES]
        + [{"rule": r, "label": label, "machine_checkable": False} for r, label in NOT_MACHINE_CHECKABLE],
    }
