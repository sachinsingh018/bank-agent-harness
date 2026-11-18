"""What a module gives the harness: tools, prompt, output model, verifiers and eval dataset,
plus an optional checker (ReviewerSpec) for maker-checker."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters
from pydantic import BaseModel

from harness.models.types import ToolSpec
from harness.prompts import PromptRef
from harness.verifiers.base import Verifier

VERDICT_FIELD = "verdict"


@dataclass(frozen=True)
class ReviewerSpec:
    """The checker: a second agent that reviews verified findings and approves or rejects.

    Its output model must have a `verdict` field with the values approve or reject; the
    harness reads only that field, everything else is module data. It runs with the module's
    read-only tools and lookup, its own prompt and the [reviewer] limits."""

    prompt: PromptRef  # versioned file
    output_model: type[BaseModel]
    submit_tool: ToolSpec  # input schema is output_model
    # (case id, the maker's findings) -> first user message, from a versioned template file
    build_review_message: Callable[[str, dict[str, Any]], str]
    # the maker's findings -> deterministic verifiers for the review
    make_verifiers: Callable[[dict[str, Any]], Sequence[Verifier]]
    message_prompt: PromptRef | None = None  # the review message template, for manifests

    def __post_init__(self) -> None:
        if VERDICT_FIELD not in self.output_model.model_fields:
            raise ValueError("a reviewer output model needs a verdict field")


@dataclass(frozen=True)
class ModuleSpec:
    name: str  # "aml"
    server_params: Callable[[str], StdioServerParameters]  # database url -> tools server launch
    tool_timeout_seconds: float  # read timeout for each tool call
    prompt: PromptRef  # versioned file, loaded and hashed at start
    output_model: type[BaseModel]  # for example AmlFindings
    # The final-answer tool, whose input schema is output_model. The loop knows it by
    # submit_tool.name and never counts it as a tool step.
    submit_tool: ToolSpec
    verifiers: Sequence[Verifier]
    eval_dataset: Path  # dataset manifest
    build_case_message: Callable[[str], str]  # case id -> first user message
    # database url -> factory of the read-only lookup the verifiers use (VerifyContext.lookup).
    # If the factory has a close() method, run_case calls it when the case ends.
    make_lookup_factory: Callable[[str], Callable[[], Any]]
    reviewer: ReviewerSpec | None = None  # the checker; None means no maker-checker
