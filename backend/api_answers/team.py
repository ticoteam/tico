# pyright: strict
"""The answers of the Team chart routes in the stable contract that are declared in code (backend/org_routes.py)."""

from __future__ import annotations

from typing import Literal, TypedDict

from ._base import answer


class MentionCandidate(TypedDict):
    id: str
    """The actor id: `human:<id>` or `bot:<id>`"""
    kind: Literal["person", "agent"]
    name: str
    handle: str | None
    """The text to insert, @ included, which a post reads as this teammate and no one else; null when every name they
    go by also names someone else"""
    on_task: bool
    """One of the task's people (`task=`): who asked, who it is for, who has it, or someone in a role"""


@answer
class MentionCandidates(TypedDict):
    candidates: list[MentionCandidate]
    """Best match first: the task's people, then a name or handle starting with `q`, a word in one starting with it,
    one containing it, then by name"""
