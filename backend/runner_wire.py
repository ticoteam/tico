"""The runner wire keeps the names it had before the stored columns dropped their `_json` suffix.

Runners already in the field read these bodies (contracts/runner, runner-rs serde, runner/), so a hub answers them
with the old names: a message's stored `refs` text as `refs_json` beside the decoded `refs`, a conversation's
`participants_json` beside `participants`, an assignment's `config_json` beside `config`, a task's text columns (`labels_json`, ...) as they were, and a worktree
link's `detail_json`. Renamed time and actor columns go back under their old names too (`created` beside a message's
`created_at`, `from_actor` beside `sender_id`, a task's `owner`, a worktree link's `updated`). Everything else the hub
serves uses the new column names (backend/migrations/0013, 0014, 0031).
"""
from . import hubdb as H
from .migrations.renames_data import COLUMNS

# Renamed time and actor columns of the tables a runner reads: {table: {new name: old name}}.
TIMES = {}
for _table, _old, _new, _wave in COLUMNS:
    if _wave in ("2", "2s", "2b", "3") and _table in ("assignments", "conversations", "messages", "task_links", "tasks"):
        TIMES.setdefault(_table, {})[_new] = _old


def _times(table, row):
    """`row` with each renamed time column's old name beside the new one, in place."""
    for new, old in TIMES.get(table, {}).items():
        if new in row and old not in row:
            row[old] = row[new]
    return row

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
        _times("messages", m)
    return rows


def message(c, m, *, as_read=False):
    """`as_read`: the message was read before this turn changed its refs (a completed turn's reply), so its text is
    the refs it was read with, written the way the hub writes them (hubdb._dump), not the row as it stands now."""
    if isinstance(m, dict):
        if as_read:
            m["refs_json"] = H._dump(m.get("refs") if m.get("refs") is not None else {})
            _times("messages", m)
        else:
            messages(c, [m])
    return m


def conversation(c, conv):
    """A conversation dict (decoded `participants`) also gets its stored text as `participants_json`."""
    if isinstance(conv, dict) and conv.get("id"):
        stored = _stored(c, "conversations", "participants", [conv["id"]])
        if conv["id"] in stored:
            conv["participants_json"] = stored[conv["id"]]
        _times("conversations", conv)
    return conv


def assignment(row):
    """The fields an assignment adds for the runner wire: its bot's stored configuration text as `config_json` and its
    time columns under their old names."""
    return {"config_json": row["config"], **{old: row[new] for new, old in TIMES.get("assignments", {}).items()
                                             if new in row.keys()}}


def task(t):
    """A task row with its stored text columns and its time columns under their old names."""
    if not isinstance(t, dict):
        return t
    times = TIMES.get("tasks", {})
    return {(k + "_json" if k in TASK_TEXT else times.get(k, k)): v for k, v in t.items()}


def worktree(row):
    """A worktree link row with its stored `detail` text as `detail_json` and its time columns under their old names."""
    if not isinstance(row, dict) or "detail" not in row:
        return row
    out = _times("task_links", {("detail_json" if k == "detail" else k): v for k, v in row.items()})
    if "owner_id" in out:
        out["owner"] = out["owner_id"]
    return out
