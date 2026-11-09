-- The refs keys that route, queue and fence a message, as typed columns: every `messages` column plus
--   ref_task_id  the task it is about (`refs.task`; a list names its first task, as hubdb._task_ref reads it)
--   ref_wake     why it wakes its bot (`refs.wake`: a sweep, a thread's waiting notes, a backlog digest)
--   ref_quiet    1 when it starts no run (`refs.quiet` set to anything but false, null or 0), else 0
--   ref_command  1 when it is a command for the bot's harness (`refs.command`), else 0
--   ref_live_kind what a live meeting line is (`refs.live.kind`: transcript, decision, tool, ask, batch)
-- Readers that filter on these keys read `message_refs` where they read `messages`. `refs` stays the stored record:
-- writers, the API and the runner wire keep it as it is. A view, so nothing is stored twice or kept in step with the
-- many writers of `refs`, a row whose refs are not JSON reads NULL here instead of failing the query (json_valid flag 6
-- accepts what json_extract reads: JSON5 text such as Infinity, and JSONB), and a
-- `SELECT *` on `messages` returns what it always did. The planner inlines it: a query on it costs what the same
-- query with json_extract on `messages` does.
CREATE VIEW IF NOT EXISTS message_refs AS SELECT messages.*,
 CASE WHEN json_valid(refs,6) THEN CASE WHEN json_type(refs,'$.task')='array' THEN json_extract(refs,'$.task[0]')
  ELSE json_extract(refs,'$.task') END END AS ref_task_id,
 CASE WHEN json_valid(refs,6) THEN json_extract(refs,'$.wake') END AS ref_wake,
 CASE WHEN json_valid(refs,6) AND coalesce(json_extract(refs,'$.quiet'),0)<>0 THEN 1 ELSE 0 END AS ref_quiet,
 CASE WHEN json_valid(refs,6) AND coalesce(json_extract(refs,'$.command'),0)<>0 THEN 1 ELSE 0 END AS ref_command,
 CASE WHEN json_valid(refs,6) THEN json_extract(refs,'$.live.kind') END AS ref_live_kind
FROM messages;
