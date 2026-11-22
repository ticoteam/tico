# pyright: strict
"""What a move and a reassignment do beyond the task's own row (docs/dev/pipelines-v3-cutover.md, "What a move still
does").

The engine calls `owner_moved` first on a move that gives the task another owner, before it wakes anyone, `moved` at
the end of every move and `reassigned` at the end of every reassignment, inside the caller's transaction. Nothing outside the engine calls them, so no caller can skip one: a routine's day is marked, the
webhooks and automations hear, work on a closed task stops, whoever moves the task.

The engine writes the history row, cancels and writes the wakes, closes sub-tasks and tells the task that filed this
one. Those are not here.

Nothing here tells a person: no chat, email or push about a task. A person reads Needs you. One exception: a task the
person handed off through their Assistant is reported back in their Assistant chat when it ends (`_report_back`).
"""

import re
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, cast

from . import hubdb_core
from . import task_requirements as R

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
_core: Any = hubdb_core
SYSTEM: str = _core.TICO_ACTOR

# Whose act made the engine move a task: the owner who said the work is done, the person who gave the last OK. The
# engine moves as SYSTEM; what the move causes is theirs (a routine's day counts as done by its owner, not by nobody).
_CAUSE: ContextVar[str | None] = ContextVar("task_effects_cause", default=None)


def _h() -> Any:
    from . import hubdb

    return hubdb


@contextmanager
def because(actor: str) -> Generator[None]:
    """While this is open, a move the engine makes by itself is `actor`'s for what it causes."""
    token = _CAUSE.set(str(actor))
    try:
        yield
    finally:
        _CAUSE.reset(token)


# A completion note said on a move that carries none (task_calls.task_done's plain forward move), for that task only:
# `moved` keeps it on that move's history row (data.done_note), apart from its why, for the report back. It never
# reaches the move itself, so it lets nothing past a gate.
_NOTE: ContextVar[tuple[str, str] | None] = ContextVar("task_effects_note", default=None)


@contextmanager
def noting(task_id: str, note: str | None) -> Generator[None]:
    """While this is open, `note` is the completion note of a move of `task_id` that carries none."""
    token = _NOTE.set((str(task_id), note.strip()) if note and note.strip() else None)
    try:
        yield
    finally:
        _NOTE.reset(token)


def cause(by: str) -> str:
    """Who a move is attributed to: whoever made it, or for the engine's own move whoever caused it."""
    return (_CAUSE.get() or by) if by == SYSTEM else by


def _event_name(old: str, new: str) -> str | None:
    if new in ("done", "closed"):
        return f"task.{new}"
    if old in ("done", "closed") and new == "open":
        return "task.reopened"
    return None


def _publish(conn: "sqlite3.Connection", name: str, row: Row, actor: str, change: Row, revision: str | None) -> None:
    """One event for the task trigger rules, the outgoing webhooks and the automations."""
    H = _h()
    from . import task_trigger_rules

    triggers: Any = task_trigger_rules
    triggers.enqueue(conn, name, row["id"], actor, revision, field=change["field"], new=change["to"])
    facts: Row = {
        "id": row["id"],
        "title": row.get("title"),
        "status": row.get("status"),
        "pipeline": row.get("pipeline_id"),
        "stage": row.get("stage_id"),
        "owner_id": row.get("owner_id"),
        "requester_id": row.get("requester_id"),
        "actor": actor,
        "change": change,
    }
    H.hook(conn, name, row["id"], lambda: facts, actor, revision=revision)


def filed_ended(conn: "sqlite3.Connection", above: Row, by: str) -> Row:
    """The last task `above` filed on this visit ended (task_engine._ended, before it wakes anyone): a snooze a bot set
    on `above` was a wait for what it filed ("snoozed till Chris applies it"), and that wait is over, so the snooze ends
    and the owner is woken now with how each ended, not on the snooze's date. The same rule as a done ending your own
    snooze (backend/answers.py): a snooze a person set is theirs to end, and stays. Returns `above` as it is now."""
    H = _h()
    snoozed: Row | None = above.get("snooze")
    if not snoozed or not H.is_bot(snoozed.get("by_id")):
        return above
    from . import task_engine

    engine: Any = task_engine
    return engine.unsnooze(conn, above["id"], by, "What it filed has ended", consequence="filed_ended")


def _tell_requester(conn: "sqlite3.Connection", after: Row, actor: str, why: str | None) -> None:
    """A bot that asked for a task hears once how it ended, on its next run: no run starts for it (never-stuck rule 7).
    Not when it ended it itself or owns it. Not when the engine already told it, on the task that filed this one, that
    what it filed there has ended (task_engine._ended, which runs before this): only a wake it wrote counts, so a
    parent that is closing, that a person snoozed, or that has left the stage the task was filed from takes nothing
    away. A bot's snooze there ended with the last filed task (`filed_ended`), so that parent's owner was woken."""
    H = _h()
    requester = after.get("requester_id")
    if not H.is_bot(requester) or requester in (actor, after.get("owner_id")):
        return
    link = conn.execute("SELECT from_task_id, visit_id FROM links WHERE to_task_id=?", (after["id"],)).fetchone()
    if link and link[1]:
        # The key task_engine._ended gives its wake on the filing task.
        told = f"{link[0]}:{link[1]}:filed:{after['id']}"
        if conn.execute("SELECT 1 FROM messages WHERE wake_key=? AND recipient_id=?", (told, requester)).fetchone():
            return
    visit: Row = after.get("visit") or {}
    wake_key = f"{after['id']}:{visit.get('id')}:result"
    if conn.execute("SELECT 1 FROM messages WHERE wake_key=?", (wake_key,)).fetchone():
        return
    said = f"{'Finished' if after['status'] == 'done' else 'Closed'}: {after.get('title') or 'A task'}"
    if (why or "").strip():
        said += f"\n{why}"
    # News, not work: it waits in the bot's room for its next run. Nothing wakes a bot about a Done or Closed task.
    message = H._wake(conn, after, requester, said, {"news": "task_done"}, quiet_bots=True, quiet=True)
    if message:
        conn.execute("UPDATE messages SET wake_key=? WHERE id=?", (wake_key, message["id"]))


# The latest completion note of this cycle (since the last reopen): the owner's done, a done again after a send back
# (answers.reopen_sent_back), or a done's note kept on its move (`noting`). What a report back says when the move that
# ended the task carried no note of its own.
_CYCLE_NOTE_SQL = (
    "WITH s(at) AS (SELECT coalesce(max(entered_at),'') FROM visits WHERE task_id=?1 AND entered_how='reopened') "
    "SELECT note FROM (SELECT a.notes AS note,a.created_at AS at FROM visits v JOIN answers a ON a.visit_id=v.id "
    "JOIN requirements r ON r.id=a.requirement_id WHERE v.task_id=?1 AND v.entered_at>=(SELECT at FROM s) "
    "AND (a.answer='again' OR a.answer='done' AND r.kind='owner_done') AND a.undone_at IS NULL "
    "UNION ALL SELECT json_extract(h.data,'$.done_note'),h.created_at FROM history_events h WHERE "
    "h.subject_type='task' AND h.subject_id=?1 AND h.kind='move' AND h.created_at>=(SELECT at FROM s)) "
    "WHERE trim(coalesce(note,''),char(9,10,13,32))<>'' ORDER BY at DESC LIMIT 1"
)
NOTE_CHARS = 4000


def _report_back(conn: "sqlite3.Connection", after: Row, by: str, why: str | None, revision: str | None) -> None:
    """A task the person handed off through their Assistant (its `task.create` event says via assistant: filed on their
    Assistant turn or on their click on its card) has ended: one system line from the Assistant in their Assistant chat
    (opened again if it is gone). Not when the move's own actor is the person (their close, Done or move, by hand or by
    their Assistant for them), and not for a call's follow-up, which reports in its call (call_follow_ups._return); an
    engine or automatic move always reports. Its wake key is
    the dedupe record, one per (task, terminal history event), so a reopen and a second end report again. All of it in
    its own savepoint: a report that fails is logged and never undoes the move."""
    if not revision:
        return
    from . import batch_work

    work: Any = batch_work
    with work.isolated(conn, "report_back", after["id"], private=True):
        _write_report(conn, after, by, why, f"report_back:{after['id']}:{revision}")


def plain_line(text: object, empty: str = "A task") -> str:
    """Text as plain Markdown on one line: whitespace collapsed, Markdown marks escaped, and no list marker or heading
    at its start. `empty` stands in for no text."""
    flat = re.sub(r"([\\`*_\[\]<>#|~!])", r"\\\1", " ".join(str(text or "").split())) or empty
    return re.sub(r"^(\d+)([.)])", r"\1\\\2", re.sub(r"^([-+])", r"\\\1", flat))


def _write_report(conn: "sqlite3.Connection", after: Row, by: str, why: str | None, key: str) -> None:
    """The report itself. The person's read right is checked now (auth.actor_reads_task): a task they can no longer
    read is named by the title it was filed with, alone, since its current title, a link or a task reference would tell
    or hide too much."""
    H = _h()
    task_id = str(after["id"])
    made = conn.execute(
        "SELECT actor, detail FROM events WHERE action='task.create' AND target=? ORDER BY created_at, id LIMIT 1",
        (task_id,),
    ).fetchone()
    detail: object = H._json(made["detail"], {}) if made else None
    if not isinstance(detail, dict):
        return
    filed = cast(Row, detail)
    person = str(made["actor"])
    # Only the person it was filed for, while still here (a task filed `--for` someone else, or a filer who has left,
    # gets nothing and opens no room).
    if (
        filed.get("via") != "assistant"
        or by == person
        or after.get("requester_id") != person
        or not R.active_person(conn, person)
    ):
        return
    if (
        conn.execute("SELECT 1 FROM messages WHERE wake_key=?", (key,)).fetchone()
        or conn.execute("SELECT 1 FROM call_follow_ups WHERE task_id=?", (task_id,)).fetchone()
    ):
        return  # already reported, or a call's follow-up, which reports in its call (call_follow_ups._return)
    from . import auth

    reads: Any = auth
    # A system line: names in it mention nobody and it starts no run.
    refs: Row = {"assistant": True, "report_back": True, "quiet": True, "no_mentions": True}
    if reads.actor_reads_task(conn, person, after):
        title = plain_line(after.get("title"))
        note = (why or "").strip()
        if not note:
            found = conn.execute(_CYCLE_NOTE_SQL, (task_id,)).fetchone()
            note = str(found[0]).strip() if found else ""
        # The link is its own paragraph before the note, so nothing the note's Markdown leaves open can swallow it.
        link = f"[Open task](#/task/{task_id})"
        said = (
            f"Back on {title}:\n\n{link}\n\n{note[:NOTE_CHARS]}"
            if note
            else f"{title} ended without a result\n\n{link}"
        )
        body, refs = said, {**refs, "task": task_id}
    else:
        body = f"{plain_line(filed.get('title'))} has ended"
    assistant_line(conn, person, body, refs, key)


def assistant_line(conn: "sqlite3.Connection", person: str, body: str, refs: Row, key: str) -> None:
    """One delivered message from the Assistant in the person's Assistant chat, with no run: a report back, or another
    agent's reply relayed (backend/assistant_relay.py). The room is opened if missing, a paused Assistant too, and a
    deleted one gives up its place first. `key` goes on the message as its wake key: the caller's dedupe record."""
    H = _h()
    from . import assistant, rooms

    helper: Any = assistant
    place: Any = rooms
    # The Assistant as the chat names it: the app's settings, which the connection carries (store.Store._take).
    slug = str(getattr(getattr(conn, "settings", None), "assistant_bot", "") or "assistant")
    # A deleted Assistant room still holds the person's one open place (one_active_personal_room): it gives it up.
    conn.execute(
        "UPDATE conversations SET closed_at=? WHERE scope='personal' AND owner_id=? AND room_key=? "
        "AND closed_at IS NULL AND deleted_at IS NOT NULL",
        (H.now(), person, place.ASSISTANT_ROOM),
    )
    room = helper.ensure_room(conn, person, slug, allow_held=True)
    message = H._write_message(conn, "bot:" + slug, person, body, room, "say", refs, None, None, delivered_at=H.now())
    conn.execute("UPDATE messages SET wake_key=? WHERE id=?", (key, message["id"]))


def moved(
    conn: "sqlite3.Connection",
    before: Row,
    after: Row,
    kind: str,
    by: str,
    why: str | None = None,
    revision: str | None = None,
    *,
    batch: str | None = None,
) -> None:
    """A task moved: `kind` is forward, back, done, closed, reopen or moved (to another pipeline). `before` and `after`
    are `task_engine.task()`'s shape; `revision` is the id of the move's history row; `batch` is the bulk move it is
    part of."""
    H = _h()
    actor = cause(by)
    old, new = str(before.get("status")), str(after.get("status"))
    noted = _NOTE.get()
    if noted and noted[0] == after["id"] and revision:
        # A done's note on a plain forward move, kept on that move's history row apart from its why (`noting`).
        conn.execute(
            "UPDATE history_events SET data=json_set(coalesce(data,'{}'),'$.done_note',?) WHERE id=?",
            (noted[1], revision),
        )
    if before.get("stage_id") != after.get("stage_id"):
        # The old stage's "leaves" and the new stage's "enters" read this one event: its from and its to.
        change: Row = {"field": "stage", "from": before.get("stage_id"), "to": after.get("stage_id"), "move": kind}
        _publish(conn, "task.step_changed", after, actor, change, revision)
    if before.get("pipeline_id") != after.get("pipeline_id"):
        moved: Row = {
            "field": "pipeline",
            "from": before.get("pipeline_id"),
            "to": after.get("pipeline_id"),
            "from_pipeline": before.get("pipeline_id"),
            "from_stage": before.get("stage_id"),
            "to_pipeline": after.get("pipeline_id"),
            "to_stage": after.get("stage_id"),
            "by": actor,
            "batch_id": batch,
        }
        _publish(conn, "task.moved", after, actor, moved, revision)
    if before.get("owner_id") != after.get("owner_id"):
        # The next stage has another owner. What follows the owner already happened (`owner_moved`).
        _publish_owner(conn, before, after, actor, revision)
    if kind in ("forward", "done"):
        # A task filed for a Jira issue whose merges are in a release closes the loop outside, once
        # (backend/release_closeout.py).
        from . import release_closeout

        release_closeout.moved(conn, str(after["id"]))
    if old == new:
        return
    from . import delivery, reconnects, rule_due, run_triggers

    reconnects_: Any = reconnects
    reconnects_.moved(conn, after["id"], "status", old, new)
    name = _event_name(old, new)
    if name:
        _publish(conn, name, after, actor, {"field": "status", "from": old, "to": new, "move": kind}, revision)
    if before.get("occurrence"):
        # A routine's task for a due date: finishing it marks that day, reopening takes the mark back.
        rules: Any = rule_due
        rules.on_task_status(conn, actor, before, old, new)
    runs: Any = run_triggers
    if old == "done" and new == "open":
        # Someone sent a bot's finished result back: that is what became of the run that made it.
        runs.turn_result(conn, actor, before, "overridden", H.event, new)
    elif new == "done":
        # Someone else's act took the bot's task to Done (an OK, the manager or a person saying it is done): its run
        # was accepted. The bot's own move, or Tico's, writes nothing (turn_result).
        runs.turn_result(conn, actor, after, "accepted", H.event)
    if new in ("done", "closed"):
        deliveries: Any = delivery
        deliveries.task_closed(conn, before)
        _tell_requester(conn, after, actor, why)
        _report_back(conn, after, by, why, revision)
    H.event(conn, actor, "task.close" if new == "closed" else "task.update", after["id"], {"status": new, "note": why})
    H.recount(conn, [after.get("owner_id"), after.get("requester_id"), before.get("owner_id")])
    if new == "closed":
        from . import email_sending

        # An email still to go for it, or whose OK waits on it, is canceled with it (backend/email_sending.py).
        mail: Any = email_sending
        mail.task_closed(conn, after["id"])
        # Last, so a wake this move queued is stopped too. The run that makes the close is never stopped.
        H.stop_ended_work(conn, actor, after["id"], "closed")


def owner_moved(conn: "sqlite3.Connection", before: Row, after: Row, by: str) -> None:
    """A move gave the task another owner (a stage someone else owns, or a new task's first stage): what follows the
    owner (`_follow_owner`). The engine runs it first, before it wakes anyone or anything hears of the move, so a task
    the new owner keeps private is private by then. `moved` publishes `task.assigned` at the end."""
    if before.get("owner_id") != after.get("owner_id"):
        # A task takes a place in the owner's queue on first entry or a delayed pick. A move keeps its rank.
        stage = before.get("stage_id")
        _follow_owner(conn, before, after, cause(by), rerank=not stage or stage == after.get("stage_id"))


def _follow_owner(conn: "sqlite3.Connection", before: Row, after: Row, actor: str, *, rerank: bool) -> None:
    """What follows a task's owner, whichever way it changed (a reassignment, or a move into a stage someone else
    owns): the new owner's private default, the sessions and worktree, the per-task model, the followers, the old
    owner's goal on the task and, with `rerank`, its place at the end of the new owner's queue. Before anything about
    the change is published or woken."""
    H = _h()
    task_id = after["id"]
    old, new = before.get("owner_id"), after.get("owner_id")
    from . import reconnects, task_followers, task_run_models

    made_private = bool(new and H.private_tasks_default(conn, new) and not H.task_private(conn, before))
    if made_private:
        # A bot whose tasks are private by default: this one is now, and every task under it with it.
        H._made_private(conn, actor, task_id)
        for below in H.descendants(conn, task_id):
            if not H.task_private(conn, below):
                H._made_private(conn, actor, below["id"])
    # Rights lent on the task were lent by the owner it had.
    conn.execute("DELETE FROM task_delegations WHERE task_id=?", (task_id,))
    if H.is_bot(old):
        H._demote_sessions(conn, task_id, owner=new)
        for link in H.task_links(conn, task_id):
            if link["kind"] == "worktree":
                detail: Row = H._json(link.get("detail"), {}) or {}
                detail.setdefault("owner", old)
                conn.execute("UPDATE task_links SET detail=? WHERE id=?", (H._dump(detail), link["id"]))
        room = H.conversation(conn, before.get("conversation_id")) if before.get("conversation_id") else None
        if room:
            # The goal Tico set for the old owner ends with its ownership. A new bot owner's first run sets its own.
            from . import chat_goals

            goals: Any = chat_goals
            goals.clear_task_goal(conn, room)
    models: Any = task_run_models
    models.moved(conn, actor, task_id)
    followers: Any = task_followers
    followers.owner_moved(conn, after, old, new)
    if rerank:
        conn.execute("UPDATE tasks SET rank=? WHERE id=?", (H._queue_end(conn, new) if new else None, task_id))
    reconnects_: Any = reconnects
    reconnects_.moved(conn, task_id, "owner", old, new)


def _publish_owner(conn: "sqlite3.Connection", before: Row, after: Row, actor: str, revision: str | None) -> None:
    change: Row = {"field": "owner", "from": before.get("owner_id"), "to": after.get("owner_id")}
    _publish(conn, "task.assigned", after, actor, change, revision)


def reassigned(
    conn: "sqlite3.Connection", before: Row, after: Row, by: str, why: str | None = None, revision: str | None = None
) -> None:
    """A task was given to another owner in its stage. Someone handing a bot's work to someone else is what became of
    the run that made it; then everything that follows the owner (`_follow_owner`); then `task.assigned` is published.
    Nobody is made a reader of a private task."""
    H = _h()
    actor = cause(by)
    old, new = before.get("owner_id"), after.get("owner_id")
    if old == new:
        return
    from . import run_triggers

    runs: Any = run_triggers
    runs.turn_result(conn, actor, before, "overridden", H.event)
    # The task stays in its stage, so it keeps its rank: rank is one order, read by the stage's board as well as by
    # each owner's queue, and a person may have put it at the top of the stage on purpose. Only a task with no rank
    # yet takes the end of the new owner's queue.
    _follow_owner(conn, before, after, actor, rerank=before.get("rank") is None)
    _publish_owner(conn, before, after, actor, revision)
    H.event(conn, actor, "task.update", after["id"], {"owner": new, "note": why})
    H.recount(conn, [new, old, after.get("requester_id")])
