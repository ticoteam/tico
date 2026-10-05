"""Deleting tasks made by mistake, such as a bulk import run twice, into a trash they can be restored from.

The API deletes one task at a time for a person (`POST /api/v2/tasks/{id}/delete`); the offline
command deletes a list. Either moves the tasks, their own rows (events, links, labels, delegations,
reminders, a service key's mapping) and the conversations that belong to them (`conversations.task_id`)
with every message into `task_trash`, in one transaction. A chat room a task only points at (a bot's
room, a person's room with it) is never moved: the task just stops pointing at it. Every read of
`tasks` then simply no longer sees the task, and a restore puts the same rows back. A task that
carries work is refused whole, so nothing anyone or any bot did is touched: a turn, a job, an
approval, a file, a routine occurrence, a meeting delivery, a subtask or a blocked task outside the
list. The audit log keeps one `task.deleted` (and `task.restored`, `task.purged`) event per task.
Purging is offline and separate; a purged task keeps only its trash row's number, so no new task takes it.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from .store import H


TRASH = """CREATE TABLE IF NOT EXISTS task_trash(
 task_id TEXT PRIMARY KEY, number INTEGER, title TEXT, requester TEXT, owner TEXT, status TEXT,
 private INTEGER NOT NULL DEFAULT 1, deleted_at TEXT NOT NULL, deleted_by TEXT NOT NULL,
 rows_json TEXT NOT NULL, purged_at TEXT)"""

# Rows that only describe the task: kept with it in the trash.
TASK_ROWS = ("task_events", "task_links", "task_tags", "task_delegations", "task_reminders",
             "service_key_tasks")
# Rows that are work done on the task: any one of them refuses the run.
TASK_WORK = ("turns", "approvals", "task_assets", "bot_files", "bot_file_activity",
             "bot_tool_requests", "meeting_deliveries", "schedule_occurrences", "watcher_items")
MESSAGE_WORK = ("turns", "jobs", "attempt_inputs", "approvals", "credential_requests",
                "bot_transition_checkpoints", "batches", "message_assets")
CONVERSATION_WORK = ("attempt_conversations", "session_epochs", "assistant_actions", "chat_goals",
                     "credential_requests", "bot_transition_checkpoints")
# Delivery bookkeeping for the messages: kept with them.
MESSAGE_ROWS = (("slack_posts", "message_id"), ("slack_digests", "message_id"), ("update_queue", "message_id"),
                ("task_file_reviews", "comment_id"), ("task_file_reviews", "ask_message_id"))
CONVERSATION_ROWS = (("slack_digests", "conversation_id"), ("slack_threads", "conversation_id"))
# Rows that point at the task but belong to something else, by their key: restored only if still unset.
POINTERS = (("market_insights", "id", "filed_task"),)
LINKS = ("conversation_id", "parent_id", "blocked_by")
CORE = ("tasks", "conversations", "messages")


def ensure(c):
    c.execute(TRASH)


def _columns(c, table):
    return [r[1] for r in c.execute(f"PRAGMA table_info('{table}')")]


def _has(c, table, column):
    """An older database may lack a table or a column; there is nothing in it to move."""
    return column in _columns(c, table)


def _marks(values):
    return ",".join("?" * len(values))


def _parts(values):
    for i in range(0, len(values), 500):
        yield values[i:i + 500]


def _count(c, table, column, values):
    if not values or not _has(c, table, column):
        return 0
    return sum(c.execute(f"SELECT count(*) FROM {table} WHERE {column} IN ({_marks(p)})", p).fetchone()[0]
               for p in _parts(values))


def _rows(c, table, column, values):
    """Each row of `table` whose `column` is in `values`, with its rowid, as dicts."""
    if not values or not _has(c, table, column):
        return []
    out = []
    for p in _parts(values):
        out += [dict(r) for r in c.execute(f"SELECT rowid AS _rowid, * FROM {table} WHERE {column} IN ({_marks(p)})", p)]
    return out


def _delete(c, table, column, values):
    if not values or not _has(c, table, column):
        return 0
    return sum(c.execute(f"DELETE FROM {table} WHERE {column} IN ({_marks(p)})", p).rowcount for p in _parts(values))


def _column(c, sql, values):
    out = []
    for p in _parts(values):
        out += [r[0] for r in c.execute(sql.format(_marks(p)), p)]
    return out


def _owned_conversations(c, ids):
    """The conversations that belong to these tasks; a room a task merely points at is not one."""
    return list(dict.fromkeys(_column(c, "SELECT id FROM conversations WHERE task_id IN ({})", ids)))


def _rowid_alias(c, table):
    """The INTEGER PRIMARY KEY column that is the rowid, if any: a restore lets SQLite pick it anew."""
    pks = [r for r in c.execute(f"PRAGMA table_info('{table}')") if r[5]]
    return pks[0][1] if len(pks) == 1 and str(pks[0][2]).upper() == "INTEGER" else None


def _snapshot(c, tid):
    """Every row deleting task `tid` removes, by table, in the order a restore puts them back."""
    task = dict(c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())
    conversations = _owned_conversations(c, [tid])
    messages = _column(c, "SELECT id FROM messages WHERE conversation_id IN ({})", conversations)
    rows = {"tasks": [task], "conversations": _rows(c, "conversations", "id", conversations),
            "messages": _rows(c, "messages", "id", messages)}
    seen = set()
    def add(table, found):
        for row in found:
            if (table, row["_rowid"]) not in seen:
                seen.add((table, row["_rowid"]))
                rows.setdefault(table, []).append(row)
    for table in TASK_ROWS:
        add(table, _rows(c, table, "task_id", [tid]))
    for table, column in MESSAGE_ROWS:
        add(table, _rows(c, table, column, messages))
    for table, column in CONVERSATION_ROWS:
        add(table, _rows(c, table, column, conversations))
    pointers = {f"{t}.{key}.{col}": [r[key] for r in _rows(c, t, col, [tid])] for t, key, col in POINTERS}
    return rows, conversations, messages, pointers


def delete_tasks(c, ids, apply=False, actor=None):
    """What deleting `ids` moves to the trash, and with apply=True moves it. Refuses the whole list if
    any task is unknown or carries work; the report says which and why."""
    ids = list(dict.fromkeys(i.strip() for i in ids if i and i.strip()))
    found = set(_column(c, "SELECT id FROM tasks WHERE id IN ({})", ids))
    missing = [i for i in ids if i not in found]
    conversations = _owned_conversations(c, ids)
    messages = _column(c, "SELECT id FROM messages WHERE conversation_id IN ({})", conversations)
    chosen = set(ids)
    refusals = {}
    for table in TASK_WORK:
        if n := _count(c, table, "task_id", ids):
            refusals[f"{table}.task_id"] = n
    for table in MESSAGE_WORK:
        if n := _count(c, table, "message_id", messages):
            refusals[f"{table}.message_id"] = n
    for table in CONVERSATION_WORK:
        if n := _count(c, table, "conversation_id", conversations):
            refusals[f"{table}.conversation_id"] = n
    outside = [r for r in _column(c, "SELECT id FROM tasks WHERE parent_id IN ({})", ids) if r not in chosen]
    if outside:
        refusals["subtasks outside the list"] = len(outside)
    blocked = [r for r in _column(c, "SELECT id FROM tasks WHERE blocked_by IN ({})", ids) if r not in chosen]
    if blocked:
        refusals["tasks blocked by one outside the list"] = len(blocked)
    pointing = [r for r in _column(c, "SELECT task_id FROM task_delegations WHERE message_id IN ({})", messages)
                if r not in chosen] if _has(c, "task_delegations", "message_id") else []
    if pointing:
        refusals["delegations of another task"] = len(pointing)
    # A bot not yet woken about the task (its notice waits in a shared room) would wake to a missing task.
    waking = _column(c, "SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id "
                        "WHERE j.state NOT IN ('completed','cancelled','failed') "
                        "AND json_extract(m.refs_json,'$.task') IN ({})", ids)
    if waking:
        refusals["bot work queued about the task"] = len(waking)
    report = {"tasks": len(found), "missing": missing, "refused": refusals,
              "conversations": len(conversations), "messages": len(messages),
              "rows": {t: _count(c, t, "task_id", ids) for t in TASK_ROWS},
              "applied": False}
    if missing or refusals or not apply:
        return report
    ensure(c)
    actor = actor or H.KEEPER
    now = H.now()
    for tid in ids:
        rows, convs, msgs, pointers = _snapshot(c, tid)
        task = rows["tasks"][0]
        c.execute("INSERT INTO task_trash(task_id,number,title,requester,owner,status,private,deleted_at,"
                  "deleted_by,rows_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (tid, task.get("number"), task["title"], task["requester"], task["owner"], task["status"],
                   task.get("private", 1) if task.get("private") is not None else 1, now, actor,
                   json.dumps({"rows": rows, "pointers": pointers}, default=str)))
        for table, column in MESSAGE_ROWS:
            _delete(c, table, column, msgs)
        for table, column in CONVERSATION_ROWS:
            _delete(c, table, column, convs)
        for table in TASK_ROWS:
            _delete(c, table, "task_id", [tid])
        # Status lines and filed insights keep their own history; they only stop pointing at the task.
        if _has(c, "bot_status", "task_id"):
            c.execute("UPDATE bot_status SET task_id=NULL WHERE task_id=?", (tid,))
        for table, _, column in POINTERS:
            if _has(c, table, column):
                c.execute(f"UPDATE {table} SET {column}=NULL WHERE {column}=?", (tid,))
        _delete(c, "messages", "id", msgs)
        c.execute("UPDATE tasks SET conversation_id=NULL WHERE id=?", (tid,))
        _delete(c, "conversations", "id", convs)
    # Links between tasks inside the list go first, so no row points at a deleted one.
    for p in _parts(ids):
        c.execute(f"UPDATE tasks SET parent_id=NULL, blocked_by=NULL WHERE id IN ({_marks(p)})", p)
    _delete(c, "tasks", "id", ids)
    parties = []
    for tid in ids:
        t = c.execute("SELECT title,requester,owner,status FROM task_trash WHERE task_id=?", (tid,)).fetchone()
        parties += [t[1], t[2]]
        H.event(c, actor, "task.deleted", tid, {"title": t[0], "requester": t[1], "owner": t[2], "status": t[3],
                                                "restorable": True})
    H.recount(c, parties)       # what a bot waited on a person for leaves with the task
    report["applied"] = True
    return report


def trash(c, actor=None, audience=None):
    """Deleted tasks, newest first. `audience` is the reader's task-visibility SQL (auth.task_sql),
    so a private task's title never shows to someone who could not open it; with `actor`, only the
    ones that person deleted or asked for."""
    ensure(c)
    sql = ("SELECT task_id AS id, number, title, requester, owner, status, deleted_at, deleted_by "
           "FROM task_trash WHERE purged_at IS NULL")
    args = []
    if audience:
        sql += f" AND ({audience})"
    if actor:
        sql += " AND (deleted_by=? OR requester=?)"
        args += [actor, actor]
    return [dict(r) for r in c.execute(sql + " ORDER BY deleted_at DESC, task_id", args)]


def trashed(c, ref, audience=None):
    """The restorable trash row for a task id or number (`42` or `#42`) the reader may see, or None."""
    ensure(c)
    where = "purged_at IS NULL" + (f" AND ({audience})" if audience else "")
    row = c.execute(f"SELECT * FROM task_trash WHERE task_id=? AND {where}", (ref,)).fetchone()
    if not row and str(ref).lstrip("#").isdigit():
        row = c.execute(f"SELECT * FROM task_trash WHERE number=? AND {where}", (int(str(ref).lstrip("#")),)).fetchone()
    return dict(row) if row else None


def _insert(c, table, row, skip=()):
    present = set(_columns(c, table))
    alias = _rowid_alias(c, table)
    data = {k: v for k, v in row.items() if k in present and k != alias and k not in skip}
    c.execute(f"INSERT INTO {table} ({','.join(data)}) VALUES ({_marks(list(data))})", list(data.values()))


def _try_insert(c, table, row, skipped):
    """Bookkeeping that something newer has taken (a service key's mapping, a Slack thread, a deleted
    label) is left out rather than failing the restore; the report counts it."""
    c.execute("SAVEPOINT trash_row")
    try:
        _insert(c, table, row)
        c.execute("RELEASE trash_row")
    except sqlite3.IntegrityError:
        c.execute("ROLLBACK TO trash_row")
        c.execute("RELEASE trash_row")
        skipped[table] = skipped.get(table, 0) + 1


def restore_tasks(c, ids, actor=None):
    """Put trashed tasks back with every row they took. A task, conversation or message that cannot go
    back (its id in use again) refuses the whole restore with `conflict`; links to a task or room that
    is gone come back unset, and the report says which."""
    ensure(c)
    actor = actor or H.KEEPER
    entries = []
    for tid in dict.fromkeys(ids):
        row = c.execute("SELECT * FROM task_trash WHERE task_id=? AND purged_at IS NULL", (tid,)).fetchone()
        if not row:
            return {"restored": [], "missing": [tid]}
        entries.append(dict(row))
    restoring = {e["task_id"] for e in entries}
    unlinked, skipped = {}, {}
    snapshots = [(e, json.loads(e["rows_json"])) for e in entries]
    c.execute("SAVEPOINT trash_restore")
    try:
        for e, snap in snapshots:
            task = snap["rows"]["tasks"][0]
            number = task.get("number")
            if number is not None and c.execute("SELECT 1 FROM tasks WHERE number=?", (number,)).fetchone():
                task["number"] = None
                unlinked.setdefault(e["task_id"], []).append("number")
            _insert(c, "tasks", task, skip=LINKS)
        messages = []
        for e, snap in snapshots:
            for conv in snap["rows"].get("conversations", []):
                _insert(c, "conversations", conv)
            for msg in snap["rows"].get("messages", []):
                _insert(c, "messages", msg)
                messages.append(msg["id"])
    except sqlite3.IntegrityError as error:
        c.execute("ROLLBACK TO trash_restore")
        c.execute("RELEASE trash_restore")
        return {"restored": [], "conflict": str(error)}
    c.execute("RELEASE trash_restore")
    # The messages were read long ago: putting them back must not wake a bot on them again.
    _delete(c, "jobs", "message_id", messages)
    for e, snap in snapshots:
        for table, found in snap["rows"].items():
            if table not in CORE:
                for row in found:
                    _try_insert(c, table, row, skipped)
    for e, snap in snapshots:
        task = snap["rows"]["tasks"][0]
        links = {}
        for column in LINKS:
            value = task.get(column)
            table = "conversations" if column == "conversation_id" else "tasks"
            if value and (value in restoring or c.execute(f"SELECT 1 FROM {table} WHERE id=?", (value,)).fetchone()):
                links[column] = value
            elif value:
                unlinked.setdefault(e["task_id"], []).append(column)
        if links:
            c.execute(f"UPDATE tasks SET {', '.join(k + '=?' for k in links)} WHERE id=?",
                      (*links.values(), e["task_id"]))
        for spec, keys in snap.get("pointers", {}).items():
            table, key, column = spec.split(".")
            for value in keys:
                c.execute(f"UPDATE {table} SET {column}=? WHERE {key}=? AND {column} IS NULL", (e["task_id"], value))
        c.execute("DELETE FROM task_trash WHERE task_id=?", (e["task_id"],))
        H.event(c, actor, "task.restored", e["task_id"], {"title": e["title"], "deleted_by": e["deleted_by"],
                                                          "unlinked": unlinked.get(e["task_id"], [])})
    H.recount(c, [t for e in entries for t in (e["owner"], e["requester"])])
    return {"restored": [e["task_id"] for e in entries], "unlinked": unlinked, "skipped": skipped}


def purge_trash(c, older_than_days, apply=False, actor=None):
    """Remove for good what has been in the trash longer than `older_than_days`. Lists it unless apply.
    The trash row stays as a bare tombstone (number, title, who and when) so its number is never reused."""
    ensure(c)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    rows = [dict(r) for r in c.execute("SELECT task_id, title, deleted_at, deleted_by FROM task_trash "
                                       "WHERE purged_at IS NULL AND deleted_at < ? ORDER BY deleted_at", (cutoff,))]
    report = {"tasks": len(rows), "older_than": cutoff, "applied": False}
    if not apply or not rows:
        return report
    now = H.now()
    for r in rows:
        c.execute("UPDATE task_trash SET rows_json='{}', purged_at=? WHERE task_id=?", (now, r["task_id"]))
        H.event(c, actor or H.KEEPER, "task.purged", r["task_id"], {"title": r["title"], "deleted_at": r["deleted_at"]})
    report["applied"] = True
    return report
