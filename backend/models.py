"""The HTTP contract. Unknown fields fail validation rather than changing identity."""

from typing import Annotated, Any, Literal

import re

from pydantic import AfterValidator, BeforeValidator, BaseModel, ConfigDict, Field, SecretStr, StrictBool, StrictInt, StrictStr, ValidationError, WrapValidator, model_serializer, model_validator

Text = Annotated[str, Field(min_length=1, max_length=200_000)]
ID = Annotated[str, Field(min_length=1, max_length=200)]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)]


def _weekly_runtime(value):
    from clients.harness_manifests import caps
    if not caps(value).get("weekly_usage"):
        raise ValueError("this runtime reports no weekly usage")
    return value


# A runtime whose subscription has a weekly allowance (its manifest's `weekly_usage`).
WeeklyRuntime = Annotated[str, Field(max_length=40), AfterValidator(_weekly_runtime)]
# A person chooses how work reaches the company from a fixed list. Both spellings are
# accepted so the wizard may send either the answer or the catalog tag it maps to.
WorkArrival = Literal["email", "slack", "crm", "tickets",
                      "uses_email", "uses_slack", "uses_crm", "uses_tickets"]


def repo_reference(value):
    """A bot repository as the operator wrote it: a bare name, `owner/name`, or an https URL.
    The environment's GitHub owner completes a bare name when one is configured."""
    repo = str(value or "").strip()
    if any(character.isspace() for character in repo):
        raise ValueError("A repository reference cannot contain spaces")
    if "://" in repo and not repo.startswith("https://"):
        raise ValueError("A repository URL must use https")
    return repo


Repo = Annotated[str, Field(max_length=200), AfterValidator(repo_reference)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TurnLevels(Contract):
    warning_per_person: Annotated[int, Field(strict=True, ge=1, le=10000)] = 40
    stop_per_person: Annotated[int, Field(strict=True, ge=1, le=10000)] = 100

    @model_validator(mode='after')
    def ordered(self):
        if self.warning_per_person > self.stop_per_person:
            raise ValueError('The warning level must not exceed the stop level')
        return self


class ConversationCreate(Contract):
    participants: list[ID] = Field(min_length=1, max_length=20)
    kind: Literal["chat", "ask", "notice"] = "chat"
    subject: str = Field(default="", max_length=300)


class VisualRef(Contract):
    """A file version attached to a message with the title it shows under (backend/visuals.py)."""
    ref: Annotated[StrictStr, Field(pattern=r"^[^@]+@[1-9][0-9]*$")]
    title: Annotated[StrictStr, Field(max_length=200)] = ""


Attachment = Annotated[StrictStr, Field(pattern=r"^[^@]+@[1-9][0-9]*$")] | VisualRef
# How a send reaches its bot (backend/delivery.py): steer into its running turn, queue right after it, or defer to its
# next turn there, waking it at the latest after `defer_for` (`<n>m`, `<n>h`, `<n>d` or `never`).
Delivery = Literal["steer", "queue", "defer"]
DeferFor = Annotated[str, Field(pattern=r"^(never|[1-9][0-9]{0,5}[mhd])$")]


class MessageCreate(Contract):
    command: bool = False
    to: ID
    text: Text
    conversation_id: ID | None = None
    kind: Literal["say", "ask", "notice", "steer"] = "say"
    refs: dict = Field(default_factory=dict)
    in_reply_to: ID | None = None
    wait_s: int | None = Field(default=None, ge=0, le=300)
    # Versions of files in this conversation, shown with the message (backend/visuals.py), in this order.
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    # A bot's message to a bot waits for that bot's next run unless it needs action now (backend/bot_to_bot.py).
    needs_action: bool = False
    # Omitted: a person's message to a bot is queued; a bot's follows bot_to_bot as before.
    delivery: Delivery | None = None
    defer_for: DeferFor | None = None


class ChatCreate(Contract):
    command: bool = False
    text: Text
    delivery: Delivery | None = None
    defer_for: DeferFor | None = None
    refs: dict = Field(default_factory=dict)
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    # With files uploaded alongside (POST /api/v2/uploads/chat/{bot}): each one's caption, in upload order.
    titles: list[Annotated[StrictStr, Field(max_length=200)]] = Field(default_factory=list, max_length=10)


class BatchStart(Contract):
    """Which part of the list: omitted for all of it, `next` for the bot that most needs the
    person, a bot slug or person for theirs, `me` for the person's own (backend/batch.py)."""
    bot: str | None = Field(default=None, max_length=80)


class BatchRespond(Contract):
    kind: Literal["decide", "needs_info", "instruct", "rule", "skip", "later"]
    text: str = Field(default="", max_length=4000)
    item: int | None = Field(default=None, ge=1)          # 1-based position; the current item when omitted
    decision: Literal["approve", "decline", "done", "close", "answer"] | None = None
    heard: str = Field(default="", max_length=2000)       # what the person actually said, when spoken
    until: str | None = Field(default=None, max_length=40)


class ChangelogPost(Contract):
    title: str = Field(min_length=1, max_length=90)
    bullets: list[str] = Field(min_length=1, max_length=12)


class ChangelogRead(Contract):
    ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(max_length=250)


class TaskChat(Contract):
    text: Text
    expected_recipient: ID | None = None


class PageChat(Contract):
    page: Literal["tasks"]
    text: str = Field(default="", max_length=200_000)
    task_id: str | None = None


class Answer(Contract):
    text: Text
    unknown: bool = False


class TakeRecommendation(Contract):
    ask: Annotated[StrictStr, Field(min_length=1, max_length=100)]     # the bot's ask the person says yes to
    version: Annotated[int, Field(strict=True, ge=1)] | None = None     # the card's task version: a changed task is refused


class StaleDecision(Contract):
    """A person's answer to one row of their Stale tickets card (backend/stale_review.py): accept or skip. Accept names
    the task version the row showed (409 version_conflict once the task changed), as a change by hand does."""
    decision: Literal["accept", "skip"] = "accept"
    version: int | None = None


class StaleShown(Contract):
    id: ID
    version: int


class StaleAcceptAll(Contract):
    """Accept all on a Stale tickets card: the rows the card showed, each with its task's version then. A row added
    since is not in the list and stays; a row whose task changed is refused on its own."""
    rows: list[StaleShown] = Field(min_length=1, max_length=500)


class ClarifyAsk(Contract):
    ask: Annotated[StrictStr, Field(min_length=1, max_length=100)]     # the stale bot ask to send the clarify prompt to


Lane = Literal["company", "product"]
# A task's number: one sequence for the whole team, like one board's ticket numbers.
TaskNumber = Annotated[int, Field(ge=1, le=999_999_999)]


RelationKind = Literal["parent", "blocks", "blocked_by", "related", "duplicate_of", "follow_up"]


class TaskRelationRef(Contract):
    """The new task `kind` the other: `parent` files it under `task`, `blocked_by` makes it wait on it."""
    task: ID
    kind: RelationKind = "related"


class TaskBlocker(Contract):
    """What refused: an ask about the same repo, status and endpoint as an open one joins it (backend/blocker_asks.py)."""
    repo: str = Field(min_length=3, max_length=200)
    status: StrictInt = Field(ge=100, le=599)
    endpoint: str = Field(min_length=1, max_length=200)


class TaskCreate(Contract):
    visibility: Literal['public', 'private'] | None = None
    # Publishing at creation needs explicit public text; the internal title and body are never copied.
    public_title: str | None = Field(default=None, max_length=300)
    public_summary: str | None = Field(default=None, max_length=8000)
    private: StrictBool | None = None
    title: str = Field(min_length=1, max_length=300)
    body: Text
    owner: ID
    due: str | None = None
    relations: list[TaskRelationRef] = Field(default_factory=list, max_length=20)
    goal_id: ID | None = None
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)
    lane: Lane | None = None
    labels: list[str] = Field(default_factory=list, max_length=20)
    change_paths: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(default_factory=list, max_length=3000)
    qa: StrictBool | None = None
    qa_not_checkable_live: StrictBool = False
    blocker: TaskBlocker | None = None
    top: bool = False
    links: list[str] = Field(default_factory=list, max_length=20)
    # Wait for the owner's next run instead of starting one (a bot owner only): the same as delivery defer.
    next_run: bool = False
    # A new task is queued or deferred, never steered (422); defer is a deferred task, due within `defer_for` (24h).
    delivery: Delivery | None = None
    defer_for: DeferFor | None = None
    request_id: ID | None = None
    type: ID | None = None
    step: str | None = Field(default=None, max_length=200)
    # An imported ticket's own number (a mover's); a numbered type gives the next one otherwise.
    number: TaskNumber | None = None
    # Who else is on the task, by role: {role: [actors]} (backend/task_roles.py).
    roles: dict[str, list[str]] | None = None
    # A bot's own subtask run on another model, `<runtime>[:<model>]`, as a review or a check (backend/check_models.py).
    check_model: str | None = Field(default=None, max_length=120)
    check_role: Literal["review", "check"] | None = None
    # The model a bot-owned task's runs use, with its effort and harness (backend/task_run_models.py). No model is the
    # bot's task model, then its own; an effort or harness needs a model.
    model: ID | None = None
    effort: ID | None = None
    harness: ID | None = None
    # A company-visible task a private task's owning bot files from that work, in its own words (task_privacy.from_private).
    from_private: StrictBool = False


class TaskRunModel(Contract):
    """A task's own model (backend/task_run_models.py). An absent `model` keeps the task's and changes only the
    effort or harness named; a null `model` clears it, so the task's runs use the bot's task model again."""
    model: ID | None = Field(None, description="The model the task's runs use; null clears the task's choice")
    effort: ID | None = None
    harness: ID | None = None


class NoteCreate(Contract):
    """A deferred message: something for a bot's next run to know, asking nothing (backend/hubdb.py). It wakes the bot
    after `defer_for` if no run carried it by then; never when omitted."""
    to: ID
    text: Text
    defer_for: DeferFor | None = None


class TaskUpdate(Contract):
    visibility: Literal['public', 'private'] | None = None
    public_title: str | None = Field(default=None, min_length=1, max_length=300)
    public_summary: str | None = Field(default=None, max_length=8000)
    approval_id: ID | None = None
    size: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    private: StrictBool | None = None
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    status: Literal["open", "doing", "waiting", "review", "ready", "done", "declined", "closed"] | None = None
    note: str | None = Field(default=None, max_length=200_000)
    quiet: bool = False
    owner: ID | None = None
    due: str | None = None
    body: Text | None = None
    goal_id: str | None = Field(default=None, max_length=200)   # "" takes the goal off
    close: bool = False
    lane: Lane | None = None
    labels: list[str] | None = Field(default=None, max_length=20)
    waiting_on: str | None = Field(default=None, max_length=200)    # the person it waits on, or a bot owning its blocker; "" clears
    rank: float | None = None
    type: ID | None = None
    step: str | None = Field(default=None, max_length=200)   # "" clears the step
    step_rank: float | None = Field(default=None, allow_inf_nan=False)   # its place within its step
    number: TaskNumber | None = None      # a mover's, for a task that has none
    # A review or QA subtask's verdict and the pull request head it judged; its parent's owner is woken with both.
    verdict: Literal["approve", "changes", "pass", "fail"] | None = None
    verdict_head: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{7,40}$")
    # Replace the people in the roles named: {role: [actors]}; [] clears one.
    roles: dict[str, list[str]] | None = None
    # BotOps applying a person's own request to a task they own or requested (backend/app.py
    # delegated_identity): checked as that person, never as BotOps.
    on_behalf_of: ID | None = None

    @model_validator(mode="after")
    def closed_status(self):
        # Use the same permissions, version check and close/edit separation as close:true.
        if self.status == "closed":
            self.status = None
            self.close = True
        return self


class TaskFilter(Contract):
    """The tasks a bulk change applies to: the filters `GET /api/v2/tasks` takes, with the same meanings."""
    owner: ID | None = None
    requester: ID | None = None
    status: str | None = Field(default=None, max_length=200)     # comma-separated, as the list takes it
    lane: Lane | None = None
    label: str | None = Field(default=None, max_length=200)      # a tag's key
    type: ID | None = None
    step: str | None = Field(default=None, min_length=1, max_length=200)
    number: TaskNumber | None = None
    updated_since: str | None = Field(default=None, max_length=100)
    member: ID | None = None
    role: str | None = Field(default=None, max_length=100)
    snoozed: Literal["hide", "only", "all"] | None = None
    ids: list[ID] | None = Field(default=None, min_length=1, max_length=100)


class TaskBulk(Contract):
    """One change to every task `filter` selects (`POST /api/v2/task-bulk`): `action` names it and its one field
    carries the value: `step` (id or name), `status`, `owner`, `add` or `remove` (tags, for `tags`), or none for
    `close`. `note` goes with each task's change, as on a single update."""
    filter: TaskFilter
    action: Literal["step", "status", "owner", "tags", "close"]
    step: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["open", "doing", "waiting", "review", "ready", "done", "declined"] | None = None
    owner: ID | None = None
    add: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=20)
    remove: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=20_000)
    dry_run: bool = False

    @model_validator(mode="after")
    def one_change(self):
        given = {name for name in ("step", "status", "owner") if getattr(self, name) is not None}
        if self.add or self.remove:
            given.add("tags")
        if given != ({self.action} if self.action != "close" else set()):
            takes = {"step": "step", "status": "status", "owner": "owner", "tags": "add or remove", "close": "no value"}
            raise ValueError(f"action {self.action} takes {takes[self.action]} and nothing else")
        return self


class TaskStepInput(Contract):
    public_name: str | None = Field(default=None, max_length=200)      # "" keeps the step off public pages
    id: ID | None = None
    name: ID
    position: int | None = None
    status: Literal["open", "doing", "waiting", "review", "ready", "done", "closed", "declined"]
    # The bots that may do this step's check, and how many open checks each may hold (backend/check_pools.py).
    # Left out, a step keeps what it had; an empty list makes it no check step.
    check_pool: list[ID] | None = Field(default=None, max_length=40)
    check_limit: int | None = Field(default=None, ge=1, le=20)
    # What the step does with a ticket (backend/step_flow.py): where a check's pass or other outcome sends it, the
    # merged step, a skip tag, a check timeout. Left out, a step keeps what it had; {} clears it.
    flow: dict | None = Field(default=None, description="The step's flow (docs/tasks.md, Step flow). On a check step, "
                              "other_company is prefer (the default) or require.")


# What every bot may do with a type's tasks beyond its own (hubdb.TYPE_BOTS): parties keeps them to
# the bots on each task; read opens all of them to read and comment on; work also to change.
TypeBots = Literal["parties", "read", "work"]


class TaskTypeCreate(Contract):
    default_visibility: Literal['public', 'private'] = 'private'
    name: ID
    steps: list[TaskStepInput] = Field(default_factory=list)
    bots: TypeBots | None = None
    numbered: bool = False
    steps_only: bool = False


# Refusing a `manager` field says where the choice it was after lives.
MANAGER_FIXED = ("A task type's manager is fixed by its role and can't be changed. To choose which bot receives public "
                 "roadmap suggestions, use Settings → Tasks → Public roadmap → Suggestions go to (or `hub roadmap settings`).")


class TaskTypeUpdate(Contract):
    default_visibility: Literal['public', 'private'] | None = None
    name: ID | None = None
    steps: list[TaskStepInput] | None = None
    bots: TypeBots | None = None
    numbered: bool | None = None
    steps_only: bool | None = None


# What a pick says on a person's card (ui/app/question-card.js): `then` is its "what happens" line, under its button;
# `recommended` pre-ticks it among several picks. A bot's pick to a person needs `then` (hubdb.lint_question).
OptionThen = Annotated[StrictStr, Field(max_length=120)]


class QuestionOption(Contract):
    label: Annotated[StrictStr, Field(min_length=1, max_length=60)]
    description: Annotated[StrictStr, Field(max_length=200)] = ""
    file: Annotated[StrictStr, Field(pattern=r"^[^@]+@[1-9][0-9]*$")] | None = None
    then: OptionThen = ""
    recommended: StrictBool = False


class QuestionFile(Contract):
    """A picture or file shown with one option, sent with the question (backend/media.py task_question)."""
    name: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    content_base64: Annotated[StrictStr, Field(min_length=1, max_length=14_000_000)]


class IfNoAnswer(Contract):
    """What the bot does if the person does not answer by `by` (backend/ask_deadlines.py). `kind` says what that step is:
    only a `reversible` internal step goes ahead on its own; a send, a payment or a delete waits for an answer."""
    do: Annotated[StrictStr, Field(min_length=1, max_length=300)]
    kind: Literal["reversible", "send", "spend", "delete"]
    by: Annotated[StrictStr, Field(max_length=40)] | None = None


class QuestionChoice(Contract):
    label: Annotated[StrictStr, Field(min_length=1, max_length=60)]
    description: Annotated[StrictStr, Field(max_length=200)] = ""
    file: QuestionFile | None = None
    then: OptionThen = ""
    recommended: StrictBool = False


class ReviewFile(Contract):
    """One file version a question puts under review (backend/file_reviews.py); no version means the current one."""
    file: ID
    version: Annotated[int, Field(strict=True, ge=1)] | None = None


class FileReview(Contract):
    """A verdict given directly on one file version (`hub file review`, backend/file_reviews.py): by file id, or by a
    path and the SHA-256 of its bytes."""
    file: ID | None = None
    version: Annotated[int, Field(strict=True, ge=1)] | None = None
    path: Annotated[str, Field(max_length=1000)] | None = None
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    task: ID
    verdict: Literal["approve", "changes"]
    note: Annotated[str, Field(max_length=4000)] = ""


class QuestionSpend(Contract):
    """Picking `label` also approves this spend for the asking bot, decided by the person who picks it."""
    label: Annotated[StrictStr, Field(min_length=1, max_length=60)]
    amount: Annotated[StrictStr, Field(min_length=1, max_length=40)]
    what: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    account: Annotated[StrictStr, Field(min_length=1, max_length=80)] = "gateway"


class TaskQuestion(Contract):
    """One decision put to a person as its own task: created, asked and set waiting on them in one write."""
    question: Annotated[StrictStr, Field(min_length=1, max_length=300)]
    on: ID
    # The card's one "why it matters" line, under the question.
    why: Annotated[StrictStr, Field(max_length=200)] = ""
    options: list[QuestionChoice] = Field(default_factory=list, max_length=6)
    # Several picks: each a checkbox, answered with "Yes to all" or "Send ticked".
    multi: StrictBool = False
    title: Annotated[StrictStr, Field(min_length=1, max_length=300)] | None = None
    body: Text | None = None
    parent: ID | None = None
    private: StrictBool | None = None
    if_no_answer: IfNoAnswer | None = None
    # One open question per topic: a second question to the same person on this topic, while the first is open, is
    # folded into it (hubdb.open_question). Without one, an earlier question under the same parent task with no topic.
    topic: Annotated[StrictStr, Field(min_length=1, max_length=80)] | None = None
    # File versions under review: shown on the card, judged by the answer (backend/file_reviews.py). With no options
    # the options are Approve and Request changes; `pick` makes each file its own option, plus Request changes.
    review: list[ReviewFile] = Field(default_factory=list, max_length=10)
    pick: StrictBool = False
    spend: QuestionSpend | None = None

    @model_validator(mode="after")
    def unique_labels(self):
        labels = [o.label for o in self.options]
        if len(set(labels)) != len(labels):
            raise ValueError("Option labels must be unique")
        if self.options and not 2 <= len(self.options) <= 6:
            raise ValueError("A question has 2 to 6 options, or none for Yes / No")
        if self.pick and (self.options or self.multi or not 2 <= len(self.review) <= 5):
            raise ValueError("A pick reviews 2 to 5 files and makes its own options")
        if self.review and self.multi:
            raise ValueError("A review is one pick, not several")
        return self


class ReviewQuestion(Contract):
    id: Annotated[StrictStr, Field(min_length=1, max_length=40)]
    header: Annotated[StrictStr, Field(max_length=30)]
    question: Annotated[StrictStr, Field(min_length=1, max_length=300)]
    # The card's one "why it matters" line, under the question.
    why: Annotated[StrictStr, Field(max_length=200)] = ""
    options: list[QuestionOption] = Field(default_factory=list, max_length=6)
    multi: StrictBool = False
    other: StrictBool = True

    @model_validator(mode="after")
    def unique_labels(self):
        labels = [o.label for o in self.options]
        if len(set(labels)) != len(labels):
            raise ValueError("Option labels must be unique")
        return self


class ReviewAsk(Contract):
    questions: list[ReviewQuestion] = Field(min_length=1, max_length=4)
    who: Annotated[StrictStr, Field(min_length=1, max_length=200)] | None = None
    if_no_answer: IfNoAnswer | None = None

    @model_validator(mode="after")
    def unique_questions(self):
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("Question ids must be unique")
        return self


class ReviewTarget(Contract):
    comment: ID | None = None
    file: ID | None = None
    version: Annotated[int, Field(strict=True, ge=1)] | None = None

    @model_validator(mode="after")
    def one_target(self):
        if not ((self.comment is not None and self.file is None and self.version is None)
                or (self.comment is None and self.file is not None and self.version is not None)):
            raise ValueError("Target is {comment} or {file, version}")
        return self


class TaskAnswer(Contract):
    target: ReviewTarget
    answers: dict[StrictStr, list[StrictStr]] = Field(default_factory=dict)
    other: Annotated[StrictStr, Field(max_length=200_000)] | None = None
    dismiss: StrictBool = False


class FileVersionEdit(Contract):
    note: Annotated[StrictStr, Field(max_length=500)] | None = None
    ask: ReviewAsk | None = None


class TaskComment(Contract):
    public: bool = False
    # As MessageCreate.needs_action: a bot's comment to a bot wakes it now only when this is set.
    needs_action: bool = False
    approval_id: ID | None = None
    text: Text
    ask: ReviewAsk | None = None
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    # How the comment reaches the task's bot (docs/tasks.md): steer into its running turn, queue right after it, defer
    # to its next turn on the task (at the latest `defer_for`, 24h), or goal: the task session's goal becomes the text.
    # Omitted, the comment default decides (H.comment_way).
    delivery: Literal["steer", "queue", "defer", "goal"] | None = None
    defer_for: DeferFor | None = None


class TaskAskResolve(Contract):
    # The person's words from where they answered; without them the asker withdraws the question.
    quote: Annotated[StrictStr, Field(min_length=1, max_length=4000)] | None = None


class TaskLink(Contract):
    url: str | None = Field(default=None, max_length=2000)
    title: str | None = Field(default=None, max_length=200)
    remove: ID | None = None
    # Only a session link is asked for by kind: its value may be a resume command, not a URL. Tico sets
    # which session is primary; a client never does.
    kind: Literal["session"] | None = None
    # A pull request link: true wakes the owner once when its checks finish, it is reviewed or it ships; false disarms.
    wake_once: bool | None = None


class TaskWait(Contract):
    """What the task waits on: `task:<id>` (blocked_by), `pr:<url>` or `pr:<task>` (until that pull request merges),
    `release:<tag>` (until that release is published). `remove` takes it off. `note` goes in the owner's wake when a
    pr or release wait fires (not on task:, which is a blocked_by)."""
    on: Annotated[str, Field(min_length=3, max_length=400)]
    remove: bool = False
    note: str | None = Field(default=None, max_length=2000)


class TaskRelation(Contract):
    """`{id} kind {task}`: parent (task is its parent), blocks, blocked_by, related, duplicate_of,
    follow_up (it was split off or followed up from task)."""
    task: ID
    kind: RelationKind = "related"
    remove: bool = False


class Preference(Contract):
    value: dict | list | str | int | float | bool | None = None


# Getting started (backend/getting_started.py). The state is the person's own choices only;
# the checklist's ticks are computed, never sent.
class GettingStartedState(Contract):
    tour: bool | None = None
    checklist: bool | None = None
    skip: str | None = Field(default=None, max_length=32)


class GettingStartedMarket(Contract):
    text: str = Field(min_length=1, max_length=8000)


# Goals (backend/goals.py). `owner` is a bot slug, a person id,
# `me` or `company`; the colour is the owner's word with one sentence; a reading is a fact with an author.
class GoalCreate(Contract):
    title: str = Field(min_length=1, max_length=300)
    owner: ID
    parent_id: ID | None = None
    body: str = Field(default="", max_length=100_000)
    top: bool = False
    bet_id: ID | None = None               # the strategy bet this goal serves (backend/strategy.py)


class GoalStatus(Contract):
    status: Literal["red", "yellow", "green", "done", "dropped"]
    note: str = Field(default="", max_length=2000)


class GoalReopen(Contract):
    status: Literal["red", "yellow", "green", "auto"] | None = None
    note: str = Field(default="", max_length=300)
    undo: bool = False


class GoalUpdate(Contract):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, max_length=100_000)
    parent_id: str | None = Field(default=None, max_length=200)   # "" unlinks it
    owner: ID | None = None
    rank: int | None = None
    top: bool = False
    bet_id: str | None = Field(default=None, max_length=200)      # "" unlinks it from its strategy bet


class GoalRefresh(Contract):
    goal_ids: list[ID] | None = Field(default=None, max_length=500)


class GoalCheckin(Contract):
    body: str = Field(min_length=1, max_length=4000)
    signal: Literal["on_track", "at_risk", "off_track"] | None = None
    from_actor: str | None = Field(default=None, max_length=200)     # whose words these are, when recorded for them
    kpi_id: str | None = Field(default=None, max_length=200)


# KPIs (backend/kpis.py). A target lives on the link between a goal and a KPI: `kind` improve wants a
# target and a deadline (and may name a baseline), `kind` maintain wants a min, a max or both.
class KpiTarget(Contract):
    kind: Literal["none", "improve", "maintain"] = "none"
    baseline: float | None = None
    baseline_at: str | None = Field(default=None, max_length=40)
    target: float | None = None
    deadline: str | None = Field(default=None, max_length=40)
    min: float | None = None
    max: float | None = None


class KpiCreate(KpiTarget):
    name: str = Field(min_length=1, max_length=300)
    definition: str = Field(default="", max_length=2000)
    unit: str = Field(default="", max_length=40)
    direction: Literal["up", "down", "range"] = "up"
    cadence: Literal["daily", "weekly", "monthly"] = "weekly"
    source_note: str = Field(default="", max_length=2000)
    owner: str | None = Field(default=None, max_length=200)        # me, company, a bot slug or a person id
    goal_id: str | None = Field(default=None, max_length=200)      # link it to this goal, with the target above


class KpiUpdate(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    definition: str | None = Field(default=None, max_length=2000)
    unit: str | None = Field(default=None, max_length=40)
    direction: Literal["up", "down", "range"] | None = None
    cadence: Literal["daily", "weekly", "monthly"] | None = None
    source_note: str | None = Field(default=None, max_length=2000)
    owner: str | None = Field(default=None, max_length=200)
    # The KPI tree (backend/growth.py): sent as null, a field is cleared. At most one KPI is the North Star.
    parent_kpi_id: str | None = Field(default=None, max_length=200)
    growth_role: Literal["north_star", "input", "guardrail"] | None = None
    stage: Literal["reach", "acquire", "activate", "retain", "refer"] | None = None
    loop_name: str | None = Field(default=None, max_length=80)
    loop_step: int | None = Field(default=None, ge=1, le=50)


class GoalKpiLink(KpiTarget):
    kpi_id: str | None = Field(default=None, max_length=200)      # an existing KPI, or leave it out and name a new one
    name: str | None = Field(default=None, min_length=1, max_length=300)
    definition: str = Field(default="", max_length=2000)
    unit: str = Field(default="", max_length=40)
    direction: Literal["up", "down", "range"] = "up"
    cadence: Literal["daily", "weekly", "monthly"] = "weekly"
    source_note: str = Field(default="", max_length=2000)
    owner: str | None = Field(default=None, max_length=200)


class KpiReading(Contract):
    value: float
    period_start: str | None = Field(default=None, max_length=40)
    period_end: str | None = Field(default=None, max_length=40)
    collected_at: str | None = Field(default=None, max_length=40)
    evidence: str = Field(default="", max_length=2000)
    quality: Literal["measured", "estimate", "partial"] | None = None
    source: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=2000)
    definition_version: int | None = Field(default=None, ge=1)
    supersedes: str | None = Field(default=None, max_length=200)
    at: str | None = Field(default=None, max_length=40)              # the old name of period_end


class GoalProposalCreate(Contract):
    kind: Literal["goal_wording", "goal_kpi", "kpi_definition", "kpi_target", "flag", "goal_why", "recovery",
                  "strategy_draft", "brand_draft", "gtm_draft"]
    goal_id: str | None = Field(default=None, max_length=200)
    kpi_id: str | None = Field(default=None, max_length=200)
    payload: dict = Field(default_factory=dict)
    reason: str = Field(default="", max_length=1000)


GoalProposal = GoalProposalCreate


class GoalProposalDecision(Contract):
    decision: Literal["confirm", "reject"]
    note: str = Field(default="", max_length=1000)
    why: str | None = Field(default=None, max_length=1000)    # a Why proposal confirmed as edited
    option: int | None = Field(default=None, ge=0, le=9)     # the option a recovery is confirmed with (its pick if none)
    # A strategy realignment, per goal on the card: keep (the default), retire, or the id of the bet it moves to.
    moves: dict[str, str] | None = Field(default=None, max_length=200)
    target: KpiTarget | None = None      # an Add a KPI suggestion confirmed with the person's own target on the link


class GoalProposalReason(Contract):
    note: str = Field(max_length=1000)   # one line; backend/goals.py add_reason caps it at REASON_MAX after trimming


# The company at the top of Goals (backend/company.py): only the fields sent change.
class CompanyUpdate(Contract):
    vision: str | None = Field(default=None, max_length=4000)
    description: str | None = Field(default=None, max_length=10_000)


# What a person's chat bot learned (backend/learned.py): one short line, in their own layer or the company's.
class LearnedAdd(Contract):
    layer: Literal["personal", "company"] = "personal"
    text: str = Field(min_length=1, max_length=2000)


class LearnedEdit(Contract):
    text: str = Field(min_length=1, max_length=2000)


class GoalReadBack(Contract):
    """Up to 20 changes the owner's Assistant reads back in their chat (backend/goal_read_back.py): each one an object
    with a `kind` (goal_create, goal_update, measure_add, target_set, measure_remove, suggestion) and its fields."""
    changes: list[dict] = Field(min_length=1, max_length=20)


class GoalWhy(Contract):
    why: str = Field(default="", max_length=1000)     # "" clears it: the goal is not tied to the vision


# Market (backend/market.py). Reporters send prose. The Librarian (the curator) and the owner write the graph.
class MarketReport(Contract):
    kind: Literal["new-entity", "edge", "property-change", "correction", "question", "other"]
    about: str = Field(default="", max_length=300)
    claim: str = Field(min_length=1, max_length=20_000)
    source_url: str = Field(default="", max_length=2000)
    quote: str = Field(default="", max_length=20_000)
    confidence: Literal["high", "medium", "low"] = "medium"
    urgent: bool = False
    source_ref: str | None = Field(default=None, max_length=200)


class MarketAsk(Contract):
    question: str = Field(min_length=1, max_length=2000)


# The team's strategy (backend/strategy.py): the draft is edited a field at a time against the revision the writer
# read, the bets one by one, and publishing takes a one-line note. Lengths and shapes are checked there, where the
# refusal is recorded.
class StrategyDraft(Contract):
    revision: int
    guide: str | None = Field(default=None, max_length=40)
    summary: str | None = Field(default=None, max_length=2000)
    vision: str | None = Field(default=None, max_length=2000)
    sections: dict | None = None          # {serve, sell, know, not, extra: {key: {title, body}}}, any of them


class StrategyPublish(Contract):
    note: str = Field(default="", max_length=2000)
    revision: int


class StrategyDraftFrom(Contract):
    """Setup's research job: the team's website, the owner's own words, or both (backend/strategy_setup.py)."""
    url: str = Field(default="", max_length=2000)
    text: str = Field(default="", max_length=4000)


class LensRun(Contract):
    """Start a lens run (backend/lenses.py): the answers by step key, on the published version or the owner's draft."""
    answers: dict[str, Any] = Field(default_factory=dict)
    on: Literal["published", "draft"] = "published"


class LensApply(Contract):
    """Add one change of a lens run to the draft, against the draft revision the page showed."""
    index: Annotated[int, Field(strict=True, ge=0, le=100)]
    revision: Annotated[int, Field(strict=True, ge=0)]


class StrategyBetCreate(Contract):
    statement: str = Field(min_length=1, max_length=2000)
    why: str = Field(default="", max_length=20_000)
    rules_out: str = Field(default="", max_length=20_000)
    wrong_if: dict | None = None          # {kind: kpi|thesis|text, kpi_id?, direction?, threshold?, market_id?, text?, check_by?}
    cites: list[str] | None = Field(default=None, max_length=100)
    status: Literal["proposed", "active", "retired"] = "proposed"
    rank: int | None = None
    revision: int | None = None           # the draft's, when the writer wants a stale page refused


class StrategyBetUpdate(Contract):
    statement: str | None = Field(default=None, max_length=2000)
    why: str | None = Field(default=None, max_length=20_000)
    rules_out: str | None = Field(default=None, max_length=20_000)
    wrong_if: dict | None = None          # {} clears it
    cites: list[str] | None = Field(default=None, max_length=100)
    status: Literal["proposed", "active", "retired"] | None = None
    rank: int | None = None
    revision: int | None = None


class MarketEvidenceCreate(Contract):
    source_url: str = Field(default="", max_length=2000)
    source_kind: str = Field(default="other", max_length=40)
    captured_at: str | None = None
    quote: str = Field(default="", max_length=100_000)
    our_read: str = Field(default="", max_length=20_000)
    insight_id: str | None = Field(default=None, max_length=80)


class MarketEntityCreate(Contract):
    id: str | None = Field(default=None, max_length=200)
    type: Literal["company", "product", "person", "segment", "channel", "geography", "regulation", "event"]
    name: str = Field(min_length=1, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    external_ids: dict = Field(default_factory=dict)
    tier: Literal["core", "lookalike", "phrase-stealer", "secondary"] | None = None
    summary: str = Field(default="", max_length=20_000)
    properties: dict = Field(default_factory=dict)
    last_verified: str | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    force: bool = False
    insight_id: str | None = Field(default=None, max_length=80)


class MarketEntityWrite(Contract):
    name: str | None = Field(default=None, max_length=300)
    aliases: list[str] | None = None
    external_ids: dict | None = None
    tier: Literal["core", "lookalike", "phrase-stealer", "secondary"] | None = None
    clear_tier: bool = False
    summary: str | None = Field(default=None, max_length=20_000)
    properties: dict | None = None
    last_verified: str | None = None
    status: Literal["active", "retired", "merged"] | None = None
    evidence_ids: list[str] | None = None
    insight_id: str | None = Field(default=None, max_length=80)
    into: str | None = Field(default=None, max_length=200)


class MarketEdgeCreate(Contract):
    id: str | None = Field(default=None, max_length=200)
    src: str = Field(min_length=1, max_length=200)
    rel: str = Field(min_length=1, max_length=40)
    dst: str = Field(min_length=1, max_length=200)
    since: str | None = None
    until: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"
    properties: dict = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    insight_id: str | None = Field(default=None, max_length=80)


class MarketEdgeUpdate(Contract):
    rel: str | None = Field(default=None, max_length=40)
    since: str | None = None
    until: str | None = None
    set_since: bool = False
    set_until: bool = False
    confidence: Literal["high", "medium", "low"] | None = None
    properties: dict | None = None
    evidence_ids: list[str] | None = None
    insight_id: str | None = Field(default=None, max_length=80)


class MarketCitation(Contract):
    claim_kind: Literal["entity", "edge", "evidence", "document"]
    claim_id: str = Field(min_length=1, max_length=200)
    evidence_id: str = Field(min_length=1, max_length=80)
    insight_id: str | None = Field(default=None, max_length=80)


class MarketResolve(Contract):
    status: Literal["applied", "merged", "rejected", "needs-human"]
    resolution: str = Field(default="", max_length=2000)
    applied_events: list[str] = Field(default_factory=list, max_length=50)


class MarketApply(Contract):
    evidence: MarketEvidenceCreate
    entity: MarketEntityCreate | None = None
    edge: MarketEdgeCreate | None = None
    entity_id: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=20_000)
    properties: dict | None = None
    tier: Literal["core", "lookalike", "phrase-stealer", "secondary"] | None = None
    aliases: list[str] | None = None
    last_verified: str | None = None


class MarketImport(Contract):
    """A verified batch, one JSON object per line (backend/market.py `import_batch`)."""
    name: str = Field(default="", max_length=300)
    lines: str = Field(min_length=1, max_length=20_000_000)
    note: str = Field(default="", max_length=2000)
    dry_run: bool = False


class MarketUnverified(Contract):
    id: str = Field(min_length=1, max_length=200)
    look_for: str = Field(default="", max_length=2000)


class MarketSweep(Contract):
    today: str | None = None
    unverified: list[MarketUnverified] = Field(default_factory=list, max_length=100)


class MarketRefresh(Contract):
    today: str | None = None


class MarketPage(Contract):
    """A market page rewritten by the Librarian, the curator (backend/market.py `PAGES`), whole, in Markdown."""
    body: str = Field(min_length=1, max_length=100_000)


class ApprovalCreate(Contract):
    # task_cap and spend_limit are the redesign's proposals (backend/proposals.py), refused while it is off.
    kind: Literal["send", "spend", "publish", "merge", "support", "task_cap", "spend_limit"]
    payload: dict
    task_id: ID | None = None
    supersedes_approval: ID | None = None


class ApprovalDecision(Contract):
    decision: Literal["approved", "declined"]
    note: str = Field(default="", max_length=2000)


class AssistantSettings(Contract):
    """A person's own Assistant settings (Settings > General)."""
    decides_approvals: StrictBool


class AssistantReadBack(Contract):
    """The approvals the Assistant is about to approve for the person, read back in their chat first."""
    approvals: Annotated[list[Annotated[str, Field(min_length=1, max_length=64)]], Field(min_length=1, max_length=100)]


class ApprovalConsume(Contract):
    payload_hash: str = Field(min_length=64, max_length=64)


class StatusUpdate(Contract):
    state: str | None = None
    focus: str = Field(default="", max_length=2000)
    task_id: ID | None = None


class QuarantineClear(Contract):
    note: str = Field(default="", max_length=2000)     # optional
    # BotOps lifting it because a person asked in chat: checked as that person (backend/app.py).
    on_behalf_of: ID | None = None


class EnrollmentRequest(Contract):
    operator: ID | None = None


class Enrollment(Contract):
    code: str = Field(min_length=32, max_length=200)
    label: str = Field(min_length=1, max_length=100)
    platform: str = Field(default="", max_length=100)


class SlashCommand(Contract):
    # A bot's own Claude Code skills and commands may use "_" and ":" (runner-rs/src/goals.rs, repo_commands).
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_:-]{0,63}$")
    args: str = Field(default="", max_length=100)
    help: str = Field(default="", max_length=300)
    kind: Literal["tico", "harness"] = "harness"
    sub: list[str] | None = None


# Text that reads like a credential: a known key prefix, a JWT, or one unbroken run of 32 or more key characters
# (runner-rs/src/accounts.rs token_like).
TOKEN_LIKE = re.compile(r"(?i)(\b(?:sk-|sk_|ghp_|gho_|ghs_|ghu_|github_pat_|xox[abp]-|bearer\s)|eyJ[A-Za-z0-9_-]{8,}|[A-Za-z0-9_\-+/=]{32,})")


class SubscriptionAccount(Contract):
    """The account a model CLI says it is signed in to (#610), read only from its documented status command
    (`claude auth status --json`, Codex app-server `account/read`). Never a credential: a value that reads like one
    fails validation, and the account is then left out."""
    email: str | None = Field(default=None, max_length=254,
                              pattern=r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}$")
    plan: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.+-]{0,39}$")
    org: str | None = Field(default=None, min_length=1, max_length=100)
    # "api_key": an API key signs the CLI in, so there is no plan usage.
    kind: Literal["subscription", "api_key"] | None = None
    checked_at: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def _no_credentials(self):
        for value in (self.email, self.plan, self.org):
            if value and (TOKEN_LIKE.search(value) or any(ord(ch) < 32 for ch in value)):
                raise ValueError("account fields hold names, never credentials")
        return self

    @model_serializer(mode="wrap")
    def _set_fields(self, handler):
        return {k: v for k, v in handler(self).items() if v is not None}


def _account_or_none(value, handler):
    # An account that fails its checks is left out; the sign-in report it rides on still counts.
    try:
        return handler(value)
    except ValidationError:
        return None


Account = Annotated[SubscriptionAccount | None, WrapValidator(_account_or_none)]


class SubscriptionWeekly(Contract):
    used_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    resets_at: str | None = Field(default=None, max_length=50)
    reported_at: str = Field(max_length=50)
    status: Literal["allowed", "allowed_warning", "rejected"] | None = None


# The computer's own login's usage window on its runtime row: one that fails its checks is left out, like the account.
UsageWindow = Annotated[SubscriptionWeekly | None, WrapValidator(_account_or_none)]


def _cut(limit):
    """A reported string longer than `limit` is cut, never refused: a refused report would hide the computer."""
    return BeforeValidator(lambda v: v[:limit] if isinstance(v, str) else v)


class GoalReadiness(Contract):
    goals: bool = False
    commands: list[SlashCommand] = Field(default_factory=list, max_length=30)

    @model_serializer(mode="wrap")
    def _reported_capabilities(self, handler):
        data = handler(self)
        for key in ("goals", "commands"):
            if key not in self.model_fields_set:
                data.pop(key, None)
        for key in ("account", "weekly", "five_hour"):
            if key in data and data[key] is None:
                data.pop(key)
        return data


class ProfileReadiness(GoalReadiness):
    """One subscription profile on the runner: the provider login its bots share."""
    runtime: str = Field(default="", max_length=100)
    installed: bool = False
    authenticated: Literal["ready", "missing", "failed", "unknown"] = "unknown"
    version: str = Field(default="", max_length=100)
    models: list[ID] = Field(default_factory=list, max_length=50)
    controls: list[Literal["interrupt", "new-session"]] = Field(default_factory=list)
    detail: str = Field(default="", max_length=500)
    # Its turns on this runtime use the computer's own login (a profile that shares the operator's): one login, held
    # once (backend/login_holds.py canonical).
    shared_login: bool | None = None
    # Signed in, but its turns can't save a session (Muse Code's data folder): the folder and why, not a sign-in.
    data_problem: Annotated[str, _cut(500)] = ""


class HarnessReadiness(GoalReadiness):
    """One model CLI on the runner (runner/harnesses/*.toml): what is installed and whether it can
    be kept current from Settings."""
    name: str = Field(default="", max_length=100)
    runtime: str = Field(default="", max_length=100)
    installed: bool = False
    version: str = Field(default="", max_length=100)
    # "tools": installed by the runner, so it can update it; "path": the person's own install.
    managed: bool = False
    source: Literal["tools", "path", ""] = ""
    pinned: bool = False
    pin: str = Field(default="", max_length=100)
    authenticated: Literal["ready", "missing", "failed", "unknown"] = "unknown"
    update_available: bool = False
    latest: str = Field(default="", max_length=100)
    wanted: bool = False
    state: Literal["idle", "installing", "updating", "queued", "failed"] = "idle"
    detail: str = Field(default="", max_length=500)


class RuntimeKey(Contract):
    name: str = Field(max_length=100, pattern=r"^[A-Z_][A-Z0-9_]*$")
    source: Literal["computer", "terminal", "shared"]


class RuntimeReadiness(GoalReadiness):
    installed: bool
    # "rejected": the provider refused the key or sign-in on a real turn (rejected_at, rejected_reason).
    authenticated: Literal["ready", "missing", "failed", "unknown", "rejected"] = "unknown"
    rejected_at: str = Field(default="", max_length=40)
    rejected_reason: str = Field(default="", max_length=300)
    credential_source: Literal["", "credentials", "computer"] = ""
    # Which key signs it in, when one does: the computer's own from the app ("computer"), one set on the computer
    # ("terminal"), or the company's shared key ("shared"). A name, never a value (runner-rs/src/signin.rs key_source).
    key: RuntimeKey | None = None
    version: str = Field(default="", max_length=100)
    models: list[ID] = Field(default_factory=list, max_length=50)
    controls: list[Literal["interrupt", "new-session"]] = Field(default_factory=list)
    detail: str = Field(default="", max_length=500)
    data_problem: Annotated[str, _cut(500)] = ""      # as ProfileReadiness
    # On a Docker runner: "own" when each turn on this tool runs as its own leased OS user, "bot" when its turns share
    # the bot user and can enter every bot's checkout (its sign-in is owner-only state; runner/harnesses `isolated_user`).
    turn_user: Literal["", "own", "bot"] = ""
    # One machine may hold several subscriptions for the same runtime. The runtime row keeps
    # the profile that needs attention first; this is the per-profile breakdown behind it.
    profiles: dict[str, ProfileReadiness] = Field(default_factory=dict, max_length=50)
    shared_login: bool | None = None    # the one profile's row shown as the runtime's (ProfileReadiness)
    # The computer's own login's account, beside a ready sign-in. Shown only to an administrator or the
    # computer's operator (backend/views.py computer_details, /api/v2/operations).
    account: Account = None
    # The computer's own login's usage windows, as a named profile's (SubscriptionRuntime), for the same audience as
    # the account. Their resets are checked against each window's length when the heartbeat is stored.
    weekly: UsageWindow = None
    five_hour: UsageWindow = None


class McpReport(Contract):
    """The `mcp:` block of a tool as the runner reports it: the address, the transport and headers that hold only
    `${VAR}` placeholders (runner-rs/src/declared_access.rs). `status` is the runner's own cheap check of the server; it is
    `unchecked` until that has run, or when the credential it needs is not on the computer."""
    url: str = Field(min_length=1, max_length=500)
    transport: Literal["http", "sse"] = "http"
    headers: dict[Annotated[str, Field(max_length=64)], Annotated[str, Field(max_length=500)]] = Field(
        default_factory=dict, max_length=10)
    status: Literal["reachable", "auth_failed", "unreachable", "unchecked"] = "unchecked"


class ToolAccess(Contract):
    """One `access:` entry of a bot's employee.yaml as the runner reports it (runner-rs/src/declared_access.rs):
    names and verbs, never a value. `credential` is whether the variable is on the runner's computer."""
    service: str = Field(min_length=1, max_length=100)
    identity: str = Field(default="", max_length=300)
    mcp: McpReport | None = None
    can: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=20)
    scope: dict[Annotated[str, Field(max_length=40)], str | list[Annotated[str, Field(max_length=100)]]] = Field(
        default_factory=dict, max_length=20)
    env: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=500)
    credential: Literal["present", "missing", "hub-vault", "not-declared"] = "not-declared"
    held: bool = False          # present, but kept by the computer rather than in a run (the Google key on Docker)
    problem: str = Field(default="", max_length=300)


class ToolRegister(Contract):
    """A tool a person registers for a bot (backend/bot_tools.py). Names and verbs only: `env` is the
    variable's name, and the server refuses a field that looks like a credential."""
    service: str = Field(min_length=1, max_length=100)
    identity: str = Field(default="", max_length=300)
    can: list[str] = Field(min_length=1, max_length=20)
    scope: dict[str, Any] = Field(default_factory=dict, max_length=20)
    # A remote MCP server: {url, transport: http|sse, headers: {Name: "Bearer ${ENV_NAME}"}} (clients/access_entry.py).
    mcp: dict[str, Any] | None = None
    env: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=500)
    title_prefix: str = Field(default="", max_length=100)
    dry_run: bool = False


class ToolUpdate(Contract):
    """A change to a tool a bot already declares (backend/bot_tools.py): only what is sent changes. `scope` keys
    are set one by one (an empty value takes a key off); `note` "" clears the note."""
    can: list[str] | None = Field(default=None, min_length=1, max_length=20)
    scope: dict[str, Any] | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=500)
    mcp: dict[str, Any] | None = None       # url, transport, headers: what is sent replaces that key of the tool's `mcp:`
    title_prefix: str = Field(default="", max_length=100)


class MailRequest(Contract):
    """What a bot's bot.yaml asks for its mail (runner-rs/src/declared_access.rs `mail_request`): a request only. The
    server-held setting (backend/mail_settings.py) decides; Health says when the two differ."""
    outbound_send: bool = False
    forward_to: list[Annotated[str, Field(max_length=320)]] = Field(default_factory=list, max_length=20)


class MailSettingsUpdate(Contract):
    """A person's change to a bot's mail sending (backend/mail_settings.py). Only what is sent changes.
    `from_request` takes what the bot's bot.yaml asks, as its computer last reported it."""
    outbound_send: bool | None = None
    forward_to: list[Annotated[str, Field(max_length=320)]] | None = Field(default=None, max_length=20)
    from_request: bool = False


class BotReadiness(GoalReadiness):
    ready: bool
    runtime: str = Field(default="", max_length=100)
    model: str = Field(default="", max_length=200)
    # The runner's checkout path lets Health name the exact clone destination on that computer.
    repository: str = Field(default="", max_length=2000)
    repository_present: bool = False
    repository_revision: str = Field(default="", max_length=100)
    # Whether GitHub holds this checkout's history (it has an upstream). False means the only copy is on that
    # computer, so a move would strand the bot; None is a runner that does not say, or a bot with no checkout.
    published: bool | None = None
    configuration_valid: bool = False
    problems: list[str] = Field(default_factory=list, max_length=20)
    # Soft guidance that does not block claiming work (for example a long AGENT.md).
    warnings: list[str] = Field(default_factory=list, max_length=20)
    # Runners before 0.5.4 reported the routines they read from a repository manifest. Routines
    # are the hub's own rows now (backend/routines.py); these are accepted and dropped.
    schedules: list | None = None
    schedule_error: str = Field(default="", max_length=500)
    routine_revisions: list[str] = Field(default_factory=list, max_length=100)
    routine_preparation_error: str = Field(default="", max_length=500)
    # The subscription profile this bot's turns run on, and that profile's own sign-in state.
    profile: str = Field(default="", max_length=100)
    sign_in: Literal["ready", "missing", "failed", "unknown"] = "unknown"
    data_problem: Annotated[str, _cut(500)] = ""      # its runtime row's (ProfileReadiness): a folder, not a sign-in
    # The bot's declared `access:`, for the Tools row on its page. A runner from before it omits it.
    tools: list[ToolAccess] = Field(default_factory=list, max_length=30)
    # What bot.yaml asks for its mail sending; omitted when it asks for nothing.
    mail_request: MailRequest | None = None


class DiskReadiness(Contract):
    total_bytes: int = Field(ge=1)
    free_bytes: int = Field(ge=0)


class ContainerExecReadiness(Contract):
    """A throwaway container start on the computer (runner-rs/src/readiness_extra.rs)."""
    ok: StrictBool
    seconds: float = Field(ge=0, le=3600, allow_inf_nan=False)
    error: Annotated[str, BeforeValidator(lambda v: v[:300] if isinstance(v, str) else v), Field(max_length=300)] = ""
    checked_at: str = Field(max_length=50)


class EngineReadiness(Contract):
    """Which runner engine the computer runs (runner/engine.py): the Rust runner and its build, or the Python one and why."""
    name: Literal["rust", "python"]
    build: str = Field(default="", max_length=64)
    note: Annotated[str, BeforeValidator(lambda v: v[:300] if isinstance(v, str) else v), Field(max_length=300)] = ""


class ScreenReadiness(Contract):
    """The computer's screen, when its owner turned it on there (backend/screen.py): its size, or why it cannot run."""
    available: StrictBool
    width: int | None = Field(default=None, ge=1, le=10000)
    height: int | None = Field(default=None, ge=1, le=10000)
    reason: Annotated[str, BeforeValidator(lambda v: v[:200] if isinstance(v, str) else v), Field(max_length=200)] = ""


AppState = Literal["ready", "no_permission", "not_installed", "not_signed_in", "needs_foreground", "ui_changed",
                   "no_driver", "error"]
APP_STATES = AppState.__args__


class AppDriverApp(Contract):
    """One desktop app the computer's app driver can operate for a synced bot (backend/app_delivery.py)."""
    installed: StrictBool = False
    version: Annotated[str, _cut(100), Field(max_length=100)] = ""
    running: StrictBool = False
    signed_in: StrictBool = False
    background_ok: StrictBool = False
    ready: StrictBool = False
    # A state a newer driver names that this server does not know yet reads as `error`, not as a refused report.
    state: Annotated[AppState, BeforeValidator(lambda v: v if v in APP_STATES else "error")] = "error"
    detail: Annotated[str, _cut(300), Field(max_length=300)] = ""


APP_DRIVER_APPS = 10


class AppDriverReadiness(Contract):
    """The computer's desktop-app driver: whether macOS trusts it, and each app's state by app id (`grok`)."""
    trusted: StrictBool = False
    macos: Annotated[str, _cut(50), Field(max_length=50)] = ""
    detail: Annotated[str, _cut(300), Field(max_length=300)] = ""
    apps: Annotated[dict[Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,40}$")], AppDriverApp],
                    BeforeValidator(lambda v: dict(list(v.items())[:APP_DRIVER_APPS]) if isinstance(v, dict) else v)
                    ] = Field(default_factory=dict)


class StructuredReadiness(Contract):
    # Physical home of the computer's one login; present only after the computer_logins_v1 handshake.
    computer_profile: str | None = Field(default=None, max_length=80)
    worktrees: bool | None = None
    # Prepares a worktree reserved for a waiting task while its bot is idle (backend/worktree_waits.py).
    worktree_waits: bool | None = None
    schema_version: Literal[1] = 1
    disk: DiskReadiness | None = None
    # Bots run as their own user here (runner-rs/src/isolation.rs), so a Tico model may run (backend/models_preset.py).
    isolated: bool | None = None
    # Not isolated, but a Tico cloud machine: one company's trust group, whose Tico models run on the runner's relay
    # alone (runner-rs/src/cloud_machine.rs). Sent only once the config answer says `readiness_trust`.
    trust: Literal["cloud_machine"] | None = None
    runtimes: dict[str, RuntimeReadiness] = Field(default_factory=dict)
    bots: dict[str, BotReadiness] = Field(default_factory=dict)
    harnesses: dict[str, HarnessReadiness] = Field(default_factory=dict, max_length=50)
    mail_key: Literal["exposed"] | None = None      # the mail key is where bots can read it (runner/mail_key.py)
    shared_env: Literal[True] | None = None         # secrets/_shared.env holds keys every bot there receives
    container_exec: ContainerExecReadiness | None = None
    # A bot's GitHub App token asked GitHub who it is, and Playwright's Chromium started (runner-rs/src/readiness_extra.rs).
    github_auth: ContainerExecReadiness | None = None
    browser_launch: ContainerExecReadiness | None = None
    engine: EngineReadiness | None = None
    screen: ScreenReadiness | None = None
    appdriver: AppDriverReadiness | None = None
    # The runner's last WARN/ERROR-like log lines, for a support bundle a person chooses to send (backend/diagnostics.py).
    # Long lines are cut, never refused: a refused report would hide the computer (0.3.2 runners sent 301 characters).
    recent_errors: list[Annotated[str, BeforeValidator(lambda v: v[:300] if isinstance(v, str) else v),
                                  Field(max_length=300)]] = Field(default_factory=list, max_length=50)

    @model_serializer(mode="wrap")
    def _without_empty_errors(self, handler):
        data = handler(self)
        if data.get("disk") is None:
            data.pop("disk", None)
        for field in ("container_exec", "github_auth", "browser_launch", "engine", "screen", "appdriver"):
            if data.get(field) is None:
                data.pop(field, None)
        if not data.get("recent_errors"):
            data.pop("recent_errors", None)       # a stored report keeps only what the runner sent
        return data


class RepositoryStatus(Contract):
    full_name: str
    state: Literal["cloned", "cloning", "failed", "disk_low", "removed"]
    last_fetch: str | None = None
    size_mb: float = Field(default=0, ge=0)
    error: str | None = None


class SubscriptionNameEdit(Contract):
    runner_id: ID
    profile: Slug
    display_name: str = Field(min_length=1, max_length=80)


class SubscriptionWeeklyEdit(Contract):
    runner_id: ID
    profile: Slug
    runtime: WeeklyRuntime
    used_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    resets_at: str | None = Field(default=None, max_length=50)


class SubscriptionRuntime(Contract):
    signed_in: bool | None = None
    weekly: SubscriptionWeekly | None = None
    # The 5-hour usage window, when the provider reports one (#610).
    five_hour: SubscriptionWeekly | None = None
    account: Account = None


class ComputerProfile(Contract):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    runtimes: dict[str, SubscriptionRuntime] = Field(default_factory=dict, max_length=20)


class WorktreeStatus(Contract):
    repo: str | None = Field(default=None, max_length=200)
    last_activity: float | None = Field(default=None, ge=0)
    link_id: str = Field(max_length=100)
    state: Literal["pending", "present", "missing", "removed", "unknown"]
    branch: str | None = Field(default=None, max_length=200)
    checkout_state: Literal["queued", "attached_pending", "initializing", "checkout_ready", "setup_running", "setup_failed", "ready", "legacy_present", "unverified"] | None = None
    expected_head: str | None = Field(default=None, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    checkout_target: str | None = Field(default=None, max_length=300)
    expected_base: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    ahead: int = Field(default=0, ge=0)
    behind: int = Field(default=0, ge=0)
    dirty_files: int = Field(default=0, ge=0)
    last_commit: str | None = Field(default=None, max_length=100)
    size_mb: float = Field(default=0, ge=0)
    error: str | None = Field(default=None, max_length=300)
    # The record's `updated` stamp this report was built from; a report older than the record is stale.
    record_updated: str | None = Field(default=None, max_length=40)


class RunnerPanic(Contract):
    # Its file in the runner's crate (`src/runner.rs`), or empty for a panic in a dependency; never its message.
    file: str = Field(default="", max_length=200)
    line: int = Field(default=0, ge=0, le=1_000_000)


class RunnerErrors(Contract):
    version: str = Field(default="", max_length=100)
    panics: list[RunnerPanic] = Field(default_factory=list, max_length=20)


class Heartbeat(Contract):
    worktrees: list[WorktreeStatus] | None = Field(default=None, max_length=1000)
    profiles: Annotated[list[Any], BeforeValidator(lambda value: value[:100] if isinstance(value, list) else value)] | None = None
    repositories: list[RepositoryStatus] | None = None
    version: str = Field(max_length=100)
    platform: str = Field(max_length=100)
    capacity: int = Field(default=4, ge=1, le=256)
    # The assignment_* values name the retired temporary assignments (#420); an older runner still sends them.
    capabilities: list[Literal[
        "assignment_instances_v1", "assignment_cleanup_v1", "task_lanes_v1", "task_lanes_v2", "session_roots_v1",
        "task_goals_v1", "screen_v1", "model_sign_out_v1", "model_keys_v1", "model_switch_type_v1", "model_remove_v1",
        "model_switch_to_key_v1", "computer_logins_v1", "capacity_continue_v1", "source_continue_v1",
        "turns_at_once_v1", "private_lanes_v1", "model_subscriptions_v1", "browser_exit_v1"]] = Field(
        default_factory=list, max_length=20)
    # The bool map remains accepted during runner rollout. The server normalizes both
    # shapes before storing them, so every read path sees one structured document.
    readiness: StructuredReadiness | dict[str, bool] = Field(default_factory=dict)
    # Actual AGENT.md text for assigned personal inbox bots, read from their runner checkout.
    mail_agent_instructions: dict[str, str] = Field(default_factory=dict, max_length=20)
    # Changed AGENT.md files for assigned bots. The runner sends each file on first heartbeat
    # and when it changes; the server keeps the last published copy for Messaging.
    agent_instructions: dict[str, str] = Field(default_factory=dict, max_length=100)
    # The runner's own checkout against origin/main (runner-rs/src/follow.rs), #492.
    checkout: "Checkout | None" = None
    # Which release the runner is on and how its self-update stands (backend/runner_versions.py).
    release: str | None = Field(default=None, max_length=100)
    # The commit it runs: an image's release-manifest.json, else its checkout's HEAD; "" when it cannot say.
    commit: str | None = Field(default=None, pattern=r"^([0-9a-f]{40}|[0-9a-f]{64})?$")
    kind: Literal["mac", "linux", "docker"] | None = None
    update: "RunnerUpdate | None" = None
    # A restart a person asked for that has not happened yet (runner-rs runner.rs): sent until the runner restarts.
    restart: "RunnerRestart | None" = None


class RunnerRestart(Contract):
    since: str = Field(max_length=40)


class RunnerUpdate(Contract):
    running: int | None = Field(default=None, ge=0, le=256)
    handed_back: int | None = Field(default=None, ge=0, le=100_000)
    state: Literal["idle", "waiting", "updating", "failed", "rolled_back", "pinned", "blocked"]
    target: str = Field(default="", max_length=100)
    error: str = Field(default="", max_length=300)


class Checkout(Contract):
    head: str = Field(pattern=r"^[0-9a-f]{40}$")
    running: str = Field(pattern=r"^[0-9a-f]{40}$")
    ahead: int = Field(ge=0, le=100_000)
    behind: int = Field(ge=0, le=100_000)
    checked_at: str = Field(max_length=40)
    # Why the runner is not updating itself (runner-rs/src/follow.rs), when it is behind.
    blocked: str | None = Field(default=None, max_length=300)


Heartbeat.model_rebuild()


class RunnerMemberBots(Contract):
    accepts: bool


class RunnerTurnsAtOnce(Contract):
    # Required: an omitted field is a 422, not a null that would silently set automatic. Null itself clears it.
    turns_at_once: int | None = Field(..., ge=1, le=256, strict=True,
        description="Most turns this computer runs at once. Null is automatic: its own capacity.")


class Assignment(Contract):
    runner_id: ID
    expected_generation: int = Field(ge=0)
    on_behalf_of: ID | None = None


class AgentPairingCreate(Contract):
    """A Hermes or OpenClaw profile asking to be paired with a bot (backend/agents.py). No sign-in: what it says is only
    shown to the person who approves the code. A newer connector's extra fields are ignored."""
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    profile: str = Field(default="", max_length=100)
    harness: Literal["hermes", "openclaw"] = "hermes"
    host: str = Field(default="", max_length=100)
    version: str = Field(default="", max_length=100)


class AgentPairingApprove(Contract):
    code: str = Field(min_length=4, max_length=20)
    bot: ID


class AgentPairingDecline(Contract):
    code: str = Field(min_length=4, max_length=20)


class AgentHeartbeat(Contract):
    """What an external agent (a Hermes profile) says about itself when it reports in.
    Nothing here is trusted for authorization; it is what the bot page shows."""
    version: str = Field(default="", max_length=100)
    platform: str = Field(default="", max_length=100)
    model: str = Field(default="", max_length=200)
    provider: str = Field(default="", max_length=100)
    profile: str = Field(default="", max_length=100)
    detail: str = Field(default="", max_length=500)
    tools: list[ToolRegister] | None = None


class BotOwners(Contract):
    owners: list[ID] = Field(min_length=1, max_length=50)
    expected_revision: int = Field(ge=1)


class AccessAudience(Contract):
    """One level of a bot's access: everyone, or these people, teams and bots."""
    everyone: bool = False
    people: list[ID] = Field(default_factory=list, max_length=500)
    teams: list[ID] = Field(default_factory=list, max_length=500)
    bots: list[Slug] = Field(default_factory=list, max_length=500)


class BotAccess(Contract):
    see: AccessAudience
    read: AccessAudience
    write: AccessAudience
    revision: int = Field(ge=1)
    # BotOps making the change the requester asked for in chat ("turn", or their message's id).
    on_behalf_of: ID | None = None


# A bot's instructions (its AGENT.md) as a person reviewed them, one limit wherever they are sent: registering a bot,
# adding one from the catalog and the first-run wizard. The longest shipped template is about half of it, and
# backend/tests/test_onboarding.py checks every one against it.
INSTRUCTIONS_MAX = 50_000


class BotRegister(Contract):
    """Register a bot with the server, planned, before BotOps builds its repository. Left out, the slug is
    picked from the name (or the template's name)."""
    slug: Slug | None = None
    display_name: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=2000)
    reports_to: ID | None = None
    template: str = Field(default="", max_length=80)
    instructions: str = Field(default="", max_length=INSTRUCTIONS_MAX)
    title_prefix: str = Field(default="", max_length=100)
    model: ID | None = None
    build: bool = False
    on_behalf_of: ID | None = None


class CopiedTool(Contract):
    """What the original's `tools:` names about a credential it needs: the variable's name, never a value."""
    service: str = Field(default="", max_length=100)
    env: str = Field(pattern=r"^[A-Z_][A-Z0-9_]*$", max_length=100)


class BotCopy(Contract):
    """Copy a bot into a new, independent one the requester owns (backend/bot_copy.py). `sha` and `tools` are what the computer
    that holds the original's repository read from it; the new repository is made on that computer."""
    slug: Slug | None = None
    display_name: str = Field(default="", max_length=100)
    with_memory: bool = False
    computer: str = Field(default="", max_length=200)
    sha: str = Field(default="", pattern=r"^([0-9a-f]{40})?$")
    tools: list[CopiedTool] = Field(default_factory=list, max_length=50)


class BotUpdateFromOriginal(Contract):
    """Without `applied_sha`: what a copy was copied from. With it: the original's commit the copy is now up to date with."""
    applied_sha: str = Field(default="", pattern=r"^([0-9a-f]{40})?$")


class SuggestedFile(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)       # a file's own whitespace is its content
    path: str = Field(min_length=1, max_length=300)
    content: str | None = Field(default=None, max_length=200_000)      # None: the copy deleted it


class BotSuggest(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    files: list[SuggestedFile] = Field(min_length=1, max_length=50)
    diff: str = Field(default="", max_length=100_000)
    held_back: list[str] = Field(default_factory=list, max_length=100)
    title: str = Field(default="", max_length=200)


class SkillCopy(Contract):
    skill: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    to: list[Slug] = Field(min_length=1, max_length=20)


class BotCoOwners(Contract):
    """Add or remove people who own a bot (its creator is the first)."""
    add: list[ID] = Field(default_factory=list, max_length=50)
    remove: list[ID] = Field(default_factory=list, max_length=50)
    on_behalf_of: ID | None = None

    @model_validator(mode="after")
    def has_change(self):
        if not (self.add or self.remove):
            raise ValueError("Name someone to add or remove")
        return self


class GroupMembers(Contract):
    """Teammates of a group: humans by id, bots by slug."""
    people: list[ID] = Field(default_factory=list, max_length=200)
    bots: list[ID] = Field(default_factory=list, max_length=200)


class GroupCreate(Contract):
    """A new group: a name, where it nests (a group id; none = top level) and who is in it from the start."""
    name: str = Field(min_length=1, max_length=60)
    parent: ID | None = None
    add: GroupMembers = Field(default_factory=GroupMembers)
    bet_id: ID | None = None               # the strategy bet the group serves (owner only)
    lead: str | None = Field(default=None, max_length=200)   # `bot:<slug>` or `person:<id>`, a teammate of the group


class GroupUpdate(Contract):
    """Rename a group, move it under another (`parent`; "" = top level), and add or remove teammates. A teammate
    added to a group leaves the one it was in."""
    name: str | None = Field(default=None, min_length=1, max_length=60)
    parent: str | None = Field(default=None, max_length=200)
    add: GroupMembers = Field(default_factory=GroupMembers)
    remove: GroupMembers = Field(default_factory=GroupMembers)
    bet_id: str | None = Field(default=None, max_length=200)      # "" unlinks it from its bet (owner only)
    lead: str | None = Field(default=None, max_length=200)        # `bot:<slug>` or `person:<id>`; "" = no lead


class ReportOrder(Contract):
    """A manager's direct reports in the order the team chart shows them: bot slugs and human ids (`bot:` or `human:`
    in front when one could be either). Reports left out keep their places."""
    reports: list[ID] = Field(min_length=1, max_length=200)


class BotDefinitionCreate(Contract):
    private_tasks_default: StrictBool = False
    slug: Slug | None = None                 # left out, picked from the display name
    display_name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    reports_to: ID | None = None
    status: Literal["active", "paused", "planned"] = "planned"
    repo: Repo = ""
    thread_mode: Literal["personal", "shared"] = "personal"
    shared: bool = False
    model: str = Field(default="", max_length=200)
    effort: str = Field(default="", max_length=200)
    harness: ID | None = None
    operator: ID | None = None
    # Who the bot works for (shared-room members). Left out, that is its operator; who may use it is
    # its access (docs/permissions.md), which starts Open.
    owners: list[ID] = Field(default_factory=list, max_length=50)
    runner_id: ID | None = None
    # "Add from catalog": the template this bot is built from and the instructions a person
    # reviewed for it. Both are empty for a bot typed in by hand.
    template: str = Field(default="", max_length=80)
    instructions: str = Field(default="", max_length=INSTRUCTIONS_MAX)


class BotBranch(Contract):
    runner_id: ID | None = None


class OnboardingComplete(Contract):
    """Mark onboarding complete (a person above the bot), or the bot's own "I'm ready" with what it learned."""
    summary: Annotated[str, Field(max_length=2000)] = ""


class SeatCreate(Contract):
    """A Recruiting seat: a role on the org chart with its job description and reporting line, and no bot."""
    title: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=8000)] = ""
    reports_to: Annotated[str, Field(max_length=200)] = ""     # `human:<id>`, a bot's slug or `seat:<id>`; the caller by default
    team: Annotated[str, Field(max_length=100)] = ""


class SeatBatchCreate(Contract):
    """A single confirmed group of Recruiting seats."""
    seats: Annotated[list[SeatCreate], Field(min_length=1, max_length=50)]


class SeatUpdate(Contract):
    title: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    description: Annotated[str, Field(max_length=8000)] | None = None
    reports_to: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    team: Annotated[str, Field(max_length=100)] | None = None


class SeatDraft(Contract):
    """A job description for a seat not posted yet, drafted from its title (backend/recruiting.py `draft`)."""
    title: Annotated[str, Field(min_length=1, max_length=120)]
    reports_to: Annotated[str, Field(max_length=200)] = ""
    team: Annotated[str, Field(max_length=100)] = ""
    redraft: bool = False                                   # one model rewrite, when the server has a key


class SeatHire(Contract):
    """The candidate picked for a seat: a Tico template, on its suggested model unless `model` names another, named
    after the seat unless `name` says otherwise, and on `runner_id`'s computer when one is chosen."""
    template: Annotated[str, Field(min_length=1, max_length=80)]
    model: Annotated[str, Field(max_length=200)] = ""
    name: Annotated[str, Field(max_length=100)] = ""
    runner_id: ID | None = None


class BotArchive(Contract):
    successor: ID | None = None          # a bot that takes its open tasks and any team it roots
    expected_revision: int = Field(ge=1)
    # A Hermes bot's agent keeps its credential after the bot is archived and keeps reporting in to a bot that
    # no longer answers; revoking it with the archive is the default (backend/agents.py).
    revoke_agent: bool = True
    # BotOps applying a person's own request, as for a definition change (backend/app.py).
    on_behalf_of: ID | None = None


class AppDelivery(Contract):
    """Where a synced bot's messages are delivered: through `app` on the computer (runner) `computer`."""
    computer: ID
    app: Literal["grok"] = "grok"


class AppDeliveryMessage(Contract):
    """One message the app shows in the bot's conversation. `key` is the driver's own stable id for it."""
    key: Annotated[str, Field(min_length=1, max_length=200)]
    role: Literal["bot", "user"]
    text: Annotated[str, Field(min_length=1, max_length=40_000)]
    at: Annotated[str, Field(max_length=50)] | None = None
    needs_human: StrictBool = False


class AppDeliveryError(Contract):
    code: Literal["needs_human", "no_permission", "not_signed_in", "needs_foreground", "ui_changed", "not_installed",
                  "not_running", "bot_not_found", "timeout", "send_failed", "busy"]
    detail: Annotated[str, _cut(300), Field(max_length=300)] = ""
    text: Annotated[str, Field(max_length=40_000)] = ""


class AppDeliveryReport(Contract):
    """What a computer read from a bot's conversation in the app, newest last (POST /api/v2/runners/me/app-delivery)."""
    app: Literal["grok"] = "grok"
    messages: list[AppDeliveryMessage] = Field(default_factory=list, max_length=50)
    error: AppDeliveryError | None = None


class BotDefinitionUpdate(Contract):
    private_tasks_default: StrictBool | None = None
    template: str | None = Field(default=None, max_length=80)
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    reports_to: ID | None = None
    bot_contact: Literal["open", "replies", "tasks"] | None = None
    status: Literal["active", "paused", "planned"] | None = None
    repo: Annotated[str, Field(min_length=1, max_length=200), AfterValidator(repo_reference)] | None = None
    thread_mode: Literal["personal", "shared"] | None = None
    # Temporary work expected to end: a flag, not "Project"/"Temp" in the name.
    temp: bool | None = None
    shared: bool | None = None
    session: Literal["bot", "task"] | None = None
    # Unset or `parent`: a subtask continues its parent task's session (hubdb.session_root); `own`: its own session.
    subtask_session: Literal["parent", "own"] | None = None
    # `on`: each task the bot owns is worked as a goal (execution.task_goal); unset or `off`: not.
    task_goal: Literal["on", "off"] | None = None
    # `check`: a subtask it is handed under another's task runs on the company's check model
    # (check_models.review_default); unset or `own`: its own model.
    review_model: Literal["check", "own"] | None = None
    # `on`: each run's raw provider stream is kept a week for replay fixtures (backend/stream_capture.py); owner only.
    capture_stream: Literal["on", "off"] | None = None
    # A Grok Bot answered through the Grok app on one of its person's computers; null turns it off
    # (backend/app_delivery.py). Unset leaves it as it is.
    app_delivery: AppDelivery | None = None
    # Subscribe to updates: which feeds it hears about as quiet notes (backend/update_feeds.py); empty is none.
    update_subscriptions: list[Literal["templates", "tico"]] | None = Field(default=None, max_length=2)
    expected_revision: int = Field(ge=1)
    # BotOps applying a person's own request: the id of that person's message to BotOps. The
    # change is checked as that person, never as BotOps (backend/app.py update_bot).
    on_behalf_of: ID | None = None

    @model_validator(mode="after")
    def has_change(self):
        if not (self.model_fields_set - {"expected_revision", "on_behalf_of"}):
            raise ValueError("Provide at least one bot definition field to change")
        return self


class BotGoals(Contract):
    goals: str = Field(default="", max_length=8000)


RoutineKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")]


class RoutineCreate(Contract):
    """A routine: what a bot is told, and when (backend/routines.py). `cron` (five fields, in
    `timezone`) or `on` (an event the hub emits), never both. `key` names the routine stably so
    creating it again updates it instead of adding a twin."""
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(default="", max_length=100_000)
    cron: str = Field(default="", max_length=100)
    on: str = Field(default="", max_length=100)
    timezone: str = Field(default="", max_length=100)
    enabled: bool = True
    key: RoutineKey | None = None


class RoutineUpdate(Contract):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    text: str | None = Field(default=None, max_length=100_000)
    cron: str | None = Field(default=None, max_length=100)
    on: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default=None, max_length=100)
    enabled: bool | None = None


class PersonUpdate(Contract):
    # The line under their name and the description under that.
    title: str | None = Field(default=None, max_length=120)
    about: str | None = Field(default=None, max_length=2000)
    goals: str | None = Field(default=None, max_length=8000)
    notes: str | None = Field(default=None, max_length=20_000)
    notify_slack_task_done: bool | None = None
    # Their GitHub login ("" clears it): a review request naming it puts them on the linked task.
    github: str | None = Field(default=None, pattern=r"^@?[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})?$|^$")
    notify_slack_bot_messages: bool | None = None
    # Whether the Assistant drafts their daily update (on unless turned off).
    update_draft: bool | None = None
    # Bots whose messages to this person stay in Tico even while bot messages in Slack are on.
    slack_muted_bots: list[Slug] | None = Field(default=None, max_length=200)
    # Where this person sits on the org chart: another person's id, or "" for the top.
    reports_to: str | None = Field(default=None, max_length=80)
    # Someone who no longer works here is removed from the org chart. The
    # owner takes someone off the org chart; their history stays, they drop out of every list.
    left: bool | None = None
    # BotOps making the change a person asked for in chat, as that person (app.delegated_identity).
    on_behalf_of: ID | None = None

    @model_validator(mode="after")
    def has_change(self):
        if not (self.model_fields_set - {"on_behalf_of"}):
            raise ValueError("Provide title, about, goals, notes, notify_slack_task_done, notify_slack_bot_messages, update_draft, slack_muted_bots, github, reports_to or left")
        return self


class BotPlacement(Contract):
    runner_id: ID
    expected_generation: int = Field(ge=0)
    expected_revision: int = Field(ge=1)
    on_behalf_of: ID | None = None


class BotModel(Contract):
    model: ID
    effort: ID | None = None
    harness: ID | None = None
    expected_revision: int = Field(ge=1)


class BotFallbackChoice(Contract):
    harness: ID
    model: ID
    effort: ID | None = None


class BotFallback(Contract):
    fallback: BotFallbackChoice | None = None
    expected_revision: int = Field(ge=1)


class BotTurnsAtOnce(Contract):
    # Required, as for a computer: omission is refused; null is no limit.
    turns_at_once: int | None = Field(..., ge=1, le=256, strict=True,
        description="Most turns this bot runs at once across all computers. Null is no limit.")
    expected_revision: int


class BotManagesReports(Contract):
    """Whether this bot changes its direct reports' model, task model, name and description (backend/report_managers.py).
    A person who manages the bot sets it; the team rule `managers_manage_reports` is the owner's."""
    manages_reports: StrictBool
    expected_revision: int = Field(ge=1)


class BotKeepTasksGoing(Contract):
    keep_tasks_going: int | None = Field(None, ge=0, le=1000, strict=True,
        description="Automatic assigned-task starts: null is unlimited (default), 0 is off, N caps active work. Reviews have no cap.")
    expected_revision: int


class BotTaskChoice(Contract):
    """What a bot's tasks run on. Each part is optional on its own: no model is the bot's own model, so this can set
    only the task effort or only the task context length (backend/task_models.py)."""
    harness: ID | None = None
    model: ID | None = None
    effort: ID | None = None
    compact_at: Literal[150_000, 250_000, 400_000, 600_000, 1_000_000] | None = None


class BotTaskModel(Contract):
    task_model: BotTaskChoice | None = Field(None, description="Harness, model, effort and context length for the "
                                             "bot's tasks; a missing model is the bot's own, and null clears all of it")
    expected_revision: int = Field(ge=1)


class BotCompactAt(Contract):
    compact_at: Literal[150_000, 250_000, 400_000, 600_000, 1_000_000]
    expected_revision: int = Field(ge=1)


class BotWakePreset(Contract):
    wake_preset: Literal["current", "token_saver", "performance"] = Field(description="Token saver or Performance; legacy current is accepted as an alias for Performance.")
    expected_revision: int = Field(ge=1)


class BotTaskCondition(Contract):
    """One row of a filter tab (backend/bot_task_tabs.py FIELDS): a task must match every row. A list value is "any
    of"; days are whole numbers. bot_task_tabs.condition checks each value against the field."""
    field: Literal["status", "step", "type", "assigned_to", "asked_by", "waiting_on", "needs", "created_within",
                   "updated_within", "done_within", "due", "label"]
    value: int | str | list[str]


class BotTaskTab(Contract):
    """One tab over a bot's task list (backend/bot_task_tabs.py): an editable default filter, Done Recently, or a
    custom filter of rows that a task must all match. `types`, `states` and `waiting_on` are Task-list tabs 2/3's
    filter, still accepted and stored as rows."""
    name: str = Field(min_length=1, max_length=40)
    builtin: Literal["needs", "active", "blocked", "done"] | None = None
    show_count: bool = True
    conditions: list[BotTaskCondition] = Field(default_factory=list, max_length=20)
    types: list[str] = Field(default_factory=list, max_length=50)
    states: list[Literal["open", "doing", "waiting", "review", "ready", "done", "closed", "declined"]] = Field(
        default_factory=list, max_length=8)
    waiting_on: Literal["", "me", "person", "bot"] = ""


SIDEBAR_ITEMS = 60       # pages and cards on one bot's sidebar (backend/cards.py PLACE_MAX)


class BotSidebarWidget(Contract):
    """One widget of a bot's sidebar: hidden, and a height in pixels it grows to at most (none: its natural height;
    Tasks: 500)."""
    id: str = Field(pattern=r"^(tasks|updates|files|routines|(page|card):[A-Za-z0-9_.-]{1,100})$",
                    description="A built-in widget, or a pinned page (page:<file id>) or card (card:<id>)")
    hidden: bool = False
    height: int | None = Field(default=None, ge=80, le=2000)


class BotTaskTabs(Contract):
    # No cap on how many tabs (Task-list tabs spec): the request body limit bounds one write. Leaving widgets out keeps
    # the saved ones.
    tabs: list[BotTaskTab] | None
    widgets: list[BotSidebarWidget] | None = Field(default=None, max_length=4 + SIDEBAR_ITEMS)


class BotTransitionCreate(Contract):
    kind: Literal["model", "machine"]
    model: ID | None = None
    effort: ID | None = None
    harness: ID | None = None
    runner_id: ID | None = None
    expected_revision: int = Field(ge=1)
    expected_generation: int = Field(default=0, ge=0)
    # BotOps applying a person's own request, as for a definition change (backend/app.py).
    on_behalf_of: ID | None = None

    @model_validator(mode="after")
    def exact_target(self):
        if self.kind == "model" and (not self.model or self.runner_id):
            raise ValueError("A model transition requires a model and optional effort")
        if self.kind == "machine" and (not self.runner_id or self.model or self.effort or self.harness):
            raise ValueError("A machine transition requires only runner_id")
        return self


class BotTransitionApply(Contract):
    change_without_checkpoint: Literal[True]


class SettingsUndo(Contract):
    expected_revision: int = Field(ge=1)


class GatewayConnectionSave(Contract):
    """The Tico AI Gateway connection (backend/gateway_connection.py): a new key, or none to keep the saved one, and
    the Gateway's URL ('' for Tico's). `expected_revision` is the revision the editor read. `credentials` answers a
    move to another service while a key is saved (backend/destinations.py): "keep" sends the saved key to the new
    address, "clear" drops it in the same save."""
    key: SecretStr | None = None
    url: str = Field(default="", max_length=300)
    expected_revision: int = Field(default=0, ge=0)
    credentials: str = Field(default="", max_length=10)


class ProvidersUpdate(Contract):
    """The owner's AI provider choice. `expected_revision` is the revision the editor read."""
    enabled: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(max_length=10)
    runtime: str = Field(default="", max_length=40)
    model: str = Field(default="", max_length=100)
    expected_revision: int = Field(default=0, ge=0)
    # Run a limited bot's turn on another signed-in provider when it names no fallback; None keeps the saved value.
    auto_fallback: bool | None = None
    # What review and check subtasks run on, best first (`runtime` or `runtime:model`); None keeps the saved order,
    # [] follows the enabled providers' order.
    check_models: list[Annotated[str, Field(min_length=1, max_length=140)]] | None = Field(default=None, max_length=10)
    # The models choice (backend/models_preset.py): one of its four, '' for none; None keeps the saved one.
    preset: str | None = Field(default=None, max_length=40)
    # How hard the Tico AI Gateway thinks on a decision (low, medium, high); None keeps the saved one.
    decide_level: str | None = Field(default=None, max_length=10)
    # The model live-meeting replies run on: '' for the bot's own model; None keeps the saved one.
    meeting_model: str | None = Field(default=None, max_length=100)


class GatewaySearch(Contract):
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=5, ge=1, le=10)


class GatewayFetch(Contract):
    url: str = Field(min_length=1, max_length=2000, pattern=r"^https?://")
    max_tokens: int = Field(default=10000, ge=1000, le=50000)


class GatewayImage(Contract):
    prompt: str = Field(min_length=1, max_length=4000)
    category: Literal["storyboard", "photo", "character", "graphic"]
    images: list[str] = Field(default_factory=list, max_length=4)       # base64 data URLs, checked by the Gateway
    size: str | None = Field(default=None, max_length=20)
    count: int = Field(default=1, ge=1, le=4)


class SystemUpdate(Contract):
    version: str = Field(min_length=1, max_length=64)


class UsageCount(Contract):
    enabled: bool


class StrategyBotChecks(Contract):
    on: bool


class StrategyBotLevel(Contract):
    level: Literal[1, 2, 3]


# Several bots' asks waiting on one person, made one Strategy Bot item (backend/strategy_merge.py).
class StrategyBotMerge(Contract):
    person: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=1, max_length=4000)
    tasks: list[str] = Field(min_length=2, max_length=10)


# One change in a decision the Strategy Bot makes alone at level 3 (backend/strategy_acts.py).
class StrategyBotChange(Contract):
    do: Literal["create", "reorder", "reassign", "close"]
    task_id: str | None = Field(default=None, max_length=100)
    owner: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=8000)
    note: str | None = Field(default=None, max_length=1000)


class StrategyBotDecision(Contract):
    reason: str = Field(min_length=1, max_length=1000)
    goal_id: str | None = Field(default=None, max_length=100)
    changes: list[StrategyBotChange] = Field(min_length=1, max_length=10)


class UsageCountNotice(Contract):
    state: Literal["shown", "dismissed"]


class AccessPersonAdd(Contract):
    name: str = Field(default="", max_length=120)
    email: str = Field(min_length=3, max_length=320)
    title: str = Field(default="", max_length=120)
    team: str = Field(default="", max_length=80)
    reports_to: str = Field(default="", max_length=80)
    # BotOps adding the person the requester asked for: "turn" or the id of their message to it. A
    # Confirm card comes back instead of a person until the requester clicks it.
    on_behalf_of: ID | None = None


class AccessPersonEdit(Contract):
    """The owner's edits to one person. `left: false` brings back someone marked as left."""
    name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    title: str | None = Field(default=None, max_length=120)
    team: str | None = Field(default=None, max_length=80)
    left: bool | None = None
    sign_in: bool | None = None             # off keeps them on the roster but refuses their sign-in and tokens
    bot_admin: bool | None = None           # the old name for role: admin
    # Owners set roles; owners and admins set what a member may do (docs/permissions.md).
    role: Literal["admin", "member"] | None = None
    create_bots: bool | None = None
    add_people: bool | Literal["default"] | None = None
    # Who this person's message bot is ("" takes it away), and the address it reads for them when that is not
    # their sign-in email (the Google Workspace may be on another domain). Together they are what lets the bot's
    # runs ask their computer for a mail token: only owners and admins set them (docs/mail.md).
    inbox_bot: str | None = Field(default=None, max_length=80)
    mailbox: str | None = Field(default=None, max_length=320)
    # BotOps making the change a person asked for in chat (a Confirm card for the risky ones).
    on_behalf_of: ID | None = None


class AccessLimits(Contract):
    """How many active bots one member may have."""
    member_bot_limit: int = Field(ge=0, le=1000)
    on_behalf_of: ID | None = None


class AccessRules(Contract):
    """Team rules the owner tightens (backend/team_rules.py); only what is sent changes."""
    assistant_direct: bool | None = None
    assistant_approvals: bool | None = None
    botops_direct: bool | None = None
    botops_manages_bots: bool | None = None
    admin_credentials: bool | None = None
    members_store_credentials: bool | None = None
    admin_sql: bool | None = None
    member_tokens: bool | None = None
    health_monitor_reports: bool | None = None
    bots_go_ahead: bool | None = None
    support_access: bool | None = None
    stale_review: bool | None = None
    topic_fresh: bool | None = None
    stall_triage: bool | None = None
    managers_manage_reports: bool | None = None
    org_rule_checks: bool | None = None
    go_ahead_days: Annotated[int, Field(strict=True, ge=1, le=14)] | None = None
    report_creates_per_week: Annotated[int, Field(strict=True, ge=0, le=50)] | None = None
    report_archives_per_week: Annotated[int, Field(strict=True, ge=0, le=50)] | None = None


class AccessAllowUpdate(Contract):
    """Who may join by signing in. `expected_revision` is the revision the editor read."""
    allowed: list[str] = Field(max_length=500)
    allowed_domains: list[str] = Field(max_length=100)
    expected_revision: int = Field(default=0, ge=0)


class OwnerTransfer(Contract):
    person: ID
    previous_owner_bot_admin: bool = False
    expected_revision: int = Field(default=0, ge=0)
    # The client only sends this after its confirm dialog.
    confirm: Literal[True]


class OwnerEmailChange(Contract):
    """The owner's own new sign-in address, typed twice (backend/access.py change_owner_email)."""
    email: str = Field(max_length=320)
    confirm_email: str = Field(max_length=320)
    expected_revision: int = Field(default=0, ge=0)
    # The client only sends this after its confirm dialog.
    confirm: Literal[True]


# The org builder's departments (templates/groups.yaml, backend/recruit_rank.py DEPARTMENT_IDS).
Department = Literal["sales", "marketing", "support", "finance", "operations", "legal", "hr", "product", "engineering"]


class OnboardingNames(Contract):
    """What this company calls itself, its app, and the assistant people talk to."""
    company_name: str = Field(default="", max_length=100)
    app_name: str = Field(default="", max_length=100)
    assistant_name: str = Field(default="", max_length=100)
    # The owner's own name, saved on their roster entry; blank leaves it as it is.
    owner_name: str = Field(default="", max_length=100)
    # The team's email domain, for a team whose owner uses public mail: members may add coworkers at it.
    team_domain: str = Field(default="", max_length=100)
    # The team's website, from setup's first step: the research job reads it and the Librarian's market task names it.
    website: str = Field(default="", max_length=300)


class OnboardingAnswers(Contract):
    """The interview behind the recommendations. Free text is shown to a person, never parsed."""
    what_we_do: str = Field(default="", max_length=2000)
    customers: Literal["businesses", "consumers", "both", ""] = ""
    team_size: str = Field(default="", max_length=40)
    work_arrives: list[WorkArrival] = Field(default_factory=list, max_length=8)
    repetitive_work: str = Field(default="", max_length=2000)
    # Setup's first step: what the team is building, in the owner's one line, or a new idea in a few sentences.
    building: str = Field(default="", max_length=4000)
    # Accepted from older clients, discarded rather than stored or enforced.
    never_without_person: list[str] = Field(default_factory=list, exclude=True)
    # The wizard no longer asks `pains`, `pains_text` or `tools`; they are still accepted, kept as sent
    # and never read.
    pains: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(default_factory=list, max_length=8)
    pains_text: str = Field(default="", max_length=1000)
    tools: list[Literal["mail", "chat", "crm", "github", "meetings", "docs"]] = Field(
        default_factory=list, max_length=6)
    software_product: Literal["yes", "no", ""] = ""
    # The org builder: the departments chosen, in order, and the one-line answer to each department's question.
    departments: list[Department] = Field(default_factory=list, max_length=9)
    briefings: dict[Department, Annotated[str, Field(max_length=500)]] = Field(default_factory=dict, max_length=9)


class OnboardingSelection(Contract):
    """One bot the person chose: which template builds it, and what they named and told it."""
    template: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=100)
    instructions: str = Field(default="", max_length=INSTRUCTIONS_MAX)
    # Who it reports to: `human:<id>` or a bot slug. Empty is the company owner.
    reports_to: str = Field(default="", max_length=120)


class OnboardingDraft(Contract):
    names: OnboardingNames = Field(default_factory=OnboardingNames)
    answers: OnboardingAnswers = Field(default_factory=OnboardingAnswers)
    selected: dict[Slug, OnboardingSelection] = Field(default_factory=dict, max_length=300)   # the chart's 300-seat guard
    # Setup's business type (backend/sidebar.py BUSINESS_TYPES, checked there); left out, the answer stays as it is.
    business_type: str | None = Field(default=None, max_length=40)


class OnboardingCompanyFile(Contract):
    company_yaml: str = Field(min_length=1, max_length=524288)


class OnboardingCompanyImport(OnboardingCompanyFile):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class OnboardingInvite(Contract):
    """The email for a person the company file from setup names; Invite adds them and sends the invite. Empty once
    they are on the roster: Invite then sends to the address they were added with."""
    email: str = Field(default="", max_length=320)


class Recruit(Contract):
    """One department's answer in the org builder, asking which bots to suggest. `share` is the person's own toggle on
    the card ("Suggestions from Tico HQ"); the server sends the answer only when it is on and the install allows it."""
    department: Department
    briefing: str = Field(default="", max_length=500)
    share: bool = False


class Claim(Contract):
    # Opt in only after the heartbeat advertises this protocol; old workers keep their delivery leases.
    start_places: bool = False
    # Bots the runner takes no work for at all (maintenance, or a runner without lanes that is busy).
    busy_bots: list[ID] | None = Field(default=None, max_length=32)
    # Lanes the runner is already running, each "bot|lane" (clients/sessions.py lane), when it runs lanes.
    busy_lanes: list[Annotated[str, Field(min_length=3, max_length=256)]] | None = Field(default=None, max_length=256)
    bot: ID | None = None
    # The runner puts next-run tasks in its prompt. One that does not say so is never handed
    # any, so they wait for it rather than being marked carried and never read.
    next_run: bool = False


class LoadSnapshot(Contract):
    at: float = Field(ge=0, allow_inf_nan=False)
    cpu_pct: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    mem_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    running_turns: int = Field(ge=0, le=10_000)


class StartPlace(Contract):
    # The reasons runner-rs reports while a claimed turn gives its computer place back (execute.rs wait_before_start).
    waiting: Literal["", "shared login", "pre-start requirements", "Docker turn user"] = ""


class Started(Contract):
    thread_id: ID
    load_snapshot: LoadSnapshot | None = None


class SourceContinuation(Contract):
    # A source-stopped side session belongs to this job, never the primary conversation session.
    thread_id: str = Field(min_length=1, max_length=1000)
    runtime: str = Field(min_length=1, max_length=80)
    harness: str = Field(min_length=1, max_length=80)
    model: str = Field(default="", max_length=200)
    reasoning_effort: str = Field(default="", max_length=80)
    profile: str = Field(default="", max_length=80)
    epoch: tuple[str, int] | None = None



class AttemptThread(Contract):
    thread_id: ID
    source_pending: bool = False
    source_continuation: SourceContinuation | None = None


class AttemptStderr(Contract):
    tail: str = Field(max_length=65536)


class Event(Contract):
    seq: int = Field(ge=1)
    kind: Literal["delta", "message", "tokens", "status", "error", "tool", "diagnostic", "goal"]
    payload: dict


class EventBatch(Contract):
    events: list[Event] = Field(min_length=1, max_length=100)


class BackgroundCommand(Contract):
    description: str = Field(default="", max_length=300)
    exit: int | None = Field(default=None, ge=-255, le=255)
    killed: bool = False
    ci_watch: dict[str, int] | None = None
    tail: str = Field(default="", max_length=2000)


class BackgroundDone(Contract):
    """The commands a Claude turn left running when it ended on its wait, all ended (runner-rs/src/afterwatch.rs)."""
    commands: list[BackgroundCommand] = Field(min_length=1, max_length=20)


class StreamCapture(Contract):
    """A run's raw provider stream, one JSON record per line (runner-rs/src/capture.rs)."""
    provider: str = Field(min_length=1, max_length=40)
    frames: str = Field(min_length=1, max_length=5_200_000)
    truncated: bool = False
    dropped_bytes: int = Field(default=0, ge=0, le=10**12)


class AuthRejected(Contract):
    runtime: str = Field(max_length=100)
    reason: str = Field(default="", max_length=300)


RequestTokens = Annotated[int, Field(ge=0, le=10**9)]
# A request's three counts, and from a runner that can tell, whether it was the bot's own request (1) or a subagent's (0).
RequestUsage = tuple[RequestTokens, RequestTokens, RequestTokens] | tuple[RequestTokens, RequestTokens, RequestTokens,
                                                                          Literal[0, 1]]


class RunUsagePart(Contract):
    """What a run spent, as its runner counted it: uncached input, cached input and output tokens.
    `requests` is the same three per model request, sent when the runner saw every request, so a model whose
    long prompts cost more (providers.LONG_PROMPT) is priced request by request. A fourth count, when the runner
    sends one, marks the bot's own requests (1) from its subagents' (0): the newest of its own is the session's
    context size (`turns.prompt_tokens`)."""
    input_tokens: int = Field(default=0, ge=0, le=10**12)
    cached_tokens: int = Field(default=0, ge=0, le=10**12)
    output_tokens: int = Field(default=0, ge=0, le=10**12)
    requests: list[RequestUsage] | None = Field(default=None, max_length=2000)
    model: str = Field(default="", max_length=120)
    runtime: str = Field(default="", max_length=60)
    profile_used: str | None = Field(default=None, max_length=80)
    harness: str = Field(default="", max_length=60)
    effort: str = Field(default="", max_length=60)
    billing: Literal["api", "subscription"] = "api"   # `subscription`: a ChatGPT or Claude sign-in, not a key


class RunUsage(RunUsagePart):
    segments: list[RunUsagePart] = Field(default_factory=list, max_length=2)


class UsageLimit(Contract):
    per_30m_usd: float | None = None
    hourly_usd: float | None = None
    daily_usd: float | None = None
    weekly_usd: float | None = None
    monthly_usd: float | None = None


class UsageDefault(UsageLimit):
    count_subscription: bool = True


class SubscriptionUnavailable(Contract):
    profile: str = Field(min_length=1, max_length=80)
    runtime: str = Field(max_length=80)
    problem: str = Field(min_length=1, max_length=500)


class RunPush(Contract):
    path: str = Field(min_length=1, max_length=1000)  # the worktree, as its task link records it
    count: int = Field(ge=1, le=1000)


class RunRotation(Contract):
    reason: Literal["tokens", "turns"]  # which of the bot's limits the old session reached (runner-rs/src/rotate.rs)
    tokens: int = Field(ge=0)  # input tokens of the old session's newest turn
    turns: int = Field(ge=0)   # its turns on that computer
    limit: int = Field(ge=0)   # the limit it reached, in the unit of `reason`


class RunSession(Contract):
    """Whether the run continued its provider session or started a new one, and why (runner-rs/src/sessions.rs).
    `why` is one of backend/run_triggers.py SESSION_WHY for a runner of this release; a newer runner may send another,
    short one, which is kept as it is."""
    kind: Literal["resumed", "new"]
    why: str | None = Field(default=None, max_length=60)
    detail: str | None = Field(default=None, max_length=500)




class Completion(Contract):
    load_snapshot: LoadSnapshot | None = None
    subscription_unavailable: SubscriptionUnavailable | None = None
    profile_used: str | None = Field(default=None, max_length=80)
    outcome: Literal["completed", "failed", "interrupted"]
    text: str = Field(default="", max_length=200_000)
    last_seq: int = Field(ge=0)
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    limited: bool = False   # the local runtime refused the turn on a subscription usage limit
    # When the provider said that limit lifts (its own reset time); work resumes then instead of after a default wait.
    limit_resets_at: str | None = Field(default=None, max_length=40)
    retryable: bool = False  # the runtime could not renew its sign-in; the turn never started
    # The turn gave a shared one-turn login to a waiting turn at its hold cap (runner-rs/src/login_turns.rs): it stopped
    # at a safe point and its job goes back in the queue to continue, neither answered nor held for review.
    yielded: bool = False
    # The model was at capacity after the turn acted, and the runner kept its thread: the job continues that thread
    # after the capacity hold instead of being held for review (backend/execution.py capacity_continuation).
    continues: bool = False
    source_continuation: SourceContinuation | None = None
    fallback: str | None = Field(default=None, max_length=80)  # harness that actually ran the turn
    primary_limited: bool = False  # the bot's own runtime hit its usage limit and the company's default `fallback` ran the turn
    primary_held: bool = False  # the claim held the bot's own login (config `primary_held`): the runner never tried it
    auth_rejected: AuthRejected | None = None  # the provider refused this computer's key or sign-in
    usage: RunUsage | None = None  # the run's token counts and the model that ran it
    # Inputs this run took from `attempts/{aid}/inputs` but never gave the runtime (the turn ended between
    # the poll and the steer). They go back to the queue whatever the outcome, not settled with the run.
    undelivered: list[ID] = Field(default_factory=list, max_length=100)
    # Git pushes the run made from its task's worktrees (runner-rs/src/worktrees.rs pushes): writes Tico sees no
    # other way. Counted in its `writes` (backend/run_triggers.py).
    pushes: list[RunPush] = Field(default_factory=list, max_length=50)
    # The run started a fresh provider session because the bot's last one had grown past its limit (token saving).
    # Older runners only: sessions are not rotated for size any more, and a current runner sends `session` instead.
    rotated: RunRotation | None = None
    # Whether the run resumed the thread's session or started a new one, and why (feature `run_session`).
    session: RunSession | None = None


class Retry(Contract):
    acknowledge_uncertain_effects: bool


class ReconcileJob(Contract):
    attempt_id: ID
    decision: Literal["resume", "dismiss"]
    note: str = Field(min_length=20, max_length=4000)
    acknowledge_uncertain_effects: bool


class Adopt(Contract):
    bot: ID
    expected_generation: int = Field(ge=0)


class BotControl(Contract):
    """`scope` limits the bot's claims to `task_id` and its direct subtasks, leaving the rest of its queue untouched;
    `unscope` lifts that limit. Neither pauses, resumes or starts anything."""
    action: Literal["drain", "resume", "pause", "scope", "unscope"]
    expected_revision: int = Field(ge=1)
    task_id: str | None = Field(None, max_length=200)


class BotPlace(Contract):
    """Put a bot on a computer: the one named (its label or id), or the best one that takes it."""
    computer: str = Field(default="", max_length=200)


class BotGoLive(Contract):
    """Place it if it has no computer, activate it, and start its setup with the person."""
    computer: str = Field(default="", max_length=200)
    setup: bool = True


class TaskSnooze(Contract):
    # An ISO-8601 date (the start of that day in the team's zone) or a date-time (backend/task_snooze.py).
    until: str = Field(min_length=10, max_length=40)
    # What to check when the snooze ends, put in the owner's wake (at most 500 characters once stripped).
    note: str | None = Field(default=None, max_length=2000)


class TaskQaWaiver(Contract):
    # A person waives a Dev ticket's QA step (its flow's check is qa) for one exact pull request head, with a reason.
    # Never a QA verdict; CI, both reviews and release approval stay required.
    head: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    reason: str = Field(min_length=1, max_length=2000)
    step: str | None = Field(default=None, max_length=200)    # the step to waive; only the ticket's QA step is accepted


class TaskDefer(Contract):
    # Exactly one (backend/quiet_work.py defer): a task to wait for, a time, the next run, or "handled".
    after: str | None = Field(default=None, min_length=1, max_length=80)
    at: str | None = Field(default=None, min_length=10, max_length=40)
    next_run: bool = False
    handled: bool = False


class Empty(Contract):
    pass


class MemoryTidyPilot(Contract):
    bot: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    effort: Literal["low"] = "low"


class SystemRoutineUpdate(Contract):
    """One system routine's switch, schedule preset or daily time (backend/system_routines.py); only what is sent
    changes."""
    pilots: list[MemoryTidyPilot] | None = Field(default=None, max_length=3)
    on: bool | None = None
    preset: Literal["15m", "hourly", "4h", "daily", "business"] | None = None
    at: str | None = Field(default=None, max_length=5)


class ReleaseScheduleUpdate(Contract):
    """The team's release schedule (backend/release_flow.py); only what is sent changes. `bot` and `writer` name the
    Release Manager and Technical Writer ("" finds them by starter template)."""
    on: bool | None = None
    times: list[Annotated[str, Field(max_length=5)]] | None = Field(default=None, max_length=2)
    bot: str | None = Field(default=None, max_length=100)
    writer: str | None = Field(default=None, max_length=100)


class SettingsPlan(Contract):
    """Set one settings catalogue entry (backend/settings_catalog.py): the value, and the bot, computer or person it is
    for when the setting is set per one."""
    value: Any = None
    bot: str | None = Field(default=None, max_length=200)
    computer: str | None = Field(default=None, max_length=200)
    person: str | None = Field(default=None, max_length=200)
    id: str | None = Field(default=None, max_length=200)


class ReleaseNow(Contract):
    """Ask the Release Manager to release what is at Pending Release now; `note` goes in its task."""
    note: str | None = Field(default=None, max_length=1000)


class LimitRetry(Contract):
    pass


class StopTurn(Contract):
    attempt_id: ID | None = None   # the run the person saw; a newer one is not stopped by mistake
    redirect: ID | None = None     # the caller's queued message the next turn starts with (Stop and redirect)


class AssistantPage(Contract):
    path: Annotated[str, Field(max_length=300)]
    title: Annotated[str, Field(max_length=200)] = ""


class AssistantMessage(Contract):
    text: Annotated[str, Field(min_length=1, max_length=8000)]
    # Sent from the Updates chat (backend/updates_chat.py): the turn is handed the updates the person may read.
    place: Literal["updates"] | None = None
    # Sent from the Ask panel (ui/app/ask-panel.js): the page the person is looking at, handed to the turn as a hint.
    page: AssistantPage | None = None


class AssistantUpload(Contract):
    """A message with files in a person's Assistant chat (POST /api/v2/uploads/assistant/messages)."""
    text: Annotated[str, Field(min_length=1, max_length=8000)]
    # Each file's caption, in upload order.
    titles: list[Annotated[StrictStr, Field(max_length=200)]] = Field(default_factory=list, max_length=10)
    page: AssistantPage | None = None     # as AssistantMessage's, sent as JSON


class LibrarianResearch(Contract):
    """A person asks the Librarian to research and add to one page (docs/librarian.md#research)."""
    target: Annotated[str, Field(min_length=1, max_length=300)]     # a doc id, market/<page> or a market entity id
    section: Annotated[str, Field(max_length=300)] = ""             # a heading on it; empty is the whole page
    instructions: Annotated[str, Field(min_length=1, max_length=4000)]


class DocsAsk(Contract):
    question: Annotated[str, Field(min_length=1, max_length=4000)]
    conversation_id: Annotated[str | None, Field(max_length=100)] = None
    new_conversation: bool = False        # close the docs conversation and start a fresh one (no context)


class AssistantAction(Contract):
    """What the Assistant proposes: one operation on this API, run as the person only when they
    confirm it (backend/assistant.py)."""
    summary: Annotated[str, Field(min_length=1, max_length=300)]
    method: Literal["POST", "PUT", "PATCH", "DELETE"] = "POST"
    path: Annotated[str, Field(min_length=9, max_length=300)]
    body: dict = Field(default_factory=dict)


class InboxOnly(Contract):
    enabled: bool


class ScreenMode(Contract):
    mode: Literal["off", "view", "control"]


class PerformanceMode(Contract):
    mode: Literal["off", "on"]


class BrowserExitPolicy(Contract):
    """A computer's internet shared with bots (backend/browser_exit.py). Omitted bots or cap keep what is stored."""
    sharing: bool
    bots: list[Annotated[str, Field(min_length=1, max_length=128)]] | None = Field(default=None, max_length=500)
    daily_cap_bytes: Annotated[int, Field(strict=True, ge=100_000_000, le=10_000_000_000_000)] | None = None


class BotBrowserExit(Contract):
    """The computer a bot's browser reaches the web through; null is its own."""
    runner_id: Annotated[str, Field(min_length=1, max_length=128)] | None


class BrowserExitTunnel(Contract):
    bot: Annotated[str, Field(min_length=1, max_length=128)]
    host: Annotated[str, Field(min_length=1, max_length=253)]
    port: int


class _Sampled(BaseModel):
    """A newer runner's extra fields are ignored, never refused: samples are diagnostics, not a contract."""
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


Percent = Annotated[float, Field(ge=0, le=100_000, allow_inf_nan=False)]
Bytes = Annotated[int, Field(ge=0, le=1 << 60)]


class PerformanceDisk(_Sampled):
    volume: str = Field(default="", max_length=40)
    total_bytes: Bytes
    free_bytes: Bytes


class PerformanceProcess(_Sampled):
    """A process name (the executable's, never its arguments) and what every process by that name used."""
    name: str = Field(pattern=r"^[A-Za-z0-9._+-]{1,40}$")
    cpu: Percent = 0
    mem: Bytes = 0
    count: int = Field(default=1, ge=0, le=100_000)


class PerformanceSample(_Sampled):
    interval_s: Literal[30, 60] = 30  # old runners sample twice a minute
    at: float = Field(ge=0, allow_inf_nan=False)
    scope: Literal["host", "container"] = "host"
    # A container's memory is the host's when it has no memory limit; an older runner leaves this out.
    memory_scope: Literal["host", "container"] | None = None
    cpu: float = Field(ge=0, le=100, allow_inf_nan=False)
    cores: int = Field(default=1, ge=1, le=4096)
    load: list[Annotated[float, Field(ge=0, le=100_000, allow_inf_nan=False)]] = Field(default_factory=list, max_length=3)
    mem_total: Bytes | None = None
    mem_used: Bytes | None = None
    swap_total: Bytes | None = None
    swap_used: Bytes | None = None
    # Raw swap for the simple load reading; macOS omits diagnostic swap because its total grows with use.
    load_swap_total: Bytes | None = None
    load_swap_used: Bytes | None = None
    disk: list[PerformanceDisk] = Field(default_factory=list, max_length=4)
    active: int = Field(default=0, ge=0, le=10_000)
    procs: list[PerformanceProcess] = Field(default_factory=list, max_length=16)


class PerformanceBatch(_Sampled):
    samples: list[PerformanceSample] = Field(min_length=1, max_length=40)


class ScreenSessionStart(Contract):
    control: StrictBool = False


class LoginStart(Contract):
    # Which runtimes the browser can sign in is the manifests' call (backend/model_login.py RUNTIMES).
    runtime: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=40)]
    profile: Annotated[str, Field(pattern=r"^(?:[a-z0-9]+(?:-[a-z0-9]+)*)?$", max_length=80)] = ""
    # Sign in to another account in place: the old login keeps working until the new one has finished.
    switch: bool = False
    # Switch type: "subscription" moves the computer's own login off its API key, once the sign-in has worked.
    to: Literal["", "subscription"] = ""


class LogoutStart(Contract):
    runtime: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=40)]
    profile: Annotated[str, Field(pattern=r"^(?:[a-z0-9]+(?:-[a-z0-9]+)*)?$", max_length=80)] = ""


class RemovalStart(Contract):
    runtime: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=40)]
    profile: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)]


class SharedKeyUse(Contract):
    """Use the shared key again on a signed-out login; with `credential`, Add connection > API key > "Use a shared
    key": that shared key, attached to this computer (a credential administrator's change)."""
    runtime: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=40)]
    credential: str = Field(default="", max_length=100)


class KeyClear(Contract):
    """Clear key on a computer; "all" clears the company's shared key on every computer it serves."""
    runtime: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=40)]
    scope: Literal["this", "all"] = "this"


class KeyReplace(Contract):
    """Replace key (or Add key) on a computer. The key is sealed in the vault; nothing sends it back (backend/computer_keys.py)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    runtime: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=40)]
    secret: SecretStr = Field(max_length=4000)
    env: str = Field(default="", max_length=100, pattern=r"^$|^[A-Z_][A-Z0-9_]*$")
    scope: Literal["this", "all"] = "this"
    # Add connection > API key > "Shared with: [computers]": the other computers it serves (this one always does).
    share: list[Annotated[str, Field(max_length=100)]] = Field(default_factory=list, max_length=200)
    # Switch to API key: the computer's own login moves off its subscription onto this key once the key works there.
    switch: bool = False


class LoginCode(Contract):
    code: Annotated[str, Field(min_length=1, max_length=700)]


class LoginReport(Contract):
    state: Literal["starting", "waiting", "finishing", "signed_in", "signed_out", "done", "failed"]
    url: str = Field(default="", max_length=2100)
    code: str = Field(default="", max_length=40)
    lines: list[str] = Field(default_factory=list, max_length=40)
    message: str = Field(default="", max_length=500)
    code_taken: bool = False


class HarnessAction(Contract):
    harness: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=60)]
    action: Literal["update", "pin", "unpin"]


class HarnessChoice(Contract):
    harness: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=60)]
    enabled: StrictBool


class HarnessActionReport(Contract):
    state: Literal["running", "done", "failed"]
    message: str = Field(default="", max_length=500)


class UpdateKpi(Contract):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    value: Annotated[str, Field(min_length=1, max_length=40)] | float
    unit: Annotated[str, Field(max_length=20)] | None = None
    series: list[float] | None = Field(default=None, max_length=60)
    note: Annotated[str, Field(max_length=300)] | None = None


class UpdateSlides(Contract):
    """A week in review's slides; the word limits are the linter's, so a long one is refused with how to fix it."""
    goal: Annotated[str, Field(max_length=1000)] = ""
    kpis: list[UpdateKpi] = Field(default_factory=list, max_length=20)
    done: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=20)
    focus: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=20)
    blockers: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=20)


class UpdatePost(Contract):
    body: Annotated[str, Field(max_length=20_000)] = ""                # a daily's bullets
    headline: Annotated[str, Field(max_length=300)] | None = None     # ignored: an update is its bullets
    kind: Literal["daily", "weekly"] | None = None
    slides: UpdateSlides | None = None                                 # a week in review
    day: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None   # only a day Tico asked to redo
    replace_draft: bool = False                                        # a person's own words over their Assistant draft


class UpdateRedo(Contract):
    kind: Literal["daily", "weekly"] = "weekly"
    day: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    bots: list[Annotated[str, Field(max_length=80)]] | None = Field(default=None, max_length=200)
    managers: bool = False                                             # also the bots above `bots`


class UpdateRead(Contract):
    ids: list[ID] = Field(default_factory=list, max_length=500)
    all: bool = False
    read: bool = True


class UpdateArchive(Contract):
    ids: list[ID] = Field(min_length=1, max_length=500)
    archived: bool = True


class UpdateReply(Contract):
    text: Text


class UpdateSettings(Contract):
    daily: bool | None = None
    weekly: bool | None = None


class PersonalTokenCreate(Contract):
    """A personal API token (backend/personal_tokens.py): a label to tell it apart in the list
    and how long it lives; 90 days unless asked, never more than a year."""
    label: str = Field(min_length=1, max_length=80)
    expires_in_days: int = Field(default=90, ge=1, le=365)


class ServiceKeyCreate(Contract):
    """A service key (backend/service_keys.py): the label names the system that holds it, on every
    task it files. `tasks` files tasks; `update` checks for, starts and follows an update of this install."""
    label: str = Field(min_length=1, max_length=80)
    scope: Literal["tasks", "update"] = "tasks"


class InboundTask(Contract):
    """What one piece of another system's work should look like now (POST /api/v2/inbound/tasks).
    `owner`, `title` and `body` are needed only when the call files the task; anything left out
    stays as it is."""
    key: str = Field(min_length=1, max_length=200)      # the other system's own id for the work
    owner: ID | None = None                             # human:<id>, bot:<slug> or a person's email
    title: str | None = Field(default=None, min_length=1, max_length=300)
    body: Text | None = None
    type: ID | None = None
    step: str | None = Field(default=None, max_length=200)
    labels: list[str] | None = Field(default=None, max_length=20)
    links: list[str] = Field(default_factory=list, max_length=20)
    due: str | None = None
    close: bool = False
    note: str | None = Field(default=None, max_length=200_000)


class RuleCreate(Contract):
    """A wake rule (backend/rules.py): in this scope, when this event happens (and `when` holds), do `then`."""
    scope_kind: Literal["team", "type", "department", "bot", "task"]
    scope_id: str = Field(default="", max_length=120)
    event: str = Field(min_length=1, max_length=60)
    then: Literal["ignore", "note", "batch", "wake", "ask", "action"]
    when: dict | None = None
    params: dict | None = None
    on: bool | None = None
    position: int | None = Field(default=None, ge=0, le=100_000)
    note: str | None = Field(default=None, max_length=500)


class RuleUpdate(Contract):
    expected_revision: int = Field(ge=1)
    event: str | None = Field(default=None, min_length=1, max_length=60)
    then: Literal["ignore", "note", "batch", "wake", "ask", "action"] | None = None
    when: dict | None = None
    params: dict | None = None
    on: bool | None = None
    position: int | None = Field(default=None, ge=0, le=100_000)
    note: str | None = Field(default=None, max_length=500)


class RuleDelete(Contract):
    expected_revision: int | None = Field(default=None, ge=1)


class RuleSuggestionAct(Contract):
    pass


class TaskRuleUpdate(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    department: str | None = Field(default=None, min_length=1, max_length=80)
    triggers: list[dict] | None = Field(default=None, min_length=1, max_length=8)
    actions: list[str] | None = Field(default=None, min_length=1, max_length=8)
    on: bool | None = None
    preset: Literal["15m", "hourly", "4h", "daily"] | None = None
    at: str | None = Field(default=None, max_length=5)


# ----------------------------------------------------------------------------- brand, go-to-market, growth
# The brand and GTM pages (backend/brand_gtm.py): one body per version, each section optional. The draft is written a
# section at a time (null removes one) and checked whole against these shapes.
S80 = Annotated[str, Field(max_length=80)]
S300 = Annotated[str, Field(max_length=300)]
S1000 = Annotated[str, Field(max_length=1000)]


class BrandVoice(Contract):
    trait: S80
    do: S300 = ""
    dont: S300 = ""


class BrandTone(Contract):
    situation: S80
    guide: S300 = ""


class BrandWordUse(Contract):
    word: S80
    not_: S300 = Field(default="", alias="not")
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True,
                              serialize_by_alias=True)


class BrandWordAvoid(Contract):
    word: S80
    why: S300 = ""
    level: Literal["warn", "block"] = "warn"


class BrandWords(Contract):
    use: list[BrandWordUse] = Field(default_factory=list, max_length=100)
    avoid: list[BrandWordAvoid] = Field(default_factory=list, max_length=200)


class BrandNaming(Contract):
    name: S80
    rule: S300 = ""
    wrong: list[S80] = Field(default_factory=list, max_length=20)


class BrandColor(Contract):
    name: S80
    hex: Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{3,8}$")]


class BrandVisual(Contract):
    colors: list[BrandColor] = Field(default_factory=list, max_length=30)
    fonts: list[S80] = Field(default_factory=list, max_length=10)
    logo_files: list[S300] = Field(default_factory=list, max_length=20)
    imagery: S1000 = ""


class BrandClaim(Contract):
    claim: S300
    proof: S1000 = ""
    check: S300 = ""
    status: Literal["proven", "due"] = "proven"


class BrandClaims(Contract):
    allowed: list[BrandClaim] = Field(default_factory=list, max_length=100)
    never: list[S300] = Field(default_factory=list, max_length=100)


class BrandExample(Contract):
    text: S1000
    verdict: Literal["on", "off"]
    why: S300 = ""


class BrandBody(Contract):
    name: Annotated[str, Field(max_length=60)] | None = None
    positioning: Annotated[str, Field(max_length=120)] | None = None
    voice: list[BrandVoice] | None = Field(default=None, max_length=12)
    tone: list[BrandTone] | None = Field(default=None, max_length=20)
    words: BrandWords | None = None
    naming: list[BrandNaming] | None = Field(default=None, max_length=50)
    visual: BrandVisual | None = None
    claims: BrandClaims | None = None
    examples: list[BrandExample] | None = Field(default=None, max_length=30)


Motion = Literal["self_serve", "sales_led", "partner", "marketplace", "local", "community", "ecommerce"]


class GtmMotion(Contract):
    primary: Motion
    secondary: Motion | None = None


class GtmBuyer(Contract):
    who_buys: S300 = ""
    who_uses: S300 = ""
    deal_size: S80 = ""
    cycle: S80 = ""
    objections: list[S300] = Field(default_factory=list, max_length=30)


class GtmWish(Contract):
    text: S300
    scope: S80 = ""
    level: Literal["block", "warn", "setting"] = "warn"


class GtmBody(Contract):
    motion: GtmMotion | None = None
    buyer: GtmBuyer | None = None
    wishes: list[GtmWish] | None = Field(default=None, max_length=50)
    playbooks: list[S80] | None = Field(default=None, max_length=30)


class PageDraft(Contract):
    """`sections`: {name: value or null}; null removes the section. `revision` is the draft's as read; leave it out to
    write without the check."""
    sections: dict[str, Any] = Field(default_factory=dict)
    revision: int | None = None


class PagePublish(Contract):
    note: str = Field(default="", max_length=500)


class PageCheck(Contract):
    text: str = Field(max_length=200_000)


# Growth (backend/growth.py)
class GrowthChannelCreate(Contract):
    slug: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    track: Literal["performance", "brand"] = "performance"
    stage: Literal["idea", "testing", "scaling", "paused"] = "idea"
    owner_ref: str | None = Field(default=None, max_length=200)
    budget_month: float | None = Field(default=None, ge=0)
    source_id: str | None = Field(default=None, max_length=200)
    why: str = Field(default="", max_length=1000)
    guardrail_kpi_id: str | None = Field(default=None, max_length=200)
    guardrail_max: float | None = None
    sort: int = 0
    segments: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=20)


class GrowthChannelUpdate(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    track: Literal["performance", "brand"] | None = None
    stage: Literal["idea", "testing", "scaling", "paused"] | None = None
    owner_ref: str | None = Field(default=None, max_length=200)
    budget_month: float | None = Field(default=None, ge=0)
    source_id: str | None = Field(default=None, max_length=200)
    why: str | None = Field(default=None, max_length=1000)
    guardrail_kpi_id: str | None = Field(default=None, max_length=200)
    guardrail_max: float | None = None
    sort: int | None = None
    segments: list[Annotated[str, Field(max_length=200)]] | None = Field(default=None, max_length=20)


class GrowthSourceCreate(Contract):
    truth_for: Annotated[str, Field(min_length=1, max_length=80)]
    system: Annotated[str, Field(min_length=1, max_length=80)]
    reach_kind: Literal["mcp", "credential", "url", "person"]
    reach_ref: str = Field(default="", max_length=300)
    may: str = Field(default="", max_length=300)
    owner_ref: str | None = Field(default=None, max_length=200)


class GrowthSourceUpdate(Contract):
    truth_for: str | None = Field(default=None, min_length=1, max_length=80)
    system: str | None = Field(default=None, min_length=1, max_length=80)
    reach_kind: Literal["mcp", "credential", "url", "person"] | None = None
    reach_ref: str | None = Field(default=None, max_length=300)
    may: str | None = Field(default=None, max_length=300)
    owner_ref: str | None = Field(default=None, max_length=200)


IceScore = Annotated[int, Field(ge=1, le=10)]
IdeaSource = Literal["strategy", "competitor", "listening", "peec", "lost_test", "person", "other"]


class GrowthIdeaCreate(Contract):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    hypothesis: str = Field(default="", max_length=2000)
    prediction: str = Field(default="", max_length=1000)
    bet_id: str | None = Field(default=None, max_length=200)
    kpi_id: str | None = Field(default=None, max_length=200)
    channels: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=10)
    segments: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=20)
    ice_i: IceScore | None = None
    ice_c: IceScore | None = None
    ice_e: IceScore | None = None
    source_kind: IdeaSource | None = None
    source_ref: str | None = Field(default=None, max_length=500)
    follow_up_of: str | None = Field(default=None, max_length=200)
    assignee: str | None = Field(default=None, max_length=200)
    body: str = Field(default="", max_length=20_000)


class GrowthIdeaUpdate(Contract):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    hypothesis: str | None = Field(default=None, max_length=2000)
    prediction: str | None = Field(default=None, max_length=1000)
    bet_id: str | None = Field(default=None, max_length=200)
    kpi_id: str | None = Field(default=None, max_length=200)
    judged_kpi_id: str | None = Field(default=None, max_length=200)
    channels: list[Annotated[str, Field(max_length=40)]] | None = Field(default=None, max_length=10)
    segments: list[Annotated[str, Field(max_length=200)]] | None = Field(default=None, max_length=20)
    source_kind: IdeaSource | None = None
    source_ref: str | None = Field(default=None, max_length=500)
    test_from: str | None = Field(default=None, max_length=40)
    test_to: str | None = Field(default=None, max_length=40)
    change: str | None = Field(default=None, max_length=2000)
    stop_if: str | None = Field(default=None, max_length=1000)
    min_events: int | None = Field(default=None, ge=0)
    audience: str | None = Field(default=None, max_length=500)
    external_ref: str | None = Field(default=None, max_length=500)


class GrowthIdeaScore(Contract):
    i: IceScore
    c: IceScore
    e: IceScore


class GrowthIdeaStart(Contract):
    test_from: str | None = Field(default=None, max_length=40)
    test_to: Annotated[str, Field(min_length=1, max_length=40)]
    change: Annotated[str, Field(min_length=1, max_length=2000)]
    stop_if: Annotated[str, Field(min_length=1, max_length=1000)]
    min_events: int | None = Field(default=None, ge=0)
    judged_kpi_id: str | None = Field(default=None, max_length=200)
    audience: str | None = Field(default=None, max_length=500)


class GrowthIdeaVerdict(Contract):
    status: Literal["won", "lost", "inconclusive", "dropped", "paused"]
    result: str | None = Field(default=None, max_length=2000)
    learning: str | None = Field(default=None, max_length=2000)
    bet_effect: Literal["supports", "weakens", "none"] | None = None
    confidence: Literal["low", "medium", "high"] | None = None


class GrowthCohortSet(Contract):
    channel: str | None = Field(default=None, max_length=40)       # a channel slug; "unknown" or null is Unknown
    segment_id: str | None = Field(default=None, max_length=200)   # a growth segment or its market segment; null: not split
    month: Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
    spend: float | None = Field(default=None, ge=0)
    bot_cost: float | None = Field(default=None, ge=0)
    new_customers: int | None = Field(default=None, ge=0)
    kept_d30: float | None = Field(default=None, ge=0, le=100)
    kept_d90: float | None = Field(default=None, ge=0, le=100)
    kept_d180: float | None = Field(default=None, ge=0, le=100)
    revenue_month: float | None = Field(default=None, ge=0)
    margin_pct: float | None = Field(default=None, ge=0, le=100)
    quality: Literal["actual", "estimate"] | None = None
    sources: dict[Annotated[str, Field(max_length=40)], Annotated[str, Field(max_length=300)]] | None = None


SegmentStatus = Literal["serving", "testing", "next", "dropped"]


class GrowthSegmentCreate(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    segment_id: str | None = Field(default=None, min_length=1, max_length=200)
    status: SegmentStatus = "testing"
    motion: Motion | None = None
    who_buys: str = Field(default="", max_length=300)
    deal_size: str = Field(default="", max_length=80)
    why: str = Field(default="", max_length=1000)
    owner_ref: str | None = Field(default=None, max_length=200)
    sort: int = 0


class GrowthSegmentUpdate(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: SegmentStatus | None = None
    motion: Motion | None = None
    who_buys: str | None = Field(default=None, max_length=300)
    deal_size: str | None = Field(default=None, max_length=80)
    why: str | None = Field(default=None, max_length=1000)
    owner_ref: str | None = Field(default=None, max_length=200)
    sort: int | None = None


class GrowthSettingsUpdate(Contract):
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    revenue_month: float | None = Field(default=None, ge=0)
    margin_pct: float | None = Field(default=None, ge=0, le=100)
    verdict_win_pct: float | None = Field(default=None, ge=0, le=100)
    framework: str | None = Field(default=None, max_length=80)
