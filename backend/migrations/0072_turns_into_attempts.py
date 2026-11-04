"""A run is one row: `turns` moves into `attempts`, and `deltas.turn_id` becomes `deltas.attempt_id`.

Until now a run was split in two. The runner's lease, state and result lived in `attempts`; what woke the run, its
summary, its outcome and its usage lived in a `turns` row with the same id. This moves every turns column onto its
attempts row and drops `turns`:

- `turns.finished_at` becomes `attempts.settled_at`: when the run's record was closed (its exit, summary and usage
  written). It is not `attempts.finished_at`, which the lease's state machine sets a moment earlier and a late result
  never moves; `settled_at` is NULL while the run is open, and a restored lease opens it again.
- `turns.model` becomes `attempts.usage_model`, the model the usage was reported for; `attempts.model` is the model
  record the claim handed out (backend/task_models.py) and keeps its name.
- `turns.started_at` is `attempts.created_at` (both were stamped by the same claim) and `turns.bot` is `attempts.bot_id`.
  `turns.thread_id` was always written together with `attempts.thread_id`: attempts keeps its own and takes the turn's
  only where it has none.
- Every other column keeps its name: trigger, message_id, task_id, exit, summary, the token and cost columns
  (input_tokens, cached_tokens, output_tokens, prompt_tokens, provider, est_cost_usd, billing, usage_reason),
  trigger_kind, trigger_source, writes, outcome, did_nothing, purpose, rotated, session_kind, session_why, rule_id.
  Their values and meaning do not change.

A turn with no attempts row (recorded before every run had a lease, or by the demo seed) becomes an attempts row with
no lease: job_id, runner_id, generation, token_hash, state and lease_until are NULL. So those six columns become
nullable, with a CHECK that a run with a job still has all of its lease, and attempts.state's value triggers allow NULL
(backend/status_values.py). An attempts row with no turns row keeps `trigger` NULL: it never had a run record, and
backend/hubdb.py TURN_SELECT reads only rows that have one (`job_id IS NULL OR trigger IS NOT NULL`).

Usage needs no copy here: migration 0053 already moved each run's segments into usage_facts (keyed by run_id, which is
the attempts id), and turn_usage_segments is gone before this runs.

`deltas.turn_id` becomes `attempt_id` first, whether or not `turns` is still there.

The whole move is one transaction (migrations._run), with foreign keys off for the rebuild: the tables that refer to
attempts(id) keep their rows and ids. Interrupted, nothing is kept, and the next start runs it again from the top. The
copy is one INSERT ... SELECT per table, read by SQLite's own cursor rather than into Python, so no batching is needed
inside that transaction. A file that never had turns (a new file, where backend/store.py makes attempts in the new
shape after the migrations) is skipped: there is nothing to move.

Indexes and triggers on attempts go with the old table, so their saved text is put back after the rename.
backend/store.py SCHEMA and Store.initialize create the attempts indexes on every boot; status_values._ensure_triggers
rewrites attempts.state's triggers so they allow NULL.
"""



from backend import status_values

FOREIGN_KEYS_OFF = True

# The attempts table in its new shape: the lease columns nullable, with the run record's columns on the same row.
# A column the file has that this does not name (added by a later change) is kept: _rebuild_attempts adds it.
ATTEMPTS = """CREATE TABLE {name}(
 id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(id), bot_id TEXT NOT NULL,
 runner_id TEXT REFERENCES runners(id), generation INTEGER,
 token_hash TEXT UNIQUE, state TEXT, lease_until TEXT,
 created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, thread_id TEXT,
 result TEXT, final_text TEXT, last_seq INTEGER NOT NULL DEFAULT 0,
 stop_requested_at TEXT, gateway_key_id TEXT, handed_runtime TEXT, handed_model TEXT,
 lane TEXT NOT NULL DEFAULT 'main', wake TEXT, model TEXT,
 settled_at TEXT, trigger TEXT, message_id TEXT, task_id TEXT, exit TEXT, summary TEXT,
 trigger_kind TEXT, trigger_source TEXT, purpose TEXT, rule_id TEXT, writes INTEGER, outcome TEXT,
 did_nothing INTEGER, rotated TEXT, session_kind TEXT, session_why TEXT,
 input_tokens INTEGER, cached_tokens INTEGER, output_tokens INTEGER, prompt_tokens INTEGER,
 usage_model TEXT, provider TEXT, est_cost_usd REAL, billing TEXT, usage_reason TEXT,
 CHECK (job_id IS NULL OR (runner_id IS NOT NULL AND generation IS NOT NULL AND token_hash IS NOT NULL
        AND state IS NOT NULL AND lease_until IS NOT NULL)))"""

# turns column -> attempts column, for the columns that move as they are. Every column main's turns table has is here
# (or is the id, bot, started_at or thread_id above): a column added to turns later must be added here too.
MOVED = {"finished_at": "settled_at", "model": "usage_model",
         **{name: name for name in (
             "trigger", "message_id", "task_id", "exit", "summary", "trigger_kind", "trigger_source", "purpose",
             "rule_id", "writes", "outcome", "did_nothing", "rotated", "session_kind", "session_why", "input_tokens",
             "cached_tokens", "output_tokens", "prompt_tokens", "provider", "est_cost_usd", "billing",
             "usage_reason")}}


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn, table):
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _orphans(conn, target, turn_columns, guard):
    """Insert every turn that has no attempts row into `target`, as a run with no lease."""
    moved = [(old, new) for old, new in MOVED.items() if old in turn_columns]
    columns = ["id", "bot_id", "created_at", "thread_id", *(new for _, new in moved)]
    picked = ["t.id", "coalesce(t.bot,'')", "coalesce(t.started_at,t.finished_at,'')",
              "t.thread_id" if "thread_id" in turn_columns else "NULL", *(f"t.{old}" for old, _ in moved)]
    conn.execute(f"INSERT INTO {target} ({', '.join(columns)}) SELECT {', '.join(picked)} FROM turns t {guard}")


def _rebuild_attempts(conn, turn_columns):
    """attempts in the new shape, each turn's columns on its row, and the turns that had no attempts row as rows of
    their own. Indexes and triggers come back from their saved text."""
    kept = [row[0] for row in conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name='attempts' AND type IN ('index','trigger') AND sql IS NOT NULL "
        "ORDER BY type, name")]
    have = [(row[1], row[2]) for row in conn.execute('PRAGMA table_info("attempts")')]
    target = "attempts__rebuild"
    conn.execute(ATTEMPTS.format(name=target))
    wanted = set(_columns(conn, target))
    for name, kind in have:
        if name not in wanted:
            # A column this migration does not name: kept as it was, so no value is dropped.
            conn.execute(f'ALTER TABLE {target} ADD COLUMN "{name}" {kind or "TEXT"}')
            wanted.add(name)
    own = [name for name, _ in have]
    moved = [(old, new) for old, new in MOVED.items() if old in turn_columns and new not in own]
    columns = [*own, *(new for _, new in moved)]
    picked = [("coalesce(a.thread_id,t.thread_id)" if c == "thread_id" and "thread_id" in turn_columns else f"a.{c}")
              for c in own] + [f"t.{old}" for old, _ in moved]
    before = conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
    turns = conn.execute("SELECT count(*) FROM turns").fetchone()[0]
    conn.execute(f"INSERT INTO {target} ({', '.join(columns)}) SELECT {', '.join(picked)} "
                 "FROM attempts a LEFT JOIN turns t ON t.id=a.id")
    _orphans(conn, target, turn_columns, "WHERE NOT EXISTS (SELECT 1 FROM attempts a WHERE a.id=t.id)")
    matched = conn.execute(
        "SELECT count(*) FROM turns t WHERE EXISTS (SELECT 1 FROM attempts a WHERE a.id=t.id)").fetchone()[0]
    after = conn.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
    if after != before + turns - matched:
        raise RuntimeError(f"attempts: the merge made {after} rows of {before} attempts and {turns - matched} turns")
    conn.execute("DROP TABLE attempts")
    conn.execute(f"ALTER TABLE {target} RENAME TO attempts")
    for statement in kept:
        conn.execute(statement)


def _create_attempts(conn, turn_columns):
    """No attempts table yet (a file only hubdb opened): the new shape, holding the turns."""
    conn.execute(ATTEMPTS.format(name="attempts"))
    _orphans(conn, "attempts", turn_columns, "")


def up(conn):
    tables = _tables(conn)
    prior = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
    # With it off, a rename refuses while a trigger on another table names a table the rename is changing.
    conn.execute("PRAGMA legacy_alter_table=ON")
    try:
        if "deltas" in tables and "turn_id" in _columns(conn, "deltas"):
            conn.execute("ALTER TABLE deltas RENAME COLUMN turn_id TO attempt_id")
        if "turns" not in tables:
            return
        turn_columns = set(_columns(conn, "turns"))
        if "attempts" in tables:
            _rebuild_attempts(conn, turn_columns)
        else:
            _create_attempts(conn, turn_columns)
        spec = next(s for s in status_values.COLUMNS if (s.table, s.column) == ("attempts", "state"))
        status_values._ensure_triggers(conn, spec)
        conn.execute("DROP TABLE turns")
    finally:
        conn.execute(f"PRAGMA legacy_alter_table={prior}")
