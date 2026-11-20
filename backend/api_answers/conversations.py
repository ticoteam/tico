# pyright: strict
"""The answers of the Conversations routes, and the message every chat, task and thread shows: who wrote it, a review
ask it carries and the answers to it, and how a send to a bot went."""

from __future__ import annotations

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from typing_extensions import TypeAliasType

from ..sources import SourceKind
from ._base import ONE_OF, answer


# Who wrote a line (backend/sources.py): a message's, a search hit's and a task history entry's `source`.
@answer
class MessageSource(TypedDict):
    kind: SourceKind
    name: str
    link: str | None


# A review ask a message carries (`ask`): the questions, whom they are for and who asked.
@answer
class TaskReviewAskView(TypedDict):
    questions: list[Any]
    who: str | None
    by: str


@answer
class ReviewAskAnswer(TypedDict):
    target: dict[str, Any]
    answers: dict[str, Any]
    other: str | None
    dismiss: bool
    by: str
    at: str


@answer
class ReviewTextAnswer(TypedDict):
    by: str
    text: str
    at: str


# A person's answer to a review ask: the ask's form filled in, or plain text. A message carries them (`answer`,
# `answers`) and so does a task file's version (backend/api_answers/tasks.py).
ReviewAnswer = TypeAliasType("ReviewAnswer", Annotated[ReviewAskAnswer | ReviewTextAnswer, ONE_OF])


# The way a send went (backend/delivery.py): steer, queue or defer, and a defer's deadline (null: none).
@answer
class SentAs(TypedDict):
    delivery: Literal["steer", "queue", "defer"]
    until: str | None


@answer
class MessageRun(TypedDict):
    job_id: NotRequired[str]
    run_id: NotRequired[str]
    state: NotRequired[Literal["started_run", "added_to_run"]]


@answer
class Message(TypedDict):
    id: str
    conversation_id: str
    sender_id: str
    recipient_id: str | None
    kind: str
    body: str
    created_at: str
    in_reply_to: str | None
    refs: dict[str, Any]
    sender_name: NotRequired[str]
    """Display name of sender_id, when it is a person or a bot"""
    recipient_name: NotRequired[str]
    """Display name of recipient_id"""
    sender_can_approve_send: NotRequired[bool]
    """On GET /messages/{mid}: whether the human sender currently may manage the bot addressee's mail sending (Auth.bot_manager); false for bot senders or non-bot addressees. This checks authority only, not send intent or the draft's contents"""
    body_raw: NotRequired[str]
    """A notice Tico wrote, as stored (with actor ids); `body` shows names to people"""
    edited_at: NotRequired[str | None]
    """When the author last changed a task comment's text; null when never edited"""
    deleted_at: NotRequired[str | None]
    """When the author deleted a task comment. A delete or retry acknowledgment can include a tombstone with an empty body; deleted comments are never listed"""
    run: NotRequired[MessageRun]
    """The run that handled this message, once one has: on a person's message `{job_id, run_id, state}` where `state` is `started_run` (it started the run) or `added_to_run` (it was folded into a run already working); on a bot's reply `{job_id, run_id}` (the run that wrote it, plus a summary of what it did). Absent until a run takes the message"""
    ask: NotRequired[TaskReviewAskView]
    answer: NotRequired[ReviewAnswer]
    answers: NotRequired[list[Annotated[str | ReviewAnswer, ONE_OF]]]
    """On a structured ask: all review answers, oldest first. On a bot reply: handled message ids"""
    woke: NotRequired[bool]
    """On a send: false when the message was held (see `held`)"""
    held: NotRequired[str]
    """On a send: a bot's message to a bot held for that bot's next run, and how to wake it now (backend/bot_to_bot.py)"""
    sent_as: NotRequired[SentAs]
    """On a send to a bot: the way it went (steer, queue or defer) and a defer's deadline (backend/delivery.py)"""
    source: NotRequired[MessageSource]


@answer
class ChatTaskActivity(TypedDict):
    id: str
    seq: int
    created_at: str
    task_id: str
    title: str
    excerpt: str


@answer
class ChatGoal(TypedDict):
    id: str
    conversation_id: str
    bot: str
    objective: str
    status: str
    note: str
    set_by: str
    set_at: str
    updated_at: str
    ended_at: str | None


@answer
class ChatGoalResult(TypedDict):
    goal: ChatGoal | None
    supported: NotRequired[bool]
    commands: NotRequired[list[dict[str, Any]]]


@answer
class CatchUpResult(TypedDict):
    thread_id: str
    scope: str
    messages: int
    left_out: int
    posted: NotRequired[dict[str, Any]]
    words: NotRequired[str]


@answer
class ExecutionPart(TypedDict):
    kind: Literal["progress", "reply", "tool"]
    text: str
    at: str


@answer
class Execution(TypedDict):
    job_id: str
    message_id: str
    bot_id: str
    run_id: str | None
    state: str
    label: str
    text: str
    parts: list[ExecutionPart]
    """The run's pieces so far, in order, while it is leased or running (empty otherwise). `reply` and `progress` are what the bot wrote (`progress` when a tool call follows it); `tool` is a short label such as "Filing a task" or "Ran npm", never its arguments or output"""


@answer
class Conversation(TypedDict):
    id: str
    kind: str
    subject: str
    participants: list[str]
    created_at: str
    last_message_at: str
    closed_at: str | None


@answer
class ConversationList(TypedDict):
    actor: str
    conversations: list[Conversation]
    actors: NotRequired[dict[str, str]]
    """On reads: display names for every actor id in the answer, {"human:ana": "Ana Alvarez"}"""


@answer
class MessagePage(TypedDict):
    conversation: Conversation
    messages: list[Message]
    task_activity: NotRequired[list[ChatTaskActivity]]
    has_more: bool
    next_before: str | None
    actors: NotRequired[dict[str, str]]
    """On reads: display names for every actor id in the answer, {"human:ana": "Ana Alvarez"}"""


@answer
class Snapshot(TypedDict):
    messages: list[Message]
    task_activity: NotRequired[list[ChatTaskActivity]]
    has_more: bool
    next_before: str | None
    execution: Execution | None
    """The latest run: state, label, text (the reply so far; separate messages are joined by a blank line), parts, bot"""


@answer
class MessageResult(TypedDict):
    message: Message


@answer
class ChatResult(TypedDict):
    conversation: Conversation
    message: Message
