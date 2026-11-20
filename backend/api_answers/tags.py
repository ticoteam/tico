# pyright: strict
"""The answers of the Tags routes."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from ._base import answer


@answer
class Tag(TypedDict):
    id: str
    key: str
    label: str
    metadata: dict[str, Any]
    markdown: str
    is_template: bool
    kind: Literal["", "risk"]
    template_id: str | None
    owner: str | None
    bot_writers: list[str]
    version: int
    created_at: str
    updated_at: str


@answer
class TagList(TypedDict):
    tags: list[Tag]


@answer
class TagResult(TypedDict):
    tag: Tag


@answer
class TagDetail(TypedDict):
    tag: Tag
    editable: bool
    tasks: list[task_answers.Task]
    next_offset: int | None


# A tag lists its tasks and a task lists its tags, so tasks.py imports Tag from here. This module reads Task off the
# tasks module, imported last, which works whichever of the two loads first; pydantic resolves the name when FastAPI
# writes the document.
from . import tasks as task_answers
