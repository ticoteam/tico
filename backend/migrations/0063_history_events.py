"""One history table. The five logs that each said "this field of X changed from old to new, by whom, when" become
`history_events`, one row per change, keyed by what changed (`subject_type`, `subject_id`):

- `task_events` (subject_type `task`, subject_id the task id; `action_id` and `via` keep their own columns, since the
  Strategy Bot's Undo and the hot task reads filter on them);
- `goal_events` (`goal`; `status_by` and `status_source` go into `data`);
- `market_events` (`market_entity`, `market_edge`, `market_evidence` or `market_document`, from `subject_kind`;
  `insight_id` goes into `data`);
- `company_events` (`company`, subject_id `company`, the company_profile row);
- `strategy_events` (`strategy`, subject_id `strategy`; its `kind` is the row's kind and its `payload` the row's `data`).

The old `field` is `kind`, `actor` is `actor_id`. Ids are kept, so anything that names an event (an insight's events,
a Strategy Bot action's rows) still finds it; a row whose id another source already used (none is expected: every id
is a random UUID) is stored as `<subject_type>:<id>`. A task's rows keep their rowids: a persisted key names one (a
verdict's cycle is the rowid of the task's last reopen, in each message's `refs.subtask_result`), so the task log is
copied first, rowid and all, and the move refuses if any of its rows did not land with that rowid. The other sources
follow, each in its own rowid order, and take the next free rowids.

A task's old log allowed a NULL `field`, a NULL time and a NULL task id. A NULL `field` and time stay NULL (the readers
return null, as they always did), so `kind` and `created_at` are nullable here; a NULL task id is stored as '', as
before, since no task could read that row.

Rows of every type but `task` are append-only (triggers): a task's rows go with the task when it is deleted, and two
markers on them are rewritten in place (move_retry's failed_at, the close rule's note).

Kept apart, being something else: `audit_log` (the append-only security log), `events` (the hub's action log, which
provenance and read-dependency triggers watch), `attempt_events` (a run's output stream), `changes` (the change feed's
cursor), `bot_status_history` (status intervals whose end is written later), `settings_changes` (undoable settings
changes) and `bot_file_activity` (the Files version ledger: integer cursor ids, a version and digest per row that review
receipts match, strictly append-only).

On a new file hubdb's numbered steps have just made the three hubdb tables, empty; the module tables (company_events,
strategy_events) do not exist and are no longer created. A market subject kind outside the four refuses the migration
with the count, rather than storing a type the CHECK does not know.
"""
SUBJECT_TYPES = ("task", "goal", "company", "strategy",
                 "market_entity", "market_edge", "market_evidence", "market_document")
MARKET_KINDS = ("entity", "edge", "evidence", "document")

TABLE = ("CREATE TABLE IF NOT EXISTS history_events(\n"
         " id TEXT PRIMARY KEY,\n"
         " subject_type TEXT NOT NULL CHECK (subject_type IN (" + ", ".join(f"'{t}'" for t in SUBJECT_TYPES) + ")),\n"
         " subject_id TEXT NOT NULL, actor_id TEXT, kind TEXT, old TEXT, new TEXT, note TEXT,\n"
         " action_id TEXT, via TEXT, data TEXT, created_at TEXT)")
INDEXES = (
    # One subject's history, and one field of it (a task's status, owner, waiting_on).
    "CREATE INDEX IF NOT EXISTS history_events_subject ON history_events(subject_type, subject_id, created_at)",
    "CREATE INDEX IF NOT EXISTS history_events_subject_kind ON history_events(subject_type, subject_id, kind, created_at)",
    # One field across a type (the Tasks page's stats, replay, the market's weekly delta by kind).
    "CREATE INDEX IF NOT EXISTS history_events_kind ON history_events(subject_type, kind, created_at)",
    # A type's newest rows (the company's history, the market's weekly delta, the strategy's history).
    "CREATE INDEX IF NOT EXISTS history_events_type_created ON history_events(subject_type, created_at)",
    # A Strategy Bot decision's rows, for its Undo; most rows have none.
    "CREATE INDEX IF NOT EXISTS history_events_action ON history_events(action_id) WHERE action_id IS NOT NULL",
)
TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS history_events_append_only BEFORE UPDATE ON history_events "
    "WHEN OLD.subject_type<>'task' BEGIN SELECT RAISE(ABORT, 'history_events is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS history_events_no_delete BEFORE DELETE ON history_events "
    "WHEN OLD.subject_type<>'task' BEGIN SELECT RAISE(ABORT, 'history_events is append-only'); END",
)
# The sources go once copied. Spelled out, so the dev-copy table check sees each one dropped for good.
DROPS = ("DROP TABLE IF EXISTS task_events;", "DROP TABLE IF EXISTS goal_events;", "DROP TABLE IF EXISTS market_events;",
         "DROP TABLE IF EXISTS company_events;", "DROP TABLE IF EXISTS strategy_events;")


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _copy(conn, table, subject_type, subject_id, kind, old, new, note, data, action_id="NULL", via="NULL",
          keep_rowid=False):
    # One set-based statement per source. OR IGNORE makes a rerun skip the rows already copied. The clash test looks
    # for an id another type's row has (none is expected); this type's own copies do not count as a clash. keep_rowid
    # copies the source's rowid as well (the task log, which goes first); the others take the next free rowids.
    rowid, source_rowid = ("rowid, ", "s.rowid, ") if keep_rowid else ("", "")
    conn.execute(
        f"INSERT OR IGNORE INTO history_events({rowid}id, subject_type, subject_id, actor_id, kind, old, new, note, "
        "action_id, via, data, created_at) "
        f"SELECT {source_rowid}CASE WHEN EXISTS (SELECT 1 FROM history_events h WHERE h.id=s.id AND h.subject_type<>{subject_type}) "
        f"THEN {subject_type}||':'||s.id ELSE s.id END, "
        f"{subject_type}, coalesce({subject_id}, ''), s.actor, {kind}, {old}, {new}, {note}, "
        f"{action_id}, {via}, {data}, s.created_at "
        f'FROM "{table}" s ORDER BY s.rowid')


def up(conn):
    conn.execute(TABLE)
    for statement in INDEXES + TRIGGERS:
        conn.execute(statement)
    have = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "market_events" in have:
        marks = ",".join("?" * len(MARKET_KINDS))
        unknown = conn.execute(f"SELECT subject_kind, count(*) FROM market_events WHERE subject_kind NOT IN ({marks}) "
                               "OR subject_kind IS NULL GROUP BY subject_kind", MARKET_KINDS).fetchall()
        if unknown:
            raise RuntimeError(f"market_events has subject kinds history_events does not know: {unknown}")
    if "task_events" in have:
        columns = _columns(conn, "task_events")
        _copy(conn, "task_events", "'task'", "s.task_id", "s.field", "s.old", "s.new",
              "s.note", "NULL", action_id="s.action_id" if "action_id" in columns else "NULL",
              via="s.via" if "via" in columns else "NULL", keep_rowid=True)
        # A task's persisted keys name its rows by rowid, so each one must have landed with its own rowid and id.
        lost = conn.execute("SELECT count(*) FROM task_events s WHERE NOT EXISTS (SELECT 1 FROM history_events h "
                            "WHERE h.rowid=s.rowid AND h.id=s.id)").fetchone()[0]
        if lost:
            raise RuntimeError(f"{lost} task_events rows did not keep their rowid and id in history_events")
    if "goal_events" in have:
        columns = _columns(conn, "goal_events")
        status = [name for name in ("status_by", "status_source") if name in columns]
        data = ("CASE WHEN " + " OR ".join(f"s.{n} IS NOT NULL" for n in status) + " THEN json_object("
                + ", ".join(f"'{n}', s.{n}" for n in status) + ") END") if status else "NULL"
        _copy(conn, "goal_events", "'goal'", "s.goal_id", "s.field", "s.old", "s.new", "s.note", data)
    if "market_events" in have:
        _copy(conn, "market_events", "'market_'||s.subject_kind", "s.subject_id", "s.field", "s.old", "s.new", "s.note",
              "CASE WHEN s.insight_id IS NOT NULL THEN json_object('insight_id', s.insight_id) END")
    if "company_events" in have:
        _copy(conn, "company_events", "'company'", "'company'", "s.field", "s.old", "s.new", "NULL", "NULL")
    if "strategy_events" in have:
        _copy(conn, "strategy_events", "'strategy'", "'strategy'", "s.kind", "NULL", "NULL", "NULL", "s.payload")
    for statement in DROPS:
        conn.execute(statement)
