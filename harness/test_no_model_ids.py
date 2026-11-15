"""Model ids must live only in config/, never in Python or TypeScript code."""

import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_ROOTS = ("harness", "modules", "evals", "apps", "tools")
TS_ROOT = ROOT / "apps" / "web"
SKIP_DIRS = {".venv", "node_modules", ".next", "__pycache__"}
VENDORS = "anthropic|openai|deepseek|qwen|meta-llama|mistralai|moonshotai|z-ai|google|x-ai"
MODEL_ID = re.compile(
    r"claude-[a-z0-9]|gpt-[0-9a-z]|gemini-|qwen|mistral|llama-"
    rf"|(?:{VENDORS})/[a-z0-9]",
    re.I,
)


def _is_test_file(path: Path) -> bool:
    return path.name.startswith("test_") or path.name == "conftest.py"


def _scanned_files() -> Iterator[Path]:
    for top in PY_ROOTS:
        yield from (ROOT / top).rglob("*.py")
    for suffix in ("*.ts", "*.tsx"):
        yield from TS_ROOT.rglob(suffix)


def test_no_model_ids_in_code() -> None:
    offenders: list[str] = []
    for path in _scanned_files():
        if _is_test_file(path) or SKIP_DIRS.intersection(path.parts):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if MODEL_ID.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not offenders, "model ids found outside config/:\n" + "\n".join(offenders)


def test_regex_flags_sample_ids() -> None:
    samples = [
        "claude" + "-opus-5-5",
        "gpt" + "-4o",
        "gemini" + "-2.5-pro",
        "Qwen" + "3-235b",
        "mistral" + "-large",
        "llama" + "-3.3-70b",
        "deepseek" + "/deepseek-chat",
        "moonshotai" + "/kimi-k2",
        "z-ai" + "/glm-4.6",
        "x-ai" + "/grok-4",
        "google" + "/gemma-3",
    ]
    for sample in samples:
        assert MODEL_ID.search(sample), sample
    assert not MODEL_ID.search("alert_id = 7  # check the counterparty graph")
