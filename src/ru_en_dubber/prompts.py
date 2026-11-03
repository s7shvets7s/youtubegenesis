"""Editable prompt catalog shared by every local model-backed stage."""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


PROMPTS_ENV = "RU_EN_DUBBER_PROMPTS_FILE"
DEFAULT_PROMPTS_PATH = Path(__file__).with_name("agent_prompts.json")
_PLACEHOLDER = re.compile(r"\{\{([a-zA-Z][a-zA-Z0-9_]*)\}\}")


class PromptConfigError(Exception):
    """Raised when the editable prompt catalog is missing or invalid."""


def prompt_catalog_path() -> Path:
    override = os.environ.get(PROMPTS_ENV)
    return Path(override).expanduser().resolve() if override else DEFAULT_PROMPTS_PATH


@lru_cache(maxsize=8)
def _load_catalog(path_text: str, modified_ns: int, size: int) -> dict[str, Any]:
    del modified_ns, size
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PromptConfigError(f"cannot load prompt catalog {path}: {error}") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise PromptConfigError("prompt catalog must be a version 1 JSON object")
    prompts = payload.get("prompts")
    if not isinstance(prompts, dict):
        raise PromptConfigError("prompt catalog has no prompts object")
    return prompts


def _catalog() -> dict[str, Any]:
    path = prompt_catalog_path()
    try:
        stat = path.stat()
    except OSError as error:
        raise PromptConfigError(f"prompt catalog is unavailable: {path}") from error
    return _load_catalog(str(path), stat.st_mtime_ns, stat.st_size)


def get_prompt(key: str) -> str:
    value: Any = _catalog()
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            raise PromptConfigError(f"unknown prompt key: {key}")
        value = value[part]
    if not isinstance(value, str) or not value.strip():
        raise PromptConfigError(f"prompt {key} must be a non-empty string")
    return value


def render_prompt(
    key: str,
    *,
    required: tuple[str, ...] = (),
    **values: object,
) -> str:
    template = get_prompt(key)
    placeholders = set(_PLACEHOLDER.findall(template))
    missing_required = set(required) - placeholders
    if missing_required:
        raise PromptConfigError(
            f"prompt {key} is missing required placeholders: {sorted(missing_required)}"
        )
    missing_values = placeholders - values.keys()
    if missing_values:
        raise PromptConfigError(
            f"prompt {key} has unresolved placeholders: {sorted(missing_values)}"
        )
    return _PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), template)
