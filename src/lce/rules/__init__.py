"""Language rulesets for humanizer checks and QA."""

from __future__ import annotations

from functools import cache
from importlib import resources

import yaml


class RulesetNotReady(RuntimeError):
    pass


@cache
def load_ruleset(language: str) -> dict:
    try:
        text = resources.files("lce.rules").joinpath(language, "ruleset.yaml").read_text("utf-8")
    except FileNotFoundError as exc:
        raise RulesetNotReady(f"no ruleset for language {language!r}") from exc
    return yaml.safe_load(text)


def ready_ruleset(language: str) -> dict:
    rules = load_ruleset(language)
    if rules.get("status") != "ready":
        raise RulesetNotReady(f"the {language!r} ruleset is not ready (status: {rules.get('status')})")
    return rules
