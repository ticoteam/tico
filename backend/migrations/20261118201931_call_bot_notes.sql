CREATE TABLE call_bot_notes (
    call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    bot_id TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    text TEXT NOT NULL DEFAULT '',
    prep_task_id TEXT,
    PRIMARY KEY (call_id, bot_id)
);

-- Active calls already prepped by the scheduled sweep can have a task whose wake names the call.
INSERT INTO call_bot_notes(call_id, bot_id, prep_task_id)
SELECT call_id, bot_id, prep_task_id
FROM (
    SELECT c.id AS call_id, b.bot AS bot_id, (
        SELECT t.id
        FROM tasks t
        WHERE t.owner_id='bot:' || b.bot AND t.requester_id=c.connector_actor AND t.title='Prepare for ' || c.title
          AND EXISTS (
              SELECT 1
              FROM messages m
              WHERE json_extract(m.refs,'$.task')=t.id AND m.sender_id='keeper'
                AND json_extract(m.refs,'$.instructions')=1
                AND instr(m.body,'(call ' || c.id || ') in thread ' || c.thread_id || ': read Tico''s skills/meeting-prep/SKILL.md')>0
          )
        ORDER BY t.created_at DESC, t.id
        LIMIT 1
    ) AS prep_task_id
    FROM calls c JOIN live_meeting_bots b ON b.meeting_id=c.live_meeting_id
    WHERE c.state IN ('scheduled','live') AND c.prepped_at IS NOT NULL
)
WHERE prep_task_id IS NOT NULL;
