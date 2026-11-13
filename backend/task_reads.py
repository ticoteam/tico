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
  (or one change to many tasks) answers `reset: true`, the events stream's
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
SNOOZE_LINE_BYTES = 200  # a brief row's snooze line, in UTF-8 bytes: the task's own read keeps the whole why


def clip_line(text, limit=SNOOZE_LINE_BYTES):
    """`text` cut to at most `limit` UTF-8 bytes on a character boundary, ending in "…" when it was cut."""
    raw = text.encode()
    if len(raw) <= limit:
        return text
    return raw[: limit - len("…".encode())].decode(errors="ignore").rstrip() + "…"


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


def _could_read(c, visible_sql, tid, ref):
    """Whether the snapshot `ref` (owner, requester, privacy, readers, pipeline: events.py `_WAS`) let its reader open the
    task."""
    try:
        parts = json.loads(ref)
    except (TypeError, ValueError):
        return False
    if not isinstance(parts, list):
        return False
    owner, requester, private, readers, pipeline_id = (parts + [None] * 5)[:5]
    if isinstance(readers, list):
        readers = json.dumps(readers)
    elif isinstance(readers, str) and readers and not readers.startswith("["):
        readers = json.dumps([readers])
    elif not isinstance(readers, str):
        readers = None
    return (
        c.execute(
            f"SELECT 1 FROM (SELECT ? AS id, ? AS owner_id, ? AS requester_id, ? AS private, ? AS readers, "
            f"? AS pipeline_id) tasks WHERE {visible_sql}",
            (tid, owner, requester, private, readers, pipeline_id),
        ).fetchone()
        is not None
    )


# ----------------------------------------------------------------------------- the engine's part of a list row
def brief(c, rows, visible_sql="1"):
    """The engine's fields of a list row (docs/dev/pipelines-v3-cutover.md, "The task as JSON", Brief), laid over each
    row in place: `turn` (the headline wait; `running` when a bot's turn is a run going now), `late`, `snoozed`,
    `blocked_by` and `blocks` (the open tasks it depends on and those it blocks, `_dependencies`; only where there are any),
    `pushed_past` (it entered its stage pushed past a requirement), `stuck` (only when the ladder brought it to a
    person, stuck or late with them: its Needs you card is the stuck card, task #392), `stage_since` (when it
    entered its stage: its open visit, else when it was filed; a board card's age) and `on_person` (it waits only on a
    person's OK or answer, so it holds no slot where its stage has a limit). One `H.task_waits` for every row, one
    read of the open visits and one of the open snoozes, never a read per row. A sub-task the reader may not open is
    named by neither its title nor its id in a turn's line, as in the task's own read (waiting_on.unnamed). A snoozed
    turn's line is the snooze's why, which may be any length: a row clips it to SNOOZE_LINE_BYTES, and the task's own read
    keeps it whole."""
    from . import waiting_on as W
    from .store import H

    ids = [row["id"] for row in rows]
    if not ids:
        return rows
    waits = H.task_waits(c, ids)
    marks = json.dumps(ids)
    late, pushed, entered, stuck = {}, set(), {}, set()
    for r in c.execute(
        "SELECT task_id, late_told_at, late_person_at, entered_how, entered_at, level FROM visits "
        "WHERE left_at IS NULL "
        "AND task_id IN (SELECT value FROM json_each(?))",
        (marks,),
    ):
        entered[r[0]] = r[4]
        if r[5] == "person":
            stuck.add(r[0])
        if r[1]:
            late[r[0]] = {"since": r[1], "level": "person" if r[2] else "told"}
        if r[3] == "pushed_past":
            pushed.add(r[0])
    snoozed = {
        r[0]
        for r in c.execute(
            "SELECT task_id FROM snoozes WHERE ended_at IS NULL AND task_id IN (SELECT value FROM json_each(?))",
            (marks,),
        )
    }
    blocked_by, blocks = _dependencies(c, ids, visible_sql)
    heads = {tid: found[0] if found else None for tid, found in waits.items()}
    named = sorted({head.about for head in heads.values() if head and head.about})
    shown = (
        {
            r[0]
            for r in c.execute(
                f"SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?)) AND ({visible_sql})",
                (json.dumps(named),),
            )
        }
        if named
        else set()
    )
    turns = {tid: W.view(head, shown.__contains__) if head else None for tid, head in heads.items()}
    # Waits only on a person's OK or answer: it holds no slot in a stage with a limit (waiting_on.on_person_many).
    person = W.on_person_many(c, waits)
    running = _running(c, {tid: t["who"] for tid, t in turns.items() if t and t["state"] == "bot" and t["who"]})
    for row in rows:
        turn = turns.get(row["id"])
        if turn:
            turn["running"] = row["id"] in running
            if turn["state"] == W.SNOOZED:
                turn["line"] = clip_line(turn["line"])
        row["turn"] = turn
        row["late"] = late.get(row["id"])
        if row["id"] in stuck:
            row["stuck"] = True  # only when true: every list row carries the brief, under its payload budget
        row["snoozed"] = row["id"] in snoozed
        # Only on a row with a dependency: 500 rows of empty lists would cost the list's byte budget.
        if blocked_by.get(row["id"]):
            row["blocked_by"] = blocked_by[row["id"]]
        if blocks.get(row["id"]):
            row["blocks"] = blocks[row["id"]]
        row["pushed_past"] = row["id"] in pushed
        row["stage_since"] = entered.get(row["id"]) or row.get("created_at")
        row["on_person"] = row["id"] in person
    return rows


def _dependencies(c, ids, visible_sql):
    """({task id: [{id, number}]} of the open tasks each one depends on, {task id: [{id, number}]} of the open tasks
    each one blocks), for a board card's stop mark and "#304", and "Blocks #307" (backend/task_depends.py). Two reads
    for every row. A task it depends on that the reader may not open is there with neither id nor number, so the card
    still shows it is blocked; a task it blocks that the reader may not open is left out."""
    from . import task_depends as D

    on = {tid: [other for other, _since in found] for tid, found in D.blockers_of(c, ids).items()}
    of = D.blocking(c, ids)
    others = sorted({other for found in on.values() for other in found} | {d for f in of.values() for d, _n, _s in f})
    if not others:
        return {}, {}
    seen = {
        r[0]: r[1]
        for r in c.execute(
            f"SELECT id, number FROM tasks WHERE id IN (SELECT value FROM json_each(?)) AND ({visible_sql})",
            (json.dumps(others),),
        )
    }
    blocked_by = {
        tid: [{"id": o, "number": seen[o]} if o in seen else {"id": None, "number": None} for o in found]
        for tid, found in on.items()
        if found
    }
    blocks = {tid: [{"id": d, "number": n} for d, n, _s in found if d in seen] for tid, found in of.items() if found}
    return blocked_by, blocks


def _running(c, bots):
    """The tasks, of {task id: bot actor}, whose bot has a run on them now: its attempt is leased or running and its
    computer still renews the lease (task_wakes.live_run, for many tasks in one read). A bot's turn that is not running
    is waiting to try again."""
    from .store import H

    if not bots:
        return set()
    pairs = json.dumps([[tid, H.actor_id(bot)] for tid, bot in bots.items()])
    return {
        r[0]
        for r in c.execute(
            "SELECT DISTINCT json_extract(p.value, '$[0]') FROM json_each(?) p "
            "JOIN tasks t ON t.id = json_extract(p.value, '$[0]') "
            "JOIN jobs j ON j.bot_id = json_extract(p.value, '$[1]') "
            "JOIN attempts a ON a.id = j.attempt_id AND a.state IN ('leased', 'running') AND a.lease_until > ? "
            "JOIN messages m ON m.id = j.message_id "
            "AND (m.conversation_id = t.conversation_id OR substr(m.wake_key, 1, length(t.id) + 1) = t.id || ':')",
            (pairs, H.now()),
        )
    }


def comment_rights(c, auth, who, task_id):
    """Whether the caller may write on the task: every comment and file goes through here. Returns the task's row."""
    from . import lifecycle
    from .store import H, Problem

    row = auth.task(c, who, task_id)
    lifecycle.refuse_send(c, who, None, what="task comments")
    if who.role == "bot" and not H.task_participant(c, who.actor, row):
        delegated = c.execute(
            "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires_at>?",
            (task_id, who.actor, H.now()),
        ).fetchone()
        if not delegated and not H.task_ancestor_party(c, who.actor, row) and not H.task_can_work(c, who.actor, row):
            raise Problem(
                "forbidden",
                "Only the task's parties, their managers, delegates, the parties of a task above it and its "
                "pipeline's manager may comment",
                403,
            )
    origin = None if H.task_private(c, row) else H.task_origin(c, row)
    others = [
        a for a in dict.fromkeys([row["owner_id"], row["requester_id"], origin]) if a and a not in (who.actor, H.KEEPER)
    ]
    target = next((a for a in others if H.is_bot(a)), None)
    if target and not H.task_represents(c, who.actor, row["owner_id"]):
        auth.require_write(c, who, H.actor_id(target))
    if target:
        auth.require_bot_contact(c, who, target, task_id=task_id, kind="comment")
    return row


COVERS_SQL = (
    "WITH visuals AS ("
    "SELECT f.task_id,f.id,v.version,v.mime,v.created_at,v.rowid AS position,"
    "coalesce(v.width,m.width) AS width,coalesce(v.height,m.height) AS height,"
    "coalesce(v.thumb_blob_id,m.thumb_blob_id) AS thumb_blob_id,"
    "coalesce(v.poster_blob_id,m.poster_blob_id) AS poster_blob_id "
    "FROM bot_files f JOIN bot_file_versions v ON v.file_id=f.id "
    "LEFT JOIN blob_media m ON m.blob_id=v.blob_id "
    "WHERE f.task_id IN ({marks}) AND f.archived=0 AND f.locator='tico_blob' "
    "UNION ALL SELECT a.task_id,b.id,1,b.content_type,b.created_at,b.rowid,"
    "m.width,m.height,m.thumb_blob_id,m.poster_blob_id "
    "FROM task_assets a JOIN blobs b ON b.id=a.blob_id LEFT JOIN blob_media m ON m.blob_id=b.id "
    "WHERE a.task_id IN ({marks}) AND NOT EXISTS "
    "(SELECT 1 FROM bot_file_versions v WHERE v.blob_id=b.id)), "
    "ranked AS (SELECT *,ROW_NUMBER() OVER (PARTITION BY task_id "
    "ORDER BY created_at DESC,version DESC,position DESC) AS rank FROM visuals "
    "WHERE mime LIKE 'image/%' OR (mime LIKE 'video/%' AND poster_blob_id IS NOT NULL)) "
    "SELECT * FROM ranked WHERE rank=1"
)


def covers(c, task_ids):
    """The newest picture of each task, one query for a page of tasks."""
    if not task_ids:
        return {}
    marks = ",".join("?" * len(task_ids))
    out = {}
    for row in c.execute(COVERS_SQL.format(marks=marks), (*task_ids, *task_ids)):
        suffix = "/poster" if row["mime"].startswith("video/") else "/thumb" if row["thumb_blob_id"] else ""
        out[row["task_id"]] = {
            "url": f"/api/v2/files/{row['id']}{suffix}?v={row['version']}",
            "width": row["width"],
            "height": row["height"],
        }
    return out
