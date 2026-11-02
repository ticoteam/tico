"""Six columns on bots go. runtime, model and host are read from the bot's config (bot_config.config), which the
registry seed and every settings writer already fill: the runner and the bot page never read the columns, and they
went stale whenever config changed without them. cwd is the bot's folder, which bot_folder reads from config.
thread_id is the live session's id (bot_sessions). token_hash had no reader and no caller.

Where config has no runtime, model or host of its own and the column holds one, the column's value is copied into
config first, so no setting is lost. A config that has the key keeps it. A host of "dispatcher" is the default and
is not copied. Then the columns are dropped; SQLite refuses a column an index, view or trigger still names, and none does.
"""
import json

COPIED = ("runtime", "model", "host")
DROPPED = ("runtime", "model", "host", "cwd", "thread_id", "token_hash")


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _config(text):
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(bots)")}
    if not columns:
        return
    copied = [name for name in COPIED if name in columns]
    if copied and _table(conn, "bot_config"):
        select = ", ".join(f"b.{name}" for name in copied)
        for row in conn.execute(f"SELECT bc.bot, bc.config, {select} FROM bot_config bc "
                                "JOIN bots b ON b.id=bc.bot").fetchall():
            config = _config(row[1])
            if config is None:
                continue
            values = dict(zip(copied, row[2:]))
            changed = False
            for name in ("runtime", "model"):
                if name in values and values[name] and not config.get(name):
                    config[name] = values[name]
                    changed = True
            if values.get("host") not in (None, "", "dispatcher") and not config.get("host"):
                config["host"] = values["host"]
                changed = True
            if changed:
                conn.execute("UPDATE bot_config SET config=? WHERE bot=?",
                             (json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False), row[0]))
    for name in DROPPED:
        if name in columns:
            conn.execute(f"ALTER TABLE bots DROP COLUMN {name}")
