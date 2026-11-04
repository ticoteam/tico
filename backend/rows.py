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
    check_back_at: str | None
    check_back_note: str | None
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
    snoozed_until: str | None
    snoozed_by_id: str | None
    snoozed_note: str | None
    verdict: str | None
    verdict_head: str | None
    blocked_since: str | None
    blocked_reason: str | None
    blocked_kind: str | None
    blocked_from_step: str | None
    blocked_from_status: str | None
    blocked_gate: str | None
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
    acceptance: str


class TaskCheckBackWakeRow(TypedDict):
    """A reminder occurrence; jobs and attempts retain all execution state."""
    task_id: str
    check_back_at: str
    sent_at: str
    message_id: str | None


class AttemptRow(TypedDict):
    """One `attempts` row (SELECT *)."""
    id: str
    job_id: str
    bot_id: str
    runner_id: str
    generation: int
    token_hash: str
    state: str
    lease_until: str
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


class RunnerRow(TypedDict):
    """One `runners` row (SELECT *): the computer's identity and its owner's settings."""
    id: str
    label: str
    owner_id: str
    token_hash: str
    created_at: str
    revoked_at: str | None
    platform: str | None
    restart_requested_at: str | None
    accepts_member_bots: int
    screen: str
    screen_default_pending: int
    performance: str
    turns_at_once: int | None


class RunnerStateRow(TypedDict):
    """One `runner_states` row (SELECT *): what the computer's heartbeats and samples report."""
    runner_id: str
    last_seen_at: str | None
    awake_since: str | None
    version: str | None
    capacity: int
    readiness: str
    readiness_at: str | None
    capabilities: str
    checkout: str | None
    restart_since: str | None
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


class MemoryTidyPassRow(TypedDict):
    """One admission and its reconciled pilot cost."""
    id: str
    night: str
    bot: str
    model: str
    started_at: str
    deadline: str
    status: str
    reason: str
    parent_task_id: str | None
    check_task_id: str | None
    baseline: str
    cost_usd: float


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


class MemoryTidyPacketRow(TypedDict):
    """Trusted runner custody for one editing and verification attempt."""
    attempt_id: str
    pass_id: str
    memory_commit: str
    snapshot: str
    before_digest: str
    after_digest: str | None
    since: str | None
    previous_learnings: str | None
    proposal: str | None
    conflicts: int | None
    flags: str
    stage: str


class UsageFactRow(TypedDict):
    """One `usage_facts` row (SELECT *): a metered segment, append-only (backend/usage_facts.py)."""
    id: int
    source: str
    run_id: str | None
    position: int
    bot_id: str | None
    task_id: str | None
    provider: str | None
    model: str | None
    family: str | None
    runtime: str | None
    harness: str | None
    effort: str | None
    profile: str | None
    billing: str | None
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    cost_nano: int | None
    low_estimate: int | None
    meter_key: str | None
    gateway_key_id: str | None
    install_id: str | None
    started_at: str
    metered_at: str
    created_at: str


class UsageMeterReadRow(TypedDict):
    """One `usage_meter_reads` row (SELECT *): the newest read of the Gateway's meter taken for a month
    (backend/usage_facts.py `record_meter`)."""
    id: int
    month: str
    as_of: str
    total_nano: int
    read_at: str


class BudgetNodeRow(TypedDict):
    """One `budget_nodes` row (SELECT *): a money limit, its amount, period and scope (backend/usage_limits.py)."""
    id: str
    scope: str
    team_id: str | None
    bot_id: str | None
    per_bot: int
    period: str
    amount_nano: int
    created_at: str
    updated_at: str
    updated_by_id: str | None


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
