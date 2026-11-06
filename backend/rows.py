"""Row shapes for the hub database (`hub.db`): what `SELECT *` returns for the most-used tables.

These mirror the schema in `backend/store.py` and `backend/hubdb.py`, including the columns the migrations add.
A column that SQLite may leave NULL is `X | None`. They must change with the migrations: `backend/tests/test_rows_schema.py`
fails when a TypedDict and its table disagree.

Rows come from `sqlite3.Row`, which reads like a mapping but is not a dict. Code that takes one of these types
converts the row first (`dict(row)`) or annotates the parameter and lets callers pass the row it was given.
"""

from typing import TypedDict


class PageIdentityRow(TypedDict):
    """An alias keeps its page's read provenance after the bytes are purged."""
    identity: str
    file_id: str


class AlertPreferenceRow(TypedDict):
    person_id: str
    phone: str | None
    phone_version: str
    verified_at: str | None
    revocation_pending: int
    stall_after: str
    call_consent: int
    urgent_needs: int
    legal_mail: int
    bot_stuck: int
    urgent_calls: int
    timezone: str
    quiet_start: str
    quiet_end: str
    emergency_exception: int
    monthly_cap: int
    updated_at: str


class RecorderInstallRow(TypedDict):
    person_id: str
    install_id: str
    version: str
    last_seen_at: str
    created_at: str
    updated_at: str


class AlertCompanySettingRow(TypedDict):
    id: int
    monthly_cap: int


class PersonalAlertRow(TypedDict):
    id: str
    person_id: str
    incident_key: str
    kind: str
    subject_id: str
    emergency: int
    phone_version: str
    state: str
    month: str | None
    reserved: int
    send_id: str | None
    channel: str | None
    error: str
    created_at: str
    attempted_at: str | None
    next_attempt_at: str | None


class PostalMailGrantRow(TypedDict):
    id: str
    address_id: str
    grantee_type: str
    grantee_id: str
    level: str
    granted_by_id: str
    created_at: str
    updated_at: str


class PostalMailSettingsRow(TypedDict):
    id: int
    legal_bot_id: str | None
    updated_by_id: str | None
    created_at: str
    updated_at: str


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
    waiting_on: str | None
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


class MessageRouteRow(TypedDict):
    """Who routing asked for one person's message (backend/thread_routing.py): `bot_ids` is a JSON list of bot actors,
    `[]` when it asked none."""
    message_id: str
    thread_id: str
    bot_ids: str
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


class BotSettingsRow(TypedDict):
    """One `bot_settings` row (SELECT *): a bot's declared settings (backend/bot_settings.py)."""
    bot_id: str
    harness: str | None
    runtime: str | None
    model: str | None
    reasoning_effort: str | None
    host: str | None
    template: str | None
    profile: str | None
    session: str | None
    wake_preset: str | None
    shared_from: str | None
    bot_contact: str | None
    repo_access_mode: str | None
    folder: str | None
    github_login: str | None
    compact_at: int | None
    turns_per_hour: int | None
    turns_per_day: int | None
    max_run_minutes: int | None
    stateless: int | None
    fallback: str | None
    app_delivery: str | None
    extra: str
    updated_at: str | None


class LetterOutboxRow(TypedDict):
    id: str
    mode: str
    return_address: str | None
    monthly_cap_micros: int | None
    availability: str
    checked_at: str | None
    created_at: str
    updated_at: str


class LetterOutboxGrantRow(TypedDict):
    id: str
    outbox_id: str
    grantee_type: str
    grantee_id: str
    level: str
    granted_by_id: str
    created_at: str
    updated_at: str


class LetterBotSettingRow(TypedDict):
    bot_id: str
    mode: str | None
    monthly_cap_micros: int | None
    updated_by: str
    updated_at: str


class LetterRow(TypedDict):
    id: str
    outbox_id: str
    requested_by: str
    acting_bot_id: str | None
    automation_id: str | None
    task_id: str | None
    thread_id: str | None
    attempt_id: str | None
    recipient: str
    recipient_hash: str
    return_address: str
    mail_class: str
    color: int
    duplex: int
    source_blob_id: str
    original_blob_id: str | None
    source_provenance: str
    proof_blob_id: str | None
    page_count: int | None
    quote_id: str | None
    quote_expires_at: str | None
    approval_hash: str | None
    approval_id: str | None
    approved_hash: str | None
    submission_quote_id: str | None
    hq_letter_id: str | None
    total_micros: int
    currency: str
    status: str
    tracking: str
    cancel_possible: int
    always_ask: int
    approval_reason: str
    error: str
    attempts: int
    next_sync_at: str | None
    submitted_at: str | None
    accepted_at: str | None
    charged_micros: int | None
    spend_released: int
    billing: str
    created_at: str
    updated_at: str


class LetterSourceRow(TypedDict):
    letter_id: str
    source_type: str
    source_id: str


class LetterProofRow(TypedDict):
    letter_id: str
    blob_id: str
    approval_hash: str
    created_at: str


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


class EmailSendingPolicyRow(TypedDict):
    scope: str
    settings: str
    updated_at: str
    updated_by: str


class EmailSendRow(TypedDict):
    id: str
    mailbox: str
    actor: str
    bot: str
    via: str
    automation_action_id: str | None
    content_sha256: str
    idempotency_key: str
    recipients: str
    attachments: str
    subject: str
    body_sha256: str
    raw: str
    draft_id: str
    thread_id: str
    reply_to_id: str
    approval_id: str
    classification: str
    decision: str
    state: str
    created_at: str
    send_after: str
    claimed_at: str | None
    provider_attempted_at: str | None
    runner_id: str | None
    finished_at: str | None
    provider_message_id: str | None
    detail: str
    operation_key: str
    lease_id: str
    checked_at: str
    calendar_action_id: str | None


class EmailSuppressedRecipientRow(TypedDict):
    mailbox: str
    address: str
    reason: str
    created_at: str


class EmailLegacyAutomationRow(TypedDict):
    automation_id: str
    owner_id: str
    version: int


class EmailSendPreviewRow(TypedDict):
    send_id: str
    actor: str
    content_sha256: str
    shown_at: str


class EmailSendOperationRow(TypedDict):
    actor: str
    operation_key: str
    send_id: str


class ToolRow(TypedDict):
    """An API tool (backend/api_tools.py): a service bots call with a key, through the proxy or on computers."""
    id: str
    name: str
    service: str
    base_url: str
    auth_scheme: str
    check_path: str
    delivery: str
    created_by: str
    created_at: str
    updated_at: str


class ToolCheckRow(TypedDict):
    """One key's check of its tool: the status the Tools page shows until the next."""
    id: str
    tool_id: str
    credential_id: str | None
    ok: int
    status_code: int | None
    detail: str
    checked_at: str


class ToolCallRow(TypedDict):
    """One proxied call (track B's proxy writes it): the tool's activity, never the key or the body."""
    id: str
    tool_id: str
    credential_id: str | None
    actor: str
    bot: str
    access_level: str
    method: str
    path: str
    status_code: int | None
    result: str
    detail: str
    duration_ms: int | None
    created_at: str
