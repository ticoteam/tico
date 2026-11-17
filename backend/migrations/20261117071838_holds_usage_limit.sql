-- Login holds move into `holds` (reports D1, PR 6 of "one holds system"): each `login-hold:<login>` row of
-- registry_metadata becomes an open `usage_limit` hold on that login (backend/login_holds.py). Its end, streak and probe
-- go in the row's columns; the rest of the saved hold (whose limit, its reset and notice, the probe's window it cut
-- short) stays as its details. A value that is not a JSON object was read as no hold, and is dropped.
-- Ids are the old key: one per login, and never a fresh id (H.new_id). The keys are deleted after, so running this
-- again changes nothing.
INSERT OR IGNORE INTO holds(id, kind, holdable_type, holdable_id, details, streak, until_at, probe_at, probe_job_id,
  created_by_id, created_at, updated_at)
SELECT key, 'usage_limit', 'login', substr(key, 12),
  json_remove(value, '$.until', '$.streak', '$.probe_job', '$.probe_at'),
  CAST(coalesce(json_extract(value, '$.streak'), 0) AS INTEGER), json_extract(value, '$.until'),
  json_extract(value, '$.probe_at'), json_extract(value, '$.probe_job'), 'keeper',
  coalesce(json_extract(value, '$.since'), strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')),
  strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')
FROM registry_metadata
WHERE key >= 'login-hold:' AND key < 'login-hold;' AND json_valid(value) AND json_type(value) = 'object';
DELETE FROM registry_metadata WHERE key >= 'login-hold:' AND key < 'login-hold;';
