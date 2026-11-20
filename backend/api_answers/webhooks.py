# pyright: strict
"""The answers of the Webhooks routes (backend/webhooks.py)."""

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from pydantic import Field

from ._base import answer


@answer
class WebhookLast7Days(TypedDict):
    delivered: int
    failed: int
    pending: int


@answer
class Webhook(TypedDict):
    id: str
    name: str
    url: str
    host: str
    events: list[str]
    enabled: bool
    paused_reason: str
    rate_per_hour: int
    allow_http: bool
    created_by: str
    requested_by: str | None
    created_at: str
    updated_at: str
    revision: int
    old_secret_until: str | None
    can_change: bool
    last_7_days: WebhookLast7Days
    filter: dict[str, Any]
    """departments, bots, pipelines; an empty list means all"""
    headers: list[
        Annotated[dict[str, Any], Field(description="name, secret, and value unless secret (then set: true)")]
    ]
    disabled_at: str | None
    disabled_reason: str | None


@answer
class WebhookSendRow(TypedDict):
    id: str
    webhook_id: str
    event_id: str
    type: str
    subject: str
    state: Literal["ready", "sending", "delivered", "failed", "canceled", "withheld"]
    attempts: int
    last_status: int | None
    last_error: str
    next_attempt_at: str | None
    created_at: str
    delivered_at: str | None


@answer
class WebhookList(TypedDict):
    items: list[Webhook]
    more: bool
    next_offset: NotRequired[int]


@answer
class WebhookCreated(TypedDict):
    webhook: NotRequired[Webhook]
    """With `secret` once; `replayed: true` on a retry, without it"""
    approval: NotRequired[dict[str, Any]]
    """A bot's request: {id, status: pending}"""


@answer
class WebhookShown(TypedDict):
    webhook: Webhook
    recent_attempts: list[dict[str, Any]]


@answer
class WebhookChanged(TypedDict):
    webhook: Webhook


@answer
class WebhookEnabled(TypedDict):
    webhook: Webhook
    recover: dict[str, Any] | None
    """{since, failed} after an automatic turn-off"""


@answer
class WebhookRecovered(TypedDict):
    webhook: str
    since: str
    recovered: int
    more: bool


@answer
class WebhookReplayed(TypedDict):
    webhook: str
    since: str
    checked: int
    queued: int
    more: bool


@answer
class WebhookQueued(TypedDict):
    queued: int
    type: str


@answer
class WebhookSends(TypedDict):
    items: list[WebhookSendRow]
    more: bool
    next_offset: NotRequired[int]
