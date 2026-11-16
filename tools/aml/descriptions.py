"""Loads the versioned, model-facing tool descriptions. Fails loudly on any mismatch."""

import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

DESCRIPTIONS_PATH = Path(__file__).with_name("tool_descriptions_v2.json")


class DescriptionsError(Exception):
    """The descriptions file and the tool list disagree."""


class Descriptions(BaseModel):
    version: str
    tools: dict[str, str]


def load_descriptions(
    expected_names: Iterable[str], path: Path = DESCRIPTIONS_PATH
) -> Descriptions:
    """Read the file and check it has exactly one non-empty description per tool."""
    descriptions = Descriptions.model_validate(json.loads(path.read_text(encoding="utf-8")))
    expected = set(expected_names)
    missing = sorted(expected - descriptions.tools.keys())
    extra = sorted(descriptions.tools.keys() - expected)
    if missing:
        raise DescriptionsError(f"tools without a description: {missing}")
    if extra:
        raise DescriptionsError(f"descriptions without a tool: {extra}")
    empty = sorted(name for name, text in descriptions.tools.items() if not text.strip())
    if empty:
        raise DescriptionsError(f"empty descriptions: {empty}")
    if not descriptions.version.strip():
        raise DescriptionsError("descriptions file has no version")
    return descriptions
