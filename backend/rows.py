"""Row shapes for the hub database (`hub.db`): what `SELECT *` returns for the most-used tables.

These mirror the schema in `backend/store.py` and `backend/hubdb.py`, including the columns the migrations add.
A column that SQLite may leave NULL is `X | None`. They must change with the migrations: `backend/tests/test_rows_schema.py`
fails when a TypedDict and its table disagree.

The shapes are split by area into `backend/row_shapes/`; this module re-exports them and
merges their TABLES maps.

Rows come from `sqlite3.Row`, which reads like a mapping but is not a dict. Code that takes one of these types
converts the row first (`dict(row)`) or annotates the parameter and lets callers pass the row it was given.
"""

from backend.row_shapes.mail import TABLES as MAIL_TABLES
from backend.row_shapes.mail import (
    EmailSendingPolicyRow,
    EmailSendRow,
    EmailSuppressedRecipientRow,
    EmailLegacyAutomationRow,
    EmailSendPreviewRow,
    EmailSendOperationRow,
)
from backend.row_shapes.letters import TABLES as LETTERS_TABLES
from backend.row_shapes.letters import (
    PostalMailGrantRow,
    PostalMailSettingsRow,
    LetterOutboxRow,
    LetterOutboxGrantRow,
    LetterBotSettingRow,
    LetterRow,
    LetterSourceRow,
    LetterProofRow,
)
from backend.row_shapes.people import TABLES as PEOPLE_TABLES
from backend.row_shapes.people import (
    PageIdentityRow,
    AlertPreferenceRow,
    RecorderInstallRow,
    AlertCompanySettingRow,
    PersonalAlertRow,
    PrincipalRow,
    BotSettingsRow,
)
from backend.row_shapes.tasks import TABLES as TASKS_TABLES
from backend.row_shapes.tasks import (
    MeetingTaskLinkRow,
    SuggestionRow,
    TaskRow,
    PresenceRow,
    TaskCheckBackWakeRow,
    TaskHoldRow,
    QuarantineHoldRow,
    FeedbackRow,
    ThreadRankingRow,
    ThreadPinRow,
    MessageRouteRow,
    HomeMergeRow,
    HomeMergeMessageRow,
    HistoryEventRow,
)
from backend.row_shapes.runs import TABLES as RUNS_TABLES
from backend.row_shapes.runs import (
    AttemptRow,
    CloudMachineDeletionRow,
    CloudMachineEnrollmentFailureRow,
    RunnerRow,
    RunnerStateRow,
    ApprovalRow,
    AttemptEventRow,
)
from backend.row_shapes.maintenance import TABLES as MAINTENANCE_TABLES
from backend.row_shapes.maintenance import (
    MemoryTidyPassRow,
    MemoryTidyPacketRow,
    MaintenanceStepRow,
    MaintenanceRunRow,
    MaintenanceRunStepRow,
    HeartbeatStepRow,
    HeartbeatBeatRow,
    HeartbeatStateRow,
    MaintenanceListingRow,
    MaintenanceHandoffRow,
    MaintenanceRequestRow,
)
from backend.row_shapes.usage import TABLES as USAGE_TABLES
from backend.row_shapes.usage import (
    UsageFactRow,
    UsageMeterReadRow,
    BudgetNodeRow,
)
from backend.row_shapes.tools import TABLES as TOOLS_TABLES
from backend.row_shapes.tools import (
    ToolRow,
    ToolCheckRow,
    ToolCallRow,
)
from backend.row_shapes.skills import TABLES as SKILLS_TABLES
from backend.row_shapes.skills import (
    SkillRow,
    SkillVersionRow,
    SkillProposalRow,
    SkillScreeningRow,
    SkillInstallRow,
    SkillReceiptRow,
    SkillUsageRow,
    SkillUsageDailyRow,
    UnlistedSkillUsageDailyRow,
    SkillSuggestionRow,
    SkillDeviceRow,
    SkillDeviceCodeRow,
)


TABLES = {
    **MAIL_TABLES,
    **LETTERS_TABLES,
    **PEOPLE_TABLES,
    **TASKS_TABLES,
    **RUNS_TABLES,
    **MAINTENANCE_TABLES,
    **USAGE_TABLES,
    **TOOLS_TABLES,
    **SKILLS_TABLES,
}


__all__ = [
    "EmailSendingPolicyRow",
    "EmailSendRow",
    "EmailSuppressedRecipientRow",
    "EmailLegacyAutomationRow",
    "EmailSendPreviewRow",
    "EmailSendOperationRow",
    "PostalMailGrantRow",
    "PostalMailSettingsRow",
    "LetterOutboxRow",
    "LetterOutboxGrantRow",
    "LetterBotSettingRow",
    "LetterRow",
    "LetterSourceRow",
    "LetterProofRow",
    "PageIdentityRow",
    "AlertPreferenceRow",
    "RecorderInstallRow",
    "AlertCompanySettingRow",
    "PersonalAlertRow",
    "PrincipalRow",
    "BotSettingsRow",
    "MeetingTaskLinkRow",
    "SuggestionRow",
    "TaskRow",
    "PresenceRow",
    "TaskCheckBackWakeRow",
    "TaskHoldRow",
    "QuarantineHoldRow",
    "FeedbackRow",
    "ThreadRankingRow",
    "ThreadPinRow",
    "MessageRouteRow",
    "HomeMergeRow",
    "HomeMergeMessageRow",
    "HistoryEventRow",
    "AttemptRow",
    "CloudMachineDeletionRow",
    "CloudMachineEnrollmentFailureRow",
    "RunnerRow",
    "RunnerStateRow",
    "ApprovalRow",
    "AttemptEventRow",
    "MemoryTidyPassRow",
    "MemoryTidyPacketRow",
    "MaintenanceStepRow",
    "MaintenanceRunRow",
    "MaintenanceRunStepRow",
    "HeartbeatStepRow",
    "HeartbeatBeatRow",
    "HeartbeatStateRow",
    "MaintenanceListingRow",
    "MaintenanceHandoffRow",
    "MaintenanceRequestRow",
    "UsageFactRow",
    "UsageMeterReadRow",
    "BudgetNodeRow",
    "ToolRow",
    "ToolCheckRow",
    "ToolCallRow",
    "SkillRow",
    "SkillVersionRow",
    "SkillProposalRow",
    "SkillScreeningRow",
    "SkillInstallRow",
    "SkillReceiptRow",
    "SkillUsageRow",
    "SkillUsageDailyRow",
    "UnlistedSkillUsageDailyRow",
    "SkillSuggestionRow",
    "SkillDeviceRow",
    "SkillDeviceCodeRow",
    "TABLES",
]
