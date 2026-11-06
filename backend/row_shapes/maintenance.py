"""Maintenance, heartbeat and memory tidy: their steps, runs, listings, handoffs and requests.

Rows come from `sqlite3.Row` (SELECT *); see `backend/rows.py` for the rules."""

from typing import TypedDict


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


class MaintenanceStepRow(TypedDict):
    """One `maintenance_steps` row (SELECT *): a Maintenance step's switch, "How often", custom interval, "Most bots per
    run" and cursors (migration 0090, backend/maintenance.py)."""

    key: str
    enabled: int
    frequency: str
    custom_every: int | None
    custom_unit: str | None
    max_bots: int | None
    cursor_at: str | None
    last_started_at: str | None
    last_ran_at: str | None
    updated_at: str | None
    updated_by_id: str | None


class MaintenanceRunRow(TypedDict):
    """One `maintenance_runs` row (SELECT *): one Maintenance run, claimed by its occurrence key (migration 0090)."""

    id: str
    occurrence_key: str
    trigger: str
    step: str | None
    started_at: str
    heartbeat_at: str
    finished_at: str | None
    ok: int | None
    result: str
    cost_usd: float


class MaintenanceRunStepRow(TypedDict):
    """One `maintenance_run_steps` row (SELECT *): one step of one Maintenance run, with its status and counts."""

    id: str
    maintenance_run_id: str
    step: str
    status: str
    reason: str
    checked: int
    found: int
    woke: int
    cost_usd: float
    started_at: str
    finished_at: str | None
    window_end: str | None
    details: str


class HeartbeatStepRow(TypedDict):
    """One `heartbeat_steps` row (SELECT *): one Heartbeat step's last run, result and error, and its day counts in two
    12-hour windows (migration 20261106094503_heartbeat)."""

    key: str
    last_run_at: str | None
    last_ms: int | None
    last_result: str | None
    last_did_at: str | None
    last_did: str | None
    last_error: str | None
    last_error_at: str | None
    window_started_at: str | None
    runs_count: int
    did_count: int
    failed_count: int
    previous_runs_count: int
    previous_did_count: int
    previous_failed_count: int
    created_at: str
    updated_at: str


class HeartbeatBeatRow(TypedDict):
    """One `heartbeat_beats` row (SELECT *): a beat where a step did something or failed; `steps` is those steps' lines
    as JSON ({key, text, ms, error}); a beat repeating only its failures folds into it (`repeat_count`)."""

    id: str
    started_at: str
    total_ms: int
    did_count: int
    failed_count: int
    steps: str
    repeat_count: int
    last_repeated_at: str | None
    created_at: str


class HeartbeatStateRow(TypedDict):
    """The one `heartbeat_states` row (id 1): the last beat, beats in the day windows, quiet beats counted."""

    id: int
    last_beat_at: str | None
    window_started_at: str | None
    beats_count: int
    previous_beats_count: int
    quiet_beats_count: int
    quiet_since_at: str | None
    last_pruned_at: str | None
    created_at: str
    updated_at: str


class MaintenanceListingRow(TypedDict):
    """One `maintenance_listings` row (SELECT *): a task Find stuck listed for one reason at one appointment."""

    task_id: str
    kind: str
    appointment: str
    bot_id: str
    listed_at: str


class MaintenanceHandoffRow(TypedDict):
    """One `maintenance_handoffs` row (SELECT *): a quiet task waiting to ride along in its bot's Update turn."""

    id: str
    bot_id: str
    task_id: str
    kind: str
    created_at: str
    delivered_at: str | None


class MaintenanceRequestRow(TypedDict):
    """One `maintenance_requests` row (SELECT *): the run an actor's Idempotency-Key was given to."""

    actor: str
    request_key: str
    maintenance_run_id: str
    created_at: str


TABLES = {
    MemoryTidyPassRow: "memory_tidy_passes",
    MemoryTidyPacketRow: "memory_tidy_packets",
    MaintenanceStepRow: "maintenance_steps",
    MaintenanceRunRow: "maintenance_runs",
    MaintenanceRunStepRow: "maintenance_run_steps",
    HeartbeatStepRow: "heartbeat_steps",
    HeartbeatBeatRow: "heartbeat_beats",
    HeartbeatStateRow: "heartbeat_states",
    MaintenanceListingRow: "maintenance_listings",
    MaintenanceHandoffRow: "maintenance_handoffs",
    MaintenanceRequestRow: "maintenance_requests",
}
