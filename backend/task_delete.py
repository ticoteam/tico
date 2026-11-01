"""Deleting tasks made by mistake, such as a bulk import run twice, into a trash they can be restored from.

The API deletes one task at a time for a person (`POST /api/v2/tasks/{id}/delete`); the offline
command deletes a list. Either moves the tasks, their own rows (events, links, labels, delegations,
reminders, a service key's mapping) and the conversations that belong to them (`conversations.task_id`)
with every message into `task_trash`, in one transaction. A chat room a task only points at (a bot's
room, a person's room with it) is never moved: the task just stops pointing at it. Every read of
`tasks` then simply no longer sees the task, and a restore puts the same rows back. A task that
carries work is refused whole by the offline command, so nothing anyone or any bot did is touched: a turn, a
job, an approval, a bot's own file, a routine occurrence, a meeting delivery, a subtask or a blocked task
outside the list. A person's delete through the API takes it anyway (keep_work): the work stays where it is. Files attached to the task or its comments are part of it, not work: their
attachment rows go into the trash, a task file stays where it is (no one may open it while its task
is gone, since its visibility is the task's) and its append-only activity stays as history. Its relations to other tasks (task_relations, from either end) go into the trash with it, and a
restore puts back each one whose other task still exists. The audit log keeps one `task.deleted` (and `task.restored`, `task.purged`) event per task.
Purging is offline and separate; a purged task keeps only its trash row's number, so no new task takes it.
A file attached when the task was made (`task_assets`) leaves with the task like its other rows, but
`task_trash_assets` remembers it, through a purge too, so its uploader cannot open it once nothing
links it (task_privacy.blob_readable). A purge cannot remove a task's files, so it reports them.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from .store import H
from . import task_relations as TR
from .migrations import renames


TRASH = """CREATE TABLE IF NOT EXISTS task_trash(
 task_id TEXT PRIMARY KEY, number INTEGER, title TEXT, requester TEXT, owner TEXT, status TEXT,
 private INTEGER NOT NULL DEFAULT 1, deleted_at TEXT NOT NULL, deleted_by TEXT NOT NULL,
 rows TEXT NOT NULL, purged_at TEXT, guest TEXT)"""
# The files attached when a deleted task was made, kept past a purge: no foreign key, the task is gone.
TRASH_ASSETS = """CREATE TABLE IF NOT EXISTS task_trash_assets(
 task_id TEXT NOT NULL, blob_id TEXT NOT NULL, PRIMARY KEY(task_id, blob_id))"""

# Rows that only describe the task: kept with it in the trash.
TASK_ROWS = ("task_events", "task_links", "task_tags", "task_delegations", "task_reminders",
             "service_key_tasks", "task_roles", "task_assets", "task_requesters", "task_milestone_waits",
             "task_check_models", "task_run_models", "growth_ideas", "growth_idea_channels",
             "growth_idea_segments")
# Rows that are work done on the task: any one of them refuses the run.
TASK_WORK = ("turns", "approvals", "bot_tool_requests", "meeting_deliveries", "schedule_occurrences",
             "watcher_items")
# A file a person attached to the task itself (scope `task:<id>`) is not work; any other file touched on the task is.
# A verdict on a file under review (`reviewed`, backend/file_reviews.py) is not work either: the file is the work, and
# a deleted task's verdicts stop counting.
FILE_WORK = (("bot_files", "SELECT count(*) FROM bot_files WHERE task_id IN ({}) AND scope <> 'task:' || task_id"),
             ("bot_file_activity", "SELECT count(*) FROM bot_file_activity a WHERE a.task_id IN ({}) "
                                   "AND a.action <> 'reviewed' AND NOT EXISTS "
                                   "(SELECT 1 FROM bot_files f WHERE f.id=a.file_id AND f.scope='task:' || a.task_id)"),
             # A task file a bot made (from a run, or as itself) is work even though it sits on the task.
             ("bot_file_versions", "SELECT count(*) FROM bot_files f WHERE f.task_id IN ({}) AND f.scope='task:' || f.task_id "
                                   "AND EXISTS (SELECT 1 FROM bot_file_versions v WHERE v.file_id=f.id "
                                   "AND (v.attempt_id IS NOT NULL OR v.actor LIKE 'bot:%'))"))
MESSAGE_WORK = ("turns", "jobs", "attempt_inputs", "approvals", "credential_requests",
                "bot_transition_checkpoints", "batches")
CONVERSATION_WORK = ("attempt_conversations", "session_epochs", "assistant_actions", "chat_goals",
                     "credential_requests", "bot_transition_checkpoints")
# Delivery bookkeeping for the messages: kept with them.
MESSAGE_ROWS = (("message_assets", "message_id"), ("slack_posts", "message_id"), ("slack_digests", "message_id"), ("update_queue", "message_id"),
                ("task_file_reviews", "comment_id"), ("task_file_reviews", "ask_message_id"))
CONVERSATION_ROWS = (("slack_digests", "conversation_id"), ("slack_threads", "conversation_id"))
# Rows that point at the task but belong to something else, by their key: restored only if still unset.
POINTERS = (("market_insights", "id", "filed_task"),)
LINKS = ("conversation_id",)
# Trash rows written before task_relations kept a task's parent and blocker on the task row itself.
LEGACY_LINKS = {"parent_id": ("parent", False), "blocked_by": ("blocks", True)}
CORE = ("tasks", "conversations", "messages")
# Keys a trashed row holds that are not columns: the rowid the snapshot read it with, and the task links kept on the
# task row before task_relations (LEGACY_LINKS, restored as relations).
NOT_COLUMNS = {"_rowid"}
HANDLED = {"tasks": set(LEGACY_LINKS)}


def ensure(c):
    c.execute(TRASH)
    # A private task's guest (hubdb.task_reach) sees it in the trash as its two parties do (auth.task_sql).
    if "guest" not in _columns(c, "task_trash"):
        c.execute("ALTER TABLE task_trash ADD COLUMN guest TEXT")
    c.execute(TRASH_ASSETS)
    c.execute("CREATE INDEX IF NOT EXISTS task_trash_assets_blob ON task_trash_assets(blob_id)")
    # A task deleted before this table existed has its attachments only in its trash row. Only the rows that
    # mention attachments and have none listed are parsed: every delete runs this, and parsing all of the trash's
    # rows grows with the trash.
    c.execute("INSERT OR IGNORE INTO task_trash_assets(task_id, blob_id) "
              "SELECT t.task_id, json_extract(a.value, '$.blob_id') FROM task_trash t, "
              "json_each(t.rows, '$.rows.task_assets') a "
              "WHERE t.purged_at IS NULL AND instr(t.rows, '\"task_assets\"') > 0 "
              "AND NOT EXISTS (SELECT 1 FROM task_trash_assets x WHERE x.task_id=t.task_id) "
              "AND json_extract(a.value, '$.blob_id') IS NOT NULL")


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


def _snapshot(c, tid, relations=None):
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
    # Taken for the whole list before any is deleted, so each entry keeps every relation it had.
    rows["task_relations"] = TR.snapshot_rows(c, tid) if relations is None else relations
    for table, column in MESSAGE_ROWS:
        add(table, _rows(c, table, column, messages))
    for table, column in CONVERSATION_ROWS:
        add(table, _rows(c, table, column, conversations))
    pointers = {f"{t}.{key}.{col}": [r[key] for r in _rows(c, t, col, [tid])] for t, key, col in POINTERS}
    return rows, conversations, messages, pointers


def _refusals(c, ids, conversations, messages):
    """The work `ids` carry, by where it sits: empty when deleting them takes nothing anyone did."""
    chosen = set(ids)
    refusals = {}
    for table in TASK_WORK:
        if n := _count(c, table, "task_id", ids):
            refusals[f"{table}.task_id"] = n
    for table, sql in FILE_WORK:
        if _has(c, "bot_files", "task_id") and (n := sum(_column(c, sql, ids))):
            refusals[f"{table}.task_id"] = n
    for table in MESSAGE_WORK:
        if n := _count(c, table, "message_id", messages):
            refusals[f"{table}.message_id"] = n
    for table in CONVERSATION_WORK:
        if n := _count(c, table, "conversation_id", conversations):
            refusals[f"{table}.conversation_id"] = n
    outside = [r for r in _column(c, "SELECT from_task FROM task_relations WHERE kind='parent' AND to_task IN ({})", ids)
               if r not in chosen]
    if outside:
        refusals["subtasks outside the list"] = len(outside)
    blocked = [r for r in _column(c, "SELECT to_task FROM task_relations WHERE kind='blocks' AND from_task IN ({})", ids)
               if r not in chosen]
    if blocked:
        refusals["tasks blocked by one outside the list"] = len(blocked)
    pointing = [r for r in _column(c, "SELECT task_id FROM task_delegations WHERE message_id IN ({})", messages)
                if r not in chosen] if _has(c, "task_delegations", "message_id") else []
    if pointing:
        refusals["delegations of another task"] = len(pointing)
    # A bot not yet woken about the task (its notice waits in a shared room) would wake to a missing task.
    # CROSS JOIN keeps jobs the outer loop: each job reads its message by primary key, where the plain JOIN
    # scanned every message to read its refs.
    waking = _column(c, "SELECT j.id FROM jobs j CROSS JOIN messages m ON m.id=j.message_id "
                        "WHERE j.state NOT IN ('completed','canceled','failed') "
                        "AND json_extract(m.refs,'$.task') IN ({})", ids)
    if waking:
        refusals["bot work queued about the task"] = len(waking)
    return refusals


def _task_work(c, tid):
    conversations = _owned_conversations(c, [tid])
    messages = _column(c, "SELECT id FROM messages WHERE conversation_id IN ({})", conversations)
    return _refusals(c, [tid], conversations, messages)


def delete_tasks(c, ids, apply=False, actor=None, keep_work=False):
    """What deleting `ids` moves to the trash, and with apply=True moves it. Refuses the whole list if
    any task is unknown or carries work; the report says which and why. With keep_work=True a task that
    carries work is deleted anyway: its conversation and messages stay where they are, since turns, jobs
    and runs point at them, and no one can read them while the task is gone (task_privacy.readable needs
    the task). Its queued bot jobs are cancelled. A restore links the conversation back."""
    ids = list(dict.fromkeys(i.strip() for i in ids if i and i.strip()))
    found = set(_column(c, "SELECT id FROM tasks WHERE id IN ({})", ids))
    missing = [i for i in ids if i not in found]
    conversations = _owned_conversations(c, ids)
    messages = _column(c, "SELECT id FROM messages WHERE conversation_id IN ({})", conversations)
    refusals = _refusals(c, ids, conversations, messages)
    report = {"tasks": len(found), "missing": missing, "refused": refusals,
              "conversations": len(conversations), "messages": len(messages),
              "rows": {t: _count(c, t, "task_id", ids) for t in TASK_ROWS},
              "applied": False}
    if missing or (refusals and not keep_work) or not apply:
        return report
    kept = {tid for tid in ids if _task_work(c, tid)} if refusals else set()
    if kept:
        # A bot not yet woken about the task would wake to a missing task.
        # Queued jobs only (jobs_queued), each checked against its own message by primary key.
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued' AND EXISTS "
                  "(SELECT 1 FROM messages m WHERE m.id=jobs.message_id AND (json_extract(m.refs,'$.task') IN ({}) "
                  "OR m.conversation_id IN (SELECT id FROM conversations WHERE task_id IN ({}))))"
                  .format(_marks(list(kept)), _marks(list(kept))), [*kept, *kept])
    ensure(c)
    actor = actor or H.KEEPER
    now = H.now()
    relations = {tid: TR.snapshot_rows(c, tid) for tid in ids}
    found_rows = {tid: H.task(c, tid) for tid in ids}
    for tid in ids:
        rows, convs, msgs, pointers = _snapshot(c, tid, relations[tid])
        if tid in kept:
            for table in ("conversations", "messages", *{t for t, _ in MESSAGE_ROWS + CONVERSATION_ROWS}):
                rows.pop(table, None)
            convs, msgs = [], []
        task = rows["tasks"][0]
        c.execute("INSERT INTO task_trash(task_id,number,title,requester,owner,status,private,deleted_at,"
                  "deleted_by,rows,guest) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (tid, task.get("number"), task["title"], task["requester_id"], task["owner_id"], task["status"],
                   task.get("private", 1) if task.get("private") is not None else 1, now, actor,
                   json.dumps({"rows": rows, "pointers": pointers}, default=str), task.get("guest")))
        for table, column in MESSAGE_ROWS:
            _delete(c, table, column, msgs)
        for table, column in CONVERSATION_ROWS:
            _delete(c, table, column, convs)
        if _has(c, "task_assets", "task_id"):
            c.execute("INSERT OR IGNORE INTO task_trash_assets(task_id, blob_id) "
                      "SELECT task_id, blob_id FROM task_assets WHERE task_id=?", (tid,))
        for table in TASK_ROWS:
            _delete(c, table, "task_id", [tid])
        c.execute("DELETE FROM task_relations WHERE from_task=? OR to_task=?", (tid, tid))
        # Status lines and filed insights keep their own history; they only stop pointing at the task.
        if _has(c, "bot_status", "task_id"):
            c.execute("UPDATE bot_status SET task_id=NULL WHERE task_id=?", (tid,))
        if tid in kept and _has(c, "schedule_occurrences", "task_id"):
            # The routine's occurrence keeps its outcome, so it does not file the task again.
            c.execute("UPDATE schedule_occurrences SET task_id=NULL WHERE task_id=?", (tid,))
        for table, _, column in POINTERS:
            if _has(c, table, column):
                c.execute(f"UPDATE {table} SET {column}=NULL WHERE {column}=?", (tid,))
        _delete(c, "messages", "id", msgs)
        c.execute("UPDATE tasks SET conversation_id=NULL WHERE id=?", (tid,))
        _delete(c, "conversations", "id", convs)
    _delete(c, "tasks", "id", ids)
    parties = []
    for tid in ids:
        t = c.execute("SELECT title,requester,owner,status FROM task_trash WHERE task_id=?", (tid,)).fetchone()
        parties += [t[1], t[2]]
        H.event(c, actor, "task.deleted", tid, {"title": t[0], "requester": t[1], "owner": t[2], "status": t[3],
                                                "restorable": True, **({"kept_work": True} if tid in kept else {})})
    H.recount(c, parties)       # what a bot waited on a person for leaves with the task
    from .roadmap_flow import sync
    # A roadmap item counts its children and the Dev tickets it is blocked by.
    for parent in {r["to_task"] for edges in relations.values() for r in edges if r["kind"] in ("parent", "blocks")}:
        sync(c, parent)
    # A deleted child or blocker is a hold cleared on the task it held (backend/task_holds.py).
    from . import task_holds
    held = {r["to_task"]: r["from_task"] for edges in relations.values() for r in edges
            if r["kind"] in ("parent", "blocks") and r["from_task"] in ids and r["to_task"] not in ids}
    for tid, gone in held.items():
        t = found_rows[gone]
        live = t["status"] not in ("done", "closed", "declined")
        task_holds.refresh(c, tid, f"{task_holds.title(c, t)} was deleted" if live else None)
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


def _current(snap):
    """A trash row's snapshot under this code's table and column names. It was saved with the names its rows had when
    the task was deleted, and a release since may have renamed them (backend/migrations/renames_data.py, every wave)."""
    pointers = {}
    for spec, keys in (snap.get("pointers") or {}).items():
        table, key, column = spec.split(".")
        table = renames.current_table(table)
        pointers[f"{table}.{renames.current_column(table, key)}.{renames.current_column(table, column)}"] = keys
    return {**snap, "rows": renames.current_rows(snap.get("rows") or {}), "pointers": pointers}


def _unplaced(c, snap):
    """{table: [keys]} a snapshot holds, after the renames, that this database has no column for. Restoring it would
    drop them, and the snapshot goes once the task is back, so a restore with any is refused and the trash row kept.
    The relations are left out: they go back through task_relations.restore_row, which reads each by name."""
    out = {}
    for table, found in snap["rows"].items():
        if table == "task_relations" or not found:
            continue
        present = set(_columns(c, table))
        keys = {k for row in found for k in row} - present - NOT_COLUMNS - HANDLED.get(table, set())
        if keys:
            out[table] = sorted(keys)
    return out


def restore_tasks(c, ids, actor=None):
    """Put trashed tasks back with every row they took. A task, conversation or message that cannot go
    back (its id in use again) refuses the whole restore with `conflict`; links to a task or room that
    is gone come back unset, and the report says which. A snapshot saved before a column was renamed is read under
    the new names; one holding a value this database has no column for refuses the whole restore with `unplaced`
    and stays in the trash, since restoring would lose that value for good."""
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
    snapshots = [(e, _current(json.loads(e["rows"]))) for e in entries]
    unplaced = {e["task_id"]: found for e, snap in snapshots if (found := _unplaced(c, snap))}
    if unplaced:
        return {"restored": [], "unplaced": unplaced}
    c.execute("SAVEPOINT trash_restore")
    try:
        for e, snap in snapshots:
            task = snap["rows"]["tasks"][0]
            number = task.get("number")
            if number is not None and c.execute("SELECT 1 FROM tasks WHERE number=?", (number,)).fetchone():
                task["number"] = None
                unlinked.setdefault(e["task_id"], []).append("number")
            if task.get("type_id") and not c.execute("SELECT 1 FROM task_types WHERE id=?", (task["type_id"],)).fetchone():
                # Its type was deleted while it sat in the trash: it comes back on General, without a step.
                task["type_id"] = H.GENERAL_TYPE
                task["step_id"] = None
                task["step_rank"] = None
                unlinked.setdefault(e["task_id"], []).append("type")
            if task.get("step_id") and not c.execute("SELECT 1 FROM task_steps WHERE id=?", (task["step_id"],)).fetchone():
                # Its step was retired (Waiting and Won't do are no longer steps): it comes back without one.
                task["step_id"] = None
                task["step_rank"] = None
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
            if table not in CORE and table != "task_relations":
                for row in found:
                    _try_insert(c, table, row, skipped)
    for e, snap in snapshots:
        tid = e["task_id"]
        relations = [r for r in snap["rows"].get("task_relations", []) if "kind" in r]
        task = snap["rows"]["tasks"][0]
        for column, (kind, inward) in LEGACY_LINKS.items():
            if task.get(column):
                ends = (task[column], tid) if inward else (tid, task[column])
                relations.append({"from_task": ends[0], "to_task": ends[1], "kind": kind, "created_at": task.get("updated_at") or H.now()})
        for row in snap["rows"].get("task_relations", []):
            if "related_id" in row:     # draft #110's related-only rows
                a, b = sorted((row["task_id"], row["related_id"]))
                relations.append({"from_task": a, "to_task": b, "kind": "related", "created_by": row.get("added_by"),
                                  "created_at": row["created_at"]})
        for row in relations:
            if TR.restore_row(c, row) is False:
                unlinked.setdefault(tid, []).append(row["kind"])
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
        c.execute("DELETE FROM task_trash_assets WHERE task_id=?", (e["task_id"],))
        H.event(c, actor, "task.restored", e["task_id"], {"title": e["title"], "deleted_by": e["deleted_by"],
                                                          "unlinked": unlinked.get(e["task_id"], [])})
    from .roadmap_flow import changed
    for e in entries:
        changed(c, e["task_id"])
    H.recount(c, [t for e in entries for t in (e["owner"], e["requester"])])
    return {"restored": [e["task_id"] for e in entries], "unlinked": unlinked, "skipped": skipped}


def _files_left(c, tid):
    """The files attached to a deleted task, which a purge leaves behind: their versions and activity
    are append-only and their bytes may be shared with other files, so they stay, unreadable for good.
    `ids` are task files (with their versions); `attachments` are the blobs attached when the task was
    made that no counted version already holds."""
    scope = "task:" + tid
    ids, versions, size = [], 0, 0
    if _has(c, "bot_files", "scope"):
        ids = [r[0] for r in c.execute("SELECT id FROM bot_files WHERE scope=? ORDER BY id", (scope,))]
        versions, size = c.execute("SELECT count(*), COALESCE(SUM(size),0) FROM bot_file_versions "
                                   "WHERE file_id IN (SELECT id FROM bot_files WHERE scope=?)", (scope,)).fetchone()
    attached = c.execute("SELECT b.id, b.size FROM task_trash_assets a JOIN blobs b ON b.id=a.blob_id "
                         "WHERE a.task_id=? AND NOT EXISTS (SELECT 1 FROM bot_file_versions v JOIN bot_files f "
                         "ON f.id=v.file_id WHERE f.scope=? AND v.blob_id=b.id) ORDER BY b.id",
                         (tid, scope)).fetchall() if _has(c, "blobs", "size") else []
    return {"ids": ids, "versions": versions, "attachments": [r[0] for r in attached],
            "bytes": size + sum(r[1] for r in attached)}


def purge_trash(c, older_than_days, apply=False, actor=None):
    """Remove for good what has been in the trash longer than `older_than_days`. Lists it unless apply.
    The trash row stays as a bare tombstone (number, title, who and when) so its number is never reused.
    The report and each `task.purged` event name the task files left behind (`files_left`)."""
    ensure(c)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    rows = [dict(r) for r in c.execute("SELECT task_id, title, deleted_at, deleted_by FROM task_trash "
                                       "WHERE purged_at IS NULL AND deleted_at < ? ORDER BY deleted_at", (cutoff,))]
    left = {r["task_id"]: _files_left(c, r["task_id"]) for r in rows}
    report = {"tasks": len(rows), "older_than": cutoff, "applied": False,
              "files_left": {"files": sum(len(f["ids"]) + len(f["attachments"]) for f in left.values()),
                             "versions": sum(f["versions"] for f in left.values()),
                             "bytes": sum(f["bytes"] for f in left.values()),
                             "ids": [i for f in left.values() for i in f["ids"]],
                             "attachments": [i for f in left.values() for i in f["attachments"]]}}
    if not apply or not rows:
        return report
    now = H.now()
    for r in rows:
        c.execute("UPDATE task_trash SET rows='{}', purged_at=? WHERE task_id=?", (now, r["task_id"]))
        H.event(c, actor or H.KEEPER, "task.purged", r["task_id"], {"title": r["title"], "deleted_at": r["deleted_at"],
                                                                    "files_left": left[r["task_id"]]["ids"],
                                                                    "attachments_left": left[r["task_id"]]["attachments"]})
    report["applied"] = True
    return report
