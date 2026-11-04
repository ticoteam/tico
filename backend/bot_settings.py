"""A bot's declared settings: one `bot_settings` row per bot, with a real column for each setting the server reads by
name and the rest of the declaration (bot.yaml's other keys) in `extra`.

This module is the one way in and out. A caller reads the whole declaration as the dict bot.yaml describes (`read`,
`read_many`, or the `CONFIG` expression in SQL that reads it as JSON) and writes it back whole (`write`) or a few keys
at a time (`update`, `remove`). A key goes to its column only when its value has the column's type (a text setting a
string, a number a whole number inside 64 bits, a flag a boolean, a structured one anything but null); otherwise it
stays in `extra` under its own name, null included, so a declaration reads back as it was written: a null reads as
null and an absent key as absent. SQL that filters on a setting reads its column (`s.template`, `s.harness`), never a
path into `extra`.

The org-chart fields that live beside the declaration (owner, team, reports_to, description, repo, access...) stay on
`bot_config`; a row there without a settings row reads as an empty declaration.
"""
import json
from typing import Any

# (key, type): the settings with a column. The column has the key's name.
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
# A path no declared key uses: where config_sql sets an absent structured setting, then removes.
_NOTHING = "$.__absent__"


def config_sql(alias="s"):
    """SQL for the whole declaration as JSON text from the bot_settings row aliased `alias` ('' for none). A missing
    row (a LEFT JOIN that found none) reads as '{}'. Scalars are patched over `extra`; structured settings are set
    afterwards, so a null inside one is kept as it was written. A null kept in `extra` survives too: the patch only
    sets keys that have a value. It reads the same dict as `join`."""
    p = f"{alias}." if alias else ""
    scalars = []
    for key, kind in FIELDS:
        if kind == "boolean":
            scalars.append(f"'{key}', CASE {p}{key} WHEN 1 THEN json('true') WHEN 0 THEN json('false') END")
        elif kind != "json":
            scalars.append(f"'{key}', {p}{key}")
    structured = [key for key, kind in FIELDS if kind == "json"]
    # The inner json_patch drops the absent (null) columns, so the outer one only adds; a key kept in `extra` because
    # its value did not fit its column stays.
    base = f"json_patch(coalesce({p}extra,'{{}}'), json_patch('{{}}', json_object({', '.join(scalars)})))"
    setting = ", ".join(f"CASE WHEN {p}{key} IS NULL THEN '{_NOTHING}' ELSE '$.{key}' END, json({p}{key})"
                        for key in structured)
    return f"json_remove(json_set({base}, {setting}), '{_NOTHING}')"


# The declaration of the row aliased `s`, and the join that finds it for a bot_config row aliased `bc`.
CONFIG = config_sql("s")
JOIN = "LEFT JOIN bot_settings s ON s.bot_id=bc.bot"
# One bot's declaration as `config`, a row only when the bot has a bot_config row, as `SELECT config FROM bot_config`
# answered before the declaration moved here.
ONE_SQL = f"SELECT {CONFIG} AS config FROM bot_config bc {JOIN} WHERE bc.bot=?"
# Every bot_config column with the declaration as `config`: what `SELECT * FROM bot_config` answered.
ROW_SQL = f"SELECT bc.*, {CONFIG} AS config FROM bot_config bc {JOIN}"


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


def _stored(kind, value):
    if kind == "boolean":
        return 1 if value else 0
    if kind == "json":
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return value


def split(config):
    """(columns, extra) for a declaration: {column: stored value} for every column (None where the key is absent or
    does not fit), and every other key, a null included, as a dict."""
    config = config if isinstance(config, dict) else {}
    columns = {key: None for key in COLUMNS}
    extra = {}
    for key, value in config.items():
        kind = TYPES.get(key)
        if kind and _fits(kind, value):
            columns[key] = _stored(kind, value)
        else:
            extra[key] = value
    return columns, extra


def join(row):
    """The declaration a bot_settings row holds, as a dict. A column overrides the same key in `extra`."""
    if row is None:
        return {}
    try:
        config = json.loads(row["extra"] or "{}")
    except (TypeError, ValueError):
        config = {}
    config = config if isinstance(config, dict) else {}
    for key, kind in FIELDS:
        value = row[key]
        if value is None:
            continue
        if kind == "boolean":
            value = bool(value)
        elif kind == "json":
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                continue
        config[key] = value
    return config


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def read(c: Any, bot: str) -> dict[str, Any]:
    """A bot's declaration as a dict; {} for a bot with none."""
    return join(c.execute("SELECT * FROM bot_settings WHERE bot_id=?", (bot,)).fetchone())


def read_many(c, bots=None):
    """{bot: declaration} for `bots`, or for every bot with a settings row."""
    if bots is None:
        return {row["bot_id"]: join(row) for row in c.execute("SELECT * FROM bot_settings")}
    found, ordered = {}, sorted(set(bots))
    for start in range(0, len(ordered), 500):
        part = ordered[start:start + 500]
        for row in c.execute(f"SELECT * FROM bot_settings WHERE bot_id IN ({','.join('?' * len(part))})", part):
            found[row["bot_id"]] = join(row)
    return found


def write(c, bot, config, at=None):
    """Store `config` as the bot's whole declaration, replacing the one before. A new row gets the company's default
    repository access when the declaration names none (the repository_new_bot_default trigger)."""
    from . import hubdb as H
    columns, extra = split(config)
    names = list(COLUMNS)
    values = [columns[key] for key in names] + [_encode(extra), at or H.now()]
    c.execute(f"INSERT INTO bot_settings(bot_id,{','.join(names)},extra,updated_at) "
              f"VALUES(?,{','.join('?' * len(names))},?,?) ON CONFLICT(bot_id) DO UPDATE SET "
              + ",".join(f"{key}=excluded.{key}" for key in names) + ",extra=excluded.extra,updated_at=excluded.updated_at",
              [bot, *values])


def update(c, bot, changes, at=None):
    """Set the keys in `changes` (a value of None removes its key) and keep the rest of the declaration."""
    config = read(c, bot)
    for key, value in changes.items():
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value
    write(c, bot, config, at)
    return config


def remove(c, bot, *keys):
    """Remove `keys` from the bot's declaration."""
    return update(c, bot, {key: None for key in keys})
