"""Usage facts (backend/usage_facts.py): one append-only row per metered segment replaces turn_usage_segments.

Each segment a run kept becomes an `estimate` fact with its run's bot, task and times; a run without segments that
counted tokens or a cost becomes one fact from its own columns, so every reader's sums stay what they were. A run with no
usage (0 tokens and $0, with a reason) gets none and keeps reading from its own row.
Costs move from float USD to whole nanodollars, rounded as usage_facts.nano rounds a new run's cost (a value that is not
a finite number becomes no cost). Gateway-billed facts get the meter key of the month their run finished in; the
Gateway's meter facts start at its next daily read (the month-to-date total, so nothing is lost). Then
turn_usage_segments goes, and triggers refuse any change or removal of a fact.

The run columns are read where they are: `turns`, or `attempts` once runs are one table, by the names either uses.
The whole move is two INSERT ... SELECT statements (no row is read into Python), inside the migration's one transaction:
interrupted, nothing is kept, and run again, INSERT OR IGNORE on the per-run unique index adds nothing twice."""
from backend import hubdb as H
from backend.usage_facts import nano
from backend.usage_records import family

TABLE = """
CREATE TABLE IF NOT EXISTS usage_facts (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL CHECK (source IN ('estimate', 'meter')),
  run_id TEXT,
  position INTEGER NOT NULL DEFAULT 0,
  bot_id TEXT,
  task_id TEXT,
  provider TEXT,
  model TEXT,
  family TEXT,
  runtime TEXT,
  harness TEXT,
  effort TEXT,
  profile TEXT,
  billing TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  cached_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_nano INTEGER,
  low_estimate INTEGER CHECK (low_estimate IN (0, 1)),
  meter_key TEXT,
  gateway_key_id TEXT,
  install_id TEXT,
  started_at TEXT NOT NULL,
  metered_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  CHECK (source = 'meter' OR run_id IS NOT NULL),
  CHECK (source = 'estimate' OR meter_key IS NOT NULL))
"""

# The newest read of the Gateway's meter taken for each month (usage_facts.record_meter): what an older or a smaller
# same-time read is checked against, moved by every read taken, even one where nothing changed.
READS = """
CREATE TABLE IF NOT EXISTS usage_meter_reads (
  id INTEGER PRIMARY KEY,
  month TEXT NOT NULL UNIQUE,
  as_of TEXT NOT NULL,
  total_nano INTEGER NOT NULL,
  read_at TEXT NOT NULL)
"""

# The run index comes first: the backfill's INSERT OR IGNORE and NOT EXISTS use it. The read indexes follow the backfill.
RUN_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS usage_facts_run ON usage_facts(run_id, position) WHERE source = 'estimate'"
INDEXES = (
    "CREATE INDEX IF NOT EXISTS usage_facts_started ON usage_facts(started_at)",
    "CREATE INDEX IF NOT EXISTS usage_facts_bot ON usage_facts(bot_id, started_at)",
    "CREATE INDEX IF NOT EXISTS usage_facts_metered ON usage_facts(metered_at)",
    "CREATE INDEX IF NOT EXISTS usage_facts_meter ON usage_facts(meter_key) WHERE meter_key IS NOT NULL",
)

TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS usage_facts_append_only_update BEFORE UPDATE ON usage_facts "
    "BEGIN SELECT RAISE(ABORT, 'usage_facts is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS usage_facts_append_only_delete BEFORE DELETE ON usage_facts "
    "BEGIN SELECT RAISE(ABORT, 'usage_facts is append-only'); END",
)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _pick(found, *names):
    """The first of `names` the table has, as SQL, else NULL."""
    return next((name for name in names if name in found), "NULL")


def _runs_table(conn):
    """The table that holds a run's bot, task, times and legacy usage, and its columns."""
    for table in ("turns", "attempts"):
        found = _columns(conn, table)
        if "id" in found and found & {"bot", "bot_id"} and "started_at" in found:
            return table, found
    return None, set()


def up(conn):
    conn.execute(TABLE)
    conn.execute(READS)
    conn.execute(RUN_INDEX)
    conn.create_function("usage_family", 1, lambda model: family({"model": model}), deterministic=True)
    conn.create_function("usage_nano", 1, nano, deterministic=True)      # the rounding record_run uses, exactly
    stamp = H.now()
    runs, rc = _runs_table(conn)
    if runs:
        def column(*options):
            name = _pick(rc, *options)
            return "NULL" if name == "NULL" else "t." + name
        t = {name: column(*options) for name, options in {
            "bot": ("bot", "bot_id"), "task_id": ("task_id",), "started_at": ("started_at",),
            "finished_at": ("finished_at",), "input_tokens": ("input_tokens",), "cached_tokens": ("cached_tokens",),
            "output_tokens": ("output_tokens",), "model": ("model",), "provider": ("provider",),
            "est_cost_usd": ("est_cost_usd",), "billing": ("billing",)}.items()}
        finished = f"coalesce({t['finished_at']}, {t['started_at']}, '{stamp}')"
        started = f"coalesce({t['started_at']}, '{stamp}')"
        sc = _columns(conn, "turn_usage_segments")
        link = _pick(sc, "turn_id", "attempt_id", "run_id")
        if sc and link != "NULL":
            s = {name: ("s." + name if name in sc else "NULL") for name in (
                "position", "input_tokens", "cached_tokens", "output_tokens", "model", "provider", "est_cost_usd",
                "billing", "runtime", "harness", "effort", "profile", "est_cost_low", "gateway_key_id", "install_id")}
            conn.execute(f"""
INSERT OR IGNORE INTO usage_facts(source, run_id, position, bot_id, task_id, provider, model, family, runtime, harness,
  effort, profile, billing, input_tokens, cached_tokens, output_tokens, cost_nano, low_estimate, meter_key,
  gateway_key_id, install_id, started_at, metered_at, created_at)
SELECT 'estimate', s.{link}, coalesce({s['position']}, 0), {t['bot']}, {t['task_id']}, {s['provider']}, {s['model']},
  usage_family({s['model']}), {s['runtime']}, {s['harness']}, {s['effort']}, {s['profile']}, {s['billing']},
  coalesce({s['input_tokens']}, 0), coalesce({s['cached_tokens']}, 0), coalesce({s['output_tokens']}, 0),
  usage_nano({s['est_cost_usd']}),
  CASE WHEN {s['est_cost_low']} IS NULL THEN NULL WHEN {s['est_cost_low']} THEN 1 ELSE 0 END,
  CASE WHEN {s['model']} LIKE 'tico-%' AND coalesce({s['billing']}, 'api') != 'subscription'
       THEN 'gateway:' || substr({finished}, 1, 7) || ':' || {s['model']} END,
  {s['gateway_key_id']}, {s['install_id']}, {started}, {finished}, '{stamp}'
FROM turn_usage_segments s JOIN {runs} t ON t.id = s.{link}""")
        # A run without segments: one fact from its own columns, when it counted something (what RUNS read from it).
        tokens = f"coalesce({t['input_tokens']}, 0) + coalesce({t['cached_tokens']}, 0) + coalesce({t['output_tokens']}, 0)"
        conn.execute(f"""
INSERT OR IGNORE INTO usage_facts(source, run_id, position, bot_id, task_id, provider, model, family, billing,
  input_tokens, cached_tokens, output_tokens, cost_nano, meter_key, started_at, metered_at, created_at)
SELECT 'estimate', t.id, 0, {t['bot']}, {t['task_id']}, {t['provider']}, {t['model']}, usage_family({t['model']}),
  {t['billing']}, coalesce({t['input_tokens']}, 0), coalesce({t['cached_tokens']}, 0), coalesce({t['output_tokens']}, 0),
  usage_nano({t['est_cost_usd']}),
  CASE WHEN {t['model']} LIKE 'tico-%' AND coalesce({t['billing']}, 'api') != 'subscription'
       THEN 'gateway:' || substr({finished}, 1, 7) || ':' || {t['model']} END,
  {started}, {finished}, '{stamp}'
FROM {runs} t
WHERE ({tokens} > 0 OR coalesce({t['est_cost_usd']}, 0) != 0)
  AND NOT EXISTS (SELECT 1 FROM usage_facts f WHERE f.run_id = t.id AND f.source = 'estimate')""")
    for statement in INDEXES:
        conn.execute(statement)
    conn.execute("DROP TABLE IF EXISTS turn_usage_segments;")
    for statement in TRIGGERS:
        conn.execute(statement)
