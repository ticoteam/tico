"""A run is one row: `turns` moves into `attempts`, and `turn_usage_segments` becomes `attempt_usage_segments`.

Until now a run was split in two. The runner's lease, state and result lived in `attempts`; what woke the run, its
summary, its outcome and its usage lived in a `turns` row with the same id. This moves every turns column onto its
attempts row and drops `turns`:

- `turns.finished_at` becomes `attempts.settled_at`: when the run's record was closed (its exit, summary and usage
  written). It is not `attempts.finished_at`, which the lease's state machine sets a moment earlier and a late result
  never moves; `settled_at` is NULL while the run is open, and a restored lease opens it again.
- `turns.model` becomes `attempts.usage_model`, the model the usage was reported for; `attempts.model` is the model
  record the claim handed out (backend/task_models.py) and keeps its name.
- `turns.started_at` is `attempts.created_at` (both were stamped by the same claim) and `turns.bot` is
  `attempts.bot_id`; `turns.thread_id` was always written together with `attempts.thread_id`.
- Every other column keeps its name: trigger, message_id, task_id, exit, summary, the token and cost columns
  (input_tokens, cached_tokens, output_tokens, prompt_tokens, provider, est_cost_usd, billing, usage_reason),
  trigger_kind, trigger_source, writes, outcome, did_nothing, purpose, rotated, session_kind, session_why, rule_id.
  Their values and meaning do not change.

A turn saved with no trigger takes 'message', turn_start's default: readers tell a run record from a bare lease by
its trigger, and the turn existed. A turn with no attempts row (recorded before every run had a lease, or by the demo seed) becomes an attempts row with
no lease: job_id, runner_id, generation, token_hash, state and lease_until are NULL. So those six columns become
nullable, with a CHECK that a run with a job still has all of its lease, and attempts.state's value triggers allow NULL
(backend/status_values.py). An attempts row with no turns row keeps `trigger` NULL: it never had a run record.

`deltas.turn_id` becomes `attempt_id`. Segments whose turn was already gone refer to nothing and are not copied.

Foreign keys are off for the rebuild (the tables that refer to attempts(id) keep their rows and their ids). A file
with no attempts table (a new file, where backend/store.py would make it after the migrations, a database only hubdb
opened, or a legacy file) gets it here in the new shape, holding its turns, so every reader and writer of a run finds
it; the store's CREATE then has nothing to do.
"""

from backend import status_values

FOREIGN_KEYS_OFF = True

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

SEGMENTS = """CREATE TABLE IF NOT EXISTS attempt_usage_segments (
  attempt_id TEXT NOT NULL REFERENCES attempts(id), position INTEGER NOT NULL,
  input_tokens INTEGER, cached_tokens INTEGER, output_tokens INTEGER,
  model TEXT, provider TEXT, est_cost_usd REAL, billing TEXT,
  runtime TEXT, harness TEXT, effort TEXT, profile TEXT, est_cost_low INTEGER, gateway_key_id TEXT, install_id TEXT,
  PRIMARY KEY(attempt_id, position))"""

# The tables that go once their rows have moved.
DROP = """
DROP TABLE IF EXISTS turn_usage_segments;
DROP TABLE IF EXISTS turns;
"""
# turns column -> attempts column, for the columns that move as they are.
MOVED = {"finished_at": "settled_at", "model": "usage_model",
         **{name: name for name in (
             "trigger", "message_id", "task_id", "exit", "summary", "trigger_kind", "trigger_source", "purpose",
             "rule_id", "writes", "outcome", "did_nothing", "rotated", "session_kind", "session_why", "input_tokens",
             "cached_tokens", "output_tokens", "prompt_tokens", "provider", "est_cost_usd", "billing",
             "usage_reason")}}
SEGMENT_COLUMNS = ("position", "input_tokens", "cached_tokens", "output_tokens", "model", "provider", "est_cost_usd",
                   "billing", "runtime", "harness", "effort", "profile", "est_cost_low", "gateway_key_id", "install_id")


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn, table):
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _picked(old):
    """A turn's column as its attempts row takes it. A run record is told from a bare lease by its trigger
    (`job_id IS NULL OR trigger IS NOT NULL`), so a turn saved with none takes turn_start's default."""
    return "CASE WHEN t.id IS NULL THEN NULL ELSE coalesce(t.trigger,'message') END" if old == "trigger" else f"t.{old}"


def _orphans(conn, target, turn_columns, guard):
    """Insert every turn with no attempts row into `target` as a run with no lease."""
    moved = [(old, new) for old, new in MOVED.items() if old in turn_columns]
    columns = ["id", "bot_id", "created_at", "thread_id", *(new for _, new in moved)]
    picked = ["t.id", "coalesce(t.bot,'')", "coalesce(t.started_at,t.finished_at,'')",
              "t.thread_id" if "thread_id" in turn_columns else "NULL", *(_picked(old) for old, _ in moved)]
    conn.execute(f"INSERT INTO {target} ({', '.join(columns)}) SELECT {', '.join(picked)} FROM turns t {guard}")


def _rebuild_attempts(conn, turn_columns):
    """attempts in the new shape, with each turn's columns on its row and the turns that had none as rows of their own.
    Indexes and triggers come back from their saved text; attempts.state's triggers then allow NULL."""
    kept = [row[0] for row in conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name='attempts' AND type IN ('index','trigger') AND sql IS NOT NULL "
        "ORDER BY type, name")]
    have = _columns(conn, "attempts")
    target = "attempts__rebuild"
    conn.execute(ATTEMPTS.format(name=target))
    wanted = set(_columns(conn, target))
    own = [c for c in have if c in wanted]
    moved = [(old, new) for old, new in MOVED.items() if old in turn_columns and new not in own]
    columns = [*own, *(new for _, new in moved)]
    picked = [("coalesce(a.thread_id,t.thread_id)" if c == "thread_id" and "thread_id" in turn_columns else f"a.{c}")
              for c in own] + [_picked(old) for old, _ in moved]
    before = conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
    turns = conn.execute("SELECT count(*) FROM turns").fetchone()[0]
    conn.execute(f"INSERT INTO {target} ({', '.join(columns)}) SELECT {', '.join(picked)} "
                 "FROM attempts a LEFT JOIN turns t ON t.id=a.id")
    _orphans(conn, target, turn_columns, "WHERE NOT EXISTS (SELECT 1 FROM attempts a WHERE a.id=t.id)")
    matched = conn.execute("SELECT count(*) FROM turns t WHERE EXISTS (SELECT 1 FROM attempts a WHERE a.id=t.id)").fetchone()[0]
    after = conn.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
    if after != before + turns - matched:
        raise RuntimeError(f"attempts: the merge made {after} rows of {before} attempts and {turns - matched} turns")
    conn.execute("DROP TABLE attempts")
    conn.execute(f"ALTER TABLE {target} RENAME TO attempts")
    for statement in kept:
        conn.execute(statement)


def _segments(conn):
    """attempt_usage_segments in place of turn_usage_segments, with every segment whose run exists."""
    conn.execute(SEGMENTS)
    have = set(_columns(conn, "turn_usage_segments"))
    columns = [c for c in SEGMENT_COLUMNS if c in have]
    conn.execute(f"INSERT OR IGNORE INTO attempt_usage_segments (attempt_id, {', '.join(columns)}) "
                 f"SELECT s.turn_id, {', '.join('s.' + c for c in columns)} FROM turn_usage_segments s "
                 "WHERE EXISTS (SELECT 1 FROM attempts a WHERE a.id=s.turn_id)")


def up(conn):
    tables = _tables(conn)
    if "deltas" in tables and "turn_id" in _columns(conn, "deltas"):
        conn.execute("ALTER TABLE deltas RENAME COLUMN turn_id TO attempt_id")
    if "turns" not in tables:
        return
    prior = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
    # With it off, the rename refuses while a trigger on another table names attempts, gone for a moment.
    conn.execute("PRAGMA legacy_alter_table=ON")
    try:
        turn_columns = set(_columns(conn, "turns"))
        if "attempts" in tables:
            _rebuild_attempts(conn, turn_columns)
        else:
            conn.execute(ATTEMPTS.format(name="attempts"))
            _orphans(conn, "attempts", turn_columns, "")
        spec = next(s for s in status_values.COLUMNS if (s.table, s.column) == ("attempts", "state"))
        status_values._ensure_triggers(conn, spec)
        if "turn_usage_segments" in tables:
            _segments(conn)
        else:
            conn.execute(SEGMENTS)
        for statement in DROP.strip().splitlines():
            conn.execute(statement)
    finally:
        conn.execute(f"PRAGMA legacy_alter_table={prior}")
