# pyright: strict
"""The answers of the Risk routes (backend/risk.py)."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from ._base import answer


@answer
class RiskClaim(TypedDict):
    currency: str
    amount: int


@answer
class Risk(TypedDict):
    id: str
    key: str
    label: str
    kind: Literal["risk"]
    owner: str | None
    version: int
    bot_writers: NotRequired[list[str]]
    markdown: NotRequired[str]
    """Omitted in the list"""
    level: Literal["live", "open", "quiet"]
    waits: bool
    """An open matter waits on the caller (their Needs you)"""
    has_rules: bool
    """An active rule is linked"""
    open: int
    closed: int
    claimed: list[RiskClaim]
    next_date: str | None
    """The soonest obligation still due (YYYY-MM-DD), from the dates the reader sees"""
    unconfirmed: NotRequired[int]
    """Occurrences of its dates that are unconfirmed"""
    created_at: NotRequired[str]
    updated_at: NotRequired[str]


@answer
class MatterRecord(TypedDict):
    id: str
    name: str


@answer
class MatterDate(TypedDict):
    id: str
    label: str
    """What it is for, such as Hearing"""
    starts_at: str | None
    """Not before: the earliest its step may be done, never a deadline. A day (YYYY-MM-DD) or a UTC time (YYYY-MM-DDTHH:MM:SSZ)"""
    due_at: str | None
    """Due by: the deadline, a day or a UTC time"""
    done_at: str | None
    """When it was marked done; a done date leaves the calendar"""
    primary: bool
    """The matter's first date: its starts_at, due_at and dates_label show this one"""
    created_by_id: str
    created_at: str
    updated_by_id: str
    updated_at: str


@answer
class Matter(TypedDict):
    task_id: str
    kind: Literal["dispute", "request", "notice", "incident"]
    party_entity_id: str | None
    party: MatterRecord | None
    """A Directory record, null when the reader may not see it"""
    amount: int | None
    currency: str | None
    claim: str
    side: Literal["theirs", "ours"]
    forum: str
    starts_at: str | None
    """Its first key date's not before (see dates)"""
    due_at: str | None
    """Its first key date's due by"""
    dates_label: str
    """What the dates are for, such as Produce records"""
    dates: list[MatterDate]
    """Its key dates, by date (due by, else not before)"""
    place_id: str | None
    place: MatterRecord | None
    """A Directory record, null when the reader may not see it"""
    product_id: str | None
    product: MatterRecord | None
    """A Directory record, null when the reader may not see it"""
    version: int
    created_by_id: str
    created_at: str
    updated_by_id: str
    updated_at: str


@answer
class MatterTask(TypedDict):
    id: str
    number: NotRequired[int | None]
    label: NotRequired[str | None]
    title: NotRequired[str | None]
    status: str
    due_at: NotRequired[str | None]
    created_at: NotRequired[str | None]
    owner_id: NotRequired[str | None]
    pipeline_id: NotRequired[str | None]
    stage_id: NotRequired[str | None]
    stage: NotRequired[str | None]


@answer
class RiskBrief(TypedDict):
    id: str
    key: str
    label: str


@answer
class MatterRow(TypedDict):
    task: MatterTask
    matter: Matter | None
    risks: list[RiskBrief]


@answer
class RiskLinkRow(TypedDict):
    link_id: str


@answer
class ObligationRule(TypedDict):
    id: str
    kind: str
    text: str
    source: dict[str, Any] | None


@answer
class ObligationTask(TypedDict):
    id: str
    label: str | None
    title: str | None
    status: str


@answer
class ObligationProof(TypedDict):
    kind: str
    ref: str | None
    name: str | None


@answer
class ObligationMark(TypedDict):
    satisfied_by: str
    via: str
    satisfied_at: str
    task_id: str | None
    outcome: Literal["met", "waived", "missed"]
    proof: ObligationProof | None
    """ref and name are null for a file or email the reader cannot open"""
    note: str
    confirmed_by_id: str | None
    confirmed_at: str | None


@answer
class RiskObligation(TypedDict):
    id: str
    """The due date's id (POST /api/v2/rule-due/{id}/satisfied)"""
    due_on: str
    """This occurrence's day, YYYY-MM-DD"""
    title: str
    basis: Literal["", "law", "order", "agency", "contract", "ours", "asked", "theirs"]
    state: Literal["due", "met", "waived", "missed", "passed", "unconfirmed"] | None
    """null: theirs to meet and past; we only watch it"""
    group: Literal["unconfirmed", "next", "later", "past"]
    repeats: NotRequired[str]
    first_due_on: NotRequired[str]
    timezone: NotRequired[str]
    owner: NotRequired[str]
    routine_id: NotRequired[str | None]
    rule: ObligationRule
    task: NotRequired[ObligationTask | None]
    """The matter the date belongs to"""
    risks: list[RiskBrief]
    mark: ObligationMark | None
    can_edit: NotRequired[bool]
    can_satisfy: NotRequired[bool]
    can_waive: NotRequired[bool]


@answer
class RiskObligationList(TypedDict):
    obligations: list[RiskObligation]
    next_offset: int | None


@answer
class RiskList(TypedDict):
    risks: list[Risk]


@answer
class RiskResult(TypedDict):
    risk: Risk


@answer
class RiskDetail(TypedDict):
    risk: Risk
    editable: bool
    matters: list[MatterRow]
    next_offset: int | None
    rules: list[RiskLinkRow]
    sources: list[RiskLinkRow]
    coverage: list[RiskLinkRow]
    obligations: list[RiskObligation]


@answer
class MatterList(TypedDict):
    matters: list[MatterRow]
    next_offset: int | None


@answer
class MatterResult(TypedDict):
    task_id: str
    matter: Matter | None


@answer
class MatterDateResult(TypedDict):
    task_id: str
    date_id: str
    """The date added, changed or removed"""
    matter: Matter | None


@answer
class MatterDateList(TypedDict):
    task_id: str
    dates: list[MatterDate]


@answer
class RiskLinkResult(TypedDict):
    link: dict[str, Any] | None
    removed: NotRequired[str]
    """The unlinked link's id"""
