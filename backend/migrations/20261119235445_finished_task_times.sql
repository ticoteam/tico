-- The finished tasks' times, in the orders two reads ask for them. Only finished tasks are in them, so the open
-- tasks' frequent writes never touch them.
-- tasks_done_at: the Dashboard's and a team's cards count the tasks done since a date (backend/cards.py
-- tasks_by_status, done_per_week), a range of this index instead of a scan of every task.
-- tasks_finished_time: the Done and Closed archives (`sort=finished`, backend/hubdb.py tasks) list one status most
-- recently finished first. The index holds that order, its expression word for word, so a page reads its rows in
-- order and stops at its limit instead of sorting every finished task; the read names the index's WHERE.
CREATE INDEX IF NOT EXISTS tasks_done_at ON tasks(done_at) WHERE status='done';
CREATE INDEX IF NOT EXISTS tasks_finished_time ON tasks(status, COALESCE(done_at,closed_at,updated_at,created_at) DESC, id DESC) WHERE status IN ('done','closed');
