from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.models.config import (
    ModelProfile,
    Prices,
    cost_usd,
    get_profile,
    load_profiles,
)
from harness.models.types import Usage

PRICES = """
[profiles.{name}.prices]
input = "1.00"
output = "2.00"
cache_read = "0.10"
cache_write = "1.25"
"""

SCRIPTED = '[profiles.p]\nprovider = "scripted"\nmodel = "scripted"\n'


def _write(tmp_path: Path, body: str, name: str = "p") -> Path:
    path = tmp_path / "models.toml"
    path.write_text(body + PRICES.format(name=name))
    return path


def test_repo_config_loads() -> None:
    profiles = load_profiles()
    assert set(profiles) >= {
        "claude-opus",
        "openrouter-free-dev",
        "openrouter-eu-us-open",
        "scripted",
    }
    opus = profiles["claude-opus"]
    assert opus.provider == "anthropic"
    assert opus.effort == "medium"
    assert opus.prices.input == Decimal("4.00")
    assert opus.prices.cache_write == Decimal("5.00")
    eu = profiles["openrouter-eu-us-open"]
    assert eu.provider_routing is not None
    assert eu.provider_routing.allow_fallbacks is False
    assert profiles["openrouter-free-dev"].prices.output == Decimal(0)
    assert profiles["scripted"].provider == "scripted"
    for name, profile in profiles.items():
        assert profile.name == name


def test_get_profile() -> None:
    assert get_profile("scripted").model == "scripted"
    with pytest.raises(KeyError, match="unknown model profile"):
        get_profile("nope")


def test_minimal_profile(tmp_path: Path) -> None:
    path = _write(tmp_path, SCRIPTED + "max_output_tokens = 10\n")
    profile = load_profiles(path)["p"]
    assert profile.effort is None and profile.provider_routing is None


def test_effort_only_for_anthropic(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '[profiles.p]\nprovider = "openrouter"\nmodel = "m"\n'
        'max_output_tokens = 10\neffort = "medium"\n',
    )
    with pytest.raises(ValidationError, match="effort is only allowed"):
        load_profiles(path)


def test_bad_effort_value(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '[profiles.p]\nprovider = "anthropic"\nmodel = "m"\n'
        'max_output_tokens = 10\neffort = "turbo"\n',
    )
    with pytest.raises(ValidationError):
        load_profiles(path)


def test_routing_only_for_openrouter(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '[profiles.p]\nprovider = "anthropic"\nmodel = "m"\nmax_output_tokens = 10\n'
        '[profiles.p.provider_routing]\nonly = ["x"]\n',
    )
    with pytest.raises(ValidationError, match="provider_routing is only allowed"):
        load_profiles(path)


def test_routing_fields(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '[profiles.p]\nprovider = "openrouter"\nmodel = "m"\nmax_output_tokens = 10\n'
        '[profiles.p.provider_routing]\norder = ["a", "b"]\nonly = ["a"]\n'
        "allow_fallbacks = true\n",
    )
    routing = load_profiles(path)["p"].provider_routing
    assert routing is not None
    assert routing.order == ["a", "b"] and routing.only == ["a"] and routing.allow_fallbacks


@pytest.mark.parametrize(
    "body",
    [
        # unknown profile key
        SCRIPTED + "max_output_tokens = 10\ncolour = 1\n",
        # unknown routing key
        '[profiles.p]\nprovider = "openrouter"\nmodel = "m"\nmax_output_tokens = 10\n'
        '[profiles.p.provider_routing]\nignore = ["x"]\n',
        # unknown provider
        '[profiles.p]\nprovider = "acme"\nmodel = "m"\nmax_output_tokens = 10\n',
        # non positive max output tokens
        SCRIPTED + "max_output_tokens = 0\n",
        # name must come from the table key
        SCRIPTED + 'name = "q"\nmax_output_tokens = 1\n',
    ],
)
def test_invalid_profiles_rejected(tmp_path: Path, body: str) -> None:
    with pytest.raises(ValueError):
        load_profiles(_write(tmp_path, body))


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "other = 1\n" + SCRIPTED + "max_output_tokens = 1\n",
    )
    with pytest.raises(ValidationError):
        load_profiles(path)


def test_unknown_price_key_and_negative_price_rejected() -> None:
    with pytest.raises(ValidationError):
        Prices.model_validate(
            {"input": "1", "output": "1", "cache_read": "1", "cache_write": "1", "batch": "1"}
        )
    with pytest.raises(ValidationError):
        Prices.model_validate({"input": "-1", "output": "1", "cache_read": "1", "cache_write": "1"})


def test_cost_hand_computed_with_cache() -> None:
    profile = ModelProfile(
        name="x",
        provider="anthropic",
        model="m",
        max_output_tokens=10,
        prices=Prices(
            input=Decimal("4.00"),
            output=Decimal("20.00"),
            cache_read=Decimal("0.20"),
            cache_write=Decimal("5.00"),
        ),
    )
    usage = Usage(
        input_tokens=12_345,
        output_tokens=678,
        cache_read_tokens=50_000,
        cache_write_tokens=2_000,
    )
    # 12345*4 = 49380; 678*20 = 13560; 50000*0.20 = 10000; 2000*5 = 10000
    # total 82940 / 1_000_000 = 0.08294
    assert cost_usd(profile, usage) == Decimal("0.08294")


def test_cost_free_profile_is_zero() -> None:
    profile = get_profile("openrouter-free-dev")
    assert cost_usd(profile, Usage(input_tokens=10**6, output_tokens=10**6)) == 0


def test_scripted_provider_requires_scripted_model(tmp_path: Path) -> None:
    path = _write(
        tmp_path, '[profiles.p]\nprovider = "scripted"\nmodel = "m"\nmax_output_tokens = 10\n'
    )
    with pytest.raises(ValidationError, match='requires model "scripted"'):
        load_profiles(path)


def test_empty_profile_name_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelProfile.model_validate(
            {
                "name": "",
                "provider": "scripted",
                "model": "scripted",
                "max_output_tokens": 1,
                "prices": {"input": "0", "output": "0", "cache_read": "0", "cache_write": "0"},
            }
        )


def test_float_price_rejected(tmp_path: Path) -> None:
    path = tmp_path / "models.toml"
    path.write_text(
        SCRIPTED + "max_output_tokens = 1\n"
        "[profiles.p.prices]\ninput = 0.1\noutput = 0\ncache_read = 0\ncache_write = 0\n"
    )
    with pytest.raises(ValidationError, match="price must be a decimal string"):
        load_profiles(path)


def test_int_and_string_prices_accepted() -> None:
    prices = Prices.model_validate(
        {"input": 3, "output": "15.5", "cache_read": 0, "cache_write": "0.30"}
    )
    assert prices.input == Decimal(3) and prices.output == Decimal("15.5")
