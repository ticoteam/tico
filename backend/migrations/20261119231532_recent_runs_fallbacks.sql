-- Health's fallback line (backend/health.py _fallbacks) reads each finished turn of the last day: when it began, its
-- bot and model, and two fields of its result. This index holds exactly those, so each Health poll reads them from the
-- index instead of loading and parsing every recent turn's result document; a turn's fields are extracted once, when it
-- finishes. Its WHERE and expressions are the query's, word for word.
CREATE INDEX IF NOT EXISTS attempts_recent_runs ON attempts(
  finished_at,
  bot_id,
  coalesce(started_at, created_at),
  json_extract(result, '$.fallback'),
  json_extract(result, '$.waiting_for_sign_in'),
  usage_model
) WHERE state IN ('completed', 'failed', 'interrupted');
