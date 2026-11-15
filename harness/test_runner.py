"""run_case setup failures, without a database."""

import asyncio
from pathlib import Path

from mcp import StdioServerParameters

from harness.models.scripted_adapter import ScriptedAdapter
from harness.models.types import ToolSpec
from harness.module_spec import ModuleSpec
from harness.prompts import PromptRef
from harness.runner import run_case


def _missing_server(url: str) -> StdioServerParameters:
    return StdioServerParameters(command="/nonexistent/tools-server", args=[])


def test_server_start_failure_is_tool_failure(tmp_path: Path) -> None:
    prompt = tmp_path / "system_v1.md"
    prompt.write_text("system", encoding="utf-8")
    spec = ModuleSpec(
        name="test",
        server_params=_missing_server,
        tool_timeout_seconds=1.0,
        prompt=PromptRef(prompt, "v1"),
        output_model=ToolSpec,
        submit_tool=ToolSpec(name="submit", description="d", input_schema={}),
        verifiers=(),
        eval_dataset=Path("unused.json"),
        build_case_message=lambda case_id: case_id,
    )
    adapter = ScriptedAdapter([])
    result = asyncio.run(run_case("C-1", spec, "scripted", database_url="unused", adapter=adapter))
    assert result.status == "human_review" and result.reason == "tool_failure"
    assert result.trace.model_calls == [] and result.tool_steps == 0
    assert result.trace.error_class is not None
    assert len(adapter.requests) == 0
