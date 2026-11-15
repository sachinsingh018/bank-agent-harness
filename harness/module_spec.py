"""What a module gives the harness: tools, prompt, output model, verifiers and eval dataset."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from mcp import StdioServerParameters
from pydantic import BaseModel

from harness.prompts import PromptRef
from harness.verifiers.base import Verifier


@dataclass(frozen=True)
class ModuleSpec:
    name: str  # "aml"
    server_params: Callable[[str], StdioServerParameters]  # database url -> tools server launch
    tool_timeout_seconds: float  # read timeout for each tool call
    prompt: PromptRef  # versioned file, loaded and hashed at start
    output_model: type[BaseModel]  # for example AmlFindings
    verifiers: Sequence[Verifier]
    eval_dataset: Path  # dataset manifest
    build_case_message: Callable[[str], str]  # case id -> first user message
