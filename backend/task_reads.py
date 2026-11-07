"""Task lists that cost only what changed.

A board re-read every two minutes, on waking and on reconnecting loaded every task it shows to
find that almost none had moved. Two things make a re-read cheap:

- **A cursor and a delta read.** Every task list answer carries `cursor`: the change number
  (backend/events.py) it was read at, and a tag of what the reader may see, as one opaque string.
  `GET /api/v2/tasks?changed_after=<cursor>` with the same filters looks up only the tasks the
  change log names since then (and the tasks that show them: a parent's progress, a relation's
  other end) and answers `tasks` (changed and still matching), `gone` (ids that changed and no
  longer match: deleted, no longer readable by the caller, or out of the filters) and a new
  `cursor`. A cursor older than the log keeps (24 h), ahead of it, malformed, read with different
  access (a grant, a team or a bot's access changed), or naming more changes than a page holds
  (or one change to many tasks, such as a type edit) answers `reset: true`, the events stream's
  word for it, and the client reads in full.
- **ETags.** A full read of tasks or labels is tagged with the newest task change, the reader's
  access, the display names and the query; routines with their rows. A matching `If-None-Match`
  is answered 304 with no body before any task row is loaded.

`gone` never names a task the caller could not have read: the log keeps a task's earlier parties
and privacy on the change that altered them (and on its deletion), and an id is reported only when
the caller could read the task now, or could under one of those earlier states.
"""

import hashlib
import json

from fastapi.responses import Response

from . import events as E

CAP = 500  # more changed tasks than one page: a full read is no dearer


def access(visible_sql):
    """A tag of what the reader may read: `Auth.task_sql` spells it out whole, so the tag moves
    with any grant, team, bot access or assignment that changes it."""
    return hashlib.sha256(visible_sql.encode()).hexdigest()[:12]


def names(c):
    """A tag of the display names an answer is annotated with (backend/names.py)."""
    digest = hashlib.sha256()
    for row in c.execute(
        "SELECT 'human:'||id, name FROM humans UNION ALL SELECT 'bot:'||id, display_name FROM bots ORDER BY 1"
    ):
        digest.update(f"{row[0]}={row[1]}\n".encode())
    return digest.hexdigest()[:12]


def cursor(c, visible_sql):
    return f"{E.latest(c)}.{access(visible_sql)}"


def etag(*parts):
    return 'W/"' + hashlib.sha256(json.dumps(parts, default=str, sort_keys=True).encode()).hexdigest()[:24] + '"'


def fresh(request, tag):
    """Whether the request's If-None-Match already holds `tag`."""
    wanted = request.headers.get("if-none-match")
    if not wanted:
        return False
    bare = tag.removeprefix("W/")
    return any(item.strip() == "*" or item.strip().removeprefix("W/") == bare for item in wanted.split(","))


def not_modified(tag):
    return Response(status_code=304, headers={"ETag": tag})


def changed(c, after, visible_sql):
    """({task id: [earlier readers as JSON]}, new cursor) for the tasks changed since `after`, or
    None when the client must read in full."""
    seq, _, tag = str(after).partition(".")
    if not seq.isdigit() or tag != access(visible_sql):
        return None
    seq, newest, first = int(seq), E.latest(c), E.oldest(c)
    floor = (first - 1) if first is not None else newest
    if seq > newest or seq < floor:
        return None
    was = {}
    for subject, ref, kind, at in c.execute(
        "SELECT subject_id, ref, kind, created_at FROM changes WHERE topic='tasks' AND seq>? ORDER BY seq", (seq,)
    ):
        if kind == "bulk":  # one change to many tasks (events.FAN_CAP): read in full
            return None
        if subject:
            was.setdefault(subject, [])
            if ref:
                was[subject].append((ref, at))  # the snapshot and when it was replaced: its pipeline is then
    if len(was) > CAP:
        return None
    return was, f"{newest}.{tag}"


def around(c, ids):
    """`ids` and the tasks that show them: every ancestor (progress, children summary) and each
    relation's other end (its title and status)."""
    if not ids:
        return []
    found = c.execute(
        "WITH RECURSIVE changed(id) AS (SELECT value FROM json_each(?)), "
        "up(id) AS (SELECT to_task FROM task_relations WHERE kind='parent' AND from_task IN (SELECT id FROM changed) "
        "UNION SELECT r.to_task FROM task_relations r JOIN up ON r.from_task=up.id WHERE r.kind='parent') "
        "SELECT id FROM up UNION SELECT to_task FROM task_relations WHERE from_task IN (SELECT id FROM changed) "
        "UNION SELECT from_task FROM task_relations WHERE to_task IN (SELECT id FROM changed)",
        (json.dumps(list(ids)),),
    )
    return list(dict.fromkeys([*ids, *(row[0] for row in found)]))


def gone(c, visible_sql, was, matched):
    """The changed ids not in `matched` that the caller could have read: now (it left the filters)
    or under an earlier owner, requester or privacy the log kept."""
    out = []
    missing = [tid for tid in was if tid not in matched]
    if not missing:
        return out
    readable = {
        row[0]
        for row in c.execute(
            f"SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?)) AND ({visible_sql})",
            (json.dumps(missing),),
        )
    }
    for tid in missing:
        if tid in readable or any(_could_read(c, visible_sql, tid, ref, at) for ref, at in was[tid]):
            out.append(tid)
    return out


def _pipeline_of(c, tid):
    """(known, type_id): the pipeline a ticket the change log names was on, from the ticket itself or, once it is deleted,
    from the row its trash kept. When neither says, the pipeline is unknown: with no members-only pipeline in the company
    no pipeline rule can apply, so the ticket counts as on none (`known` True). Otherwise `known` is False and the caller
    fails closed."""
    row = c.execute("SELECT type_id FROM tasks WHERE id=?", (tid,)).fetchone()
    if row is not None:
        return True, row[0]
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_trash'").fetchone() is not None:
        found = c.execute(
            "SELECT json_type(rows,'$.rows.tasks[0].type_id'), json_extract(rows,'$.rows.tasks[0].type_id') "
            "FROM task_trash WHERE task_id=?",
            (tid,),
        ).fetchone()
        if found is not None and found[0] is not None:
            return True, found[1]
    # A purged or unreadable trash row keeps no snapshot.
    private = c.execute("SELECT 1 FROM task_types WHERE pipeline_visibility='members' LIMIT 1").fetchone()
    return private is None, None


def _type_at(c, tid, at):
    """(known, type_id): the pipeline a ticket was on just before `at`, from its type history (the type events name each
    change of pipeline, and creation's old is None). Without that history the pipeline is unknown, so the caller fails
    closed once a members-only pipeline exists."""
    # The change log stamps milliseconds, truncated, and the history event that made this change lands within a
    # millisecond or two of that stamp: a type event that near is the change's own, so the pipeline before it is
    # the one this snapshot names.
    before = c.execute(
        "SELECT new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='type' "
        "AND julianday(created_at)<julianday(?)-0.002/86400.0 ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (tid, at),
    ).fetchone()
    if before is not None:
        return True, before[0]
    first = c.execute(
        "SELECT old FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='type' "
        "ORDER BY created_at, rowid LIMIT 1",
        (tid,),
    ).fetchone()
    if first is not None and first[0] is not None:
        return True, first[0]
    private = c.execute("SELECT 1 FROM task_types WHERE pipeline_visibility='members' LIMIT 1").fetchone()
    return private is None, None


def _could_read(c, visible_sql, tid, ref, at=None):
    """Whether the snapshot `ref` (owner, requester, privacy, readers) let its reader open the ticket. `at` is when that
    snapshot was replaced: the pipeline then is the one the ticket was on, not the one it is on now."""
    try:
        # (owner, requester, private, readers): an older change has three parts, or a one-person `guest` fourth.
        owner, requester, private, readers = (json.loads(ref) + [None])[:4]
    except (TypeError, ValueError):
        return False
    if isinstance(readers, list):
        readers = json.dumps(readers)
    elif isinstance(readers, str) and readers and not readers.startswith("["):
        readers = json.dumps([readers])
    elif not isinstance(readers, str):
        readers = None
    known, type_id = _type_at(c, tid, at) if at is not None else _pipeline_of(c, tid)
    if not known:  # the pipeline rule (pipeline_rights.list_sql) cannot be applied: the ticket is not read
        return False
    return (
        c.execute(
            f"SELECT 1 FROM (SELECT ? AS id, ? AS owner_id, ? AS requester_id, ? AS private, ? AS readers, "
            f"? AS type_id) tasks WHERE {visible_sql}",
            (tid, owner, requester, private, readers, type_id),
        ).fetchone()
        is not None
    )
