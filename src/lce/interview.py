"""Progressive interview: which answers are missing, and storing answers safely.

The interview never guesses. A question counts as answered only when its
target field holds a value the owner provided.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

import yaml

from lce.store import DataStore, StoreError, now_iso

TARGETS = {"profile": ("profile_path", "profile"), "voice": ("voice_path", "voice"),
           "settings": ("settings_path", "settings")}
GROUP_ORDER = ["positioning", "audience", "expertise", "pillars", "voice", "topics", "formats",
               "frequency", "languages"]


@dataclass(frozen=True)
class Question:
    id: str
    group: str
    target: str
    path: str
    type: str
    prompt: str
    required: bool = False
    options: tuple[str, ...] = ()
    # List questions only: an explicit "none" answer is valid and stored as [].
    allow_none: bool = False


# Explicit answers meaning "no items" (case-insensitive, surrounding punctuation ignored).
NONE_ANSWERS = frozenset({"none", "no", "nothing", "n/a", "no avoided phrases", "no phrases",
                          "no items"})
LITERAL_EMPTY = frozenset({"[]", "[ ]", "{}", '""', "''"})


def questions() -> list[Question]:
    raw = yaml.safe_load(
        resources.files("lce.data").joinpath("interview_questions.yaml").read_text("utf-8")
    )
    out = []
    for q in raw["questions"]:
        q = dict(q)
        q["options"] = tuple(q.get("options", ()))
        out.append(Question(**q))
    return out


def get_question(qid: str) -> Question:
    for q in questions():
        if q.id == qid:
            return q
    raise StoreError(f"unknown question {qid!r}")


def _get(doc: dict, path: str) -> object:
    cur: object = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set(doc: dict, path: str, value: object) -> None:
    parts = path.split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _doc(store: DataStore, target: str) -> dict:
    attr, _ = TARGETS[target]
    return store.read_doc(getattr(store, attr))


def is_answered(q: Question, value: object) -> bool:
    """Three states: unanswered (absent/None/empty), explicitly none, or answered.

    An empty list counts as answered only for questions that allow an explicit
    "none"; for every other question it still means unanswered.
    """
    if value is None:
        return False
    if value == []:
        return q.type == "list" and q.allow_none
    return value != "" and value != {}


def status(store: DataStore) -> list[tuple[Question, bool]]:
    docs = {t: _doc(store, t) for t in TARGETS}
    return [(q, is_answered(q, _get(docs[q.target], q.path))) for q in questions()]


def missing(store: DataStore, required_only: bool = False) -> list[Question]:
    todo = [q for q, done in status(store) if not done and (q.required or not required_only)]
    return sorted(todo, key=lambda q: (not q.required, GROUP_ORDER.index(q.group)))


def ready_for_drafting(store: DataStore) -> list[Question]:
    """Required questions still unanswered (empty list = ready)."""
    return missing(store, required_only=True)


def coerce(q: Question, raw: str) -> object:
    raw = raw.strip()
    if not raw:
        raise StoreError("empty answer")
    if q.type == "text":
        return raw
    if q.type == "list":
        if raw in LITERAL_EMPTY:
            hint = "answer 'none'" if q.allow_none else "give at least one item"
            raise StoreError(f"{q.id}: a literal empty value is not an answer; {hint}")
        if raw.lower().strip(" .!") in NONE_ANSWERS:
            if not q.allow_none:
                raise StoreError(f"{q.id} needs at least one item")
            return []
        items = [i.strip() for i in raw.replace("\n", ";").split(";")]
        items = [i for i in items if i]
        if not items:
            raise StoreError(f"{q.id} needs at least one item")
        return items
    if q.type == "int":
        try:
            return int(raw)
        except ValueError as exc:
            raise StoreError(f"{q.id} expects a whole number") from exc
    if q.type == "bool":
        low = raw.lower()
        if low in {"yes", "y", "true", "1"}:
            return True
        if low in {"no", "n", "false", "0"}:
            return False
        raise StoreError(f"{q.id} expects yes or no")
    if q.type == "enum":
        if raw not in q.options:
            raise StoreError(f"{q.id} must be one of {', '.join(q.options)}")
        return raw
    if q.type == "yaml":
        try:
            return yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise StoreError(f"{q.id}: invalid YAML ({exc})") from exc
    raise StoreError(f"unsupported question type {q.type}")


def answer(store: DataStore, qid: str, raw: str, source: str = "interview") -> object:
    """Validate and store one answer in its private target file."""
    q = get_question(qid)
    value = coerce(q, raw)
    attr, kind = TARGETS[q.target]
    path = getattr(store, attr)
    doc = store.read_doc(path)
    _set(doc, q.path, value)
    if q.target == "profile":
        doc.setdefault("sources", {})[q.path] = source
    store.write_doc(path, kind, doc)  # raises on schema violations, nothing is written
    log = store.root / "interview" / "log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": now_iso(), "question": qid, "source": source}) + "\n")
    return value
