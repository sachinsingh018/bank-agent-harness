from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.config import HarnessLimits, effective_max_output_tokens, load_limits
from harness.models.config import get_profile


def test_repo_limits_load() -> None:
    assert load_limits() == HarnessLimits(
        max_tool_steps=12,
        max_total_tokens=600_000,
        max_wall_seconds=300,
        max_output_tokens_per_call=16_000,
    )


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "harness.toml"
    path.write_text(body)
    return path


@pytest.mark.parametrize(
    "body",
    [
        "[limits]\nmax_tool_steps = 12\nmax_retries = 3\n",
        "[limits]\nmax_tool_steps = 0\n",
        "[limits]\nmax_wall_seconds = -5\n",
        "other = 1\n[limits]\nmax_tool_steps = 3\n",
        "max_tool_steps = 3\n",
    ],
)
def test_invalid_limits_rejected(tmp_path: Path, body: str) -> None:
    with pytest.raises(ValidationError):
        load_limits(_write(tmp_path, body))


def test_partial_limits_use_defaults(tmp_path: Path) -> None:
    limits = load_limits(_write(tmp_path, "[limits]\nmax_tool_steps = 3\n"))
    assert limits.max_tool_steps == 3
    assert limits.max_total_tokens == 600_000


def test_effective_max_output_tokens() -> None:
    profile = get_profile("claude-opus")
    small = HarnessLimits(max_output_tokens_per_call=1_000)
    big = HarnessLimits(max_output_tokens_per_call=profile.max_output_tokens + 1)
    assert effective_max_output_tokens(small, profile) == 1_000
    assert effective_max_output_tokens(big, profile) == profile.max_output_tokens
