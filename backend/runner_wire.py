"""The runner wire keeps the names it had before the stored columns dropped their `_json` suffix.

Runners already in the field read these bodies (contracts/runner, runner-rs serde, runner/), so a hub answers them
with the old names: a message's stored `refs` text as `refs_json` beside the decoded `refs`, a conversation's
`participants_json` beside `participants`, an assignment's `config_json` beside `config`, a task's text columns (`labels_json`, ...) as they were, and a worktree
link's `detail_json`. Everything else the hub serves uses the new column names (backend/migrations/0013).
"""
from . import hubdb as H

# The task columns a claim hands out as stored text, under their old names.
TASK_TEXT = ("acceptance", "blocked_gate", "labels", "resume")


def _stored(c, table, column, ids):
    found = {}
    for part in H.chunks(sorted({i for i in ids if i})):
        found.update(c.execute(f"SELECT id,{column} FROM {table} WHERE id IN ({','.join('?' * len(part))})",
                               part).fetchall())
    return found


def messages(c, rows):
    """Each message dict (decoded `refs`) also gets its stored text as `refs_json`, in place; returns `rows`."""
    rows = [m for m in rows or [] if isinstance(m, dict)]
    stored = _stored(c, "messages", "refs", [m.get("id") for m in rows])
    for m in rows:
        if m.get("id") in stored:
            m["refs_json"] = stored[m["id"]]
    return rows


def message(c, m, *, as_read=False):
    """`as_read`: the message was read before this turn changed its refs (a completed turn's reply), so its text is
    the refs it was read with, written the way the hub writes them (hubdb._dump), not the row as it stands now."""
    if isinstance(m, dict):
        if as_read:
            m["refs_json"] = H._dump(m.get("refs") if m.get("refs") is not None else {})
        else:
            messages(c, [m])
    return m


def conversation(c, conv):
    """A conversation dict (decoded `participants`) also gets its stored text as `participants_json`."""
    if isinstance(conv, dict) and conv.get("id"):
        stored = _stored(c, "conversations", "participants", [conv["id"]])
        if conv["id"] in stored:
            conv["participants_json"] = stored[conv["id"]]
    return conv


def assignment(row):
    """The fields an assignment adds for the runner wire: its bot's stored configuration text as `config_json`."""
    return {"config_json": row["config"]}


def task(t):
    """A task row with its stored text columns under their old names."""
    if not isinstance(t, dict):
        return t
    return {(k + "_json" if k in TASK_TEXT else k): v for k, v in t.items()}


def worktree(row):
    """A worktree link row with its stored `detail` text as `detail_json`."""
    if not isinstance(row, dict) or "detail" not in row:
        return row
    return {("detail_json" if k == "detail" else k): v for k, v in row.items()}
