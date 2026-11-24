# pyright: strict
"""The answers of the Team chart routes in the stable contract that are declared in code (backend/org_routes.py)."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from ._base import answer


class MentionCandidate(TypedDict):
    id: str
    """The actor id: `human:<id>` or `bot:<id>`"""
    kind: Literal["person", "agent"]
    name: str
    handle: str
    """The text to insert, @ included: the server reads it back as this teammate and no one else"""
    on_task: bool
    """One of the task's people (`task=`): who asked, who it is for, who has it, or someone in a role"""
    wakes: bool
    """Whether an @mention by the caller would reach them: an agent needs the caller's Write on it, and on a task or in
    a chat they must be able to read it (or be added to a private task's readers by the mention)"""
    reason: NotRequired[str]
    """Why it would not, when `wakes` is false"""


@answer
class MentionCandidates(TypedDict):
    candidates: list[MentionCandidate]
    """Best match first: the task's people, then a name or handle starting with `q`, a word in one starting with it,
    one containing it, then by name"""
