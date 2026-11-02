"""Row shapes for the hub database (`hub.db`): what `SELECT *` returns for the most-used tables.

These mirror the schema in `backend/store.py` and `backend/hubdb.py`, including the columns the migrations add.
A column that SQLite may leave NULL is `X | None`. They must change with the migrations: `backend/tests/test_rows_schema.py`
fails when a TypedDict and its table disagree.

Rows come from `sqlite3.Row`, which reads like a mapping but is not a dict. Code that takes one of these types
converts the row first (`dict(row)`) or annotates the parameter and lets callers pass the row it was given.
"""

from typing import TypedDict


class TaskRow(TypedDict):
    """One `tasks` row (SELECT *)."""
    id: str
    title: str | None
    body: str | None
    requester_id: str | None
    owner_id: str | None
    status: str | None
    due_at: str | None
    conversation_id: str | None
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
    waiting_on: str | None
    guest: str | None
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


class TaskHoldRow(TypedDict):
    """One `task_holds` row (SELECT *): a task's move to Blocked or its snooze (migration 0061). At most one of each
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


class AttemptRow(TypedDict):
    """One `attempts` row (SELECT *): a run, its lease and its record. The lease columns are NULL together on a run
    recorded with no lease (migration 0060); the record's columns are NULL on one that failed before it had one."""
    id: str
    job_id: str | None
    bot_id: str
    runner_id: str | None
    generation: int | None
    token_hash: str | None
    state: str | None
    lease_until: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    thread_id: str | None
    result: str | None
    final_text: str | None
    last_seq: int
    stop_requested_at: str | None
    gateway_key_id: str | None
    handed_runtime: str | None
    handed_model: str | None
    lane: str
    wake: str | None
    model: str | None
    settled_at: str | None
    trigger: str | None
    message_id: str | None
    task_id: str | None
    exit: str | None
    summary: str | None
    trigger_kind: str | None
    trigger_source: str | None
    purpose: str | None
    rule_id: str | None
    writes: int | None
    outcome: str | None
    did_nothing: int | None
    rotated: str | None
    session_kind: str | None
    session_why: str | None
    input_tokens: int | None
    cached_tokens: int | None
    output_tokens: int | None
    prompt_tokens: int | None
    usage_model: str | None
    provider: str | None
    est_cost_usd: float | None
    billing: str | None
    usage_reason: str | None


class RunnerRow(TypedDict):
    """One `runners` row (SELECT *)."""
    id: str
    label: str
    owner_id: str
    token_hash: str
    created_at: str
    last_seen_at: str | None
    revoked_at: str | None
    platform: str | None
    version: str | None
    capacity: int
    readiness: str
    capabilities: str
    awake_since: str | None
    checkout: str | None
    restart_requested_at: str | None
    readiness_at: str | None
    accepts_member_bots: int
    screen: str
    screen_default_pending: int
    performance: str
    restart_since: str | None
    turns_at_once: int | None
    memory_hold: int
    last_sampled_at: int | None


class ApprovalRow(TypedDict):
    """One `approvals` row (SELECT *)."""
    id: str
    kind: str | None
    task_id: str | None
    message_id: str | None
    payload: str | None
    payload_hash: str | None
    requested_by_id: str | None
    decided_by_id: str | None
    decision: str | None
    decided_at: str | None
    consumed_at: str | None
    created_at: str | None
    decided_via: str | None
    decided_for: str | None
    ready_since: str | None
    decision_owner: str | None


class AttemptEventRow(TypedDict):
    """One `attempt_events` row (SELECT *)."""
    id: int
    attempt_id: str
    seq: int
    kind: str
    payload: str
    created_at: str


class PrincipalRow(TypedDict):
    """One `principals` row (SELECT *): an actor string's identity (`human:<id>`, `bot:<bot id>`, a system actor).
    A deleted human or bot keeps its row with `deleted_at` set (backend/migrations/0012_bot_ids.py)."""
    id: str
    kind: str
    deleted_at: str | None
