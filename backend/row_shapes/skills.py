"""Skills: versions, proposals, screening, installs, usage, suggestions and devices.

Rows come from `sqlite3.Row` (SELECT *); see `backend/rows.py` for the rules."""

from typing import TypedDict


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


TABLES = {
    SkillRow: "skills",
    SkillVersionRow: "skill_versions",
    SkillProposalRow: "skill_proposals",
    SkillScreeningRow: "skill_screenings",
    SkillInstallRow: "skill_installs",
    SkillReceiptRow: "skill_receipts",
    SkillUsageRow: "skill_usages",
    SkillUsageDailyRow: "skill_usage_dailies",
    UnlistedSkillUsageDailyRow: "unlisted_skill_usage_dailies",
    SkillSuggestionRow: "skill_suggestions",
    SkillDeviceRow: "skill_devices",
    SkillDeviceCodeRow: "skill_device_codes",
}
