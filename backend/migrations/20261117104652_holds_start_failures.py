"""Start failures move into `holds` (reports D1, PR 4 of "one holds system"): kinds `claim_error` and `cant_start`
(backend/bot_stops.py).

1. A queued run a claim held (jobs.hold_reason, the claim's reason) gets an open claim_error hold on the job: its wait
   (hold_until), count (hold_streak) and Needs-you item (hold_task_id), from its latest job.claim_failed event. Its
   held flag goes: the claim reads the hold's wait now, and jobs.held is a wake preset's alone.
2. A queued run an older server held for its login's quota reset (wake_class 'quota-reset') is let go: the login's hold
   does that waiting now (backend/login_holds.py).
3. Each task a `task.cant_start` event holds while the BotOps task it names is open gets an open cant_start hold on the
   task for that bot, naming BotOps's task (its latest event per task and bot).
4. jobs.hold_reason, hold_task_id and hold_streak go. jobs.held and hold_until stay, for wake presets.
5. Two triggers release a run's start-failure holds when the run ends, and a task's cant_start hold when it or BotOps's
   task closes, so no open hold outlives its run or task.
A hand-back's wait in flight is not copied: it was read from the run's attempts, and lasts at most five minutes.
Ids name the kind and the thing, never a fresh id. Running this again changes nothing.
"""

# The holds table as 20261117061834_holds_runaway makes it, word for word: pending migrations run in any order.
HOLDS = """
CREATE TABLE IF NOT EXISTS holds (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('paused','failing','runaway','claim_error','cant_start','quarantine','usage_limit')),
  holdable_type TEXT NOT NULL CHECK (holdable_type IN ('company','login','bot','task','conversation','job','automation')),
  holdable_id TEXT NOT NULL,
  bot_id TEXT REFERENCES bots(id),
  reason TEXT NOT NULL DEFAULT '',
  details TEXT NOT NULL DEFAULT '{}',
  streak INTEGER NOT NULL DEFAULT 0,
  until_at TEXT,
  probe_at TEXT, probe_job_id TEXT,
  upkeep_task_id TEXT,
  created_by_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  released_at TEXT, released_by_id TEXT,
  released_how TEXT CHECK (released_how IN ('expired','probe_passed','person','botops','raised','moot','superseded'))
);
-- At most one open hold of a kind on one thing (holds.put upserts on it).
CREATE UNIQUE INDEX IF NOT EXISTS holds_open ON holds(kind, holdable_type, holdable_id, coalesce(bot_id,''))
  WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS holds_bot ON holds(bot_id) WHERE released_at IS NULL;
-- The last release of a kind on one thing: a run budget counts runs from it, on every claim of a bot's own work.
CREATE INDEX IF NOT EXISTS holds_released ON holds(holdable_type, holdable_id, kind, released_at)
  WHERE released_at IS NOT NULL;
"""

CLAIM_REASON = "Couldn't start: an error while preparing this run"

# A run's claim_error and cant_start holds end with the run: nothing waits on a run that completed, failed or was
# canceled. Two seeks of holds_open per job that ends.
JOB_ENDS = (
    "CREATE TRIGGER IF NOT EXISTS holds_job_ends AFTER UPDATE OF state ON jobs "
    "WHEN NEW.state IN ('completed','failed','canceled') AND OLD.state NOT IN ('completed','failed','canceled') "
    "BEGIN UPDATE holds SET released_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),released_by_id='keeper',"
    "released_how='moot',updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
    "WHERE kind IN ('claim_error','cant_start') AND holdable_type='job' AND holdable_id=NEW.id "
    "AND released_at IS NULL; END"
)
# A task's cant_start hold ends when BotOps's task about it closes (then the never-stuck check wakes the bot on the task
# again), or when the held task itself closes. The open cant_start holds are few: a seek of holds_open on the kind.
TASK_CLOSES = (
    "CREATE TRIGGER IF NOT EXISTS holds_cant_start_closes AFTER UPDATE OF status ON tasks "
    "WHEN OLD.status='open' AND NEW.status IS NOT 'open' "
    "BEGIN UPDATE holds SET released_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),released_by_id='keeper',"
    "released_how=CASE WHEN upkeep_task_id=NEW.id THEN 'botops' ELSE 'moot' END,"
    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
    "WHERE kind='cant_start' AND holdable_type='task' AND released_at IS NULL "
    "AND (upkeep_task_id=NEW.id OR holdable_id=NEW.id); END"
)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    for statement in HOLDS.split(";"):
        if statement.strip():
            conn.execute(statement)
    jobs = _columns(conn, "jobs")
    if "hold_reason" in jobs:
        conn.execute(
            "INSERT OR IGNORE INTO holds(id,kind,holdable_type,holdable_id,bot_id,reason,details,streak,until_at,"
            "upkeep_task_id,created_by_id,created_at,updated_at) "
            "SELECT 'claim_error:job:'||j.id,'claim_error','job',j.id,j.bot_id,j.hold_reason,"
            "json_object('error',coalesce(json_extract(e.detail,'$.error'),'')),max(j.hold_streak,1),"
            "coalesce(j.hold_until,strftime('%Y-%m-%dT%H:%M:%fZ','now')),j.hold_task_id,'keeper',"
            "coalesce(e.created_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')),strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "FROM jobs j JOIN bots b ON b.id=j.bot_id "
            "LEFT JOIN events e ON e.id=(SELECT x.id FROM events x WHERE x.actor='keeper' AND x.action='job.claim_failed' "
            "AND x.target=j.id ORDER BY x.created_at DESC,x.rowid DESC LIMIT 1) "
            "WHERE j.state='queued' AND j.hold_reason=?",
            (CLAIM_REASON,),
        )
        conn.execute(
            "UPDATE jobs SET held=0,hold_until=NULL WHERE state='queued' AND (hold_reason=? OR wake_class='quota-reset')",
            (CLAIM_REASON,),
        )
        for column in ("hold_reason", "hold_task_id", "hold_streak"):
            conn.execute(f"ALTER TABLE jobs DROP COLUMN {column}")
    conn.execute(
        "INSERT OR IGNORE INTO holds(id,kind,holdable_type,holdable_id,bot_id,reason,details,upkeep_task_id,"
        "created_by_id,created_at,updated_at) "
        "SELECT 'cant_start:task:'||e.target||':'||b.id,'cant_start','task',e.target,b.id,"
        "coalesce(json_extract(e.detail,'$.reason'),''),json_object('job',json_extract(e.detail,'$.job')),r.id,"
        "'keeper',e.created_at,e.created_at "
        "FROM events e JOIN bots b ON b.id=json_extract(e.detail,'$.bot') "
        "JOIN tasks r ON r.id=json_extract(e.detail,'$.review') AND r.status='open' "
        "JOIN tasks t ON t.id=e.target AND t.status='open' "
        "WHERE e.actor='keeper' AND e.action='task.cant_start' AND NOT EXISTS (SELECT 1 FROM events n "
        "WHERE n.actor='keeper' AND n.action='task.cant_start' AND n.target=e.target "
        "AND json_extract(n.detail,'$.bot')=b.id AND (n.created_at>e.created_at OR n.created_at=e.created_at "
        "AND n.rowid>e.rowid))"
    )
    conn.execute(JOB_ENDS)
    conn.execute(TASK_CLOSES)
