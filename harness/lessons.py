"""Lessons: short notes a human saved after earlier cases, shown to the agent as guidance.

The harness only knows the shape (an id and a text) and how to render them into the first user
message through the module's versioned template. Where lessons come from is the module's
business: a provider outside the agent picks them (for AML, modules/aml/memory.py) and the
runner passes them to the loop. Lessons are guidance, never evidence about the current case,
and they change neither the verifiers nor the checker.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field

LESSONS_PLACEHOLDER = "{lessons}"


class Lesson(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(ge=1)
    text: str = Field(min_length=1)


# case id -> the lessons to show for that case (already selected and capped by the module)
LessonsProvider = Callable[[str], Sequence[Lesson]]


def render_lessons(template: str, lessons: Sequence[Lesson]) -> str:
    """The template with {lessons} replaced by one bullet line per lesson."""
    if LESSONS_PLACEHOLDER not in template:
        raise ValueError("the lessons template needs a {lessons} placeholder")
    body = "\n".join(f"- {' '.join(lesson.text.split())}" for lesson in lessons)
    return template.replace(LESSONS_PLACEHOLDER, body).strip()
