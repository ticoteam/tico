# pyright: strict
"""The answers of the Outbound actions routes (backend/outbound_actions.py)."""

from typing import Any, Literal, NotRequired, TypedDict

from ._base import answer

State = Literal["waiting_approval", "queued", "attempting", "done", "failed", "unknown", "blocked", "canceled"]
Choice = Literal["on_their_own", "ask_first", "never"]


@answer
class OutboundAction(TypedDict):
    id: str
    connection_id: str
    channel: str
    audience: Literal["internal", "external"]
    operation: str
    destination: str
    occurrence_key: str
    actor: str
    state: State
    attempts: int
    send_after: str
    next_attempt_at: str
    provider_attempted_at: str | None
    """Set in the one write before the provider is called; cleared again only when the provider safely refused"""
    approval_id: str
    approval_task_id: str | None
    task_id: str | None
    last_error: str
    created_at: str
    updated_at: str
    policy_decision: dict[str, Any]
    """{choice, why, floor} as the last check read it"""
    metadata: NotRequired[dict[str, Any]]
    """The caller's own record, kept after the payload is wiped (detail only)"""
    receipt: NotRequired[dict[str, Any]]
    """The provider's receipt (detail only)"""
    payload: NotRequired[dict[str, Any] | None]
    """The frozen content (detail only); null once the action ended"""


@answer
class OutboundActionResult(TypedDict):
    action: OutboundAction
    created: bool
    """False when the occurrence_key already had this action"""


@answer
class OutboundActionDetail(TypedDict):
    action: OutboundAction
    shown_hash: str | None
    """What an OK sends back (POST /api/v2/outbound-actions/{id}/approve); null unless it waits for one"""


@answer
class OutboundActionList(TypedDict):
    items: list[OutboundAction]
    next_cursor: str | None


@answer
class OutboundPolicy(TypedDict):
    settings: dict[str, Choice]
    """What the company set, by "<channel>:<audience>\""""
    effective: dict[str, Choice]
    """What applies, by "<channel>:<audience>\" (the setting, else on_their_own inside and ask_first outside)"""
    choices: list[Choice]
    channels: list[str]
    """The channels Tico has a sender for (none yet: Jira is first)"""
