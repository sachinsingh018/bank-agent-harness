"""Load agent loop limits from config/harness.toml: [limits] for the investigating agent (the
maker) and [reviewer] for the checker in maker-checker."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from harness.models.config import ModelProfile

DEFAULT_HARNESS_PATH = Path(__file__).resolve().parents[1] / "config" / "harness.toml"


class HarnessLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_tool_steps: int = Field(default=12, gt=0)
    max_total_tokens: int = Field(default=600_000, gt=0)
    max_wall_seconds: int = Field(default=300, gt=0)
    max_output_tokens_per_call: int = Field(default=16_000, gt=0)


class _HarnessFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limits: HarnessLimits
    reviewer: HarnessLimits | None = None


def _load(path: Path) -> _HarnessFile:
    with path.open("rb") as f:
        return _HarnessFile.model_validate(tomllib.load(f))


def load_limits(path: Path = DEFAULT_HARNESS_PATH) -> HarnessLimits:
    return _load(path).limits


def load_reviewer_limits(path: Path = DEFAULT_HARNESS_PATH) -> HarnessLimits:
    """Limits for the checker run. Without a [reviewer] section the maker's limits apply."""
    loaded = _load(path)
    return loaded.reviewer if loaded.reviewer is not None else loaded.limits


def effective_max_output_tokens(limits: HarnessLimits, profile: ModelProfile) -> int:
    """Output token cap for one model call: the smaller of the harness and profile caps."""
    return min(limits.max_output_tokens_per_call, profile.max_output_tokens)
