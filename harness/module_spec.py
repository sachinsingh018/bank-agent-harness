"""What a module gives the harness: tools, prompt, output model, verifiers and eval dataset."""

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
