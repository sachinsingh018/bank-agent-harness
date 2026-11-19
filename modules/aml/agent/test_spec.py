"""The AML spec, the prompt files and the case message."""

import re
from pathlib import Path

from harness.prompts import REPO_ROOT, load_prompt
from modules.aml.agent.lookup import make_aml_lookup_factory
from modules.aml.agent.models import SUBMIT_TOOL_NAME, AmlFindings
from modules.aml.agent.spec import AML_SPEC, build_case_message
from tools.aml.caps import TOOL_TIMEOUT_SECONDS
from tools.aml.launch import server_params

PROMPT_DIR = REPO_ROOT / "modules" / "aml" / "prompts"
MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
    "|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
DATE_PATTERNS = (
    re.compile(rf"\b({MONTHS})\b"),
    re.compile(r"\b(19|20)[0-9]{2}\b"),
    re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}"),
    re.compile(r"\b[0-9]{1,2}/[0-9]{1,2}\b"),
)
LATE_HINTS = ("late activity", "end of the data", "date range", "recent transfers")


def _prompt_files() -> list[Path]:
    files = sorted(PROMPT_DIR.glob("*.md"))
    assert len(files) >= 2
    return files


def test_prompt_files_have_no_dates_or_date_hints() -> None:
    for path in _prompt_files():
        text = path.read_text(encoding="utf-8")
        for pattern in DATE_PATTERNS:
            assert not pattern.search(text), f"{path.name} matches {pattern.pattern}"
        for hint in LATE_HINTS:
            assert hint not in text.lower(), f"{path.name} mentions {hint}"


def test_prompt_files_have_no_em_or_en_dashes() -> None:
    for path in _prompt_files():
        text = path.read_text(encoding="utf-8")
        assert "\u2014" not in text and "\u2013" not in text, path.name


def test_investigator_prompt_loads_and_names_the_tools() -> None:
    loaded = load_prompt(AML_SPEC.prompt)
    assert loaded.version == "v4"
    assert loaded.path == "modules/aml/prompts/investigator_v4.md"
    words = ("submit_findings", "get_alert", "describe_detector", "run_pattern_check", "NONE")
    for word in (*words, "sum_transfers"):
        assert word in loaded.text
    for limit in ("at most 50", "at most 20 amounts", "at most 10 claims", "1,200 characters"):
        assert limit in loaded.text


def test_case_message_contains_the_alert_id() -> None:
    message = build_case_message("AL-0123456789ab")
    assert "AL-0123456789ab" in message
    assert "{alert_id}" not in message


def test_spec_wiring() -> None:
    assert AML_SPEC.name == "aml"
    assert AML_SPEC.output_model is AmlFindings
    assert AML_SPEC.submit_tool.name == SUBMIT_TOOL_NAME
    assert AML_SPEC.server_params is server_params
    assert AML_SPEC.tool_timeout_seconds == TOOL_TIMEOUT_SECONDS
    assert [v.name for v in AML_SPEC.verifiers] == [
        "citations",
        "arithmetic",
        "alert_membership",
    ]
    assert AML_SPEC.make_lookup_factory is make_aml_lookup_factory
    assert AML_SPEC.eval_dataset == Path("evals/datasets/aml_alerts_dev_v1.json")
