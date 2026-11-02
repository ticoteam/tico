"""Who is on a task, by role.

Besides its owner, any number of people or bots can be on a task, each under a role: a row of
`task_roles` (`task_id`, `role`, `actor`). A role is a short name the team chooses (`developer`,
`reviewer`, `qa`, `designer`); Tico keeps no list of them and gives them no meaning or rights. A
person may hold several roles on one task, and the owner may hold any of them too. What a role
means downstream (which column waits on whom, a dependency monitor) is the client's to decide.

`set_roles` is the only writer (task update and create, the task page, `hub task update --role`,
the MCP tools). Whoever may change the task's other fields may change who is on it.

This module imports nothing from hubdb at load, so hubdb can import it; the writer reaches hubdb
through `_H()`.
"""
import re

ROLE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")   # a role name: a short lower-case slug

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_roles(
  task_id TEXT NOT NULL REFERENCES tasks(id), role TEXT NOT NULL,
  actor TEXT NOT NULL, added_by TEXT, created_at TEXT NOT NULL,
  PRIMARY KEY(task_id, role, actor));
CREATE INDEX IF NOT EXISTS task_roles_actor ON task_roles(actor, role);
"""


def _H():
    from . import hubdb
    return hubdb


def role_name(value):
    """A role as stored, or None when the value is not a role name."""
    name = str(value or "").strip().lower()
    return name if ROLE.match(name) else None


def migrate(conn):
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


# ----------------------------------------------------------------------------- reads
def roles_of(conn, task_id):
    """{role: [actor, ...]} for one task, each role's people in the order they were added."""
    return grouped(conn, [task_id])[task_id]


def grouped(conn, task_ids):
    """{task id: {role: [actors]}} for each of `task_ids`, in a few queries."""
    out = {tid: {} for tid in task_ids}
    ids = list(task_ids)
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        marks = ",".join("?" * len(part))
        for row in conn.execute(f"SELECT task_id, role, actor FROM task_roles WHERE task_id IN ({marks}) "
                                "ORDER BY created_at, rowid", part):
            out[row[0]].setdefault(row[1], []).append(row[2])
    return out


def member_sql(role=None):
    """A WHERE fragment over `tasks`: the actor bound to its `?` is on the task (in `role`, when given)."""
    return ("EXISTS (SELECT 1 FROM task_roles r WHERE r.task_id=tasks.id AND r.actor=?"
            + (" AND r.role=?" if role else "") + ")")


# ----------------------------------------------------------------------------- writes
def clean(value):
    """`{role: [actors]}` from what a client sent: each role a short name, each a list of ids."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("roles is an object: {role: [people]}, such as {reviewer: [\"dana\"], qa: []}")
    out = {}
    for raw, actors in value.items():
        role = role_name(raw)
        if not role:
            raise ValueError(f"a role is a short name in lower-case letters, digits, - or _, not {raw!r}")
        if isinstance(actors, str):
            actors = actors.split(",")
        if not isinstance(actors, (list, tuple, type(None))):
            raise ValueError(f"{role} is a list of people")
        out[role] = [str(a).strip() for a in actors or [] if str(a or "").strip()]
    return out


def set_roles(conn, actor, task_id, roles, mover=None, note="", checked=False):
    """Replace the people in each role named in `roles` ({role: [actors]}); a role not named is
    left as it is, and [] clears one. Returns True when anything changed. `checked`: the caller
    (task update) has already said this actor may change the task. A refusal on a private task
    stays content-free (hubdb.private_task_write)."""
    return _H().private_task_write(_set_roles)(conn, actor, task_id, roles, mover, note, checked)


def _set_roles(conn, actor, task_id, roles, mover, note, checked):
    H = _H()
    from . import task_relations as TR
    wanted = clean(roles)
    if not wanted:
        return False
    row = H.task(conn, task_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no task {task_id}")
    if not checked:
        H._writer(conn, actor)
        H._task_private_writer(conn, actor, row)
        TR._mine(conn, actor, row, TR._mover(conn, actor, mover))
    have = roles_of(conn, task_id)
    changed, at = False, H.now()
    for role, people in wanted.items():
        resolved = []
        for person in people:
            who = H.resolve_actor(conn, person)
            if not who:
                H.refuse(conn, actor, "reach", f"{person} is not a bot or a person on the roster")
            if H.task_private(conn, row) and not H.task_private_readable(conn, who, row):
                H.refuse(conn, actor, "private", f"{H.actor_id(who)} cannot read this private task")
            if who not in resolved:
                resolved.append(who)
        before = have.get(role, [])
        if resolved == before:
            continue
        conn.execute("DELETE FROM task_roles WHERE task_id=? AND role=?", (task_id, role))
        for who in resolved:
            conn.execute("INSERT INTO task_roles(task_id,role,actor,added_by,created_at) VALUES(?,?,?,?,?)",
                         (task_id, role, who, actor, at))
        H._task_event(conn, task_id, actor, "role:" + role, ", ".join(before) or None, ", ".join(resolved) or None, note or "")
        H.event(conn, actor, "task.roles", task_id, {"role": role, "actors": resolved})
        changed = True
    if changed:
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (at, task_id))
    return changed
