"""Pick the model adapter for a profile by its provider."""

from __future__ import annotations

from collections.abc import Sequence

from harness.models.anthropic_adapter import AnthropicAdapter
from harness.models.config import ModelProfile
from harness.models.openrouter_adapter import OpenRouterAdapter
from harness.models.scripted_adapter import ScriptedAdapter, ScriptItem
from harness.models.types import ModelAdapter


def make_adapter(
    profile: ModelProfile, *, scripted_responses: Sequence[ScriptItem] | None = None
) -> ModelAdapter:
    """Build the adapter for a profile. Clients are created lazily on the first call."""
    if profile.provider == "anthropic":
        return AnthropicAdapter(profile)
    if profile.provider == "openrouter":
        return OpenRouterAdapter(profile)
    if profile.provider == "scripted":
        if scripted_responses is None:
            raise ValueError("the scripted provider needs scripted_responses")
        return ScriptedAdapter(scripted_responses, profile)
    raise ValueError(f"unknown provider {profile.provider!r}")
