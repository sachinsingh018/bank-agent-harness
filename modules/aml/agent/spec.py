"""The AML module as the harness sees it: tools, prompt, output model, verifiers and dataset."""

from __future__ import annotations

from pathlib import Path

from harness.module_spec import ModuleSpec
from harness.prompts import PromptRef, load_prompt
from modules.aml.agent.lookup import make_aml_lookup_factory
from modules.aml.agent.models import AmlFindings, submit_findings_tool_spec
from modules.aml.agent.verifiers import (
    AlertMembershipVerifier,
    ArithmeticVerifier,
    CitationVerifier,
)
from tools.aml.caps import TOOL_TIMEOUT_SECONDS
from tools.aml.launch import server_params

PROMPTS = Path("modules/aml/prompts")
INVESTIGATOR_PROMPT = PromptRef(PROMPTS / "investigator_v3.md", "v3")
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
    submit_tool=submit_findings_tool_spec(),
    # Cheapest first. All of them run on every output.
    verifiers=(CitationVerifier(), ArithmeticVerifier(), AlertMembershipVerifier()),
    eval_dataset=Path("evals/datasets/aml_alerts_dev_v1.json"),
    build_case_message=build_case_message,
    make_lookup_factory=make_aml_lookup_factory,
)
