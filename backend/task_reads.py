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
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import orjson
from fastapi.responses import Response

from . import events as E

if TYPE_CHECKING:
    import sqlite3

    from .waiting_on import Wait

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
    """A weak validator of these parts: a hash of their JSON with sorted keys, encoded by orjson (a page of 500 tasks
    is a few ms, not the 50 the json module takes), or by json for a value orjson refuses (an int past 64 bits)."""
    try:
        text = orjson.dumps(parts, default=str, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS)
    except TypeError:
        text = json.dumps(parts, default=str, sort_keys=True).encode()
    return 'W/"' + hashlib.sha256(text).hexdigest()[:24] + '"'


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


def help_tasks(c, ids):
    """The page's tasks filed by the HQ watcher, including earlier tasks for a ticket."""
    if not ids:
        return set()
    return {
        r[0]
        for r in c.execute(
            "SELECT task_id FROM hq_help_tasks WHERE task_id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(ids)),),
        )
    }


def visible_entities(c, auth, who, ids):
    """The linked Directory records every effective reader may see, checked together."""
    if not ids:
        return set()
    from .entities import store as ES
    from .threads import entity_readers

    viewer = auth.run_viewer(c, who)
    readers = entity_readers(c, viewer)
    if not readers or viewer.role == "none":
        return set()
    return set.intersection(*(ES.visible_ids(c, actor, ids) for actor in readers))


def hidden_help_links(c, auth, who, linked):
    """The linked Help tasks whose Directory record the reader cannot see."""
    help_ids = help_tasks(c, linked.keys())
    visible = visible_entities(c, auth, who, {linked[task_id] for task_id in help_ids})
    return {task_id for task_id in help_ids if linked[task_id] not in visible}


def readable_history(c, auth, who, history, visible_sql):
    """A task's history rows (H.task_history) as `who` may read them, as the task's own read gives them: rows whose
    content they may not read go, and so does a row naming a task they may not open; a subtask summary names only
    subtasks they may open; and a bound OK keeps no words for anyone but its parties. `visible_sql` is their task rule
    (Auth.task_sql). The caller still runs files.readable_page_refs on what it shows: an ask's words come from the
    pages it showed."""
    from . import task_asks
    from . import task_privacy as privacy

    history = privacy.content_readable_rows(c, privacy.actor(who), history)
    linked = [e for e in history if e.get("field") == "entity_id"]
    help_ids = help_tasks(c, {e["task_id"] for e in linked})
    entity_changes = {e[k] for e in linked if e["task_id"] in help_ids for k in ("old", "new") if e.get(k)}
    visible = visible_entities(c, auth, who, entity_changes)
    history = [
        e
        for e in history
        if e.get("field") != "entity_id"
        or e["task_id"] not in help_ids
        or all(not e.get(k) or e[k] in visible for k in ("old", "new"))
    ]
    named = sorted({str(e[k]) for e in history for k in ("old", "new") if e.get(k)})
    if named:
        # The named tasks that exist and that the reader may not read, in one statement.
        hidden = {
            r[0]
            for r in c.execute(
                "SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?)) AND NOT coalesce(("
                + visible_sql
                + "),0)",
                (json.dumps(named),),
            )
        }
        history = [e for e in history if not ({str(e.get(k)) for k in ("old", "new")} & hidden)]
    history = children_text(c, visible_sql, history)
    principals = privacy.actor(who)  # a run of the shared Assistant reads as its run (hubdb.read_as)
    return task_asks.private_history(c, principals[0] if isinstance(principals, tuple) else principals, history)


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
def came_from(c: "sqlite3.Connection", ids: list[str]) -> dict[str, dict[str, str]]:
    """Each side-stage task's latest main work visit in its current pipeline, if it has one."""
    return {
        task: {"id": stage, "name": name}
        for task, stage, name in c.execute(
            "SELECT t.id, prior.id, prior.name FROM json_each(?) ids "
            "JOIN tasks t ON t.id=ids.value JOIN stages here ON here.id=t.stage_id AND here.side=1 "
            "JOIN visits previous ON previous.rowid=("
            "SELECT v.rowid FROM visits v JOIN stages s ON s.id=v.stage_id "
            "WHERE v.task_id=t.id AND s.pipeline_id=t.pipeline_id AND s.kind='work' AND s.side=0 "
            "ORDER BY v.entered_at DESC, v.rowid DESC LIMIT 1) "
            "JOIN stages prior ON prior.id=previous.stage_id",
            (json.dumps(ids),),
        )
    }


def brief(c, rows, visible_sql="1", viewer=None):
    """The engine's fields of a list row (docs/dev/pipelines-v3-cutover.md, "The task as JSON", Brief), laid over each
    row in place: `turn` (the headline wait; `running` when a bot's turn is a run going now), `status_line`
    (`status_line`), `late`, `snoozed`, `blocked_by` and
    `blocks` (the open tasks it depends on and those it blocks, `_dependencies`; only where there are any),
    `pushed_past` (it entered its stage pushed past a requirement), `stuck` (only when the ladder brought it to a
    person, stuck or late with them: its Needs you card is the stuck card, task #392), `stage_since` (when it
    entered its stage: its open visit, else when it was filed; a board card's age), `visit_id` (its open visit, only when
    its turn is an ask: Needs you reads that one ask by id) and `on_person` (it waits only on a
    person's OK or answer, so it holds no slot where its stage has a limit). One `H.task_waits` for every row, one
    read of the open visits and one of the open snoozes, never a read per row. A sub-task the reader may not open is
    named by neither its title nor its id in a turn's line, as in the task's own read (waiting_on.unnamed). A snoozed
    turn's line is the snooze's why, which may be any length: a row clips it to SNOOZE_LINE_BYTES, and the task's own read
    keeps it whole."""
    from datetime import datetime, timezone

    from . import cloud_machines, late_rule
    from . import waiting_on as W
    from .store import H

    ids = [row["id"] for row in rows]
    if not ids:
        return rows
    origins = came_from(c, ids)
    waits = H.task_waits(c, ids)
    marks = json.dumps(ids)
    marked, pushed, entered, stuck, visits = {}, set(), {}, set(), {}
    for r in c.execute(
        "SELECT v.task_id, v.late_told_at, v.late_person_at, v.entered_how, v.entered_at, v.level, s.expected_minutes, v.id "
        "FROM visits v LEFT JOIN stages s ON s.id=v.stage_id "
        "WHERE v.left_at IS NULL "
        "AND v.task_id IN (SELECT value FROM json_each(?))",
        (marks,),
    ):
        entered[r[0]], visits[r[0]] = r[4], r[7]
        if r[5] == "person":
            stuck.add(r[0])
        if r[1]:
            # Late is read against the owner who holds the task now (late_rule), not the owner the mark was written for.
            marked[r[0]] = ({"late_told_at": r[1], "late_person_at": r[2], "entered_at": r[4], "id": r[7]}, r[6])
        if r[3] == "pushed_past":
            pushed.add(r[0])
    now = datetime.now(timezone.utc)
    # A person who answered after the mark gave the bot its whole time again (never_stuck.clock_from): one read for all.
    answered = late_rule.last_answers(
        c, [(str(v["id"]), str(tid), str(v["entered_at"])) for tid, (v, _m) in marked.items()]
    )
    snoozed = {
        r[0]
        for r in c.execute(
            "SELECT task_id FROM snoozes WHERE ended_at IS NULL AND task_id IN (SELECT value FROM json_each(?))",
            (marks,),
        )
    }
    blocked_by, blocks = _dependencies(c, ids, visible_sql)
    # A page several people approve is each approver's turn: `viewer` sees it as theirs (waiting_on.head_for).
    heads = {tid: W.head_for(found, viewer) for tid, found in waits.items()}
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
    running = live_runs(c, {tid: t["who"] for tid, t in turns.items() if t and t["state"] == "bot" and t["who"]})
    seen: dict[str, str | None] = {}
    turn_bots = {str(H.actor_id(h.who)) for h in heads.values() if h and h.state == W.BOT and h.who}
    machines = cloud_machines.states(c, turn_bots) if turn_bots else {}
    from . import paid_renders

    cards = paid_renders.cards(c)  # paid shots to reconcile: a card's own line, apart from its turn and status
    for row in rows:
        reconcile = W.reconcile_of(c, row["id"], cards)
        if reconcile:
            row["reconcile"] = reconcile
        turn = turns.get(row["id"])
        head = heads.get(row["id"])
        line = status_line(
            row.get("status"),
            head,
            running.get(row["id"]),
            machine(c, head, seen, machines),
            stopped=row["id"] in stuck,
        )
        row["status_line"] = line
        paused_turn(c, turn, line)
        if turn and turn["state"] == W.BOT:
            turn["running"] = row["id"] in running  # only on an agent's turn, the one it speaks of
        if turn and turn["state"] == W.SNOOZED:
            turn["line"] = clip_line(turn["line"])
        row["turn"] = turn
        if turn and turn["ask"] and visits.get(row["id"]):
            row["visit_id"] = visits[row["id"]]  # only on an ask: with it the ask is read by id (waiting_on.ask_id)
        mark = marked.get(row["id"])
        row["late"] = (
            late_rule.late_of(
                row,
                mark[0],
                mark[1],
                now,
                persons_turn=W.persons_turn(waits.get(row["id"]) or []),
                answered_at=answered.get(str(mark[0]["id"])),
            )
            if mark
            else None
        )
        if row["id"] in stuck:
            row["stuck"] = True  # only when true: every list row carries the brief, under its payload budget
        # Blocked wins over a snooze: no list says Snoozed for a blocked task (hubdb.tasks' snoozed filter agrees).
        row["snoozed"] = row["id"] in snoozed and not blocked_by.get(row["id"])
        # Only on a row with a dependency: 500 rows of empty lists would cost the list's byte budget.
        if blocked_by.get(row["id"]):
            row["blocked_by"] = blocked_by[row["id"]]
        if blocks.get(row["id"]):
            row["blocks"] = blocks[row["id"]]
        row["pushed_past"] = row["id"] in pushed
        row["stage_since"] = entered.get(row["id"]) or row.get("created_at")
        row["came_from"] = origins.get(row["id"])
        row["on_person"] = row["id"] in person
    return rows


# How much of a blocker's title a list row carries: the row shows a few words of it, and its tip the task's whole line.
DEPENDENCY_TITLE = 80


def _dependencies(c, ids, visible_sql):
    """({task id: [{id, number, label, title}]} of the open tasks each one depends on, {task id: [{id, number, label}]} of
    the open tasks each one blocks), for a board card's stop mark and "E304" (its label; its title, short, when it has no
    number), and "Blocks #307" (backend/task_depends.py). Two reads for every row. A task it depends on that the reader
    may not open is there with no id, number, label or title, so the card still shows it is blocked; a task it blocks
    that the reader may not open is left out."""
    from . import task_depends as D

    on = {tid: [other for other, _since in found] for tid, found in D.blockers_of(c, ids).items()}
    of = D.blocking(c, ids)
    others = sorted({other for found in on.values() for other in found} | {d for f in of.values() for d, _n, _s in f})
    if not others:
        return {}, {}
    # The letters are the task's own (`tasks.prefix`); one made before they were kept reads its pipeline's.
    seen = {
        r[0]: r[1:]
        for r in c.execute(
            f"SELECT id, number, COALESCE(prefix, (SELECT p.prefix FROM pipelines p WHERE p.id = tasks.pipeline_id)), "
            f"substr(title, 1, {DEPENDENCY_TITLE}) FROM tasks "
            f"WHERE id IN (SELECT value FROM json_each(?)) AND ({visible_sql})",
            (json.dumps(others),),
        )
    }

    def named(o):
        number, prefix, title = seen[o]
        label = f"{prefix}{number}" if prefix and number is not None else None
        return {"id": o, "number": number, "label": label, "title": title}

    hidden = {"id": None, "number": None, "label": None, "title": None}
    blocked_by = {tid: [named(o) if o in seen else dict(hidden) for o in found] for tid, found in on.items() if found}
    blocks = {
        tid: [{"id": d, "number": n, "label": named(d)["label"]} for d, n, _s in found if d in seen]
        for tid, found in of.items()
        if found
    }
    return blocked_by, blocks


# A live run as `run_task` reads it: the attempt, its own message's refs, and its job's message (refs, wake, chat).
_RUN_ROWS = (
    "SELECT a.id, a.bot_id, a.state, a.task_id, am.refs AS refs, jm.refs AS job_refs, jm.wake_key, jm.conversation_id "
    "FROM attempts a LEFT JOIN messages am ON am.id = a.message_id LEFT JOIN jobs j ON j.id = a.job_id "
    "LEFT JOIN messages jm ON jm.id = j.message_id "
)


def run_task(c: "sqlite3.Connection", run: Any) -> str | None:
    """The one task a run (a `_RUN_ROWS` row) is on, for the reads (`live_runs`) and the stream (`run_moved`) alike: the
    task the attempt names (`task_id`), else the one its message's refs name (hubdb.refs_task_id: the attempt's message,
    then its job's), else the one its job's wake key names, else the one open task of its chat; None when none, or when
    its chat has more than one open task."""
    from .store import H

    if run["task_id"]:
        return str(run["task_id"])

    def known(tid: object) -> bool:
        return bool(tid) and c.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone() is not None

    for refs in (run["refs"], run["job_refs"]):
        named = H.refs_task_id(H._json(refs, {}) or {})
        if known(named):
            return str(named)
    wake = str(run["wake_key"] or "")
    if ":" in wake and known(wake.split(":", 1)[0]):
        return wake.split(":", 1)[0]
    if run["conversation_id"]:
        found = c.execute(
            "SELECT id FROM tasks WHERE conversation_id=? AND status='open' LIMIT 2", (run["conversation_id"],)
        ).fetchall()
        if len(found) == 1:
            return str(found[0][0])
    return None


def live_runs(c: "sqlite3.Connection", bots: dict[str, str]) -> dict[str, str]:
    """{task id: "leased" or "running"} of the tasks, of {task id: bot actor}, whose bot has a run on them now
    (`run_task`): its attempt is leased (its computer is starting it) or running, and its computer still renews the
    lease. A bot's turn that is not running is waiting to try again."""
    from .store import H

    if not bots:
        return {}
    wanted = {tid: str(H.actor_id(bot)) for tid, bot in bots.items()}
    out: dict[str, str] = {}
    for run in c.execute(
        _RUN_ROWS + "WHERE a.bot_id IN (SELECT value FROM json_each(?)) AND a.state IN ('leased', 'running') "
        "AND a.lease_until > ?",
        (json.dumps(sorted(set(wanted.values()))), H.now()),
    ).fetchall():
        tid = run_task(c, run)
        if tid in wanted and wanted[tid] == run["bot_id"] and out.get(tid) != "running":
            out[tid] = str(run["state"])  # "running" wins over "leased"
    return out


# The one status line of a card (its board card, its head and over its chat's message box), `status_line` on every
# task read: Starting (its bot's computer is waking, or has its run and is starting it), Up next (its bot's turn, no run
# yet), Working (a run going now), Needs you (a person's turn: `turn.who`, so a page says it only to that person),
# Paused (its bot cannot run: `turn.line` says why and `turn.href` is where a person fixes it). Only the word: what it
# is about is the turn's, already on the row, so 500 rows cost a few kilobytes more.
STARTING, UP_NEXT, WORKING, NEEDS_YOU, PAUSED = "starting", "up_next", "working", "needs_you", "paused"
WAKING, FAILED = "waking", "failed"  # a bot's cloud computer (cloud_machines.states)


def status_line(
    status: str | None, head: "Wait | None", run: str | None = None, machine: str | None = None, stopped: bool = False
) -> str | None:
    """A task's status line from what it holds now: its `status`, its headline wait `head` (a waiting_on.Wait),
    `run` ("leased" or "running" when its bot has a live run on it, `live_runs`), `machine` (`machine`: its bot's cloud
    computer waking, or failed to start) and `stopped` (the ladder brought its visit to a person: a bot's turn with no
    run is not Up next then, and the card keeps its Stopped mark). None when none of the five says it: a finished task,
    or one that waits on nobody, a sub-task, a check or a snooze."""
    from . import waiting_on as W

    if status != "open" or head is None:
        return None
    if head.state == W.PERSON:
        return NEEDS_YOU if head.who else None
    if head.state == W.PAUSED:
        return STARTING if machine == WAKING else PAUSED
    if head.state != W.BOT:
        return None
    # A live run says itself before anything its computer's machine says.
    if run == "running":
        return WORKING
    if run == "leased":
        return STARTING
    if machine == FAILED:
        return PAUSED
    if machine == WAKING:
        return STARTING
    return None if stopped else UP_NEXT


def machine(
    c: "sqlite3.Connection",
    head: "Wait | None",
    seen: dict[str, str | None] | None = None,
    machines: dict[str, str] | None = None,
) -> str | None:
    """WAKING when its bot's cloud computer is on its way, so it starts by itself and says Starting: a Paused bot with no
    computer waiting for its cloud machine (task_wakes.CLOUD_STARTING), or a bot whose enrolled cloud machine was woken
    and is not up yet. FAILED when that enrolled machine's start failed before any run was claimed: Paused, fixed on
    Settings > Computers (`paused_turn`). None otherwise. `seen` keeps each bot's cause for one pass, and `machines`
    is cloud_machines.states read for a pass's bots at once."""
    from . import cloud_machines, task_wakes
    from . import waiting_on as W
    from .store import H

    if head is None or not head.who or head.fix:
        return None
    if head.state == W.PAUSED:
        return WAKING if task_wakes.cannot_run(c, head.who, seen) == task_wakes.CLOUD_STARTING else None
    if head.state != W.BOT:
        return None
    bot = str(H.actor_id(head.who))
    found = machines if machines is not None else cloud_machines.states(c, [bot])
    return found.get(bot)


def paused_turn(c: "sqlite3.Connection", turn: dict[str, Any] | None, line: str | None) -> None:
    """A bot's turn whose cloud computer failed to start says so, as a Paused turn does: why, and Settings > Computers."""
    from . import task_wakes

    if turn and line == PAUSED and turn.get("state") == "bot":
        turn["line"] = task_wakes.cause_line(c, turn["who"], task_wakes.CLOUD_FAILED)
        turn["href"] = task_wakes.COMPUTERS_PAGE


class _Sent(set[str]):
    """The tasks this transaction has sent again already: held in its after-commit list, which the commit and a
    rollback empty, so it lasts exactly one transaction."""

    def __call__(self) -> None:
        return None


def _send(c: "sqlite3.Connection", ids: Iterable[str], kind: str) -> None:
    """One `tasks` change for each of these open tasks, once per transaction; past events.FAN_CAP one `bulk` change."""
    from .store import H

    hooks = getattr(c, "after_commit", None)
    sent = next((h for h in hooks if isinstance(h, _Sent)), None) if hooks is not None else None
    if sent is None:
        sent = _Sent()
        if hooks is not None:
            hooks.append(sent)
    if "*" in sent:
        return
    fresh = sorted(set(ids) - sent)
    if not fresh:
        return
    if "*" in fresh or len(fresh) > E.FAN_CAP:
        sent.add("*")
        c.execute("INSERT INTO changes(topic,kind,created_at) VALUES('tasks','bulk',?)", (H.now(),))
        return
    sent.update(fresh)
    at = H.now()
    c.executemany(
        "INSERT INTO changes(topic,kind,subject_id,created_at) VALUES('tasks',?,?,?)", [(kind, i, at) for i in fresh]
    )


def run_moved(c: "sqlite3.Connection", attempt_id: str) -> None:
    """A bot's run was leased, started, ended or failed: its open task (`run_task`, as the reads say it) is sent again
    with its new `status_line` (`_send`). A closed or finished task gets nothing."""
    run = c.execute(_RUN_ROWS + "WHERE a.id = ?", (attempt_id,)).fetchone()
    tid = run_task(c, run) if run else None
    if tid and c.execute("SELECT 1 FROM tasks WHERE id=? AND status='open'", (tid,)).fetchone():
        _send(c, [tid], "run")


def status_moved(
    c: "sqlite3.Connection", bots: Iterable[str] | None, tasks: Iterable[str] = (), kind: str = "status"
) -> None:
    """What decides whether these agents can run changed (a cloud machine's state, a hold, a computer assigned, placed or
    revoked): every open task one of them owns or that waits on one of them (an ask), and `tasks`, is sent again, once
    per transaction (`_send`). None is every agent (a company-wide hold): one `bulk` change. `kind` "shots": its paid
    shots to reconcile moved (backend/paid_renders.py), so an open task view reads its waits again."""
    from .store import H

    if bots is None:
        _send(c, ["*"], "status")  # every list read again, once per transaction
        return
    actors = json.dumps(sorted({f"bot:{H.actor_id(b)}" for b in bots if b}))
    found = [
        r[0]
        for r in c.execute(
            "SELECT id FROM tasks WHERE status='open' AND owner_id IN (SELECT value FROM json_each(?1)) UNION "
            "SELECT v.task_id FROM requirements r JOIN visits v ON v.left_at IS NULL AND (v.id = r.visit_id OR "
            "v.stage_id = r.stage_id) JOIN tasks t ON t.id = v.task_id AND t.status = 'open' "
            "WHERE r.person_id IN (SELECT value FROM json_each(?1)) AND r.withdrawn_at IS NULL",
            (actors,),
        )
    ]
    _send(c, [*found, *tasks], kind)


def comment_rights(c, auth, who, task_id):
    """Whether the caller may write on the task: every comment and file goes through here. Returns the task's row.

    Anyone who can read the task may comment and attach files on it, a bot as a person: a bot Related input picks is no
    party to the task (related_input.candidates), and its comment is what the stage waits on. What stays: the read
    check, onboarding, and the owner bot's own Write access and contact settings (refuse_comment). The task's pages stay
    with its parties (page_rights)."""
    row = auth.task(c, who, task_id)
    refuse_comment(c, auth, who, row)
    return row


def page_rights(c, auth, who, task_id):
    """Whether the caller may write the task's pages: a page linked to the task, or one published in a role on it (its
    Spec). A page is what a stage decides on, so this keeps the party rule comments dropped: a bot writes one only as
    the task's party, a manager of one, a delegate, a party of a task above it, or one who can work the task.
    Returns the task's row."""
    from .store import H, Problem

    row = comment_rights(c, auth, who, task_id)
    if who.role == "bot" and not H.task_participant(c, who.actor, row):
        delegated = c.execute(
            "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires_at>?",
            (row["id"], who.actor, H.now()),
        ).fetchone()
        if not delegated and not H.task_ancestor_party(c, who.actor, row) and not H.task_can_work(c, who.actor, row):
            raise Problem(
                "forbidden",
                "Only the task's parties, their managers, delegates, the parties of a task above it and its "
                "pipeline's manager may write its pages",
                403,
            )
    return row


def refuse_comment(c, auth, who, row):
    """Refuse a comment on a task the caller already reads: an onboarding bot, or one the task's bot (owner, requester
    or origin) gives no Write access or does not take contact from. related_input.candidates asks it of each bot."""
    from . import lifecycle
    from .store import H

    lifecycle.refuse_send(c, who, None, what="task comments")
    origin = None if H.task_private(c, row) else H.task_origin(c, row)
    others = [
        a
        for a in dict.fromkeys([row["owner_id"], row["requester_id"], origin])
        if a and a not in (who.actor, H.TICO_ACTOR)
    ]
    target = next((a for a in others if H.is_bot(a)), None)
    if target and not H.task_represents(c, who.actor, row["owner_id"]):
        auth.require_write(c, who, H.actor_id(target))
    if target:
        auth.require_bot_contact(c, who, target, task_id=row["id"], kind="comment")


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
