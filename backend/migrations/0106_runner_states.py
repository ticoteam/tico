"""A computer's identity stays on `runners`; what its heartbeats report moves to `runner_states`, one row per runner.

`runners` keeps what a person or enrolment sets: id, label, owner, token, platform (named at enrolment and never changed
by a heartbeat), created/revoked times and the owner's settings (screen, performance, turns at once, members' bots,
a pending restart request). `runner_states` holds what changes on every beat or sample: last contact, awake since,
version, capacity, readiness and when it was reported, capabilities, the checkout, a restart under way, the memory hold
and the last load sample. A heartbeat then writes one narrow row and never rewrites the identity row, whose UPDATE
trigger (changes_runner_restart) it no longer wakes.

Readers that want both read `runners_with_state`, a view of each runner with its state (a runner without a state row
reads as never seen). A trigger gives every new runner its state row.

On an existing file the state columns are copied, one row per runner, then dropped from `runners`; none is named by an
index, trigger or view. On a new file `runners` does not exist yet when the migrations run: it is created here in its
new shape (store.py no longer creates it), so the view and the trigger always have their table.
"""

STATE = (
    ("last_seen_at", "TEXT"),
    ("awake_since", "TEXT"),
    ("version", "TEXT"),
    ("capacity", "INTEGER NOT NULL DEFAULT 4"),
    ("readiness", "TEXT NOT NULL DEFAULT '{}'"),
    ("readiness_at", "TEXT"),
    ("capabilities", "TEXT NOT NULL DEFAULT '[]'"),
    ("checkout", "TEXT"),
    ("restart_since", "TEXT"),
    ("memory_hold", "INTEGER NOT NULL DEFAULT 0"),
    ("last_sampled_at", "INTEGER"),
)

RUNNERS = """CREATE TABLE IF NOT EXISTS runners(
 id TEXT PRIMARY KEY, label TEXT NOT NULL, owner_id TEXT NOT NULL REFERENCES humans(id),
 token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, revoked_at TEXT, platform TEXT,
 restart_requested_at TEXT, accepts_member_bots INTEGER NOT NULL DEFAULT 0, screen TEXT NOT NULL DEFAULT 'off',
 screen_default_pending INTEGER NOT NULL DEFAULT 0, performance TEXT NOT NULL DEFAULT 'on', turns_at_once INTEGER)"""

RUNNER_STATES = ("CREATE TABLE IF NOT EXISTS runner_states(\n"
                 " runner_id TEXT PRIMARY KEY REFERENCES runners(id) ON DELETE CASCADE,\n "
                 + ",\n ".join(f"{name} {declaration}" for name, declaration in STATE) + ")")

TRIGGER = ("CREATE TRIGGER IF NOT EXISTS runner_states_new_runner AFTER INSERT ON runners BEGIN "
           "INSERT OR IGNORE INTO runner_states(runner_id) VALUES(NEW.id); END")

# A missing state row reads as the column defaults, as the runner had before it first reported.
_DEFAULTS = {"capacity": "4", "readiness": "'{}'", "capabilities": "'[]'", "memory_hold": "0"}
VIEW = ("CREATE VIEW IF NOT EXISTS runners_with_state AS SELECT r.*, "
        + ", ".join(f"coalesce(s.{name},{_DEFAULTS[name]}) AS {name}" if name in _DEFAULTS else f"s.{name} AS {name}"
                    for name, _ in STATE)
        + " FROM runners r LEFT JOIN runner_states s ON s.runner_id=r.id")


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(runners)")}
    if not columns:
        conn.execute(RUNNERS)
    conn.execute(RUNNER_STATES)
    moved = [name for name, _ in STATE if name in columns]
    if moved:
        names = ", ".join(moved)
        conn.execute(f"INSERT OR IGNORE INTO runner_states(runner_id, {names}) SELECT id, {names} FROM runners")
        for name in moved:
            conn.execute(f"ALTER TABLE runners DROP COLUMN {name}")
    # A runner from before a column existed still gets its row (the defaults).
    conn.execute("INSERT OR IGNORE INTO runner_states(runner_id) SELECT id FROM runners")
    conn.execute(TRIGGER)
    conn.execute(VIEW)
