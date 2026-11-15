"""Load model profiles from config/models.toml and compute call cost."""

from __future__ import annotations

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from harness.models.types import Usage

DEFAULT_MODELS_PATH = Path(__file__).resolve().parents[2] / "config" / "models.toml"

Provider = Literal["anthropic", "openrouter", "scripted"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]

_PER_MILLION = Decimal(1_000_000)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Prices(_Strict):
    """USD per million tokens."""

    input: Decimal = Field(ge=0)
    output: Decimal = Field(ge=0)
    cache_read: Decimal = Field(ge=0)
    cache_write: Decimal = Field(ge=0)

    @field_validator("input", "output", "cache_read", "cache_write", mode="before")
    @classmethod
    def _no_floats(cls, value: Any) -> Any:
        # Floats lose precision (0.1 is not exact), so prices must be decimal strings.
        if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
            raise ValueError(
                f'price must be a decimal string such as "0.20" or an int, got {value!r}'
            )
        return value


class ProviderRouting(_Strict):
    """OpenRouter hosting provider preferences."""

    order: list[str] | None = None
    only: list[str] | None = None
    allow_fallbacks: bool | None = None


class ModelProfile(_Strict):
    name: str = Field(min_length=1)
    provider: Provider
    model: str = Field(min_length=1)
    max_output_tokens: int = Field(gt=0)
    effort: Effort | None = None
    provider_routing: ProviderRouting | None = None
    prices: Prices

    @model_validator(mode="after")
    def _provider_specific_fields(self) -> Self:
        if self.effort is not None and self.provider != "anthropic":
            raise ValueError("effort is only allowed for the anthropic provider")
        if self.provider_routing is not None and self.provider != "openrouter":
            raise ValueError("provider_routing is only allowed for the openrouter provider")
        if self.provider == "scripted" and self.model != "scripted":
            raise ValueError('the scripted provider requires model "scripted"')
        return self


class _ModelsFile(_Strict):
    profiles: dict[str, dict[str, object]]


def load_profiles(path: Path = DEFAULT_MODELS_PATH) -> dict[str, ModelProfile]:
    with path.open("rb") as f:
        raw = _ModelsFile.model_validate(tomllib.load(f))
    profiles: dict[str, ModelProfile] = {}
    for name, body in raw.profiles.items():
        if "name" in body:
            raise ValueError(f"profile {name}: 'name' comes from the table key")
        profiles[name] = ModelProfile.model_validate({"name": name, **body})
    return profiles


def get_profile(name: str, path: Path = DEFAULT_MODELS_PATH) -> ModelProfile:
    profiles = load_profiles(path)
    try:
        return profiles[name]
    except KeyError:
        known = ", ".join(sorted(profiles))
        raise KeyError(f"unknown model profile {name!r}; known: {known}") from None


def cost_usd(profile: ModelProfile, usage: Usage) -> Decimal:
    """Exact cost in USD: tokens times price per million, summed over token kinds."""
    p = profile.prices
    total = (
        usage.input_tokens * p.input
        + usage.output_tokens * p.output
        + usage.cache_read_tokens * p.cache_read
        + usage.cache_write_tokens * p.cache_write
    )
    return total / _PER_MILLION
