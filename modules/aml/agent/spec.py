"""The AML module as the harness sees it: tools, prompt, output model, verifiers and dataset."""

from __future__ import annotations

from pathlib import Path

from harness.module_spec import ModuleSpec
from harness.prompts import PromptRef, load_prompt
from modules.aml.agent.models import AmlFindings
from tools.aml.caps import TOOL_TIMEOUT_SECONDS
from tools.aml.launch import server_params

PROMPTS = Path("modules/aml/prompts")
INVESTIGATOR_PROMPT = PromptRef(PROMPTS / "investigator_v1.md", "v1")
CASE_MESSAGE_PROMPT = PromptRef(PROMPTS / "case_message_v1.md", "v1")


def build_case_message(alert_id: str) -> str:
    """The first user message of a case, from a versioned template file."""
    template = load_prompt(CASE_MESSAGE_PROMPT).text
    return template.replace("{alert_id}", alert_id).strip()


AML_SPEC = ModuleSpec(
    name="aml",
    server_params=server_params,
    tool_timeout_seconds=TOOL_TIMEOUT_SECONDS,
    prompt=INVESTIGATOR_PROMPT,
    output_model=AmlFindings,
    verifiers=(),
    eval_dataset=Path("evals/datasets/aml_alerts_dev_v1.json"),
    build_case_message=build_case_message,
)
