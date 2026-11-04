"""A bot's declaration moves from the JSON blob `bot_config.config` to `bot_settings`: one row per bot, a real column for
each setting the server reads by name, the other keys in `extra` (backend/bot_settings.py).

Each bot_config row's config is split as backend/bot_settings.split splits it, frozen here: a key goes to its column
when its value has the column's type, otherwise it stays in `extra` under its own name, and a null value stays in `extra`
too, so it reads back as null. A config that is not a JSON object (no reader could use it) becomes an empty declaration. A row
whose bot is gone has no settings row: bot_settings refers to bots(id).

The triggers that read bot_config.config are dropped first (SQLite refuses to drop a column a trigger names);
Store.initialize makes their new versions, which read bot_settings, right after the migrations. Then the column goes.
This migration makes the table wherever bots or bot_config exists (hubdb's first step makes bots on every file), so
bot_settings is made here on a new file too; store.SCHEMA does not make it. On a new file bot_config does not exist yet
and store.SCHEMA makes it in its new shape. A rerun once the column is gone copies nothing.
"""
import json
import re

FIELDS = (
    ("harness", "text"), ("runtime", "text"), ("model", "text"), ("reasoning_effort", "text"), ("host", "text"),
    ("template", "text"), ("profile", "text"), ("session", "text"), ("wake_preset", "text"),
    ("shared_from", "text"), ("bot_contact", "text"), ("repo_access_mode", "text"), ("folder", "text"),
    ("github_login", "text"),
    ("compact_at", "integer"), ("turns_per_hour", "integer"), ("turns_per_day", "integer"),
    ("max_run_minutes", "integer"),
    ("stateless", "boolean"),
    ("fallback", "json"), ("app_delivery", "json"),
)
TYPES = dict(FIELDS)
COLUMNS = tuple(key for key, _ in FIELDS)
TABLE = ("CREATE TABLE IF NOT EXISTS bot_settings(\n bot_id TEXT PRIMARY KEY REFERENCES bots(id),\n "
         + ",\n ".join(f"{key} {'INTEGER' if kind in ('integer', 'boolean') else 'TEXT'}"
                       + (f" CHECK ({key} IN (0,1))" if kind == "boolean" else "") for key, kind in FIELDS)
         + ",\n extra TEXT NOT NULL DEFAULT '{}',\n updated_at TEXT)")
BATCH = 500


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fits(kind, value):
    if value is None:
        return False
    if kind == "text":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool) and -2**63 <= value <= 2**63 - 1
    if kind == "boolean":
        return isinstance(value, bool)
    return True


def split(text):
    try:
        config = json.loads(text) if text else {}
    except (TypeError, ValueError):
        config = {}
    config = config if isinstance(config, dict) else {}
    columns = {key: None for key in COLUMNS}
    extra = {}
    for key, value in config.items():
        kind = TYPES.get(key)
        if kind and _fits(kind, value):
            columns[key] = (1 if value else 0) if kind == "boolean" else _encode(value) if kind == "json" else value
        else:
            extra[key] = value
    return columns, extra


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"bots", "bot_config"} & tables:
        return      # an empty file: no bot for a declaration to belong to
    conn.execute(TABLE)
    if "bot_config" not in tables or "config" not in {row[1] for row in conn.execute("PRAGMA table_info(bot_config)")}:
        return      # a new file (bot_config comes later, in its new shape), or a file where the column is already gone
    insert = (f"INSERT OR REPLACE INTO bot_settings(bot_id,{','.join(COLUMNS)},extra,updated_at) "
              f"VALUES(?,{','.join('?' * len(COLUMNS))},?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))")
    last = ""
    while True:
        rows = conn.execute("SELECT bc.bot, bc.config FROM bot_config bc JOIN bots b ON b.id=bc.bot "
                            "WHERE bc.bot>? ORDER BY bc.bot LIMIT ?", (last, BATCH)).fetchall()
        if not rows:
            break
        for bot, text in rows:
            columns, extra = split(text)
            conn.execute(insert, [bot, *[columns[key] for key in COLUMNS], _encode(extra)])
        last = rows[-1][0]
    # A trigger that reads the column, on bot_config (OLD.config / NEW.config) or on another table (a subquery on
    # bot_config): queue_bot_message, wake_preset_hold, fin_view_bot_update, changes_runner_bot_config_update and
    # repository_new_bot_default. `\bconfig\b` is the bare column name, never part of `bot_config`.
    column = re.compile(r"\bconfig\b", re.I)
    for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'").fetchall():
        if sql and "bot_config" in sql and column.search(sql):
            conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    conn.execute("ALTER TABLE bot_config DROP COLUMN config")
