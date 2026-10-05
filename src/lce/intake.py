"""Owner-material intake: the voice-learning workflow (owner rule 2026-10-05).

    raw owner answer
      → normalized interpretation (proposal: text, semantic scope, allowed paraphrases,
        forbidden stronger interpretations, ambiguities)
      → owner confirmation of exactly that proposal (bound by its hash)
      → private Golden Voice Set (or a voice trait)

Nothing is ever promoted silently. A raw answer is stored verbatim and never overwritten (new
answers are appended). A proposal with open ambiguities is `unresolved` and cannot be confirmed;
it waits for the owner. Confirming requires the proposal hash that was shown to the owner, so a
proposal edited after review cannot be confirmed by mistake. Confirmed owner material is never
overwritten by a later intake.

The intake lives in the private data repository: `interview/intake.yaml`.
"""

from __future__ import annotations

import hashlib
import json

from lce import clock, persona
from lce.store import DataStore, StoreError, dump_yaml, now_iso

PATH = "interview/intake.yaml"
GOLDEN_KINDS = tuple(k for k in persona.KINDS if k != "samples")


def path(store: DataStore):
    return store.root / PATH


def load(store: DataStore) -> dict:
    doc = store.read_doc(path(store))
    if not doc:
        raise StoreError(f"no intake yet ({PATH}); create one with `lce intake init`")
    return doc


def save(store: DataStore, doc: dict) -> None:
    store.write_doc(path(store), "intake", doc)


def _item(doc: dict, qid: str) -> dict:
    for it in doc["items"]:
        if it["id"] == qid:
            return it
    raise StoreError(f"no intake question {qid!r}")


def proposal_hash(proposal: dict) -> str:
    return hashlib.sha256(json.dumps(proposal, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def init(store: DataStore, items: list[dict], *, round_: int, note: str = "") -> dict:
    """Create the intake (questions only). Existing confirmed owner material is listed, not asked."""
    if path(store).exists():
        raise StoreError(f"{PATH} exists; finish or archive it first")
    confirmed = persona.confirmed(store, GOLDEN_KINDS)
    doc = {
        "round": round_,
        "opened": clock.now().date().isoformat(),
        "note": note,
        "already_confirmed": [
            {"id": i["id"], "kind": i["kind"], "text": i["text"]} for i in confirmed.values()
        ],
        "items": [{**it, "status": it.get("status", "open")} for it in items],
    }
    save(store, doc)
    return doc


def answer(store: DataStore, qid: str, raw: str) -> dict:
    """Store the owner's raw answer verbatim (appended; earlier answers stay)."""
    if not raw.strip():
        raise StoreError("the answer is empty")
    doc = load(store)
    it = _item(doc, qid)
    if it["status"] == "confirmed":
        raise StoreError(f"{qid} is already confirmed; confirmed owner material is not overwritten")
    it.setdefault("answers", []).append({"raw": raw, "at": now_iso()})
    if it["status"] in ("open", "rejected"):
        it["status"] = "answered"
    save(store, doc)
    return it


def propose(store: DataStore, qid: str, proposal: dict, *, by: str = "writer") -> dict:
    """The writer's normalized interpretation, for the owner to confirm. Never confirms anything."""
    doc = load(store)
    it = _item(doc, qid)
    if it["status"] == "confirmed":
        raise StoreError(f"{qid} is already confirmed")
    if not it.get("answers") and not it.get("draft"):
        raise StoreError(f"{qid} has no owner answer (or owner draft) to interpret; nothing is invented")
    if not str(proposal.get("text", "")).strip():
        raise StoreError("a proposal needs the normalized text")
    it["proposal"] = proposal
    it["proposal_hash"] = proposal_hash(proposal)
    it["proposed_by"] = by
    it["status"] = "unresolved" if proposal.get("ambiguities") else "proposed"
    save(store, doc)
    return it


def mark_unresolved(store: DataStore, qid: str, note: str) -> dict:
    doc = load(store)
    it = _item(doc, qid)
    if it["status"] == "confirmed":
        raise StoreError(f"{qid} is already confirmed")
    it["status"] = "unresolved"
    it["note"] = note
    save(store, doc)
    return it


def reject(store: DataStore, qid: str, note: str = "") -> dict:
    """The owner rejected this draft or interpretation; nothing is stored as owner material."""
    doc = load(store)
    it = _item(doc, qid)
    if it["status"] == "confirmed":
        raise StoreError(f"{qid} is already confirmed")
    it["status"] = "rejected"
    if note:
        it["note"] = note
    save(store, doc)
    return it


def review(store: DataStore) -> list[dict]:
    """Proposals waiting for the owner, with the hash the owner confirms."""
    doc = load(store)
    return [
        {
            "id": it["id"],
            "kind": it["kind"],
            "status": it["status"],
            "hash": it.get("proposal_hash"),
            "raw": [a["raw"] for a in it.get("answers") or []],
            "draft": it.get("draft"),
            "proposal": it.get("proposal"),
        }
        for it in doc["items"]
        if it["status"] in ("proposed", "unresolved")
    ]


def confirm(store: DataStore, qid: str, shown_hash: str, *, today: str | None = None) -> dict:
    """Promote exactly the proposal the owner saw (by hash) into the private voice profile."""
    doc = load(store)
    it = _item(doc, qid)
    if it["status"] == "unresolved":
        raise StoreError(f"{qid} has unresolved ambiguities; the owner must resolve them first")
    if it["status"] != "proposed" or not it.get("proposal"):
        raise StoreError(f"{qid} has no proposal to confirm (status {it['status']})")
    if shown_hash != it.get("proposal_hash"):
        raise StoreError("the proposal changed since the owner reviewed it; show it again")
    day = today or clock.now().date().isoformat()
    p = it["proposal"]
    raw = "\n".join(a["raw"] for a in it.get("answers") or [])
    provenance = {
        "channel": "owner intake",
        "question_id": qid,
        "recorded_at": now_iso(),
        "confirmed_at": now_iso(),
        "proposal_hash": it["proposal_hash"],
    }
    if it["kind"] == "public_use":
        _set_public_use(store, it["target_id"], p.get("public_use"), provenance)
    elif it["kind"] == "voice_trait":
        _set_trait(store, it["trait"], p["text"], day)
    else:
        _add_golden(store, it, p, raw, day, provenance)
    it["status"] = "confirmed"
    it["confirmed_at"] = now_iso()
    save(store, doc)
    return it


def _golden_path(store: DataStore, kind: str):
    return store.root / persona.GOLDEN_DIR / f"{kind}.yaml"


def _add_golden(store, it, p, raw, day, provenance) -> None:
    kind = it["kind"]
    if kind not in GOLDEN_KINDS:
        raise StoreError(f"unknown kind {kind!r}")
    persona.init_templates(store)
    gp = _golden_path(store, kind)
    gdoc = store.read_doc(gp)
    items = list(gdoc.get("items") or [])
    tid = it.get("target_id") or it["id"]
    existing = next((x for x in items if x["id"] == tid), None)
    if existing and persona.is_confirmed(existing):
        raise StoreError(f"{tid} is already confirmed owner material; it is not overwritten")
    original = raw or ""
    if existing and existing.get("original"):
        original = existing["original"] + ("\n" + raw if raw else "")
    elif it.get("draft") and raw:
        original = f"[draft shown] {it['draft']}\n[owner answer] {raw}"
    item = {
        "id": tid,
        "text": p["text"],
        "original": original or p["text"],
        "status": "owner_confirmed",
        "source": f"owner-{day}",
        "confidence": "confirmed",
        "provenance": provenance,
    }
    for k in (
        "why",
        "instead",
        "applies_to",
        "public_use",
        "scope",
        "allowed_paraphrases",
        "forbidden_interpretations",
    ):
        if p.get(k) not in (None, "", [], {}):
            item[k] = p[k]
    if "public_use" in p and p["public_use"] is None:
        item["public_use"] = None
    items = [x for x in items if x["id"] != tid] + [item]
    gdoc["items"] = items
    gdoc.setdefault("kind", kind)
    store.write_doc(gp, "golden", gdoc)


def _set_public_use(store, target_id, value, provenance) -> None:
    if value not in (True, False):
        raise StoreError("public_use must be yes or no")
    for kind in GOLDEN_KINDS:
        gp = _golden_path(store, kind)
        gdoc = store.read_doc(gp)
        for x in gdoc.get("items") or []:
            if x["id"] == target_id:
                x["public_use"] = value
                x.setdefault("provenance", {})["note"] = (
                    f"public use set by the owner ({provenance['question_id']})"
                )
                store.write_doc(gp, "golden", gdoc)
                return
    raise StoreError(f"no owner item {target_id!r}")


def _set_trait(store, trait, text, day) -> None:
    v = store.voice()
    if trait not in v and trait not in dict(persona.VOICE_TRAITS):
        raise StoreError(f"unknown voice trait {trait!r}")
    v[trait] = text
    v.setdefault("sources", {})[trait] = f"owner-{day}: owner intake"
    store.write_doc(store.voice_path, "voice", v)


def questionnaire(store: DataStore) -> str:
    """The consolidated questionnaire, as Markdown (questions, drafts, proposals; no answers)."""
    doc = load(store)
    out = [f"# Owner intake, round {doc['round']}", ""]
    if doc.get("already_confirmed"):
        out += ["## Already confirmed (not asked again)", ""]
        out += [f"- {c['kind']} `{c['id']}`: {c['text']}" for c in doc["already_confirmed"]]
        out.append("")
    section = None
    for it in doc["items"]:
        if it["status"] in ("confirmed", "rejected"):
            continue
        if it.get("section") != section:
            section = it.get("section")
            out += ["", f"## {section or it['kind']}", ""]
        line = f"- **{it['id']}**{' (optional)' if it.get('optional') else ''}: {it['question']}"
        if it.get("draft"):
            line += f"\n  - Draft: {it['draft']}"
        if it.get("proposal"):
            p = it["proposal"]
            line += f"\n  - Proposed wording: {p['text']}"
            if p.get("allowed_paraphrases"):
                line += "\n  - Allowed paraphrases: " + " | ".join(p["allowed_paraphrases"])
            if p.get("forbidden_interpretations"):
                line += "\n  - Not allowed without you: " + " | ".join(p["forbidden_interpretations"])
        out.append(line)
    return "\n".join(out).strip() + "\n"


def to_yaml(doc: dict) -> str:
    return dump_yaml(doc)
