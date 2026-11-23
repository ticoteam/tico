"""Antigravity is a runtime of its own, not the Gemini runtime's second CLI, and Gemini 4 Hydrogen takes Gemini 3.8
Flash's place on it.

Every pick on the `antigravity` harness moves to runtime `antigravity`: a bot's own (`bot_settings`), its fallback
(`bot_settings.fallback`), its task model (`task_model` in `extra`), and a task's own run or check model
(`task_run_models`, `task_check_models`). On each of them Gemini 3.8 Flash becomes Gemini 4 Hydrogen. The company
default and its check models on Gemini 4 Neon, which only Antigravity runs, move to the new runtime too. The Gemini
CLI's bots and picks keep their runtime and model. Its earlier threads were saved under `gemini:antigravity`: each
starts over once, as a thread the runner no longer has. Running this again changes nothing.
"""

import json

HARNESS = RUNTIME = "antigravity"
FLASH, HYDROGEN, NEON = "gemini-3.8-flash", "gemini-4-hydrogen", "gemini-4-neon"


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _moved(pick):
    """A fallback or task model record on Antigravity, moved; None when it is not one or needs no change."""
    if not isinstance(pick, dict) or pick.get("harness") != HARNESS:
        return None
    out = {**pick, "runtime": RUNTIME, **({"model": HYDROGEN} if pick.get("model") == FLASH else {})}
    return out if out != pick else None


def _json(text):
    try:
        value = json.loads(text) if text else None
    except ValueError:
        return None
    return value


def up(conn):
    if _table(conn, "bot_settings"):
        conn.execute(
            "UPDATE bot_settings SET runtime=?, model=CASE WHEN model=? THEN ? ELSE model END WHERE harness=?",
            (RUNTIME, FLASH, HYDROGEN, HARNESS),
        )
        for bot, fallback, extra in conn.execute(
            "SELECT bot_id, fallback, extra FROM bot_settings WHERE fallback LIKE ? OR extra LIKE ?",
            (f"%{HARNESS}%", f"%{HARNESS}%"),
        ).fetchall():
            moved = _moved(_json(fallback))
            if moved:
                conn.execute("UPDATE bot_settings SET fallback=? WHERE bot_id=?", (json.dumps(moved), bot))
            rest = _json(extra)
            task = _moved(rest.get("task_model")) if isinstance(rest, dict) else None
            if task:
                conn.execute(
                    "UPDATE bot_settings SET extra=? WHERE bot_id=?", (json.dumps({**rest, "task_model": task}), bot)
                )
    for table in ("task_run_models", "task_check_models"):
        if _table(conn, table):
            conn.execute(
                f"UPDATE {table} SET runtime=?, model=CASE WHEN model=? THEN ? ELSE model END WHERE harness=?",
                (RUNTIME, FLASH, HYDROGEN, HARNESS),
            )
    if not _table(conn, "registry_metadata"):
        return
    row = conn.execute("SELECT value FROM registry_metadata WHERE key='providers'").fetchone()
    record = _json(row[0]) if row else None
    if not isinstance(record, dict):
        return
    changed = dict(record)
    if record.get("model") == NEON and record.get("runtime") != RUNTIME:
        changed["runtime"] = RUNTIME
    checks = record.get("check_models")
    if isinstance(checks, list):
        changed["check_models"] = [f"{RUNTIME}:{NEON}" if entry == f"gemini:{NEON}" else entry for entry in checks]
    if changed != record:
        conn.execute(
            "UPDATE registry_metadata SET value=? WHERE key='providers'", (json.dumps(changed, sort_keys=True),)
        )
