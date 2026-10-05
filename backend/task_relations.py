"""Every task-to-task relationship, in one table, written in one place.

A row says `from_task` stands in `kind` to `to_task`:

- `parent`: from is a subtask of to. One parent a task (a partial unique index).
- `blocks`: from blocks to. A task may have several blockers; finishing one takes its row away.
- `related`: the two belong together. No direction: stored once, lower id first (a CHECK).
- `duplicate_of`: from duplicates to.
- `follow_up`: from was split off or followed up from to.

`relate` is the only writer (the task page, `hub task relate`, the MCP tool and task create all
come here), so the rules live in one place: who may change which end, no loops for `parent` and
`blocks`, a private task only under or behind tasks its own parties can read, and a history row on
both tasks. Reads filter the other end through the reader's task visibility, so a task someone
cannot open is never named to them. Goals keep their own `parent_id`; this is tasks only.

This module imports nothing from hubdb at load, so hubdb can import it; the writers reach hubdb
through `_H()`.
"""

KINDS = ("parent", "blocks", "related", "duplicate_of", "follow_up")

# Directed kinds read differently from each end; `related` reads the same from both.
FIELDS = {  # kind -> (history field on from_task, history field on to_task)
    "parent": ("parent_id", "subtask"),
    "blocks": ("blocks", "blocked_by"),
    "related": ("related", "related"),
    "duplicate_of": ("duplicate_of", "duplicated_by"),
    "follow_up": ("follow_up_of", "follow_ups"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_relations(
  from_task TEXT NOT NULL REFERENCES tasks(id), to_task TEXT NOT NULL REFERENCES tasks(id),
  kind TEXT NOT NULL CHECK (kind IN ('parent','blocks','related','duplicate_of','follow_up')),
  created_by TEXT, created TEXT NOT NULL,
  PRIMARY KEY(from_task, to_task, kind),
  CHECK (from_task <> to_task), CHECK (kind <> 'related' OR from_task < to_task));
CREATE INDEX IF NOT EXISTS task_relations_to ON task_relations(to_task, kind);
CREATE INDEX IF NOT EXISTS task_relations_from ON task_relations(from_task, kind);
CREATE UNIQUE INDEX IF NOT EXISTS task_relations_one_parent ON task_relations(from_task) WHERE kind='parent';
"""


def _H():
    from . import hubdb
    return hubdb


# ----------------------------------------------------------------------------- schema
def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn):
    """Create the table, move `tasks.parent_id` and `tasks.blocked_by` into it, and drop both columns.

    Idempotent: a database that already has the table and lacks the columns is left as it is. A
    column pointing at a task that no longer exists is dropped with nothing to move. Draft #110's
    related-only table (`task_id`, `related_id`) is converted to `related` rows."""
    if "task_id" in _columns(conn, "task_relations"):
        conn.execute("ALTER TABLE task_relations RENAME TO task_relations_v110")
        for index in ("task_relations_related",):
            conn.execute(f"DROP INDEX IF EXISTS {index}")
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)
    if _columns(conn, "task_relations_v110"):
        conn.execute("INSERT OR IGNORE INTO task_relations(from_task,to_task,kind,created_by,created) "
                     "SELECT min(task_id,related_id),max(task_id,related_id),'related',added_by,created "
                     "FROM task_relations_v110 WHERE task_id<>related_id")
        conn.execute("DROP TABLE task_relations_v110")
    columns = _columns(conn, "tasks")
    if "parent_id" in columns:
        conn.execute("INSERT OR IGNORE INTO task_relations(from_task,to_task,kind,created_by,created) "
                     "SELECT t.id,t.parent_id,'parent',NULL,coalesce(t.updated,t.created,'') FROM tasks t "
                     "WHERE t.parent_id IS NOT NULL AND t.parent_id<>t.id "
                     "AND t.parent_id IN (SELECT id FROM tasks)")
        conn.execute("DROP INDEX IF EXISTS tasks_parent")
        conn.execute("ALTER TABLE tasks DROP COLUMN parent_id")
    if "blocked_by" in columns:
        conn.execute("INSERT OR IGNORE INTO task_relations(from_task,to_task,kind,created_by,created) "
                     "SELECT t.blocked_by,t.id,'blocks',NULL,coalesce(t.updated,t.created,'') FROM tasks t "
                     "WHERE t.blocked_by IS NOT NULL AND t.blocked_by<>t.id "
                     "AND t.blocked_by IN (SELECT id FROM tasks)")
        conn.execute("DROP INDEX IF EXISTS tasks_blocked_by")
        conn.execute("ALTER TABLE tasks DROP COLUMN blocked_by")


# ----------------------------------------------------------------------------- reads
def parent_sql(alias="tasks"):
    """SQL for a task's parent id, given the alias its `tasks` row has in the query."""
    return (f"(SELECT r.to_task FROM task_relations r WHERE r.from_task={alias}.id "
            "AND r.kind='parent')")


CHILD_IDS = "SELECT from_task FROM task_relations WHERE kind='parent' AND to_task=?"


def parent_of(conn, task_id):
    row = conn.execute("SELECT to_task FROM task_relations WHERE from_task=? AND kind='parent'",
                       (task_id,)).fetchone()
    return row[0] if row else None


def child_ids(conn, task_id):
    return [r[0] for r in conn.execute(CHILD_IDS + " ORDER BY created, rowid", (task_id,))]


def blocker_ids(conn, task_id):
    """The tasks blocking this one, oldest first."""
    return [r[0] for r in conn.execute("SELECT from_task FROM task_relations WHERE to_task=? AND kind='blocks' "
                                       "ORDER BY created, rowid", (task_id,))]


def blocked_ids(conn, task_id):
    """The tasks this one blocks."""
    return [r[0] for r in conn.execute("SELECT to_task FROM task_relations WHERE from_task=? AND kind='blocks' "
                                       "ORDER BY created, rowid", (task_id,))]


def live_blocker(conn, task_id, statuses):
    """The first blocker still in one of `statuses`, or None."""
    marks = ",".join("?" * len(statuses))
    row = conn.execute("SELECT t.id FROM task_relations r JOIN tasks t ON t.id=r.from_task "
                       f"WHERE r.to_task=? AND r.kind='blocks' AND t.status IN ({marks}) "
                       "ORDER BY r.created, r.rowid LIMIT 1", (task_id, *statuses)).fetchone()
    return row[0] if row else None


def ancestors(conn, task_id):
    """Parent, grandparent, ... nearest first; stops at a loop."""
    out, seen = [], {task_id}
    cursor = parent_of(conn, task_id)
    while cursor and cursor not in seen:
        out.append(cursor)
        seen.add(cursor)
        cursor = parent_of(conn, cursor)
    return out


def descendant_ids(conn, task_id):
    return [r[0] for r in conn.execute(
        "WITH RECURSIVE tree(id) AS (" + CHILD_IDS + " UNION SELECT r.from_task FROM task_relations r "
        "JOIN tree ON r.to_task=tree.id AND r.kind='parent') SELECT id FROM tree", (task_id,))]


def _parts(values):
    values = list(values)
    for i in range(0, len(values), 400):
        yield values[i:i + 400]


def grouped(conn, task_ids, visible_sql="1"):
    """{task id: {kind: [{id, title, status, owner, direction}]}} for each of `task_ids`.

    `direction` is `out` when the task is the row's from_task (it is the subtask, the blocker,
    the duplicate, the follow-up) and `in` when it is the to_task; `related` has none and says
    `both`. A task's own subtasks are left out (they are `children`). The other end must be in
    `visible_sql` (the reader's auth.task_sql), so a task the reader cannot open is never listed."""
    out = {tid: {} for tid in task_ids}
    for part in _parts(task_ids):
        marks = ",".join("?" * len(part))
        rows = conn.execute(
            "SELECT * FROM (SELECT r.from_task AS here, CASE WHEN r.kind='related' THEN 'both' ELSE 'out' END "
            "AS direction, r.kind, r.created AS at, r.rowid AS rid, t.id, t.title, t.status, t.owner "
            "FROM task_relations r JOIN tasks t ON t.id=r.to_task "
            f"WHERE r.from_task IN ({marks}) "
            "UNION ALL SELECT r.to_task, CASE WHEN r.kind='related' THEN 'both' ELSE 'in' END, r.kind, r.created, "
            "r.rowid, t.id, t.title, t.status, t.owner FROM task_relations r JOIN tasks t ON t.id=r.from_task "
            f"WHERE r.to_task IN ({marks}) AND r.kind<>'parent') "
            f"WHERE id IN (SELECT id FROM tasks WHERE {visible_sql}) ORDER BY at, rid", (*part, *part))
        for row in rows:
            entry = {"id": row["id"], "title": row["title"], "status": row["status"], "owner": row["owner"],
                     "direction": row["direction"]}
            out[row["here"]].setdefault(row["kind"], []).append(entry)
    return out


def for_prompt(conn, task_id, readable):
    """The relations a bot may read, for its run's prompt: kind, direction, id, title, status.
    `readable(row)` decides each other end; one it may not read is left out without a trace."""
    H = _H()
    lines = []
    for kind, entries in grouped(conn, [task_id]).get(task_id, {}).items():
        for entry in entries:
            if readable(H.task(conn, entry["id"])):
                lines.append({"kind": kind, "direction": entry["direction"], "id": entry["id"],
                              "title": entry["title"], "status": entry["status"]})
    children = conn.execute("SELECT t.id,t.title,t.status FROM tasks t WHERE t.id IN (" + CHILD_IDS + ") "
                            "ORDER BY t.created", (task_id,)).fetchall()
    for row in children:
        if readable(H.task(conn, row["id"])):
            lines.append({"kind": "parent", "direction": "in", "id": row["id"], "title": row["title"],
                          "status": row["status"]})
    return lines


# ----------------------------------------------------------------------------- writes
def normalize(task_id, other_id, kind):
    """(from, to, kind) for a request; `blocked_by` reads "task is blocked by other"."""
    kind = str(kind or "related").strip().lower().replace("-", "_")
    if kind == "blocked_by":
        return other_id, task_id, "blocks"
    if kind == "follow_up_of":
        kind = "follow_up"
    return task_id, other_id, kind


def _insert(conn, actor, from_id, to_id, kind, at):
    if kind == "related":
        from_id, to_id = sorted((from_id, to_id))
    conn.execute("INSERT INTO task_relations(from_task,to_task,kind,created_by,created) VALUES(?,?,?,?,?)",
                 (from_id, to_id, kind, actor, at))


def _exists(conn, from_id, to_id, kind):
    if kind == "related":
        from_id, to_id = sorted((from_id, to_id))
    return bool(conn.execute("SELECT 1 FROM task_relations WHERE from_task=? AND to_task=? AND kind=?",
                             (from_id, to_id, kind)).fetchone())


def _delete(conn, from_id, to_id, kind):
    if kind == "related":
        from_id, to_id = sorted((from_id, to_id))
    return conn.execute("DELETE FROM task_relations WHERE from_task=? AND to_task=? AND kind=?",
                        (from_id, to_id, kind)).rowcount


def _history(conn, actor, from_id, to_id, kind, added, note="", at=None):
    """One row on each task. The other task is named by id only: its title may be private."""
    H = _H()
    out_field, in_field = FIELDS[kind]
    at = at or H.now()
    for here, field, other in ((from_id, out_field, to_id), (to_id, in_field, from_id)):
        H._task_event(conn, here, actor, field, None if added else other, other if added else None, note)
        conn.execute("UPDATE tasks SET updated=? WHERE id=?", (at, here))


def _mine(conn, actor, row, mover):
    """task_update's gate: a party, a delegate, an ancestor's party, a type's working bot, or a mover."""
    H = _H()
    mine = actor in (row["owner"], row["requester"]) or bool(H._one(
        conn, "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires>? LIMIT 1",
        (row["id"], actor, H.now())))
    mine = mine or H.task_ancestor_party(conn, actor, row) or H.type_bot_works(conn, actor, row)
    if not mine and not mover and actor != H.KEEPER:
        H.refuse(conn, actor, "identity", f"{row['id']} is not yours to change")


def _mover(conn, actor, mover):
    H = _H()
    if mover is None:
        mover = actor == H.KEEPER or H.is_human(actor) and H.can_move(conn, actor)
    return bool(mover)


def _need_mover(conn, actor, what):
    H = _H()
    H.refuse(conn, actor, "identity", f"{what} on a task is changed by a person on the "
                                      f"{', '.join(H.MOVER_TEAMS)} teams, not by {H.actor_id(actor)}")


def _task(conn, actor, task_id):
    H = _H()
    row = H.task(conn, task_id) if task_id else None
    if not row:
        H.refuse(conn, actor, "not-found", f"no task {task_id}")
    return row


def relate(conn, actor, task_id, other_id, kind="related", remove=False, mover=None, note=""):
    """Add (or with `remove`, take off) one relation between two tasks. Returns True when it changed
    something; adding what is already there changes nothing."""
    H = _H()
    H._writer(conn, actor)
    from_id, to_id, kind = normalize(str(task_id or "").strip(), str(other_id or "").strip(), kind)
    if kind not in KINDS:
        H.refuse(conn, actor, "kind", f"a relation is {'|'.join(KINDS)}, not {kind}")
    if from_id == to_id:
        H.refuse(conn, actor, "kind", "a task cannot be related to itself")
    mover = _mover(conn, actor, mover)
    if kind == "parent":
        current = parent_of(conn, from_id)
        if remove:
            if current != to_id:
                _task(conn, actor, to_id)
                H.refuse(conn, actor, "not-found", f"{to_id} is not the parent of {from_id}")
            return set_parent(conn, actor, from_id, None, mover=mover, note=note)
        return set_parent(conn, actor, from_id, to_id, mover=mover, note=note)
    if kind == "blocks":
        return (remove_blocker if remove else add_blocker)(conn, actor, to_id, from_id, mover=mover, note=note)
    rows = [_task(conn, actor, tid) for tid in (from_id, to_id)]
    for row in rows:
        H._task_link_allowed(conn, actor, row, mover)
    if remove:
        if not _delete(conn, from_id, to_id, kind):
            H.refuse(conn, actor, "not-found", f"{to_id} is not {kind.replace('_', ' ')} of {from_id}")
    else:
        if _exists(conn, from_id, to_id, kind):
            return False
        if kind != "related" and _exists(conn, to_id, from_id, kind):
            H.refuse(conn, actor, "kind", f"{to_id} is already {kind.replace('_', ' ')} {from_id}")
        _insert(conn, actor, from_id, to_id, kind, H.now())
    _history(conn, actor, from_id, to_id, kind, not remove, note)
    H.event(conn, actor, "task.unrelate" if remove else "task.relate", from_id, {"task": to_id, "kind": kind})
    return True


def set_parent(conn, actor, child_id, parent_id, mover=None, note="", private=None):
    """Put a task under `parent_id`, or take it out of its parent with None.

    The rules are the ones re-parenting always had: the child's parties (or a mover) move it; only
    a mover moves someone else's; a subtask leaves its parent only by the parent's parties or a mover;
    the new parent must be one the actor may file under (`hubdb._task_parent`: readable, a bot only
    under a task it is party to or works, never its own ancestor). A task under a private parent
    becomes private."""
    H = _H()
    H._writer(conn, actor)
    row = _task(conn, actor, child_id)
    H._task_private_writer(conn, actor, row)
    mover = _mover(conn, actor, mover)
    _mine(conn, actor, row, mover)
    if actor not in (row["owner"], row["requester"]) and not mover:
        _need_mover(conn, actor, "parent")
    old = parent_of(conn, child_id)
    new = str(parent_id or "").strip() or None
    if new == old:
        return False
    if old and not mover:
        old_parent = H.task(conn, old)
        if old_parent and actor not in (old_parent["owner"], old_parent["requester"]):
            H.refuse(conn, actor, "identity", "Only the parent's owner, requester or a mover can move a subtask away")
    target = None
    if new:
        target = H._task_parent(conn, actor, child_id, new)
        if H.task_private(conn, target) and private is False:
            H.refuse(conn, actor, "private", "A task under a private parent stays private")
        if not mover and actor not in (target["owner"], target["requester"]) and not H.task_ancestor_party(conn, actor, target):
            H.refuse(conn, actor, "identity", "Re-parent under a task you own or requested")
    at = H.now()
    if old:
        _delete(conn, child_id, old, "parent")
        H._task_event(conn, old, actor, "subtask", child_id, None, note)
    if new:
        _insert(conn, actor, child_id, new, "parent", at)
        H._task_event(conn, new, actor, "subtask", None, child_id, note)
    H._task_event(conn, child_id, actor, "parent_id", old, new, note)
    made_private = bool(target and H.task_private(conn, target) and not H.task_private(conn, row))
    conn.execute("UPDATE tasks SET updated=?" + (", private=1" if made_private else "") + " WHERE id IN (?,?,?)",
                 (at, child_id, old or child_id, new or child_id))
    if made_private:
        H._task_event(conn, child_id, actor, "private", 0, 1, "")
        H._private_followups(conn, child_id)
    H.event(conn, actor, "task.relate", child_id, {"task": new or old, "kind": "parent", "removed": not new})
    if old and row["status"] not in ("done", "closed", "declined"):
        # The old parent may have nothing left open now.
        H._parent_finished(conn, {**row, "status": "closed"}, row["status"], actor, parent=old)
    return True


def _check_blocker(conn, actor, row, blocker_id):
    H = _H()
    if blocker_id == row["id"]:
        H.refuse(conn, actor, "kind", "a task cannot block itself")
    block = H.task(conn, blocker_id)
    if not block:
        H.refuse(conn, actor, "not-found", f"no task {blocker_id} to block on")
    H._task_private_writer(conn, actor, block)
    if H.task_private(conn, block) and (not H.task_private(conn, row) or not all(
            H.task_private_readable(conn, party, block) for party in (row["owner"], row["requester"]))):
        H.refuse(conn, actor, "private", "A private dependency requires the same private audience")
    # No loop: the blocker must not already wait, directly or through others, on this task.
    loop = conn.execute("WITH RECURSIVE ahead(id) AS (SELECT to_task FROM task_relations WHERE from_task=? "
                        "AND kind='blocks' UNION SELECT r.to_task FROM task_relations r JOIN ahead "
                        "ON r.from_task=ahead.id AND r.kind='blocks') SELECT 1 FROM ahead WHERE id=?",
                        (row["id"], blocker_id)).fetchone()
    if loop:
        H.refuse(conn, actor, "kind", f"{blocker_id} already waits on this task")
    return block


def _blocker_gate(conn, actor, row, mover):
    """The owner says what its own task waits on; anyone else who may change it must be a mover."""
    _mine(conn, actor, row, mover)
    if actor != row["owner"] and not mover:
        _need_mover(conn, actor, "blocked_by")


def add_blocker(conn, actor, task_id, blocker_id, mover=None, note=""):
    """`blocker_id` blocks `task_id`. A task may wait on several."""
    H = _H()
    H._writer(conn, actor)
    row = _task(conn, actor, task_id)
    H._task_private_writer(conn, actor, row)
    mover = _mover(conn, actor, mover)
    _blocker_gate(conn, actor, row, mover)
    if _exists(conn, blocker_id, task_id, "blocks"):
        return False
    _check_blocker(conn, actor, row, blocker_id)
    _insert(conn, actor, blocker_id, task_id, "blocks", H.now())
    _history(conn, actor, blocker_id, task_id, "blocks", True, note)
    H.event(conn, actor, "task.relate", task_id, {"task": blocker_id, "kind": "blocked_by"})
    return True


def remove_blocker(conn, actor, task_id, blocker_id, mover=None, note="", keeper_note=None):
    H = _H()
    H._writer(conn, actor)
    row = _task(conn, actor, task_id)
    if actor != H.KEEPER:
        H._task_private_writer(conn, actor, row)
        _blocker_gate(conn, actor, row, _mover(conn, actor, mover))
    if not _delete(conn, blocker_id, task_id, "blocks"):
        H.refuse(conn, actor, "not-found", f"{blocker_id} does not block {task_id}")
    _history(conn, actor, blocker_id, task_id, "blocks", False, note if keeper_note is None else keeper_note)
    H.event(conn, actor, "task.unrelate", task_id, {"task": blocker_id, "kind": "blocked_by"})
    return True


# ----------------------------------------------------------------------------- trash
def snapshot_rows(conn, task_id):
    """Every relation row touching the task, for the trash."""
    return [dict(r) for r in conn.execute("SELECT rowid AS _rowid, * FROM task_relations "
                                          "WHERE from_task=? OR to_task=?", (task_id, task_id))]


def restore_row(conn, row):
    """Put one trashed relation back if both ends exist and it still fits (one parent a task).
    Returns True when it went back, None when it was already there, False when it could not."""
    if _exists(conn, row["from_task"], row["to_task"], row["kind"]):
        return None
    try:
        conn.execute("SAVEPOINT relation_row")
        conn.execute("INSERT INTO task_relations(from_task,to_task,kind,created_by,created) VALUES(?,?,?,?,?)",
                     (row["from_task"], row["to_task"], row["kind"], row.get("created_by"), row["created"]))
        conn.execute("RELEASE relation_row")
        return True
    except Exception:
        conn.execute("ROLLBACK TO relation_row")
        conn.execute("RELEASE relation_row")
        return False
