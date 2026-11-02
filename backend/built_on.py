"""The computer a new bot's repository was built on, until the bot is first placed (backend/app.py place_now).

BotOps builds a bot in its own workspace (`hub bot create`, `hub bot copy`), and only that computer's runner can
publish the history to GitHub, and only once the bot is assigned to it (runner-rs/src/execute.rs publish).
A bot first placed anywhere else would wait for a repository GitHub never gets. So BotOps's GitHub call for the build
records which computer made it, one `registry_metadata` row `bot-built-on:<bot>`, and the first placement goes
there while GitHub does not hold the history. The row is removed when the bot is first assigned (execution.assign).
"""

from .store import H, encode

PREFIX = "bot-built-on:"


def remember(c, bot, runner_id):
    """Record that `runner_id`'s workspace holds `bot`'s only copy. The first record stands while its computer does (a
    build after that computer was removed replaces it), and a bot already placed somewhere keeps that."""
    if not runner_id or not H.bot(c, bot) or c.execute("SELECT 1 FROM assignments WHERE bot=?", (bot,)).fetchone():
        return
    if not c.execute("SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL", (runner_id,)).fetchone():
        return
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value "
              "WHERE NOT EXISTS (SELECT 1 FROM runners WHERE revoked_at IS NULL "
              "AND id=json_extract(registry_metadata.value, '$.runner_id'))",
              (PREFIX + bot, encode({"runner_id": runner_id, "at": H.now()})))


def computer(c, bot):
    """The computer (`runners` row) holding a never-placed bot's build, or None: placed already, nothing recorded,
    or that computer since removed."""
    if c.execute("SELECT 1 FROM assignments WHERE bot=?", (bot,)).fetchone():
        return None
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (PREFIX + bot,)).fetchone()
    runner_id = (H._json(row["value"], {}) or {}).get("runner_id") if row else None
    if not runner_id:
        return None
    return c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (runner_id,)).fetchone()


def forget(c, bot):
    c.execute("DELETE FROM registry_metadata WHERE key=?", (PREFIX + bot,))
