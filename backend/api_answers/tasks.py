# pyright: strict
"""The answers of the Tasks routes."""

from __future__ import annotations

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from pydantic import Field

from ..hubdb import LinkKind
from ..task_documents import NAMES as DOCUMENT_NAMES
from ._base import DateTime, answer
from .conversations import Message, MessageSource, ReviewAnswer, SentAs, TaskReviewAskView
from .pages import PageLinkView, PageWriter
from .risk import Matter
from .tags import Tag


# A task is a title and a chat: its request is the first message on its chat, on a full read only. `from` is a Python
# keyword, so this one is spelled as a call.
TaskRequest = TypedDict("TaskRequest", {"id": str, "from": str, "text": str, "at": str})
answer(TaskRequest)


@answer
class TaskIdName(TypedDict):
    id: str
    name: str


@answer
class TaskLate(TypedDict):
    since: str
    level: Literal["told", "person"]


@answer
class TaskStage(TypedDict):
    id: str
    name: str
    kind: Literal["work", "done", "closed"]
    by_hand: bool
    side: bool


@answer
class TaskBlocker(TypedDict):
    id: str | None
    number: int | None
    label: NotRequired[str | None]
    title: NotRequired[str | None]


@answer
class TaskBlocked(TypedDict):
    id: str
    number: int | None
    label: NotRequired[str | None]


@answer
class TaskInput(TypedDict):
    kind: Literal["pr", "page", "done", "missing", "signoff"]
    line: str
    stage: NotRequired[str]
    by: NotRequired[str | None]
    url: NotRequired[str | None]
    file_id: NotRequired[str]
    version: NotRequired[int]
    ref: NotRequired[str]
    answer: NotRequired[Literal["ok", "no"]]
    notes: NotRequired[str]
    task_id: NotRequired[str]


@answer
class TaskSnoozeView(TypedDict):
    until: str | None
    by: str
    why: str | None


@answer
class TaskValue(TypedDict):
    value: str | int
    type: str
    by: str
    at: str
    stage_id: str


@answer
class TaskFiled(TypedDict):
    id: str
    kind: str
    status: str
    title: str | None


@answer
class TaskCover(TypedDict):
    url: str
    width: int | None
    height: int | None


@answer
class TaskRelationView(TypedDict):
    id: str
    title: str
    status: str
    owner_id: str
    direction: str
    number: NotRequired[int | None]
    stage: NotRequired[str | None]
    since: NotRequired[str]
    """depends_on only: when the dependency was written"""


@answer
class TaskReconcile(TypedDict):
    who: str | None
    line: str


class TaskFields(TypedDict):
    """What every read of a task carries; Task and TaskDetailTask add stage_since, which only the task's own read
    always has. Not published: each answer names Task or TaskDetailTask."""

    private: NotRequired[bool]
    id: str
    title: str
    request: NotRequired[TaskRequest | None]
    requester_id: str
    owner_id: str | None
    assignee_id: NotRequired[str | None]
    status: Literal["open", "done", "closed"]
    created_at: str
    updated_at: str
    due_at: str | None
    version: int
    pipeline_id: str | None
    stage_id: str | None
    came_from: NotRequired[TaskIdName | None]
    """In a side stage, the last main work stage this task visited in this pipeline. Null when there is no origin. A brief list omits the field when it is null."""
    turn: TaskWait | None
    """The headline wait: whose turn it is"""
    status_line: NotRequired[Literal["starting", "up_next", "working", "needs_you", "paused"] | None]
    """The card's one status line, on every read and the live stream (task_reads.status_line): starting (its agent's computer is waking or starting the run), up_next (its agent's turn, no run yet), working (a run going now), needs_you (a person's turn: `turn.who`), paused (its agent cannot run: `turn.line` says why, `turn.href` where it is fixed); null when none applies"""
    late: TaskLate | None
    reconcile: NotRequired[TaskReconcile]
    """Paid shots of unknown outcome it waits on a person to reconcile, apart from its turn and status line: who and "Reconcile N shots"; only when there are any"""
    stuck: NotRequired[bool]
    """Present and true when the never-stuck ladder brought it to a person (stuck or late)"""
    lane: str
    labels: list[str]
    acceptance_criteria: NotRequired[list[str]]
    occurrence: NotRequired[str | None]
    """A due-date routine's occurrence, "<automation_id>:<due_on>", on the task it opened: the due is its deadline, not a wake date."""
    owner_name: NotRequired[str]
    """Display name of owner (`owner_id` stays the actor id)"""
    human_id: NotRequired[str | None]
    """The task's Human: the person accountable for it whoever owns it, first on its ladder (asks that name nobody go to them). Set with `human` on create and update; left out of a brief row when none"""
    requester_name: NotRequired[str]
    """Display name of requester"""
    filer: NotRequired[str]
    """Who really filed it, when not its requester: a bot id (Agent Ops on a person's rights) or a built-in source key (check-health, postal-mail). Display only; absent otherwise"""
    snoozed: NotRequired[bool]
    """On a list row: whether the task is set aside. A blocked task is not (see blocked_by), and snoozed=only leaves it out"""
    pipeline: NotRequired[TaskIdName | None]
    """On the task's own read: the pipeline it is in"""
    label: NotRequired[str | None]
    """On the task's own read: its label, the letters it was made with and its number ("O561"); null when it has no number"""
    stage: NotRequired[TaskStage | None]
    """On the task's own read: the stage it is in"""
    pushed_past: NotRequired[bool]
    """On a list row: it entered its stage pushed past a requirement"""
    blocked_by: NotRequired[list[TaskBlocker]]
    """On a list row with a dependency: the open tasks it depends on ("Blocked by E304"), oldest first, each with its label (null with no number) and the start of its title; one the reader may not open has a null id, number, label and title. Absent when there are none"""
    blocks: NotRequired[list[TaskBlocked]]
    """On a list row: the open tasks that depend on it and the reader may open ("Blocks #307"). Absent when there are none"""
    visit_id: NotRequired[str | None]
    """Its open visit: on the task's own read, null when it has none; on a list row, only when its turn is an ask. With a requirement id it names that ask in Needs you (`ids=<visit>:<requirement>`)"""
    waits: NotRequired[list[TaskWait]]
    """On the task's own read: every wait, headline first"""
    to_leave: NotRequired[list[TaskRequirement]]
    """On the task's own read: every requirement of the stage and each added to this visit"""
    inputs: NotRequired[list[TaskInput]]
    """On the task's own read: what earlier stages handed on, oldest first (stage_inputs.of): its PRs, each earlier stage's page, what an owner said when it said done, a page that was never attached, and each OK or send back a person gave on work this task filed (`signoff`: answer, notes, the page version)"""
    next: NotRequired[TaskIdName | None]
    """On the task's own read: the stage a forward move goes to"""
    snooze: NotRequired[TaskSnoozeView | None]
    filed_by: NotRequired[str | None]
    """The task that filed this one"""
    values: NotRequired[dict[str, TaskValue]]
    """On the task's own read: the newest answer to each of its stages' named answers, by name, on any visit: {value, type, by, at, stage_id}; a number's value is a whole number"""
    filed: NotRequired[list[TaskFiled]]
    """The tasks this one filed; title is null for one the reader may not read"""
    cover: NotRequired[TaskCover | None]
    relations: NotRequired[dict[str, list[TaskRelationView]]]
    """Related tasks the reader may open, by kind (parent, related, duplicate_of, follow_up, after, depends_on): each {id, title, status, owner, direction}; an `after` or `depends_on` entry also has the other task's number and stage (after out: this task merges after it; in: it merges after this one; depends_on out: this task is blocked by it; in: it blocks this one). A task waits on after, in Merge, and on depends_on. Sub-tasks are in the task's children."""
    body: NotRequired[str]
    """Left out of a list asked for with brief=true, as is acceptance_criteria"""
    number: NotRequired[int | None]
    """The task's number, unique across the team"""
    readers: NotRequired[list[str]]
    """Who else can see a private task besides its requester and owner: people, bots and group:<id>s (a group by its members now). Empty on a company task. GET/PUT /api/v2/tasks/{id}/readers reads and changes it"""
    roles: NotRequired[dict[str, list[str]]]
    """Who is on the task, by role: each role a short name the team chooses (developer, reviewer, qa ...) with its actors in the order they were added; a role nobody holds is absent, and one actor may hold several roles. Tico gives roles no meaning or rights. Task update and create take the same shape to replace a role's people ([] clears one)"""
    tags: NotRequired[list[Tag]]
    """The task's tags. On a list row (GET /api/v2/tasks, a tag's tasks, the live stream) each is only its id, key, label, kind, owner and metadata, the same on every task it is on, and an attachment has no url (it is /api/v2/files/{id}); the task's own read and GET /api/v2/tags/{id} carry the whole tag"""
    documents: NotRequired[list[TaskDocument]]
    visibility: NotRequired[Literal["public", "private"]]
    public_title: NotRequired[str]
    public_summary: NotRequired[str]
    """Separate public text; omitted with brief=true"""
    size: NotRequired[float | None]
    requester_count: NotRequired[int]
    external_requesters: NotRequired[int]
    internal_requesters: NotRequired[int]
    important_requesters: NotRequired[int]
    matter: NotRequired[Matter]
    email_send: NotRequired[TaskEmailSend]
    """The newest email send that belongs to this task or whose OK sits on it, while it is open or for a day after it ended; absent when none. Its state changes reach open pages as this task's `tasks` events (kind `email_send`) on GET /api/v2/events"""


@answer
class TaskEmailSend(TypedDict):
    id: str
    state: Literal["waiting_approval", "pending", "dispatching", "sent", "failed", "canceled", "blocked", "unknown"]
    """pending: in its undo window until send_after; sent at finished_at; failed, canceled, blocked or unknown with detail"""
    send_after: str
    finished_at: str | None
    detail: str
    not_before: NotRequired[str | None]
    """The day a send past its undo window still waits for (its matter's not before), on its own case's row; else null"""


@answer
class Task(TaskFields):
    stage_since: NotRequired[DateTime | None]
    """On the task's own read, when it entered its current open visit, or null if it has none. On a list row, when it entered its stage, falling back to when it was filed."""


@answer
class ReconciledShot(TypedDict):
    key: str
    name: str


@answer
class TaskWait(TypedDict):
    state: Literal["snoozed", "person", "paused", "bot", "jev", "source", "tasks", "room", "pick", "by_hand"]
    who: str | None
    line: str
    requirement_id: str | None
    since: str | None
    until: str | None
    ask: bool
    href: NotRequired[str | None]
    """A paused bot's cause: the app page that shows or fixes it (#/settings/computers, #/settings/billing, #/tools/github)"""
    running: NotRequired[bool]
    """On a list row, on a bot's turn (state bot) only: a run is going now; false while it waits to try again"""
    share: NotRequired[list[str]]
    """A private task waits on these actors being let in (the next stage's owner and those it asks, or someone asked here, can't see it): move with to next and let_see set to exactly this list"""
    shots: NotRequired[list[ReconciledShot]]
    """Paid shots of unknown outcome it waits on its person to reconcile, each its key and full name: the keys go to POST /api/v2/tasks/{tid}/shots/reconcile"""


@answer
class TaskRequirementPagePart(TypedDict):
    file_id: str
    version: int
    ref: str


@answer
class TaskRequirementPage(TypedDict):
    file_id: str
    version: int
    ref: str
    page_version: NotRequired[str]
    pages: NotRequired[list[TaskRequirementPagePart]]


@answer
class TaskRequirementEarlier(TypedDict):
    file_id: NotRequired[str]
    version: NotRequired[int]
    ref: NotRequired[str]
    stage_id: NotRequired[str]
    stage: NotRequired[str]
    source: NotRequired[list[str]]
    removed: NotRequired[bool]
    label: NotRequired[str]
    checks: NotRequired[list[str]]


@answer
class TaskRequirementApprovalPart(TypedDict):
    file_id: str
    version: int


@answer
class TaskRequirementApproval(TypedDict):
    document: str
    visit_id: str
    file_id: str | None
    version: int | None
    unchecked: bool
    page_version: NotRequired[str]
    pages: NotRequired[list[TaskRequirementApprovalPart]]


@answer
class TaskRequirementAnswer(TypedDict):
    id: str
    value: str
    note: str | None
    by: str
    at: str


@answer
class TaskRequirement(TypedDict):
    requirement_id: str
    kind: str
    line: str
    met: bool
    standing: str
    who: str | None
    asked_by: str | None
    shows: list[str]
    page: NotRequired[TaskRequirementPage]
    earlier: NotRequired[TaskRequirementEarlier]
    checks: NotRequired[list[str]]
    approval: NotRequired[TaskRequirementApproval]
    answer: TaskRequirementAnswer | None
    may: list[Literal["done", "ok", "no", "give", "undo", "withdraw"]]
    """What this reader may do with it: a hint, the engine decides"""


@answer
class TaskLinkView(TypedDict):
    id: str
    task_id: str
    kind: LinkKind
    url: str
    """An http(s) URL; a session link's is `session:<reference>`, not a page"""
    title: NotRequired[str | None]
    state: NotRequired[str | None]
    """A pull request's (open, merged, closed, shipped) or a checkout's state"""
    added_by: NotRequired[str | None]
    created_at: str
    repo: NotRequired[str | None]
    number: NotRequired[int | None]
    branch: NotRequired[str | None]
    pr_merged_at: NotRequired[str | None]
    checks: NotRequired[str | None]
    """A pull request's checks: passing, failing, pending or cancelled"""
    mergeable: NotRequired[str | None]
    review_state: NotRequired[str | None]
    pending_comments: NotRequired[int | None]
    computer_id: NotRequired[str | None]
    path: NotRequired[str | None]
    detail: NotRequired[str | None]
    """JSON text of the link's own record (a session's thread, a checkout's setup). On the task's own read and its /links only"""
    source: NotRequired[str | None]
    """The outside item the link is (backend/item_sources), on the task's own read: its source (github, jira)"""
    item_type: NotRequired[str | None]
    item_key: NotRequired[str | None]
    connection_id: NotRequired[str | None]
    """The connection (a tool's id) the item came through; empty for none (GitHub); null when it is a tool the reader
    may not see"""
    is_primary: NotRequired[int]
    """1 on the one task an intake made for this item"""


@answer
class TaskListCounts(TypedDict):
    total: int
    """Every task the filters select, on all pages"""
    stage: dict[str, int]
    """By stage id: how many of them are in that stage. A stage with none is absent; a task in no stage counts in the total only"""


@answer
class TaskList(TypedDict):
    tasks: list[Task]
    next_offset: int | None
    next_after: NotRequired[str | None]
    """Opaque: pass as after for the next keyset page (search and filters kept); null at the end"""
    cursor: str
    """Opaque: pass as changed_after to read only what changed since"""
    gone: NotRequired[list[str]]
    """With changed_after: ids that changed and no longer match (deleted, no longer readable, or out of the filters)"""
    reset: NotRequired[bool]
    """With changed_after: the cursor is too old or no longer applies; read in full"""
    actors: NotRequired[dict[str, str]]
    """On reads: display names for every actor id in the answer, {"human:ana": "Ana Alvarez"}"""
    counts: NotRequired[TaskListCounts]
    """With counts=stage only: one grouped count of every task the same filters select, by the same reader's rules, whatever page this is"""


@answer
class TaskResult(TypedDict):
    task: Task
    private_readers: NotRequired[str]
    answer_id: NotRequired[str | None]


@answer
class TaskMergeResult(TypedDict):
    task: Task
    closed: Task
    note: str


@answer
class TaskMergeChecks(TypedDict):
    task: dict[str, Any]
    into: dict[str, Any]
    same_owner: bool
    subtask: str | None
    merge: str | None
    moves: dict[str, Any]


@answer
class OkPreview(TypedDict):
    requirement_id: str
    task_id: str
    subject: str
    content: Any
    """What the OK is for, exactly as it was asked"""
    shown_hash: str
    standing: Literal["waiting", "ok", "sent_back", "withdrawn", "used"]


@answer
class TaskRunChoice(TypedDict):
    model: str
    harness: str
    runtime: str
    reasoning_effort: str
    set_by: str | None
    set_at: str | None


@answer
class TaskRunModelResult(TypedDict):
    task: str
    run_model: TaskRunChoice | None
    readiness: dict[str, Any] | None
    warning: str


@answer
class TaskSkipped(TypedDict):
    id: str
    code: str
    reason: str


@answer
class TaskMoveResult(TypedDict):
    batch_id: str
    moved: list[str]
    moved_unseen: int
    skipped: list[TaskSkipped]


@answer
class TaskManyResult(TypedDict):
    batch_id: str
    changed: list[str]
    changed_unseen: int
    unchanged: int
    skipped: list[TaskSkipped]


@answer
class TaskBulkChanged(TypedDict):
    id: str
    number: int | None
    title: str


@answer
class TaskBulkSkipped(TypedDict):
    id: str
    number: int | None
    title: str
    code: str
    reason: str


@answer
class TaskBulkTasks(TypedDict):
    changed: list[TaskBulkChanged]
    skipped: list[TaskBulkSkipped]


@answer
class TaskBulkResult(TypedDict):
    dry_run: bool
    matched: int
    cap: int
    changed: int
    unchanged: int
    skipped: int
    tasks: TaskBulkTasks


@answer
class RanOnSegment(TypedDict):
    model: str | None
    provider: str | None
    runtime: str | None
    harness: str | None
    effort: str | None
    family: str | None


@answer
class RanOn(TypedDict):
    segments: list[RanOnSegment]
    task_model: dict[str, Any] | None
    """For a turn in a task's own session on a bot with a task model: `source` is task (it ran on the task model), fallback (the task model could not run, so the bot's own model did) or check (a check subtask kept its check model), and `note` says it in plain words"""
    fallback: str | None
    """The fallback harness that ran the turn, if any"""
    family: str | None
    """The maker of the model that did the work, from the model catalog, when every counted segment agrees; null is unknown (mixed, Cursor auto, OpenRouter, an unlisted model, nothing counted). Never the bot's configured model."""


@answer
class TaskRun(TypedDict):
    run: str
    bot: str
    started_at: str | None
    finished_at: str | None
    outcome: str | None
    ran_on: RanOn


@answer
class CommentDelivery(TypedDict):
    default: Literal["queue", "defer"] | None
    default_for: str | None
    steer: bool
    queue: bool
    steers: bool
    goal: bool


# The task's own read declares its chat and its links here, not on the shared Task: the old app's task types extend Task
# with their own spellings of both, which a declaration there would contradict.
@answer
class TaskDetailTask(TaskFields):
    stage_since: DateTime | None
    """On the task's own read, when it entered its current open visit, or null if it has none. On a list row, when it entered its stage, falling back to when it was filed."""
    conversation_id: str | None
    """The task's chat: the conversation its request, comments and bot replies are in (this read carries its messages). Null for a task with no chat"""
    links: list[TaskLinkView]
    """What the task points to, oldest first: pull requests, issues, docs, pages, checkouts and sessions, each with its whole record"""


@answer
class TaskDetailEvent(TypedDict):
    source: MessageSource


@answer
class TaskDetailParent(TypedDict):
    id: str
    title: str
    label: str | None
    step: str | None


@answer
class TaskDetailChild(TypedDict):
    """A sub-task the reader may open, in its rank order."""

    id: str
    title: str
    status: str
    owner_id: str | None
    label: str | None
    stage: str | None
    role: NotRequired[Literal["review", "check"]]
    """A check sub-task's role (`--role`, backend/check_models.py); absent on any other"""


@answer
class TaskDetail(TypedDict):
    task: TaskDetailTask
    events: list[TaskDetailEvent]
    children: list[TaskDetailChild]
    parent: NotRequired[TaskDetailParent | None]
    comments: list[Message]
    messages: list[Message]
    has_more: bool
    can_comment: NotRequired[bool]
    can_set_run_model: NotRequired[bool]
    mover: NotRequired[bool]
    runs: NotRequired[list[TaskRun]]
    run_model: NotRequired[TaskRunChoice | None]
    comment_delivery: NotRequired[CommentDelivery | None]
    actors: NotRequired[dict[str, str]]
    """On reads: display names for every actor id in the answer, {"human:ana": "Ana Alvarez"}"""


@answer
class SignedVersion(TypedDict):
    """A version a person's signing in Tico made (backend/file_signings.py)."""

    by: str
    by_name: str
    at: str


@answer
class TaskFileVersion(TypedDict):
    n: int
    name: str
    size: int
    mime: str
    sha256: str
    created_at: str
    by: str
    comment_id: str | None
    note: str | None
    ask: TaskReviewAskView | None
    answers: list[ReviewAnswer]
    url: str
    poster_url: str | None
    thumb_url: str | None
    width: int | None
    height: int | None
    duration_ms: int | None
    media_state: str | None
    signed: SignedVersion | None


@answer
class TaskFileView(TypedDict):
    id: str
    name: str
    title: str
    mime: str
    current_version: int
    archived: bool
    # A page's own archive flag (PATCH /api/v2/pages/{id}), apart from its file's.
    page_archived: NotRequired[bool]
    # A page's file flag (PATCH /api/v2/files/{id}), apart from the page's own.
    file_archived: NotRequired[bool]
    # On an archived file: whether the reader may bring it back, under the rule of the flag that archived it.
    can_restore: NotRequired[bool]
    links: list[PageLinkView]
    writers: list[PageWriter]
    versions: list[TaskFileVersion]


@answer
class TaskFileList(TypedDict):
    files: list[TaskFileView]


@answer
class TaskFileResult(TypedDict):
    file_id: str
    version: int
    file: dict[str, Any]
    link: str


@answer
class TaskComments(TypedDict):
    comments: list[Message]


@answer
class CommentResult(TypedDict):
    comment: Message
    comments: list[Message]
    woke: bool
    delivered: NotRequired[bool]
    warning: NotRequired[str]
    held: NotRequired[str]
    goal: NotRequired[dict[str, Any]]
    sent_as: NotRequired[SentAs]


@answer
class InboundTaskResultTask(TypedDict):
    id: str


@answer
class InboundTaskResult(TypedDict):
    task: InboundTaskResultTask | None
    created: bool
    changed: bool


@answer
class TaskDocumentVersion(TypedDict):
    n: int
    by: str
    at: str
    note: str | None


@answer
class TaskDocument(TypedDict):
    name: Annotated[str, Field(description=", ".join(DOCUMENT_NAMES) + " (the names Tico knows), or a bot's own")]
    file_id: str
    title: NotRequired[str]
    kind: NotRequired[Literal["html", "markdown"]]
    page_id: NotRequired[str]
    links: NotRequired[list[PageLinkView]]
    changed_since_ok: NotRequired[bool]
    approved_version: NotRequired[int]
    version: int
    by: str
    at: str
    created: str
    versions: list[TaskDocumentVersion]


@answer
class TaskCapRow(TypedDict):
    sender: str
    receiver: str
    per_day: int
    count: int
    closed: int
    updated_at: str
    updated_by: str


@answer
class TaskCapProposal(TypedDict):
    id: str
    sender: str
    receiver: str
    per_day: int
    status: str
    proposed_by: str
    created_at: str


@answer
class TaskCaps(TypedDict):
    caps: list[TaskCapRow]
    proposals: list[TaskCapProposal]


@answer
class TaskCapWrite(TypedDict):
    cap: dict[str, Any]
    approval: str
    status: str


@answer
class TaskMeeting(TypedDict):
    meeting_id: str
    title: str
    started_at: str
    url: str
    kind: Literal["linked", "sent", "proposal"]


@answer
class TaskMeetings(TypedDict):
    meetings: list[TaskMeeting]
    next_cursor: str | None


@answer
class TaskContextPage(TypedDict):
    id: str
    title: str
    version: int
    updated_at: str
    links: list[PageLinkView]
    writers: list[PageWriter]
    may_write: bool


@answer
class TaskContextMeeting(TypedDict):
    id: str
    title: str
    date: str


@answer
class TaskContext(TypedDict):
    pages: Annotated[list[TaskContextPage], Field(max_length=10)]
    meetings: Annotated[list[TaskContextMeeting], Field(max_length=10)]


@answer
class TaskMakePageState(TypedDict):
    message_id: str
    conversation_id: str
    state: Literal["making", "made", "failed", "update_required"]


@answer
class TaskMakePage(TypedDict):
    make_page: TaskMakePageState | None


@answer
class ShotsReconciled(TypedDict):
    reconciled: list[ReconciledShot]
    """The paid shots marked reconciled: they leave the card's wait and are never sent again"""
