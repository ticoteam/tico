# pyright: strict
"""The answers of the Roadmaps routes (backend/roadmaps.py): the signed-in Roadmap and a task's requesters."""

from typing import Literal, NotRequired, TypedDict

from ._base import answer


class _Tallies(TypedDict):
    """A task's requester tallies (roadmaps.STATS_COLUMNS): the same on a task's requester list and on a ranked row."""

    requester_count: int
    external_requesters: int
    """Requesters outside the team (no actor)"""
    internal_requesters: int
    """Teammates, a person or a bot"""
    important_requesters: int
    organization: str | None
    """The first organization a requester gave, alphabetically; null when none gave one"""


@answer
class RoadmapTask(_Tallies):
    """One row of the Roadmap: a Product pipeline task with its requester tallies."""

    id: str
    number: int | None
    title: str
    status: Literal["open", "done", "closed"]
    visibility: Literal["public", "private"]
    public_title: str
    """The task's public title; empty until written"""
    size: float | None
    version: int
    pipeline_id: str
    stage_id: str | None
    stage_name: str | None


@answer
class RoadmapTaskList(TypedDict):
    tasks: list[RoadmapTask]
    next_offset: int | None
    """Pass back as offset; null on the last page"""


@answer
class TaskRequester(TypedDict):
    id: str
    kind: Literal["teammate", "external"]
    actor: str | None
    """A teammate's actor id; null for an outsider"""
    name: str
    email: str
    phone: str
    other: str
    organization: str
    note: str
    source: Literal["manual", "public_form", "inbox", "meeting"]
    important: Literal[0, 1]
    """1 for an important requester"""
    created_at: str


@answer
class TaskRequesterList(_Tallies):
    requesters: list[TaskRequester]
    next_offset: int | None
    """Pass back as offset; null on the last page"""
    my_requester_id: str | None
    """The caller's own request on this task, on any page; null when they made none"""


@answer
class TaskRequesterAdded(TypedDict):
    """A person's write answers so; Agent Ops acting for a person answers its proposal card instead, without these."""

    id: NotRequired[str]
    requester_token: NotRequired[str]
    """Removes this request; shown once"""


@answer
class TaskRequesterUpdated(TypedDict):
    updated: NotRequired[bool]


@answer
class TaskRequesterRemoved(TypedDict):
    removed: NotRequired[bool]


@answer
class TaskCommentModerated(TypedDict):
    moderated: bool
