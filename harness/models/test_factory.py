from decimal import Decimal

import pytest

from harness.models.anthropic_adapter import AnthropicAdapter
from harness.models.config import ModelProfile, Prices
from harness.models.factory import make_adapter
from harness.models.openrouter_adapter import OpenRouterAdapter
from harness.models.scripted_adapter import ScriptedAdapter, text_response

_PRICES = Prices(input=Decimal(0), output=Decimal(0), cache_read=Decimal(0), cache_write=Decimal(0))


def _profile(provider: str, model: str) -> ModelProfile:
    return ModelProfile.model_validate(
        {
            "name": provider,
            "provider": provider,
            "model": model,
            "max_output_tokens": 100,
            "prices": _PRICES,
        }
    )


def test_picks_class_by_provider() -> None:
    assert isinstance(make_adapter(_profile("anthropic", "claude-x")), AnthropicAdapter)
    assert isinstance(make_adapter(_profile("openrouter", "vendor/model")), OpenRouterAdapter)
    scripted = make_adapter(
        _profile("scripted", "scripted"), scripted_responses=[text_response("hi")]
    )
    assert isinstance(scripted, ScriptedAdapter)


def test_scripted_needs_responses() -> None:
    with pytest.raises(ValueError):
        make_adapter(_profile("scripted", "scripted"))


def test_no_network_or_key_needed_to_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    make_adapter(_profile("anthropic", "claude-x"))
    make_adapter(_profile("openrouter", "vendor/model"))
