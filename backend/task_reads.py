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
    """({task id: [the snapshot the reader last had]}, new cursor) for the tasks changed since `after`, or None when the
    client must read in full. The snapshot is the task's first one after `after` (events.py `_WAS`): the state before the
    first change to its access, pipeline included. Later snapshots are states no reader saw, since one write may move a
    ticket twice."""
    seq, _, tag = str(after).partition(".")
    if not seq.isdigit() or tag != access(visible_sql):
        return None
    seq, newest, first = int(seq), E.latest(c), E.oldest(c)
    floor = (first - 1) if first is not None else newest
    if seq > newest or seq < floor:
        return None
    was = {}
    for subject, ref, kind in c.execute(
        "SELECT subject_id, ref, kind FROM changes WHERE topic='tasks' AND seq>? ORDER BY seq", (seq,)
    ):
        if kind == "bulk":  # one change to many tasks (events.FAN_CAP): read in full
            return None
        if subject:
            was.setdefault(subject, [])
            if ref and not was[subject]:
                was[subject].append(ref)
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
    or under the snapshot the log kept for it (owner, requester, privacy, readers and pipeline)."""
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
        if tid in readable or any(_could_read(c, visible_sql, tid, ref) for ref in was[tid]):
            out.append(tid)
    return out


_SUMMARY_LABELS = {"closed": "Closed with it", "open": "Still open", "kept": "Still closed from when it closed"}


def children_text(c, visible_sql, history):
    """The task's history with each subtask summary's text written for this reader. A summary keeps the ids of its
    subtasks, not their titles (hubdb._finish_children): a title shows only where the reader may open that subtask
    (`visible_sql`, the list rule), and "a subtask" otherwise, a deleted one too. A summary written before ids were kept
    keeps only its label, since its text names subtasks the reader may not see."""
    summaries = []
    for event in history:
        if event.get("field", event.get("kind")) != "children":  # app rows name it field, history rows kind
            continue
        try:
            data = json.loads(event.get("data") or "{}")
        except (TypeError, ValueError):
            data = {}
        key = next(
            (k for k in ("closed", "open", "kept") if isinstance(data, dict) and isinstance(data.get(k), list)), None
        )
        if key is None:
            event["new"] = str(event.get("new") or "").split(":", 1)[0]
            continue
        summaries.append((event, _SUMMARY_LABELS[key], [str(tid) for tid in data[key]]))
    wanted = sorted({tid for _, _, ids in summaries for tid in ids})
    if not wanted:
        return history
    shown = {
        row[0]: row[1]
        for row in c.execute(
            f"SELECT id, title FROM tasks WHERE id IN (SELECT value FROM json_each(?)) AND ({visible_sql})",
            (json.dumps(wanted),),
        )
    }
    for event, label, ids in summaries:
        event["new"] = label + ": " + "; ".join(shown.get(tid, "a subtask") for tid in ids)
    return history


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


def _could_read(c, visible_sql, tid, ref):
    """Whether the snapshot `ref` (owner, requester, privacy, readers, pipeline: events.py `_WAS`) let its reader open the
    ticket. A snapshot from before the pipeline was kept names it; an older one does not, and then the ticket's pipeline
    now (or, deleted, its trash's) stands for it."""
    try:
        parts = json.loads(ref)
    except (TypeError, ValueError):
        return False
    if not isinstance(parts, list):
        return False
    owner, requester, private, readers = (parts + [None] * 4)[:4]
    if isinstance(readers, list):
        readers = json.dumps(readers)
    elif isinstance(readers, str) and readers and not readers.startswith("["):
        readers = json.dumps([readers])
    elif not isinstance(readers, str):
        readers = None
    if len(parts) >= 5:
        known, type_id = True, parts[4]  # the pipeline it was on, NULL for an untyped ticket
    else:
        known, type_id = _pipeline_of(c, tid)
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
