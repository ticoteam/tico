"""Which instructions wrote each of Flow's opinions (backend/flow_opinions.py PROMPT_VERSION; 0 for every row written
before it was kept): a ready line from earlier instructions that today's would answer differently is worked out again.
Kept apart from `ask_version`, which a person's snooze of the ask also holds.

- `ask_opinions.prompt_version`: INTEGER NOT NULL DEFAULT 0.

Pending migrations run in any order: when this one runs before 20261115060929_ask_opinions, it first makes the table
with that migration's own statements, word for word, then adds the column (that one's IF NOT EXISTS then leaves both),
so the schema is the same either way.
"""

# 20261115060929_ask_opinions.sql's statements, verbatim: SQLite keeps a table's CREATE text, so it must match.
CREATE = """CREATE TABLE IF NOT EXISTS ask_opinions (
  visit_id TEXT NOT NULL,
  requirement_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  ask_version TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending', 'ready', 'none')),
  pick TEXT NOT NULL DEFAULT '',
  line TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  score INTEGER CHECK (score IS NULL OR score BETWEEN 0 AND 100),
  model TEXT,
  error TEXT,
  started_at TEXT NOT NULL,
  written_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (visit_id, requirement_id)
)"""
INDEX = "CREATE INDEX IF NOT EXISTS ask_opinions_task ON ask_opinions(task_id)"


def up(conn):
    conn.execute(CREATE)
    conn.execute(INDEX)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(ask_opinions)")}
    if "prompt_version" not in columns:
        conn.execute("ALTER TABLE ask_opinions ADD COLUMN prompt_version INTEGER NOT NULL DEFAULT 0")
