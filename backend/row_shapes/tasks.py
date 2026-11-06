"""Tasks, threads and their history: holds, presence, rankings, routes, feedback, merges and the history log.

Rows come from `sqlite3.Row` (SELECT *); see `backend/rows.py` for the rules."""

from typing import TypedDict


class MeetingTaskLinkRow(TypedDict):
    """A meeting/task pair; its references keep their independent read checks."""

    id: str
    meeting_id: str
    task_id: str
    kind: str
    created_by: str
    created_at: str


class SuggestionRow(TypedDict):
    id: str
    fingerprint: str
    owner_role: str
    owner_bot_id: str | None
    title: str
    body: str
    condition: str
    cooldown_days: int
    status: str
    update_batch_id: str | None
    message_id: str | None
    delivered_at: str | None
    cooldown_until: str | None
    outcome: str | None
    outcome_reported_at: str | None
    created_at: str
    updated_at: str


class TaskRow(TypedDict):
    """One `tasks` row (SELECT *)."""

    id: str
    title: str | None
    body: str | None
    requester_id: str | None
    owner_id: str | None
    status: str | None
    due_at: str | None
    check_back_at: str | None
    check_back_note: str | None
    conversation_id: str | None
    live_meeting_id: str | None
    created_at: str | None
    updated_at: str | None
    done_at: str | None
    closed_at: str | None
    closed_by_id: str | None
    note: str | None
    goal_id: str | None
    labels: str
    type_id: str | None
    step_id: str | None
    number: int | None
    step_rank: float | None
    private: int
    readers: str | None
    lane: str
    rank: float | None
    next_run: int
    carried_by: str | None
    resume: str | None
    occurrence: str | None
    occurrence_step: str | None
    rule_due_id: str | None
    checkpoint: str | None
    checkpoint_at: str | None
    checkpoint_by_id: str | None
    checkpoint_state: str | None
    stale_asked_at: str | None
    checkpoint_blocked: int
    verdict: str | None
    verdict_head: str | None
    building_started_at: str | None
    building_seconds: float
    blocker_key: str | None
    visibility: str
    public_title: str
    public_summary: str
    size: float | None
    request_id: str | None
    version: int
    acceptance: str
    # A question task's (backend/question_tasks.py): JSON text, null on any other task.
    questions: str | None
    question_topic: str | None
    if_no_answer: str | None
    review: str | None
    spend: str | None
    answer: str | None
    asked_at: str | None


class PresenceRow(TypedDict):
    """One open window at a task or meeting; expires without heartbeats."""

    id: str
    place: str
    actor: str
    session_id: str
    seen_at: str


class TaskCheckBackWakeRow(TypedDict):
    """A reminder occurrence; jobs and attempts retain all execution state."""

    task_id: str
    check_back_at: str
    sent_at: str
    message_id: str | None


class TaskHoldRow(TypedDict):
    """One `task_holds` row (SELECT *): a task's move to Blocked or its snooze (migration 0075). At most one of each
    kind per task is open (released_at NULL); a released one stays as history."""

    id: int
    task_id: str
    kind: str
    cause: str | None
    reason: str | None
    by_id: str | None
    until_at: str | None
    from_step_id: str | None
    from_status: str | None
    gate: str | None
    cleared_at: str | None
    created_at: str
    released_at: str | None


class QuarantineHoldRow(TypedDict):
    """One bot's refusal-count hold on a task or chat (migration 0094)."""

    id: int
    bot_id: str
    task_id: str | None
    conversation_id: str | None
    reason: str
    refusal: str
    created_at: str
    released_at: str | None


class FeedbackRow(TypedDict):
    """One person's 👍 or 👎 on something a bot made (backend/feedback.py); the run fields are None where it had none."""

    id: str
    person_id: str
    subject_type: str
    subject_id: str
    bot_id: str
    conversation_id: str | None
    attempt_id: str | None
    model: str | None
    harness: str | None
    subject_revision: int | None
    rating: str
    reason: str
    outcome: str
    note_id: str | None
    org_rule_id: str | None
    created_at: str
    updated_at: str


class ThreadRankingRow(TypedDict):
    """A root thread's Important level (backend/thread_rank.py); `score` is None until a call answers."""

    thread_id: str
    score: int | None
    ranked_seq: int
    tried_at: str
    created_at: str
    updated_at: str


class ThreadPinRow(TypedDict):
    """One actor's pin on a root thread (backend/thread_rail.py): `pinned` 0 unpins it for them over a pin for everyone."""

    id: str
    thread_id: str
    actor: str
    pinned: int
    created_at: str
    updated_at: str


class MessageRouteRow(TypedDict):
    """Who routing asked for one person's message, kept as history (routing was retired: backend/threads.py `plan`):
    `bot_ids` is a JSON list of bot actors, `[]` when it asked none."""

    message_id: str
    thread_id: str
    bot_ids: str
    created_at: str
    updated_at: str


class HomeMergeRow(TypedDict):
    """One `home_merges` row (SELECT *): the old personal chat with a bot that moved into that bot's Home, and the Home
    it went to (backend/bot_home_merge.py)."""

    conversation_id: str
    home_id: str
    bot_id: str
    owner_id: str | None
    room_key: str | None
    messages_count: int
    created_at: str


class HomeMergeMessageRow(TypedDict):
    """One `home_merge_messages` row (SELECT *): a message that a merged chat carried into the Home."""

    message_id: str
    conversation_id: str


class HistoryEventRow(TypedDict):
    """One `history_events` row (SELECT *): one change to a task, a goal, the company profile, the strategy or the
    market graph (backend/migrations/0064_history_events.py). `kind` is the field that changed or what happened, `data`
    a JSON object of the subject type's extras."""

    id: str
    subject_type: str
    subject_id: str
    actor_id: str | None
    kind: str | None
    old: str | None
    new: str | None
    note: str | None
    action_id: str | None
    via: str | None
    data: str | None
    created_at: str | None


TABLES = {
    MeetingTaskLinkRow: "meeting_task_links",
    SuggestionRow: "suggestions",
    TaskRow: "tasks",
    PresenceRow: "presences",
    TaskCheckBackWakeRow: "task_check_back_wakes",
    TaskHoldRow: "task_holds",
    QuarantineHoldRow: "quarantine_holds",
    FeedbackRow: "feedbacks",
    ThreadRankingRow: "thread_rankings",
    ThreadPinRow: "thread_pins",
    MessageRouteRow: "message_routes",
    HomeMergeRow: "home_merges",
    HomeMergeMessageRow: "home_merge_messages",
    HistoryEventRow: "history_events",
}
