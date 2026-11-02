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
    requester: str | None
    owner: str | None
    status: str | None
    due: str | None
    conversation_id: str | None
    created: str | None
    updated: str | None
    done_at: str | None
    closed_at: str | None
    closed_by: str | None
    note: str | None
    goal_id: str | None
    labels_json: str
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
    resume_json: str | None
    occurrence: str | None
    occurrence_step: str | None
    rule_due_id: str | None
    checkpoint: str | None
    checkpoint_at: str | None
    checkpoint_by: str | None
    checkpoint_state: str | None
    stale_asked_at: str | None
    checkpoint_blocked: int
    snoozed_until: str | None
    snoozed_by: str | None
    snoozed_note: str | None
    verdict: str | None
    verdict_head: str | None
    blocked_since: str | None
    blocked_reason: str | None
    blocked_kind: str | None
    blocked_from_step: str | None
    blocked_from_status: str | None
    blocked_gate_json: str | None
    building_started_at: str | None
    building_seconds: float
    holds_cleared_at: str | None
    blocker_key: str | None
    visibility: str
    public_title: str
    public_summary: str
    size: float | None
    request_id: str | None
    version: int
    acceptance_json: str


class AttemptRow(TypedDict):
    """One `attempts` row (SELECT *)."""
    id: str
    job_id: str
    bot: str
    runner_id: str
    generation: int
    token_hash: str
    state: str
    lease_until: str
    created: str
    started: str | None
    finished: str | None
    thread_id: str | None
    result_json: str | None
    final_text: str | None
    last_seq: int
    stop_requested: str | None
    gateway_key_id: str | None
    handed_runtime: str | None
    handed_model: str | None
    lane: str
    wake_json: str | None
    model_json: str | None


class RunnerRow(TypedDict):
    """One `runners` row (SELECT *)."""
    id: str
    label: str
    operator: str
    token_hash: str
    created: str
    last_seen: str | None
    revoked_at: str | None
    platform: str | None
    version: str | None
    capacity: int
    readiness_json: str
    capabilities_json: str
    awake_since: str | None
    checkout_json: str | None
    restart_requested: str | None
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
    payload_json: str | None
    payload_hash: str | None
    requested_by: str | None
    decided_by: str | None
    decision: str | None
    decided_at: str | None
    consumed_at: str | None
    created: str | None
    decided_via: str | None
    decided_for_json: str | None
    ready_since: str | None
    decision_owner: str | None


class AttemptEventRow(TypedDict):
    """One `attempt_events` row (SELECT *)."""
    id: int
    attempt_id: str
    seq: int
    kind: str
    payload_json: str
    created: str
