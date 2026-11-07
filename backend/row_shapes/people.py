"""People, alerts and installs: pages a person read, alert settings, recorder installs, bot settings and principals.

Rows come from `sqlite3.Row` (SELECT *); see `backend/rows.py` for the rules."""

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


class PrincipalRow(TypedDict):
    """One `principals` row (SELECT *): an actor string's identity (`human:<id>`, `bot:<bot id>`, a system actor).
    A deleted human or bot keeps its row with `deleted_at` set (backend/migrations/0012_bot_ids.py)."""

    id: str
    kind: str
    deleted_at: str | None


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


TABLES = {
    PageIdentityRow: "page_identities",
    AlertPreferenceRow: "alert_preferences",
    RecorderInstallRow: "recorder_installs",
    AlertCompanySettingRow: "alert_company_settings",
    PersonalAlertRow: "personal_alerts",
    PrincipalRow: "principals",
    BotSettingsRow: "bot_settings",
}
