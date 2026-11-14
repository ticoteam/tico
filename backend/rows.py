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
    call_consent: int
    legal_mail: int
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


class PushSubscriptionRow(TypedDict):
    id: str
    actor: str
    endpoint: str
    p256dh: str
    auth: str
    created_at: str
    last_ok_at: str | None
    failed_at: str | None
    failures: int
    gone_at: str | None


class CalendarRow(TypedDict):
    id: str
    owner_type: str
    owner_id: str | None
    name: str
    home: str
    google_calendar_id: str | None
    time_zone: str | None
    created_by: str | None
    created_at: str
    updated_at: str


class CalendarEventRow(TypedDict):
    id: str
    calendar_id: str
    uid: str
    title: str
    notes: str | None
    location: str | None
    meeting_url: str | None
    all_day: int
    starts_on: str | None
    ends_on: str | None
    starts_at_local: str | None
    ends_at_local: str | None
    time_zone: str | None
    starts_at: str
    ends_at: str
    rrule: str | None
    until_at: str | None
    recurring_event_id: str | None
    original_starts_at: str | None
    status: str
    visibility: str
    sequence: int
    created_by: str | None
    task_id: str | None
    thread_id: str | None
    created_at: str
    updated_at: str


class CalendarEventAttendeeRow(TypedDict):
    id: str
    calendar_event_id: str
    attendee_type: str
    attendee_id: str | None
    email: str | None
    response: str
    created_at: str
    updated_at: str


class AbsenceRow(TypedDict):
    id: str
    human_id: str
    starts_on: str
    ends_on: str
    note: str | None
    created_by: str | None
    created_at: str
    updated_at: str


class CalendarAccessRow(TypedDict):
    id: str
    calendar_id: str
    grantee_type: str
    grantee_id: str
    level: str
    created_by: str | None
    created_at: str
    updated_at: str


class CalendarEventLinkRow(TypedDict):
    id: str
    google_calendar_id: str | None
    ical_uid: str | None
    tico_event_id: str | None
    original_starts_at: str | None
    link_type: str
    link_id: str
    created_at: str


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
    """One `tasks` row (SELECT *). Its status, stage, owner and pipeline are written by the engine alone
    (backend/task_engine.py); what it waits on is read, not stored (backend/waiting_on.py)."""

    id: str
    title: str | None
    body: str | None
    requester_id: str | None
    owner_id: str | None
    status: str
    due_at: str | None
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
    number: int | None
    private: int
    readers: str | None
    lane: str
    rank: float | None
    occurrence: str | None
    occurrence_step: str | None
    rule_due_id: str | None
    checkpoint: str | None
    checkpoint_at: str | None
    checkpoint_by_id: str | None
    checkpoint_state: str | None
    checkpoint_blocked: int
    visibility: str
    public_title: str
    public_summary: str
    size: float | None
    request_id: str | None
    version: int
    acceptance: str
    work_revision: int
    pipeline_id: str | None
    stage_id: str | None
    assignee_id: str | None
    human_id: str | None


class PresenceRow(TypedDict):
    """One open window at a task or meeting; expires without heartbeats."""

    id: str
    place: str
    actor: str
    session_id: str
    seen_at: str


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
    cache_write_tokens: int | None
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
    gateway_fallback: int


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
    """Who routing asked for one person's message, kept as history (routing was retired: backend/threads.py `plan`):
    `bot_ids` is a JSON list of bot actors, `[]` when it asked none."""

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
    cache_write_tokens: int | None
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
    audience: str | None
    kind: str
    sites: str | None
    central_only: int


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


class SkillRow(TypedDict):
    """A company skill: the approved name; `latest_version_id` stays None until its first approval."""

    id: str
    name: str
    description: str
    latest_version_id: str | None
    created_by_type: str
    created_by_id: str
    deleted_at: str | None
    created_at: str
    updated_at: str


class SkillVersionRow(TypedDict):
    """One approved, immutable bundle: `files` is the JSON [{path, content}]."""

    id: str
    skill_id: str
    number: int
    sha256: str
    files: str
    description: str
    proposal_id: str
    approved_by_id: str
    approved_at: str
    created_at: str


class SkillProposalRow(TypedDict):
    id: str
    name: str
    skill_id: str | None
    base_version_id: str | None
    sha256: str
    files: str
    description: str
    source: str
    note: str
    status: str
    proposed_by_type: str
    proposed_by_id: str
    task_id: str | None
    decided_by_id: str | None
    decided_at: str | None
    decline_reason: str | None
    version_id: str | None
    screen_status: str
    created_at: str
    updated_at: str


class SkillScreeningRow(TypedDict):
    id: str
    proposal_id: str
    sha256: str
    base_version_id: str | None
    question_set: str
    status: str
    reason: str | None
    verdict: str | None
    risk: str | None
    summary: str | None
    findings: str
    patterns: str
    judge_call_id: str | None
    created_at: str


class SkillInstallRow(TypedDict):
    id: str
    skill_id: str
    target_type: str
    target_id: str
    version_policy: str
    pinned_version_id: str | None
    installed_by_type: str
    installed_by_id: str
    created_at: str
    updated_at: str


class SkillReceiptRow(TypedDict):
    """What a runner or helper actually staged for a target."""

    target_type: str
    target_id: str
    skill_id: str
    sha256: str
    source: str
    staged_at: str


class SkillUsageRow(TypedDict):
    """One use: skill, who, where, when. Never prompt text or arguments."""

    id: int
    skill_id: str
    sha256: str | None
    actor_type: str
    actor_id: str
    requested_by_id: str | None
    trigger: str
    source: str
    session_key: str
    tool_use_id: str
    task_id: str | None
    thread_id: str | None
    used_at: str
    created_at: str


class SkillUsageDailyRow(TypedDict):
    used_on: str
    skill_id: str
    actor_type: str
    actor_id: str
    trigger: str
    uses_count: int


class UnlistedSkillUsageDailyRow(TypedDict):
    """A bot's uses in a day of a skill the directory does not have: the name, the bot, the day and a count only."""

    used_on: str
    name: str
    bot_id: str
    uses_count: int


class SkillSuggestionRow(TypedDict):
    """A remove or promote suggestion for Library › Skills (backend/skill_suggestions.py)."""

    id: str
    kind: str
    subject: str
    name: str
    target_type: str
    target_id: str
    uses_count: int
    bots_count: int
    status: str
    closed_by_id: str | None
    closed_at: str | None
    created_at: str
    updated_at: str


class SkillDeviceRow(TypedDict):
    """A person's skills helper; the token is stored only as `token_hash`."""

    id: str
    person_id: str
    label: str
    token_hash: str
    reporting_enabled: int
    claude_version: str | None
    last_seen_at: str | None
    revoked_at: str | None
    created_at: str
    updated_at: str


class SkillDeviceCodeRow(TypedDict):
    device_code_hash: str
    user_code: str
    label: str
    status: str
    person_id: str | None
    reporting_enabled: int
    device_id: str | None
    expires_at: str
    created_at: str
    updated_at: str
    client_hash: str | None


class CiRunRow(TypedDict):
    id: int
    kind: str
    status: str
    remote_id: str
    github_run_id: str
    task_id: str
    bot: str
    cpus: int
    requested_minutes: int
    minutes: int
    reserve_cents: int
    cost_cents: int
    hold_id: str
    entry_id: str
    reason: str
    created_at: str
    seen_at: str


class CiRunExtensionRow(TypedDict):
    id: int
    task_id: str
    minutes: int
    granted_by: str
    created_at: str


class FactoryRow(TypedDict):
    """One `factories` row (SELECT *)."""

    id: str
    name: str
    owner_id: str | None
    position: int | None
    created_at: str
    updated_at: str
    retired_at: str | None
    goal: str | None
    manager_id: str | None


class FactoryManagerActionRow(TypedDict):
    """One `factory_manager_actions` row (SELECT *)."""

    id: str
    factory_id: str
    factory_manager_run_id: str
    action: str
    target_actor: str | None
    note: str | None
    created_at: str


class FactoryManagerRunRow(TypedDict):
    """One `factory_manager_runs` row (SELECT *)."""

    id: str
    factory_id: str
    started_at: str
    finished_at: str | None
    status: str
    bottleneck_stage_id: str | None
    judgment_count: int
    wake_key: str | None
    woke: int
    model_called: int
    error: str | None


# The Directory (backend/entities/): one row shape per table the Directory's tables migration makes.


class EntityActivityRow(TypedDict):
    id: str
    entity_id: str
    kind: str
    occurred_at: str
    subject_type: str | None
    subject_id: str | None
    external_ref: str | None
    actor_type: str | None
    actor_id: str | None
    direction: str | None
    summary: str


class EntityAttributeRow(TypedDict):
    id: str
    entity_type: str
    key: str
    name: str
    value_type: str
    multiple: int
    unique_value: int
    required: int
    options: str
    owner_type: str
    owner_id: str
    description: str
    sensitive: int
    position: int
    created_by_type: str
    created_by_id: str
    created_at: str
    archived_at: str | None


class EntityConnectionRow(TypedDict):
    id: str
    entity_id: str
    actor_type: str
    actor_id: str
    first_interaction_at: str | None
    last_interaction_at: str | None
    interactions_count: int
    strength: float


class EntityExternalRefRow(TypedDict):
    id: str
    entity_id: str
    connection_id: str
    system: str
    external_type: str
    external_id: str
    url: str
    revision: str
    synced_at: str | None
    status: str


class EntityLookupRow(TypedDict):
    id: str
    attempt_id: str
    entity_id: str
    created_at: str


class EntityMentionRow(TypedDict):
    id: str
    mentionable_type: str
    mentionable_id: str
    mentionable_revision: str
    entity_id: str
    role: str
    quote: str
    occurrence: int
    confidence: float
    method: str
    status: str
    created_at: str


class EntityMergeRow(TypedDict):
    id: str
    entity_id: str
    into_entity_id: str
    moved: str
    prior: str
    actor_type: str
    actor_id: str
    created_at: str
    undone_at: str | None


class EntityNoteRow(TypedDict):
    id: str
    entity_id: str
    author_type: str
    author_id: str
    body: str
    audience: str
    pinned_at: str | None
    evidence_id: str | None
    created_at: str


class EntitySourceRow(TypedDict):
    id: str
    entity_id: str
    source_type: str
    source_id: str
    visibility: str
    created_at: str


class EntitySuppressionRow(TypedDict):
    id: str
    kind: str
    value_hash: str
    created_at: str
    reason: str


class EntityTypeRow(TypedDict):
    id: str
    key: str
    name: str
    plural_name: str
    icon: str
    built_in: int
    mode: str
    linked_connection_id: str | None
    description: str
    created_by_type: str
    created_by_id: str
    created_at: str
    archived_at: str | None


class EntityValueHistoryRow(TypedDict):
    id: str
    entity_id: str
    entity_attribute_id: str
    value: str
    source_type: str | None
    source_id: str | None
    evidence_id: str | None
    confidence: float | None
    active_from: str
    active_until: str


class EntityValueRow(TypedDict):
    id: str
    entity_id: str
    entity_attribute_id: str
    single_value: int
    text_value: str | None
    number_value: float | None
    currency: str | None
    date_value: str | None
    timestamp_value: str | None
    option_key: str | None
    json_value: str | None
    source_type: str | None
    source_id: str | None
    evidence_id: str | None
    confidence: float
    updated_at: str


class EntityViewRow(TypedDict):
    id: str
    entity_type: str
    name: str
    layout: str
    filters: str
    columns: str
    sort: str
    group_attribute_id: str | None
    owner_type: str
    owner_id: str
    shared: int
    created_at: str


class LinkTypeRow(TypedDict):
    id: str
    key: str
    name: str
    inverse_name: str
    from_entity_type: str | None
    to_entity_type: str | None
    inherits_rules: int
    symmetric: int
    description: str
    built_in: int
    created_by_type: str
    created_by_id: str
    created_at: str
    archived_at: str | None


class FinStatementUploadRow(TypedDict):
    """One statement file posted to a money account (backend/finance/statement_upload.py). Integer cents."""

    id: str
    book_id: str
    money_account_id: str
    file_hash: str
    file_name: str
    file_format: str
    start_date: str
    end_date: str
    opening_balance: int | None
    closing_balance: int | None
    ends_at: int
    off_by: int
    row_count: int
    new_count: int
    duplicate_count: int
    skipped_count: int
    statement_id: str | None
    action_id: str
    created_at: str
    created_by: str
    undone_at: str | None
    undone_by: str | None


class FinStatementPreviewRow(TypedDict):
    """A statement review's token, kept only as its hash, until a post uses it or it expires."""

    token_hash: str
    book_id: str
    money_account_id: str
    file_hash: str
    new_digest: str
    created_at: str
    created_by: str
    expires_at: str


class SetupHandoffRow(TypedDict):
    token_hash: str
    created_at: str
    expires_at: str
    used_at: str | None
    revoked_at: str | None
    human_token_id: str | None


class PipelineRow(TypedDict):
    """A pipeline: an ordered list of stages a task moves through (backend/task_engine.py)."""

    id: str
    name: str
    goal: str
    prefix: str | None
    manager_id: str | None
    owner_id: str | None
    private: int
    factory_id: str | None
    preset: str | None
    settings_version: int
    created_at: str
    updated_at: str
    retired_at: str | None
    keep_warm_hours: int


class StageRow(TypedDict):
    """One stage of a pipeline: its work stages in order, then Done and Closed."""

    id: str
    pipeline_id: str
    position: int
    kind: str
    name: str
    purpose: str
    owner: str | None
    by_hand: int
    requests_move_on: int
    task_limit: int | None
    expected_minutes: int | None
    expected_bots_only: int
    on_enter: str
    created_at: str
    updated_at: str
    retired_at: str | None
    pick: str | None


class VisitRow(TypedDict):
    """One stay of a task in a stage. Exactly one is open per task (left_at NULL)."""

    id: str
    task_id: str
    stage_id: str
    entered_at: str
    entered_how: str
    left_at: str | None
    how: str | None
    by_id: str | None
    why: str | None
    tries: int
    level: str
    person_id: str | None
    next_try_at: str | None
    progress_mark: str | None
    last_change_at: str | None
    late_told_at: str | None
    late_person_at: str | None
    bot_snoozes: int
    last_change_attempt: int | None
    last_change_message: int | None
    last_change_start: int | None
    holder_id: str | None
    pick_state: str | None
    pick_why: str | None


class RequirementRow(TypedDict):
    """What a task needs to leave a stage: on the stage (every task) or on one visit (this task, this time). Kind `item`
    names the `source` and its `condition` (backend/item_sources)."""

    id: str
    stage_id: str | None
    visit_id: str | None
    kind: str
    source: str | None
    condition: str | None
    settings: str
    can_push_past: int
    who_oks: str
    person_id: str | None
    added_by_id: str | None
    by_bot: int
    line: str
    shows: str | None
    choices: str | None
    position: int
    created_at: str
    withdrawn_at: str | None


class AnswerRow(TypedDict):
    """What met a requirement for a visit, sent it back, or pushed past it."""

    id: str
    visit_id: str
    requirement_id: str
    by_id: str
    answer: str
    notes: str | None
    page_version: int | None
    created_at: str
    undone_at: str | None
    name: str | None
    value_type: str | None
    heads: str | None


class SnoozeRow(TypedDict):
    """A task set aside. At most one is open per task (ended_at NULL)."""

    id: str
    task_id: str
    visit_id: str | None
    by_id: str
    why: str | None
    until_at: str | None
    created_at: str
    ended_at: str | None
    ended_by_id: str | None
    filed: int  # 1: written as a bot filed its own idea into Product's Ideas; only a person ends it (idea_checks)


class IdeaCheckRow(TypedDict):
    """Jev's duplicate check on an idea filed into Product's Ideas (backend/idea_checks.py), owed until checked_at."""

    task_id: str
    visit_id: str | None
    owed_at: str
    checked_at: str | None
    outcome: str | None
    same_id: str | None
    confidence: float | None
    call_id: str | None


class OkGrantRow(TypedDict):
    """An OK bound to exactly what was shown, used once (backend/task_asks.py). One per OK requirement."""

    requirement_id: str
    asked_by_id: str
    subject: str
    content_hash: str
    content: str
    filed: int
    created_at: str
    ok_at: str | None
    ok_by_id: str | None
    answer_id: str | None
    used_at: str | None
    used_by_id: str | None
    asked_of_id: str | None
    revoked_at: str | None


class RunStartRow(TypedDict):
    """The order runs started in: every start takes the next id (migration 20261110100007_run_starts)."""

    id: int
    attempt_id: str


class OkShownRow(TypedDict):
    """Who was shown which content of an OK, in their own session."""

    requirement_id: str
    person_id: str
    content_hash: str
    shown_at: str


class LinkRow(TypedDict):
    """A task filed by another task, during one of its visits."""

    id: str
    from_task_id: str
    to_task_id: str
    visit_id: str | None
    kind: str
    created_at: str


class EffectRow(TypedDict):
    """A source action a move still owes (`act`) or owes back (`undo`), keyed so it happens once
    (backend/source_actions.py)."""

    id: str
    visit_id: str
    kind: str
    source: str
    action: str
    item_type: str
    item_key: str
    pinned: str | None
    key: str
    state: str
    tries: int
    last_error: str | None
    next_try_at: str | None
    created_at: str
    updated_at: str


class ItemMetadataRow(TypedDict):
    """Tico's saved copy of one item of a source (backend/item_metadata.py). Newest `metadata_version` wins."""

    id: str
    source: str
    item_type: str
    item_key: str
    title: str | None
    fields: str
    metadata_version: int
    read_at: str | None
    due_at: str | None
    read_error: str | None
    refused_at: str | None
    gone_at: str | None
    final_at: str | None
    created_at: str
    updated_at: str


class GithubReleaseRow(TypedDict):
    """A published release of a repo, never a draft (backend/item_sources/github.py)."""

    id: str
    repo: str
    tag: str
    commit_sha: str | None
    prerelease: int
    published_at: str
    metadata_version: int
    read_at: str
    created_at: str


class GithubReleaseReadRow(TypedDict):
    """When a repo's releases were last read: none saved after a read means it publishes none (C31)."""

    repo: str
    read_at: str
    metadata_version: int


class GithubReleaseCommitRow(TypedDict):
    """Whether a commit is in a release's history, read once from GitHub."""

    release_id: str
    commit_sha: str
    included: int
    checked_at: str


class GithubPrDiffRow(TypedDict):
    """What a PR commit changes, as one id (item_sources.github.diff_id); None when GitHub could not give it all. With
    it, as JSON, the PR's commits up to it ([[sha, title]]) and each file's part of the diff ({name: id}); None when
    read before Tico kept them or GitHub did not give them all."""

    repo: str
    sha: str
    diff_id: str | None
    commits: str | None
    files: str | None
    read_at: str


class GithubDeliveryRow(TypedDict):
    """A webhook delivery already taken: a repeat of it is dropped."""

    id: str
    event: str
    received_at: str


class RequirementPageRow(TypedDict):
    """The page attached to a page requirement for one visit, at one version (backend/task_pages.py)."""

    id: str
    visit_id: str
    requirement_id: str
    file_id: str
    version: int
    item_versions: str
    attached_by_id: str
    created_at: str
    replaced_at: str | None
    rubric_hash: str | None
    judged_against: str


class PageVerdictRow(TypedDict):
    """Jev's verdict on one page version against one rubric (backend/page_verdicts.py). NULL verdict: still owed."""

    id: str
    file_id: str
    version: int
    rubric_hash: str
    verdict: str | None
    p: float | None
    reasons: str | None
    model: str | None
    cost_usd: float
    tries: int
    last_error: str | None
    next_try_at: str | None
    created_at: str
    updated_at: str


class TaskMoveGuardRow(TypedDict):
    """The one row the tasks_move_only trigger reads: open is 1, for one task, only inside task_engine's write."""

    id: int
    open: int
    task_id: str | None


class ListeningSourceRow(TypedDict):
    """One source of a listening's trigger (backend/listening.py). `key` is its id in the flow."""

    id: str
    automation_id: str
    key: str
    kind: str
    site: str
    target: str
    status: str
    last_read_at: str | None
    last_ok_at: str | None
    last_error: str | None
    token_hash: str | None
    snapshot: str | None
    deleted_at: str | None
    created_at: str
    updated_at: str


class ListeningItemRow(TypedDict):
    """One post a source saved, and what became of it (backend/listening.py)."""

    id: str
    automation_id: str
    source_id: str
    native_id: str
    url: str
    author: str
    title: str
    text: str
    posted_at: str | None
    text_key: str
    status: str
    tag: str
    score: float | None
    copies: int
    duplicate_of_id: str | None
    tries: int
    claimed_at: str | None
    scored_at: str | None
    run_id: str | None
    sent_at: str | None
    delivered: str | None  # JSON list of the step ids that delivered it, for an item not all steps took
    cost: float
    created_at: str
    updated_at: str


# Customer facts Growth computes cohorts from (backend/growth_customers.py): dates are YYYY-MM-DD in the team's zone.
class GrowthCustomerRow(TypedDict):
    source: str
    customer_key: str
    became: str
    signed_up: str | None
    channel: str | None
    segment: str | None
    plan: str | None
    churned: str | None
    revenue_month: float | None
    loaded_at: str


class GrowthCustomerLoadRow(TypedDict):
    id: str
    source: str
    customers: int
    removed: int
    loaded_by: str | None
    loaded_at: str
