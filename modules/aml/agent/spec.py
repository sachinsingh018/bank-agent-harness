"""The AML module as the harness sees it: tools, prompt, output model, verifiers and dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.module_spec import ModuleSpec, ReviewerSpec
from harness.prompts import PromptRef, load_prompt
from modules.aml.agent.lookup import make_aml_lookup_factory
from modules.aml.agent.models import (
    AmlFindings,
    AmlReview,
    submit_findings_tool_spec,
    submit_review_tool_spec,
)
from modules.aml.agent.verifiers import (
    AlertMembershipVerifier,
    ArithmeticVerifier,
    CitationVerifier,
    review_verifiers,
)
from tools.aml.caps import TOOL_TIMEOUT_SECONDS
from tools.aml.launch import server_params

PROMPTS = Path("modules/aml/prompts")
INVESTIGATOR_PROMPT = PromptRef(PROMPTS / "investigator_v3.md", "v3")
CASE_MESSAGE_PROMPT = PromptRef(PROMPTS / "case_message_v1.md", "v1")
REVIEWER_PROMPT = PromptRef(PROMPTS / "reviewer_v1.md", "v1")
REVIEW_MESSAGE_PROMPT = PromptRef(PROMPTS / "review_message_v1.md", "v1")
LESSONS_BLOCK_PROMPT = PromptRef(PROMPTS / "lessons_block_v1.md", "v1")


def build_case_message(alert_id: str) -> str:
    """The first user message of a case, from a versioned template file."""
    template = load_prompt(CASE_MESSAGE_PROMPT).text
    return template.replace("{alert_id}", alert_id).strip()


def build_review_message(alert_id: str, findings: dict[str, Any]) -> str:
    """The checker's first user message: the alert id and the maker's findings as JSON."""
    template = load_prompt(REVIEW_MESSAGE_PROMPT).text
    body = json.dumps(findings, indent=2, sort_keys=True, ensure_ascii=False)
    return template.replace("{alert_id}", alert_id).replace("{findings_json}", body).strip()


AML_REVIEWER = ReviewerSpec(
    prompt=REVIEWER_PROMPT,
    output_model=AmlReview,
    submit_tool=submit_review_tool_spec(),
    build_review_message=build_review_message,
    make_verifiers=review_verifiers,
    message_prompt=REVIEW_MESSAGE_PROMPT,
)


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
    reviewer=AML_REVIEWER,
    lessons_template=LESSONS_BLOCK_PROMPT,
)
