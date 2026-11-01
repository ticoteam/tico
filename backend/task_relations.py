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
        conn.execute("INSERT OR IGNORE INTO task_relations(from_task,to_task,kind,created_by,created_at) "
                     "SELECT min(task_id,related_id),max(task_id,related_id),'related',added_by,created_at "
                     "FROM task_relations_v110 WHERE task_id<>related_id")
        conn.execute("DROP TABLE task_relations_v110")
    columns = _columns(conn, "tasks")
    if "parent_id" in columns:
        conn.execute("INSERT OR IGNORE INTO task_relations(from_task,to_task,kind,created_by,created_at) "
                     "SELECT t.id,t.parent_id,'parent',NULL,coalesce(t.updated_at,t.created_at,'') FROM tasks t "
                     "WHERE t.parent_id IS NOT NULL AND t.parent_id<>t.id "
                     "AND t.parent_id IN (SELECT id FROM tasks)")
        conn.execute("DROP INDEX IF EXISTS tasks_parent")
        conn.execute("ALTER TABLE tasks DROP COLUMN parent_id")
    if "blocked_by" in columns:
        conn.execute("INSERT OR IGNORE INTO task_relations(from_task,to_task,kind,created_by,created_at) "
                     "SELECT t.blocked_by,t.id,'blocks',NULL,coalesce(t.updated_at,t.created_at,'') FROM tasks t "
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


PARENT_SQL = "SELECT to_task FROM task_relations WHERE from_task=? AND kind='parent'"
BLOCKED_SQL = "SELECT to_task FROM task_relations WHERE from_task=? AND kind='blocks' ORDER BY created_at, rowid"


def parent_of(conn, task_id):
    # Through the snapshot's read cache, so a page that read its tasks' ancestry together (prime_ancestry) reads no
    # parent twice.
    from .read_cache import one
    row = one(conn, PARENT_SQL, (task_id,))
    return row[0] if row else None


def child_ids(conn, task_id):
    return [r[0] for r in conn.execute(CHILD_IDS + " ORDER BY created_at, rowid", (task_id,))]


def blocker_ids(conn, task_id):
    """The tasks blocking this one, oldest first."""
    return [r[0] for r in conn.execute("SELECT from_task FROM task_relations WHERE to_task=? AND kind='blocks' "
                                       "ORDER BY created_at, rowid", (task_id,))]


def blocked_ids(conn, task_id):
    """The tasks this one blocks."""
    from .read_cache import rows
    return [r[0] for r in rows(conn, BLOCKED_SQL, (task_id,))]


def prime_ancestry(conn, task_ids):
    """Read the parents of these tasks and of all their ancestors, and the tasks each of them blocks, one query a
    level, for the parent_of, ancestors and blocked_ids lookups that follow in this snapshot (a Needs you card walks
    its task's chain). Returns every task id it met. Without a read snapshot it reads nothing: those lookups then
    read for themselves."""
    from .read_cache import cache, prime, prime_rows
    met, walked, frontier = set(), set(), {tid for tid in task_ids if tid}
    if cache(conn) is None:
        return met
    while frontier:
        met |= frontier
        walked |= frontier
        parents, blocked = {}, {}
        for part in _parts(sorted(frontier)):
            for row in conn.execute("SELECT from_task,to_task,kind FROM task_relations WHERE from_task IN (%s) "
                                    "AND kind IN ('parent','blocks') ORDER BY created_at, rowid"
                                    % ",".join("?" * len(part)), part):
                if row[2] == "parent":
                    parents[row[0]] = (row[1],)
                else:
                    blocked.setdefault(row[0], []).append((row[1],))
        prime(conn, PARENT_SQL, {(tid,): parents.get(tid) for tid in frontier})
        prime_rows(conn, BLOCKED_SQL, {(tid,): blocked.get(tid, ()) for tid in frontier})
        met |= {row[0] for found in blocked.values() for row in found}
        frontier = {row[0] for row in parents.values()} - walked
    return met


def live_blocker(conn, task_id, statuses):
    """The first blocker still in one of `statuses`, or None."""
    from .read_cache import one
    row = one(conn, live_blocker_sql(len(statuses)), (task_id, *statuses))
    return row[0] if row else None


def live_blocker_sql(count):
    """live_blocker's read for `count` statuses (auto_tasks.prime_waits seeds it for many tasks)."""
    return ("SELECT t.id FROM task_relations r JOIN tasks t ON t.id=r.from_task "
            f"WHERE r.to_task=? AND r.kind='blocks' AND t.status IN ({','.join('?' * count)}) "
            "ORDER BY r.created_at, r.rowid LIMIT 1")


def live_blockers(conn, task_id, statuses):
    """Every blocker still in one of `statuses`, as task rows, oldest relation first."""
    marks = ",".join("?" * len(statuses))
    return [dict(r) for r in conn.execute(
        "SELECT t.* FROM task_relations r JOIN tasks t ON t.id=r.from_task "
        f"WHERE r.to_task=? AND r.kind='blocks' AND t.status IN ({marks}) "
        "ORDER BY r.created_at, r.rowid", (task_id, *statuses))]


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
    return [r[0] for r in conn.execute(
        "WITH RECURSIVE tree(id) AS (" + CHILD_IDS + " UNION SELECT r.from_task FROM task_relations r "
        "JOIN tree ON r.to_task=tree.id AND r.kind='parent') SELECT id FROM tree", (task_id,))]


def _parts(values):
    values = list(values)
    for i in range(0, len(values), 400):
        yield values[i:i + 400]


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
            "AS direction, r.kind, r.created_at AS at, r.rowid AS rid, t.id, t.title, t.status, t.owner "
            "FROM task_relations r INDEXED BY task_relations_from CROSS JOIN tasks t ON t.id=r.to_task "
            f"WHERE r.from_task IN ({marks}) "
            "UNION ALL SELECT r.to_task, CASE WHEN r.kind='related' THEN 'both' ELSE 'in' END, r.kind, r.created_at, "
            "r.rowid, t.id, t.title, t.status, t.owner "
            "FROM task_relations r INDEXED BY task_relations_to CROSS JOIN tasks t ON t.id=r.from_task "
            f"WHERE r.to_task IN ({marks}) AND r.kind<>'parent') x "
            # The other end's visibility by primary key, not a list of every visible task.
            f"WHERE EXISTS (SELECT 1 FROM tasks WHERE tasks.id=x.id AND ({visible_sql})) ORDER BY at, rid",
            (*part, *part))
        for row in rows:
            entry = {"id": row["id"], "title": row["title"], "status": row["status"], "owner": row["owner"],
                     "direction": row["direction"]}
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
                f"AND NOT EXISTS (SELECT 1 FROM tasks WHERE tasks.id=r.to_task AND ({visible_sql}))", part):
            out[row["from_task"]]["parent"] = [{"id": None, "title": PRIVATE_PARENT, "status": None, "owner": None,
                                               "direction": "out", "private": True}]
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
                            "ORDER BY t.created_at", (task_id,)).fetchall()
    for row in children:
        if readable(H.task(conn, row["id"])):
            lines.append({"kind": "parent", "direction": "in", "id": row["id"], "title": row["title"],
                          "status": row["status"]})
    return lines


# ----------------------------------------------------------------------------- writes
OWN_SUBTASK_BLOCKER = (
    "{blocker} is a subtask of {task}, and now blocks it. If the subtask stays open until {task} finishes "
    "(a hold, an acknowledgement, a window), neither can ever move. Say what {task} waits for with a comment or "
    "`waiting` and a reason instead, or finish the subtask once it has answered. If both go quiet, Tico wakes "
    "the task's owner about it, and BotOps sees it as stuck.")


def own_subtask_warning(conn, task_id, other_id, kind):
    """The warning for a block within a task's own tree, or None. A child blocking its parent is allowed:
    a question for a person in a subtask blocks its task until they answer (hubdb._ask_in_subtask). But a
    subtask that waits for its parent to finish is never cleared, so the writer is told."""
    from_id, to_id, kind = normalize(str(task_id or "").strip(), str(other_id or "").strip(), kind)
    if kind != "blocks":
        return None
    if is_descendant(conn, from_id, to_id):
        return "A child task cannot wait for its own parent or ancestor to finish."
    if is_descendant(conn, to_id, from_id):
        return OWN_SUBTASK_BLOCKER.format(blocker=from_id, task=to_id)
    return None


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
    conn.execute("INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,?,?,?)",
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
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (at, here))
    if added and kind == "blocks":
        # A blocker added changes what the task waits on (backend/task_holds.py). One taken off is recomputed by
        # remove_blocker after reconcile; a child moved in or out by set_parent and task_create.
        from . import task_holds
        task_holds.refresh(conn, to_id)


def _mine(conn, actor, row, mover):
    """task_update's gate: a party, a delegate, an ancestor's party, a type's working bot, or a mover."""
    H = _H()
    mover = H.task_mover(conn, actor, row, mover)
    mine = H.task_participant(conn, actor, row) or bool(H._one(
        conn, "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires_at>? LIMIT 1",
        (row["id"], actor, H.now())))
    mine = (mine or H.task_ancestor_party(conn, actor, row) or H.type_bot_works(conn, actor, row)
            or H.task_manager_acts(conn, actor, row))
    if not mine and not mover and actor != H.KEEPER:
        H.refuse(conn, actor, "identity", f"{row['id']} is changed by its parties, their managers, delegates or task movers")


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


# What each stored kind says, read from its `from` end, and from its `to` end. A write reads back in these words.
SAYS = {
    "parent": ("is a subtask of", "is the parent of"),
    "blocks": ("blocks", "waits for"),
    "related": ("is related to", "is related to"),
    "duplicate_of": ("duplicates", "is duplicated by"),
    "follow_up": ("follows up", "is followed up by"),
}


def _name(conn, task_id):
    row = conn.execute("SELECT number,title FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        return task_id[:8]
    title = str(row["title"] or "").strip()
    title = title if len(title) <= 60 else title[:59].rstrip() + "…"
    return (f"#{row['number']} " if row["number"] is not None else f"{task_id[:8]} ") + f'"{title}"'


def readback(conn, task_id, other_id, kind="related", remove=False, changed=True):
    """The relation a write stored, in words from both ends, after normalizing: `blocked_by` and `blocks` asked from
    either side read the same once stored. Dev 237 (Oct 19): a bot sent the reverse `blocks` links while reporting a
    fix; the API stored what it was sent. Each line names the direction so a reversed write is visible in the reply."""
    from_id, to_id, kind = normalize(str(task_id or "").strip(), str(other_id or "").strip(), kind)
    if kind not in SAYS:
        return []
    if kind == "blocks":
        # Lead with the waiting task: "#237 waits for #233", then "#233 blocks #237".
        lines = [f"{_name(conn, to_id)} {SAYS[kind][1]} {_name(conn, from_id)}",
                 f"{_name(conn, from_id)} {SAYS[kind][0]} {_name(conn, to_id)}"]
    elif kind == "related":
        lines = [f"{_name(conn, from_id)} {SAYS[kind][0]} {_name(conn, to_id)}"]
    else:
        lines = [f"{_name(conn, from_id)} {SAYS[kind][0]} {_name(conn, to_id)}",
                 f"{_name(conn, to_id)} {SAYS[kind][1]} {_name(conn, from_id)}"]
    prefix = "Removed: " if remove else "Already: " if not changed else ""
    return [prefix + line for line in lines]


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
    mover = H.task_mover(conn, actor, row, mover)
    if not H.task_participant(conn, actor, row) and not mover:
        _need_mover(conn, actor, "parent")
    old = parent_of(conn, child_id)
    new = str(parent_id or "").strip() or None
    if new == old:
        return False
    if old and not mover:
        old_parent = H.task(conn, old)
        if old_parent and not H.task_participant(conn, actor, old_parent):
            H.refuse(conn, actor, "identity", "Only the parent's owner, requester, their managers or a mover can move a subtask away")
    target = None
    if new:
        target = H._task_parent(conn, actor, child_id, new)
        if H.task_private(conn, target) and private is False:
            H.refuse(conn, actor, "private", "A task under a private parent stays private")
        if not mover and not H.task_participant(conn, actor, target) and not H.task_ancestor_party(conn, actor, target):
            H.refuse(conn, actor, "identity", "Re-parent under a task you own, requested or whose party you manage")
        above = {new, *ancestors(conn, new)}
        if any(above.intersection(blocker_ids(conn, tid)) for tid in (child_id, *descendant_ids(conn, child_id))):
            H.refuse(conn, actor, "kind", "A child task cannot be blocked by its own parent or ancestor")
    at = H.now()
    if old:
        _delete(conn, child_id, old, "parent")
        H._task_event(conn, old, actor, "subtask", child_id, None, note)
    if new:
        _insert(conn, actor, child_id, new, "parent", at)
        H._task_event(conn, new, actor, "subtask", None, child_id, note)
    H._task_event(conn, child_id, actor, "parent_id", old, new, note)
    made_private = bool(target and H.task_private(conn, target) and not H.task_private(conn, row))
    conn.execute("UPDATE tasks SET updated_at=? WHERE id IN (?,?,?)",
                 (at, child_id, old or child_id, new or child_id))
    if made_private:
        H._made_private(conn, actor, child_id)
        for below in H.descendants(conn, child_id):     # and the tasks under it, as making a parent private does
            if not H.task_private(conn, below):
                H._made_private(conn, actor, below["id"])
    H.event(conn, actor, "task.relate", child_id, {"task": new or old, "kind": "parent", "removed": not new})
    if old and row["status"] not in ("done", "closed", "declined"):
        # The old parent may have nothing left open now.
        H._parent_finished(conn, {**row, "status": "closed"}, row["status"], actor, parent=old)
    # What each parent waits on changed: the old one lost a child (a hold cleared, when it was open), the new one
    # gained one (backend/task_holds.py).
    from . import task_holds
    if old:
        live = row["status"] not in ("done", "closed", "declined")
        task_holds.refresh(conn, old, f"{task_holds.title(conn, row)} moved away" if live else None)
    if new:
        task_holds.refresh(conn, new)
    from .roadmap_flow import changed, sync
    changed(conn, child_id)
    if old:
        sync(conn, old)
    return True


def _check_blocker(conn, actor, row, blocker_id):
    H = _H()
    if blocker_id == row["id"]:
        H.refuse(conn, actor, "kind", "a task cannot block itself")
    block = H.task(conn, blocker_id)
    if not block:
        H.refuse(conn, actor, "not-found", f"no task {blocker_id} to block on")
    H._task_private_writer(conn, actor, block)
    if is_descendant(conn, blocker_id, row["id"]):
        H.refuse(conn, actor, "kind", "A child task cannot be blocked by its own parent or ancestor")
    if H.task_private(conn, block) and (not H.task_private(conn, row) or not all(
            H.task_private_readable(conn, party, block) for party in (row["owner"], row["requester"]))
    ) and not H.subtask_flows_up(conn, block, row):
        # Its own subtask for its guest may block it: the guest's answer comes back to it (hubdb.subtask_flows_up).
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
    """The owner says what its own task waits on; anyone else who may change it must be a mover (or the Task Manager,
    whose grant covers relations)."""
    _mine(conn, actor, row, mover)
    H = _H()
    mover = H.task_mover(conn, actor, row, mover)
    if not H.task_represents(conn, actor, row["owner"]) and not mover and not H.task_manager_acts(conn, actor, row):
        _need_mover(conn, actor, "blocked_by")


def requester_hears_block(conn, row, blocker, actor):
    """Whether a task that now waits on `blocker` (a task) wakes its requester: a bot that handed the task to someone
    else, told of a block it may act on (find the person, drop the request), as of a decline. Not when the owner waits
    on itself, nor on work it split off to a bot (a review, a delegated part: its own flow), nor on the requester (that
    is an ask, its own wake), nor when the requester made the change. A person requester sees it in Needs you."""
    H = _H()
    req, owner, waits_on = row.get("requester"), row.get("owner"), (blocker or {}).get("owner")
    split_off = H.is_bot(waits_on) and blocker.get("requester") == owner
    return bool(req and H.is_bot(req) and waits_on and req not in (owner, actor, waits_on)
                and waits_on != owner and not split_off)


def _tell_requester(conn, actor, row, blocker):
    """The requester's wake for a block (requester_hears_block); a Dev ticket's own notice carries it instead."""
    from .ticket_notices import _engineering
    from .check_blocked import kind as check_kind
    H = _H()
    if not blocker or _engineering(conn, row) or not requester_hears_block(conn, row, blocker, actor):
        return
    parent = H.task(conn, parent_of(conn, row["id"]) or "")
    if parent and check_kind(conn, row, parent) and _engineering(conn, parent) and parent["owner"] == row["requester"]:
        return                        # its builder hears once, on the ticket (check_blocked.notify)
    H._wake(conn, row, row["requester"], f"Blocked: {row['title']}: waits on {blocker['title']}",
            {"news": "task_blocked", "blocker": blocker["id"]}, quiet_bots=True)


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
    H.event(conn, actor, "task.relate", task_id, {"task": blocker_id, "kind": "blocked_by",
                                                  **({"own_subtask": True} if is_descendant(conn, task_id, blocker_id) else {})})
    from .auto_tasks import back_off
    back_off(conn, H.task(conn, task_id))
    from .roadmap_flow import sync
    from .ticket_notices import blocked
    sync(conn, task_id)               # a roadmap item now waits on this Dev ticket too
    from .task_blocked import hold
    if H.task(conn, blocker_id)["status"] in H.ACTIVE_STATUSES:
        hold(conn, task_id, "Waiting on a dependency")
    from .check_blocked import changed
    changed(conn, task_id)            # a blocked Review or QA check shows on its ticket (Dev 553)
    blocked(conn, actor, row, blocker_id)
    _tell_requester(conn, actor, H.task(conn, task_id), H.task(conn, blocker_id))
    return True


def remove_blocker(conn, actor, task_id, blocker_id, mover=None, note="", keeper_note=None):
    H = _H()
    H._writer(conn, actor)
    row = _task(conn, actor, task_id)
    if actor != H.KEEPER:
        H._task_private_writer(conn, actor, row)
        _blocker_gate(conn, actor, row, _mover(conn, actor, mover))
    blocker = H.task(conn, blocker_id)
    if not _delete(conn, blocker_id, task_id, "blocks"):
        H.refuse(conn, actor, "not-found", f"{blocker_id} does not block {task_id}")
    _history(conn, actor, blocker_id, task_id, "blocks", False, note if keeper_note is None else keeper_note)
    H.event(conn, actor, "task.unrelate", task_id, {"task": blocker_id, "kind": "blocked_by"})
    from .task_blocked import reconcile
    reconcile(conn, task_id)
    from . import task_holds
    # The blocker taken off is a hold cleared: the last one sends a bot's task back to work (task_holds.refresh).
    task_holds.refresh(conn, task_id, f"{task_holds.title(conn, blocker)} no longer blocks it" if blocker else None)
    from .check_blocked import changed
    changed(conn, task_id)
    from .blocker_asks import unjoined
    unjoined(conn, actor, task_id, blocker_id)
    from .roadmap_flow import sync
    sync(conn, task_id)
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
        conn.execute("INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,?,?,?)",
                     (row["from_task"], row["to_task"], row["kind"], row.get("created_by"), row["created_at"]))
        conn.execute("RELEASE relation_row")
        return True
    except Exception:
        conn.execute("ROLLBACK TO relation_row")
        conn.execute("RELEASE relation_row")
        return False
