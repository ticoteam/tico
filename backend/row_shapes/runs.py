"""Runs: attempts and their events, runners, approvals and cloud machines.

Rows come from `sqlite3.Row` (SELECT *); see `backend/rows.py` for the rules."""

from typing import TypedDict


class AttemptRow(TypedDict):
    """One `attempts` row (SELECT *): a run, its lease and its record. The lease columns are NULL together on a run
    recorded with no lease (migration 0072); the record's columns are NULL on one that failed before it had one."""

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
    acting_person: str | None
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


class CloudMachineDeletionRow(TypedDict):
    """Deletion intent stays tied to the live HQ machine id across replacements."""

    machine_id: str
    bot: str
    runner_id: str | None
    reason: str
    next_try_at: float | None
    failures: int
    created_at: float | None


class CloudMachineEnrollmentFailureRow(TypedDict):
    """Each live HQ machine id spends the enrollment retry budget at most once."""

    machine_id: str
    bot: str


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
    device_id: str | None


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


TABLES = {
    AttemptRow: "attempts",
    CloudMachineDeletionRow: "cloud_machine_deletions",
    CloudMachineEnrollmentFailureRow: "cloud_machine_enrollment_failures",
    RunnerRow: "runners",
    RunnerStateRow: "runner_states",
    ApprovalRow: "approvals",
    AttemptEventRow: "attempt_events",
}
