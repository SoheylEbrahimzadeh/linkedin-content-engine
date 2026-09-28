"""Validate a data directory (private data repo or demo persona) against the schemas."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from importlib import resources
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from jsonschema import Draft202012Validator, FormatChecker

LAYOUT: dict[str, str] = {
    "config/settings.yaml": "settings",
    "profile/brand.yaml": "brand_profile",
    "profile/voice.yaml": "voice_profile",
    "story_bank/facts/*.yaml": "story_fact",
    "posts/**/*.md": "post",
}


@dataclass(frozen=True)
class ValidationError:
    path: str
    message: str

    def render(self) -> str:
        return f"  {self.path}: {self.message}"


def load_schema(name: str) -> dict:
    text = resources.files("lce.schemas").joinpath(f"{name}.schema.json").read_text("utf-8")
    return json.loads(text)


def _frontmatter(text: str) -> dict | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    return yaml.safe_load(text[4:end]) or {}


def _jsonable(value: object) -> object:
    """YAML turns unquoted dates into date objects; JSON Schema expects strings."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _load(path: Path) -> object:
    text = path.read_text(encoding="utf-8")
    doc = _frontmatter(text) if path.suffix == ".md" else yaml.safe_load(text)
    return _jsonable(doc)


def _extra_checks(kind: str, doc: dict, rel: str, seen_ids: set[str]) -> list[ValidationError]:
    errors = []
    if kind == "settings":
        try:
            ZoneInfo(doc.get("timezone", ""))
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(ValidationError(rel, "timezone is not a valid IANA timezone"))
        slots = doc.get("cadence", {}).get("slots", [])
        if len(slots) != doc.get("cadence", {}).get("posts_per_week"):
            errors.append(ValidationError(rel, "posts_per_week must equal the number of slots"))
    if kind == "story_fact":
        fid = doc.get("fact_id")
        if fid in seen_ids:
            errors.append(ValidationError(rel, f"duplicate fact_id {fid!r}"))
        seen_ids.add(fid)
        if Path(rel).stem != fid:
            errors.append(ValidationError(rel, "file name must equal fact_id"))
    return errors


def validate_dir(root: Path) -> tuple[int, list[ValidationError]]:
    """Return (files_checked, errors)."""
    errors: list[ValidationError] = []
    checked = 0
    seen_ids: set[str] = set()
    fmt = FormatChecker()
    for pattern, kind in LAYOUT.items():
        validator = Draft202012Validator(load_schema(kind), format_checker=fmt)
        for path in sorted(root.glob(pattern)):
            rel = str(path.relative_to(root))
            checked += 1
            try:
                doc = _load(path)
            except yaml.YAMLError as exc:
                errors.append(ValidationError(rel, f"invalid YAML: {exc}"))
                continue
            if not isinstance(doc, dict):
                errors.append(ValidationError(rel, "expected a mapping"))
                continue
            for err in validator.iter_errors(doc):
                where = "/".join(str(p) for p in err.absolute_path) or "(root)"
                errors.append(ValidationError(rel, f"{where}: {err.message}"))
            errors.extend(_extra_checks(kind, doc, rel, seen_ids))
    return checked, errors
