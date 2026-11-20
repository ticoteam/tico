-- Covering partial index for open bot task counts (backend/bot_rows.py) and slim wait reads (backend/waiting_on.py),
-- so GET /api/v2/bots never reads tasks.body or overflow pages.
CREATE INDEX IF NOT EXISTS tasks_open_owner_flow ON tasks(
    owner_id,
    id,
    requester_id,
    status,
    conversation_id,
    private,
    readers,
    pipeline_id,
    stage_id,
    assignee_id,
    human_id,
    version,
    created_at,
    live_meeting_id
) WHERE status='open';
