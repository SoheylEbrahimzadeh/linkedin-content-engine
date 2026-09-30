"""Validate a data directory (private data repo or demo persona) against the schemas."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from importlib import resources
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from jsonschema import Draft202012Validator, FormatChecker

LAYOUT: dict[str, str] = {
    "config/settings.yaml": "settings",
    "profile/profile.yaml": "profile",
    "profile/voice.yaml": "voice",
    "profile/brand.yaml": "brand",
    "story_bank/stories/*.yaml": "story",
    "research/candidates/*.yaml": "research_candidate",
    "plan/calendar.yaml": "plan",
    "posts/*/post.yaml": "post",
    "config/automation.yaml": "automation",
    "config/linkedin.yaml": "linkedin",
    "posts/*/publication.json": "publication",
    "automation/jobs/*.yaml": "job",
}


@dataclass(frozen=True)
class ValidationError:
    path: str
    message: str

    def render(self) -> str:
        return f"  {self.path}: {self.message}"


@cache
def load_schema(name: str) -> dict:
    text = resources.files("lce.schemas").joinpath(f"{name}.schema.json").read_text("utf-8")
    return json.loads(text)


@cache
def _validator(kind: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(kind), format_checker=FormatChecker())


def jsonable(value: object) -> object:
    """YAML turns unquoted dates into date objects; JSON Schema expects strings."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    return value


def validate_doc(kind: str, doc: object) -> list[str]:
    """Schema and consistency errors for one document (empty list when valid)."""
    if not isinstance(doc, dict):
        return ["expected a mapping"]
    doc = jsonable(doc)
    errors = []
    for err in _validator(kind).iter_errors(doc):
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{where}: {err.message}")
    if kind == "settings":
        if "timezone" in doc:
            try:
                ZoneInfo(doc["timezone"])
            except (ZoneInfoNotFoundError, ValueError):
                errors.append("timezone is not a valid IANA timezone")
        cadence = doc.get("cadence")
        if cadence and len(cadence.get("slots", [])) != cadence.get("posts_per_week"):
            errors.append("cadence: posts_per_week must equal the number of slots")
    if kind == "profile":
        ids = [p.get("id") for p in doc.get("pillars", [])]
        if len(ids) != len(set(ids)):
            errors.append("pillars: duplicate pillar id")
    return errors


def validate_dir(root: Path) -> tuple[int, list[ValidationError]]:
    """Return (files_checked, errors)."""
    errors: list[ValidationError] = []
    checked = 0
    for pattern, kind in LAYOUT.items():
        for path in sorted(root.glob(pattern)):
            rel = str(path.relative_to(root))
            checked += 1
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                errors.append(ValidationError(rel, f"invalid YAML: {exc}"))
                continue
            errors.extend(ValidationError(rel, m) for m in validate_doc(kind, doc))
            id_key = {"story": "story_id", "research_candidate": "candidate_id",
                      "job": "job_id"}.get(kind)
            if id_key and isinstance(doc, dict) and path.stem != doc.get(id_key):
                errors.append(ValidationError(rel, f"file name must equal {id_key}"))
            if kind == "post" and isinstance(doc, dict) and path.parent.name != doc.get("post_id"):
                errors.append(ValidationError(rel, "directory name must equal post_id"))
    return checked, errors
