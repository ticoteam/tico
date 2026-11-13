"""Every task-to-task relationship, in one table, written in one place.

A row says `from_task` stands in `kind` to `to_task`:

- `parent`: from is a sub-task of to. One parent a task (a partial unique index). It is written when the task is
  filed (hubdb.task_create, beside the engine's own link); after that only a person changes it, from the board
  (backend/task_merge.py, which rewrites both copies).
- `related`: the two belong together. No direction: stored once, lower id first (a CHECK).
- `duplicate_of`: from duplicates to.
- `follow_up`: from was split off or followed up from to.
- `after`: from merges after to. The one relation a task waits on, and only in a stage that queues PRs (Merge): it is
  not queued until `to` has merged (backend/merge_order.py). A loop, or an order on a task that has ended, is refused;
  the writer must be able to change `from` and read `to`.
- `depends_on`: from cannot go on until to ends ("Blocked by #304"; to reads it as "Blocks #307"), in any pipeline and
  stage, with no end date (backend/task_depends.py). The same write rules as `after`.

`relate` is the only writer of the other three (the task page, `tico task relate` and the MCP tool all come here),
so the rules live in one place: who may change which end, and a history row on both tasks. A task does not wait on a
relation, except `after` in Merge and `depends_on`: what a task waits on is its requirements, the tasks it filed and
those two (backend/waiting_on.py). Reads filter the other end through the reader's task visibility, so a task someone
cannot open is never named to them. Goals keep their own `parent_id`; this is tasks only.

This module imports nothing from hubdb at load, so hubdb can import it; the writers reach hubdb
through `_H()`.
"""

KINDS = ("parent", "related", "duplicate_of", "follow_up", "after", "depends_on")
# The kinds `relate` writes: a parent is set when the task is filed, or by a person through task_merge.set_parent.
RELATED = ("related", "duplicate_of", "follow_up", "after", "depends_on")

# Directed kinds read differently from each end; `related` reads the same from both.
FIELDS = {  # kind -> (history field on from_task, history field on to_task)
    "parent": ("parent_id", "subtask"),
    "related": ("related", "related"),
    "duplicate_of": ("duplicate_of", "duplicated_by"),
    "follow_up": ("follow_up_of", "follow_ups"),
    "after": ("after", "before"),
    "depends_on": ("depends_on", "blocks"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_relations(
  from_task TEXT NOT NULL REFERENCES tasks(id), to_task TEXT NOT NULL REFERENCES tasks(id),
  kind TEXT NOT NULL CHECK (kind IN ('parent','related','duplicate_of','follow_up','after','depends_on')),
  created_by TEXT, created_at TEXT NOT NULL,
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
    """Create the table, and drop `tasks.parent_id`, `tasks.blocked_by` and Draft #110's related-only table where an
    old database still has them. Nothing is moved: a database that kept them has no tasks, since one from before the
    Pipelines v3 cut-over that has tasks is refused before any migration runs (migrations.refuse_old_tasks).

    Idempotent: a database that already has the table and lacks the columns is left as it is."""
    if "task_id" in _columns(conn, "task_relations"):
        conn.execute("ALTER TABLE task_relations RENAME TO task_relations_v110")
        conn.execute("DROP INDEX IF EXISTS task_relations_related")
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute("DROP TABLE IF EXISTS task_relations_v110")
    columns = _columns(conn, "tasks")
    for column, index in (("parent_id", "tasks_parent"), ("blocked_by", "tasks_blocked_by")):
        if column in columns:
            conn.execute(f"DROP INDEX IF EXISTS {index}")
            conn.execute(f"ALTER TABLE tasks DROP COLUMN {column}")


# ----------------------------------------------------------------------------- reads
def parent_sql(alias="tasks"):
    """SQL for a task's parent id, given the alias its `tasks` row has in the query."""
    return f"(SELECT r.to_task FROM task_relations r WHERE r.from_task={alias}.id AND r.kind='parent')"


CHILD_IDS = "SELECT from_task FROM task_relations WHERE kind='parent' AND to_task=?"


PARENT_SQL = "SELECT to_task FROM task_relations WHERE from_task=? AND kind='parent'"


def parent_of(conn, task_id):
    # Through the snapshot's read cache, so a page that read its tasks' ancestry together (prime_ancestry) reads no
    # parent twice.
    from .read_cache import one

    row = one(conn, PARENT_SQL, (task_id,))
    return row[0] if row else None


def child_ids(conn, task_id):
    return [r[0] for r in conn.execute(CHILD_IDS + " ORDER BY created_at, rowid", (task_id,))]


def prime_ancestry(conn, task_ids):
    """Read the parents of these tasks and of all their ancestors, one query a level, for the parent_of and ancestors
    lookups that follow in this snapshot. Returns every task id it met. Without a read snapshot it reads nothing:
    those lookups then read for themselves."""
    from .read_cache import cache, prime

    met, walked, frontier = set(), set(), {tid for tid in task_ids if tid}
    if cache(conn) is None:
        return met
    while frontier:
        met |= frontier
        walked |= frontier
        parents = {}
        for part in _parts(sorted(frontier)):
            for row in conn.execute(
                "SELECT from_task,to_task FROM task_relations WHERE from_task IN (%s) "
                "AND kind='parent' ORDER BY created_at, rowid" % ",".join("?" * len(part)),
                part,
            ):
                parents[row[0]] = (row[1],)
        prime(conn, PARENT_SQL, {(tid,): parents.get(tid) for tid in frontier})
        frontier = {row[0] for row in parents.values()} - walked
    return met


def is_descendant(conn, task_id, other_id):
    """True when `other_id` sits under `task_id`, at any depth."""
    return task_id in ancestors(conn, other_id)


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
    return [
        r[0]
        for r in conn.execute(
            "WITH RECURSIVE tree(id) AS (" + CHILD_IDS + " UNION SELECT r.from_task FROM task_relations r "
            "JOIN tree ON r.to_task=tree.id AND r.kind='parent') SELECT id FROM tree",
            (task_id,),
        )
    ]


def _parts(values):
    values = list(values)
    for i in range(0, len(values), 400):
        yield values[i : i + 400]


PRIVATE_PARENT = "a private task"


def grouped(conn, task_ids, visible_sql="1"):
    """{task id: {kind: [{id, title, status, owner, direction}]}} for each of `task_ids`.

    `direction` is `out` when the task is the row's from_task (it is the subtask, the blocker,
    the duplicate, the follow-up) and `in` when it is the to_task; `related` has none and says
    `both`. A task's own subtasks are left out (they are `children`). The other end must be in
    `visible_sql` (the reader's auth.task_sql), so a task the reader cannot open is never listed."""
    out = {tid: {} for tid in task_ids}
    for part in _parts(task_ids):
        marks = ",".join("?" * len(part))
        # Enumerate existing edges before targets: otherwise large IN lists can make SQLite
        # probe every page/visible-task pair through the composite relationship key.
        rows = conn.execute(
            "SELECT * FROM (SELECT r.from_task AS here, CASE WHEN r.kind='related' THEN 'both' ELSE 'out' END "
            "AS direction, r.kind, r.created_at AS at, r.rowid AS rid, t.id, t.title, t.status, t.owner_id "
            "FROM task_relations r INDEXED BY task_relations_from CROSS JOIN tasks t ON t.id=r.to_task "
            f"WHERE r.from_task IN ({marks}) "
            "UNION ALL SELECT r.to_task, CASE WHEN r.kind='related' THEN 'both' ELSE 'in' END, r.kind, r.created_at, "
            "r.rowid, t.id, t.title, t.status, t.owner_id "
            "FROM task_relations r INDEXED BY task_relations_to CROSS JOIN tasks t ON t.id=r.from_task "
            f"WHERE r.to_task IN ({marks}) AND r.kind<>'parent') x "
            # The other end's visibility by primary key, not a list of every visible task.
            f"WHERE EXISTS (SELECT 1 FROM tasks WHERE tasks.id=x.id AND ({visible_sql})) ORDER BY at, rid",
            (*part, *part),
        )
        for row in rows:
            entry = {
                "id": row["id"],
                "title": row["title"],
                "status": row["status"],
                "owner_id": row["owner_id"],
                "direction": row["direction"],
            }
            if row["kind"] in ("after", "depends_on"):
                # An order reads "After #40 · Merge", a dependency "Blocked by #304 · Build": the other task's number
                # and the stage it is in.
                entry.update(_place(conn, row["id"]))
            if row["kind"] == "depends_on":
                entry["since"] = row["at"]  # "Blocked since Nov 13"
            out[row["here"]].setdefault(row["kind"], []).append(entry)
        if visible_sql == "1":
            continue
        # A company-visible task filed from private work (task_privacy.from_private) under a private parent the reader
        # cannot open: the parent is there, as "a private task", with nothing of it but that.
        for row in conn.execute(
            "SELECT r.from_task FROM task_relations r INDEXED BY task_relations_from "
            "JOIN tasks c ON c.id=r.from_task JOIN tasks p ON p.id=r.to_task "
            f"WHERE r.from_task IN ({marks}) AND r.kind='parent' AND c.private=0 "
            "AND (p.private IS NULL OR p.private<>0) "
            f"AND NOT EXISTS (SELECT 1 FROM tasks WHERE tasks.id=r.to_task AND ({visible_sql}))",
            part,
        ):
            out[row["from_task"]]["parent"] = [
                {
                    "id": None,
                    "title": PRIVATE_PARENT,
                    "status": None,
                    "owner_id": None,
                    "direction": "out",
                    "private": True,
                }
            ]
    return out


def _place(conn, task_id):
    """{number, stage}: a task's number and the name of the stage it is in."""
    found = conn.execute(
        "SELECT t.number, s.name FROM tasks t LEFT JOIN stages s ON s.id=t.stage_id WHERE t.id=?", (task_id,)
    ).fetchone()
    return {"number": found[0], "stage": found[1]} if found else {"number": None, "stage": None}


def for_prompt(conn, task_id, readable):
    """The relations a bot may read, for its run's prompt: kind, direction, id, title, status.
    `readable(row)` decides each other end; one it may not read is left out without a trace."""
    H = _H()
    lines = []
    for kind, entries in grouped(conn, [task_id]).get(task_id, {}).items():
        for entry in entries:
            if readable(H.task(conn, entry["id"])):
                lines.append(
                    {
                        "kind": kind,
                        "direction": entry["direction"],
                        "id": entry["id"],
                        "title": entry["title"],
                        "status": entry["status"],
                    }
                )
    children = conn.execute(
        "SELECT t.id,t.title,t.status FROM tasks t WHERE t.id IN (" + CHILD_IDS + ") ORDER BY t.created_at", (task_id,)
    ).fetchall()
    for row in children:
        if readable(H.task(conn, row["id"])):
            lines.append(
                {"kind": "parent", "direction": "in", "id": row["id"], "title": row["title"], "status": row["status"]}
            )
    return lines


# ----------------------------------------------------------------------------- writes
def normalize(task_id, other_id, kind):
    """(from, to, kind) for a request."""
    kind = str(kind or "related").strip().lower().replace("-", "_")
    if kind == "follow_up_of":
        kind = "follow_up"
    return task_id, other_id, kind


def _insert(conn, actor, from_id, to_id, kind, at):
    if kind == "related":
        from_id, to_id = sorted((from_id, to_id))
    conn.execute(
        "INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,?,?,?)",
        (from_id, to_id, kind, actor, at),
    )


def _exists(conn, from_id, to_id, kind):
    if kind == "related":
        from_id, to_id = sorted((from_id, to_id))
    return bool(
        conn.execute(
            "SELECT 1 FROM task_relations WHERE from_task=? AND to_task=? AND kind=?", (from_id, to_id, kind)
        ).fetchone()
    )


def _delete(conn, from_id, to_id, kind):
    if kind == "related":
        from_id, to_id = sorted((from_id, to_id))
    return conn.execute(
        "DELETE FROM task_relations WHERE from_task=? AND to_task=? AND kind=?", (from_id, to_id, kind)
    ).rowcount


def _history(conn, actor, from_id, to_id, kind, added, note="", at=None):
    """One row on each task. The other task is named by id only: its title may be private."""
    H = _H()
    out_field, in_field = FIELDS[kind]
    at = at or H.now()
    for here, field, other in ((from_id, out_field, to_id), (to_id, in_field, from_id)):
        H._task_event(conn, here, actor, field, None if added else other, other if added else None, note)
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (at, here))


def _mine(conn, actor, row, mover):
    """task_update's gate: a party, a delegate, an ancestor's party, the General Manager, or a mover."""
    H = _H()
    mover = H.task_mover(conn, actor, row, mover)
    mine = H.task_participant(conn, actor, row) or bool(
        H._one(
            conn,
            "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires_at>? LIMIT 1",
            (row["id"], actor, H.now()),
        )
    )
    mine = mine or H.task_ancestor_party(conn, actor, row) or H.general_manager_acts(conn, actor, row)
    if not mine and not mover and actor != H.KEEPER:
        H.refuse(
            conn, actor, "identity", f"{row['id']} is changed by its parties, their managers, delegates or task movers"
        )


def _mover(conn, actor, mover):
    H = _H()
    if mover is None:
        mover = actor == H.KEEPER or H.is_human(actor) and H.can_move(conn, actor)
    return bool(mover)


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
    H._writer(conn, actor, task_id=task_id)
    H._writer(conn, actor, task_id=other_id)
    from_id, to_id, kind = normalize(str(task_id or "").strip(), str(other_id or "").strip(), kind)
    if kind == "parent":
        H.refuse(
            conn,
            actor,
            "kind",
            "A task's parent is the task that filed it. A person moves it under another task from the Tasks board; "
            "otherwise file a new sub-task under the other task, or relate the two.",
        )
    if kind not in RELATED:
        H.refuse(conn, actor, "kind", f"a relation is {'|'.join(RELATED)}, not {kind}")
    if from_id == to_id:
        H.refuse(conn, actor, "kind", "a task cannot be related to itself")
    one_sided = kind in ("after", "depends_on")
    mover = _mover(conn, actor, mover)
    rows = [_task(conn, actor, tid) for tid in (from_id, to_id)]
    # An order or a dependency is the later task's: whoever may change it sets it, and the earlier task need only be
    # readable (the caller has read it). Every other relation changes both ends.
    for row in rows[:1] if one_sided else rows:
        H._task_link_allowed(conn, actor, row, mover)
    if kind == "after" and not remove and not _exists(conn, from_id, to_id, kind):
        from . import merge_order

        merge_order.check(conn, actor, rows[0], rows[1])
    if kind == "depends_on" and not remove and not _exists(conn, from_id, to_id, kind):
        from . import task_depends

        task_depends.check(conn, actor, rows[0], rows[1])
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
    if kind == "after" and remove:
        # Taking an order off may end a hold in Merge: the later task is queued now (merge_order.release).
        from . import merge_order

        merge_order.release_one(conn, from_id, actor)
    if kind == "depends_on" and rows[0].get("pipeline_id"):
        # Blocked now, or no longer: its ladder starts again, and the never-stuck pass reads it again (task_depends).
        from . import task_engine

        task_engine.changed(conn, from_id, actor)
    return True


# What each stored kind says, read from its `from` end, and from its `to` end. A write reads back in these words.
SAYS = {
    "parent": ("is a subtask of", "is the parent of"),
    "related": ("is related to", "is related to"),
    "duplicate_of": ("duplicates", "is duplicated by"),
    "follow_up": ("follows up", "is followed up by"),
    "after": ("merges after", "merges before"),
    "depends_on": ("depends on", "blocks"),
}


def _name(conn, task_id):
    row = conn.execute("SELECT number,title FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        return task_id[:8]
    title = str(row["title"] or "").strip()
    title = title if len(title) <= 60 else title[:59].rstrip() + "…"
    return (f"#{row['number']} " if row["number"] is not None else f"{task_id[:8]} ") + f'"{title}"'


def readback(conn, task_id, other_id, kind="related", remove=False, changed=True):
    """The relation a write stored, in words from both ends, after normalizing. Each line names the direction so a
    reversed write is visible in the reply."""
    from_id, to_id, kind = normalize(str(task_id or "").strip(), str(other_id or "").strip(), kind)
    if kind not in SAYS:
        return []
    if kind == "related":
        lines = [f"{_name(conn, from_id)} {SAYS[kind][0]} {_name(conn, to_id)}"]
    else:
        lines = [
            f"{_name(conn, from_id)} {SAYS[kind][0]} {_name(conn, to_id)}",
            f"{_name(conn, to_id)} {SAYS[kind][1]} {_name(conn, from_id)}",
        ]
    prefix = "Removed: " if remove else "Already: " if not changed else ""
    return [prefix + line for line in lines]


# ----------------------------------------------------------------------------- trash
def snapshot_rows(conn, task_id):
    """Every relation row touching the task, for the trash."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT rowid AS _rowid, * FROM task_relations WHERE from_task=? OR to_task=?", (task_id, task_id)
        )
    ]


def restore_row(conn, row):
    """Put one trashed relation back if both ends exist and it still fits (one parent a task).
    Returns True when it went back, None when it was already there, False when it could not."""
    if _exists(conn, row["from_task"], row["to_task"], row["kind"]):
        return None
    try:
        conn.execute("SAVEPOINT relation_row")
        conn.execute(
            "INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,?,?,?)",
            (row["from_task"], row["to_task"], row["kind"], row.get("created_by"), row["created_at"]),
        )
        conn.execute("RELEASE relation_row")
        return True
    except Exception:
        conn.execute("ROLLBACK TO relation_row")
        conn.execute("RELEASE relation_row")
        return False
