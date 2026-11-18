"""Lessons in the loop and the runner, with a scripted model. No network, no database."""

import asyncio
import dataclasses
from pathlib import Path

import pytest
from mcp import StdioServerParameters
from pydantic import ValidationError

from harness.config import HarnessLimits
from harness.lessons import Lesson, render_lessons
from harness.loop import run_agent
from harness.models.scripted_adapter import ScriptedAdapter, tool_call_response
from harness.models.types import ToolSpec
from harness.module_spec import ModuleSpec
from harness.prompts import PromptRef
from harness.runner import run_case
from harness.test_loop import PROMPT, FakeTools, _call, _spec, _submit
from harness.trace import CaseResult

LESSONS = (Lesson(id=4, text="Payroll is not fan-out."), Lesson(id=9, text="Check  the\nsource."))


def _template(
    tmp_path: Path, text: str = "Past lessons, guidance only:\n\n{lessons}\n"
) -> PromptRef:
    path = tmp_path / "lessons_block_v1.md"
    path.write_text(text, encoding="utf-8")
    return PromptRef(path, "v1")


def _run(spec: ModuleSpec, lessons: tuple[Lesson, ...]) -> tuple[CaseResult, ScriptedAdapter]:
    adapter = ScriptedAdapter(
        [tool_call_response([_call("lookup", id=7)]), tool_call_response([_submit()])]
    )
    result = asyncio.run(
        run_agent(
            case_id="C-1",
            spec=spec,
            adapter=adapter,
            profile=adapter.profile,
            tools=FakeTools(),
            limits=HarnessLimits(),
            prompt=PROMPT,
            verify_context_factory=lambda: None,
            lessons=lessons,
        )
    )
    return result, adapter


def test_render_lists_one_line_per_lesson() -> None:
    text = render_lessons("Head\n{lessons}\n", LESSONS)
    assert text == "Head\n- Payroll is not fan-out.\n- Check the source."
    with pytest.raises(ValueError, match="placeholder"):
        render_lessons("no placeholder", LESSONS)


def test_lesson_model_is_strict() -> None:
    with pytest.raises(ValidationError):
        Lesson(id=0, text="x")
    with pytest.raises(ValidationError):
        Lesson(id=1, text="")


def test_lessons_go_into_the_first_message_through_the_template(tmp_path: Path) -> None:
    spec = dataclasses.replace(_spec(), lessons_template=_template(tmp_path))
    result, adapter = _run(spec, LESSONS)
    assert result.status == "proposed"
    first = adapter.requests[0].messages[0].text()
    assert first == (
        "Investigate C-1.\n\nPast lessons, guidance only:\n\n"
        "- Payroll is not fan-out.\n- Check the source."
    )
    assert result.trace.lesson_ids == [4, 9]
    # Only the first user message carries them.
    later = [m.text() for m in adapter.requests[1].messages[1:] if m.role == "user"]
    assert not any("Payroll" in t for t in later)


def test_no_lessons_changes_nothing(tmp_path: Path) -> None:
    spec = dataclasses.replace(_spec(), lessons_template=_template(tmp_path))
    with_template, adapter = _run(spec, ())
    plain, plain_adapter = _run(_spec(), ())
    assert adapter.requests[0].messages[0].text() == "Investigate C-1."
    assert plain_adapter.requests[0].messages[0].text() == "Investigate C-1."
    assert with_template.trace.lesson_ids == [] and plain.trace.lesson_ids == []
    assert "lesson_ids" in plain.trace.model_dump()  # default, empty


def test_lessons_without_a_template_are_internal_error() -> None:
    result, adapter = _run(_spec(), LESSONS)
    assert result.status == "human_review" and result.reason == "internal_error"
    assert result.trace.error_class == "ValueError"
    assert result.trace.lesson_ids == []
    assert adapter.requests == []


def _missing_server(url: str) -> StdioServerParameters:
    return StdioServerParameters(command="/nonexistent/tools-server", args=[])


def _runner_spec(tmp_path: Path) -> ModuleSpec:
    prompt = tmp_path / "system_v1.md"
    prompt.write_text("system", encoding="utf-8")
    return ModuleSpec(
        name="test",
        server_params=_missing_server,
        tool_timeout_seconds=1.0,
        prompt=PromptRef(prompt, "v1"),
        output_model=ToolSpec,
        submit_tool=ToolSpec(name="submit", description="d", input_schema={}),
        verifiers=(),
        eval_dataset=Path("unused.json"),
        build_case_message=lambda case_id: case_id,
        make_lookup_factory=lambda url: lambda: None,
    )


def test_runner_asks_the_provider_once_with_the_case_id(tmp_path: Path) -> None:
    asked: list[str] = []

    def provider(case_id: str) -> list[Lesson]:
        asked.append(case_id)
        return list(LESSONS)

    adapter = ScriptedAdapter([])
    result = asyncio.run(
        run_case(
            "C-7",
            _runner_spec(tmp_path),
            "scripted",
            database_url="unused",
            adapter=adapter,
            lessons_provider=provider,
        )
    )
    assert asked == ["C-7"]
    assert result.reason == "tool_failure"  # the fake server does not start


def test_a_failing_provider_is_internal_error(tmp_path: Path) -> None:
    def provider(case_id: str) -> list[Lesson]:
        raise RuntimeError("database down")

    adapter = ScriptedAdapter([])
    result = asyncio.run(
        run_case(
            "C-7",
            _runner_spec(tmp_path),
            "scripted",
            database_url="unused",
            adapter=adapter,
            lessons_provider=provider,
        )
    )
    assert result.status == "human_review" and result.reason == "internal_error"
    assert result.trace.error_class == "RuntimeError"
    assert adapter.requests == []
