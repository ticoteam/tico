# pyright: strict
"""A call hands work to a normal task and gets its answer back in its own thread."""

from collections.abc import Iterable
from sqlite3 import Connection, Row
from typing import Any

from fastapi import FastAPI, Request
from pydantic import Field

from . import batch_work as _batch_work
from . import read_cache
from . import reach as _reach
from . import task_caps as _task_caps
from . import task_privacy as _task_privacy
from . import threads as _threads
from .api_contract import api_v2
from .auth import Identity
from .models import Contract
from .store import H as _H
from .store import Problem

# Task and thread operations cross into the legacy Hub domain layer.
H: Any = _H
batch_work: Any = _batch_work
reach: Any = _reach
task_caps: Any = _task_caps
task_privacy: Any = _task_privacy
threads: Any = _threads
Record = dict[str, Any]

TRANSCRIPT_LINES = 5
NOTES_LIMIT = 100
ANSWER_CHARS = 8000

# A completion note is final even when a job that ran on the task still needs recovery. Without a note,
# an unsettled run may still provide the answer, and only a terminal job disposition establishes failure.
_READY_SQL = (
    "SELECT f.task_id,f.bot_id,t.status,t.title AS topic,s.*,"
    "n.id AS answer_id,n.notes AS answer_notes,n.by_id AS answer_author,n.created_at AS answered_at "
    "FROM call_follow_ups f JOIN tasks t ON t.id=f.task_id JOIN calls s ON s.id=f.call_id "
    "LEFT JOIN answers n ON n.id=("
    "SELECT a.id FROM visits v JOIN answers a ON a.visit_id=v.id JOIN requirements r ON r.id=a.requirement_id "
    "WHERE t.status='done' AND v.task_id=t.id AND a.by_id IN ('bot:' || f.bot_id,t.requester_id) "
    "AND a.answer='done' AND r.kind='owner_done' AND a.undone_at IS NULL AND trim(a.notes,char(9,10,13,32))<>'' "
    "ORDER BY a.created_at DESC,a.rowid DESC LIMIT 1) "
    "WHERE f.returned_state IS NULL AND (n.id IS NOT NULL OR ("
    "NOT EXISTS (SELECT 1 FROM attempts a WHERE a.task_id=t.id AND a.state IN ('leased','running','input')) "
    "AND NOT EXISTS (SELECT 1 FROM messages m CROSS JOIN jobs j ON j.message_id=m.id "
    "WHERE m.conversation_id=t.conversation_id AND j.state IN ('queued','leased','running','input','uncertain')) "
    "AND (t.status IN ('done','closed') OR (SELECT j.state FROM messages m CROSS JOIN jobs j ON j.message_id=m.id "
    "WHERE m.conversation_id=t.conversation_id AND j.bot_id=f.bot_id "
    "ORDER BY j.created_at DESC,j.rowid DESC LIMIT 1) IN ('failed','canceled')))) "
    "ORDER BY f.rowid LIMIT 20"
)
_QUEUE_SPEECH_SQL = (
    "SELECT f.bot_id,f.message_id,m.body FROM call_follow_ups f "
    "CROSS JOIN messages m ON m.id=f.message_id AND m.deleted_at IS NULL "
    "WHERE f.call_id=? AND f.speech_pending=1 ORDER BY f.rowid LIMIT 20"
)


class FollowUp(Contract):
    ask: str = Field(min_length=1, max_length=4000)
    bot: str | None = Field(default=None, min_length=1, max_length=200)


def source_thread(c: Connection, task_id: str) -> str | None:
    if not read_cache.one(c, "SELECT 1 FROM call_follow_ups LIMIT 1"):
        return None
    row = read_cache.one(
        c, "SELECT s.thread_id FROM call_follow_ups f JOIN calls s ON s.id=f.call_id WHERE f.task_id=?", (task_id,)
    )
    return row[0] if row else None


def _viewer(c: Connection, actor: str) -> _threads.Viewer | None:
    from . import calls as _calls

    calls: Any = _calls
    auth = calls.AUTH

    if H.is_human(actor) and auth is not None:
        try:
            return threads.viewer(auth, auth.identity_for_actor(c, actor))
        except Problem:
            return None
    return threads.actor_viewer(c, actor)


def thread_reader(c: Connection, actor: str, task_id: str) -> bool:
    source = source_thread(c, task_id)
    v = _viewer(c, actor) if source else None
    return bool(source and v is not None and threads.readable(c, v, source))


def reader_sql(c: Connection, actor: str) -> str:
    """Use the source thread's current standing, including membership changes after the call."""
    from . import bot_access as _bot_access

    bot_access: Any = _bot_access
    ids: list[str] = read_cache.value(
        c,
        ("follow_up_threads",),
        lambda: [
            r[0] for r in c.execute("SELECT DISTINCT s.thread_id FROM call_follow_ups f JOIN calls s ON s.id=f.call_id")
        ],
        shared=True,
    )
    v = _viewer(c, actor) if ids else None
    visible = [cid for cid in ids if threads.readable(c, v, cid)] if v is not None else []
    if not visible:
        return "0"
    return (
        "id IN (SELECT f.task_id FROM call_follow_ups f JOIN calls s ON s.id=f.call_id "
        f"WHERE s.thread_id IN {bot_access.qlist(sorted(visible))})"
    )


def may_file(c: Connection, auth: Any, who: Identity, call_id: str, bot: str | None = None) -> tuple[Record, str]:
    from . import calls as _calls

    calls: Any = _calls
    auth.domain(who)
    row: Record = calls._call(c, call_id)
    attached: dict[str, str] = {
        r["bot"]: r["joined_by"]
        for r in c.execute("SELECT bot,joined_by FROM live_meeting_bots WHERE meeting_id=?", (row["live_meeting_id"],))
    }
    if who.role == "bot":
        chosen: str = H.actor_id(who.actor)
        if chosen not in attached or not calls.attachment_standing(c, attached[chosen], row["thread_id"], chosen):
            raise Problem("not_found", "Call not found", 404)
        if bot and H.resolve_actor(c, bot) != who.actor:
            raise Problem("forbidden", "An attached bot files its own follow-up", 403)
    elif who.role in ("human", "owner"):
        if not calls.readable(c, who.actor, row["thread_id"]):
            raise Problem("not_found", "Call not found", 404)
        participant = calls._participant(c, call_id, who.actor)
        if not calls._present(participant) or not participant["in_call"]:
            raise Problem("forbidden", "Join the call before filing a follow-up", 403)
        if bot is None and len(attached) != 1:
            raise Problem("follow_up_bot", "Choose an attached bot for the follow-up", 422)
        chosen = next(iter(attached)) if bot is None else H.actor_id(auth.target(c, who, bot, need="write"))
        if chosen not in attached or not calls.attachment_standing(c, attached[chosen], row["thread_id"], chosen):
            raise Problem("follow_up_bot", "Choose an attached bot for the follow-up", 422)
    else:
        raise Problem("not_found", "Call not found", 404)
    calls._require_open(row)
    return row, chosen


def file_task(c: Connection, auth: Any, who: Identity, row: Record, bot: str, ask: str) -> dict[str, str]:
    owner = auth.target(c, who, f"bot:{bot}", need="write")
    task_caps.check(c, who.actor, owner)
    reach.check(c, who, owner, "task")
    auth.require_bot_contact(c, who, owner, kind="task")
    say: list[Row] = c.execute(
        "SELECT speaker,text FROM live_meeting_chunks WHERE meeting_id=? ORDER BY seq DESC LIMIT ?",
        (row["live_meeting_id"], TRANSCRIPT_LINES),
    ).fetchall()
    source = f"/#/thread/{row['thread_id']}?call={row['id']}"
    body = f"{ask}\n\n[Call]({source})"
    if say:
        context = "\n".join(f"{r['speaker']}: {r['text'][:1000]}" for r in reversed(say))
        body += f"\n\nRecent call transcript:\n{context}"
    task = H.task_create(
        c,
        who.actor,
        " ".join(ask.split())[:200],
        body,
        owner,
        private=True,
        reconcile_related=False,
        instructions="Answer the call's request. Put the answer in your task completion note or final reply.",
    )
    c.execute("INSERT INTO call_follow_ups(task_id,call_id,bot_id) VALUES(?,?,?)", (task["id"], row["id"], bot))
    return {"task_id": task["id"]}


def _ready(c: Connection) -> list[Row]:
    return c.execute(_READY_SQL).fetchall()


def _answer(c: Connection, row: Row) -> tuple[str, str, Record]:
    actor = f"bot:{row['bot_id']}"
    if row["status"] != "done":
        return "", actor, {}
    if row["answer_id"]:
        via = c.execute(
            "SELECT via FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='answer' "
            "AND json_extract(data,'$.answer_id')=? LIMIT 1",
            (row["task_id"], row["answer_id"]),
        ).fetchone()
        refs: Record = {"answer_id": row["answer_id"]}
        if via and via[0]:
            refs["via"] = via[0]
        if row["answer_author"] == actor:
            run = c.execute(
                "SELECT id FROM attempts WHERE task_id=? AND bot_id=? AND started_at<=? "
                "AND (finished_at IS NULL OR finished_at>=?) "
                "ORDER BY started_at DESC,rowid DESC LIMIT 1",
                (row["task_id"], row["bot_id"], row["answered_at"], row["answered_at"]),
            ).fetchone()
            if run:
                refs["turn_id"] = run["id"]
        return row["answer_notes"].strip(), row["answer_author"], refs
    run = c.execute(
        "SELECT id,final_text,result FROM attempts WHERE task_id=? AND bot_id=? "
        "ORDER BY created_at DESC,rowid DESC LIMIT 1",
        (row["task_id"], row["bot_id"]),
    ).fetchone()
    if run and run["final_text"]:
        verdict: Record = H._json(run["result"], {}) or {}
        if verdict.get("outcome") == "completed" and not verdict.get("held_reply"):
            return run["final_text"].strip(), actor, {"turn_id": run["id"]}
    return "", actor, {}


def _speakers_read(c: Connection, row: Row, message: Record) -> bool:
    return all(
        task_privacy.message_readable(c, p[0], message)
        for p in c.execute(
            "SELECT actor FROM call_participants WHERE call_id=? AND in_call=1 AND left_at IS NULL AND removed_at IS NULL",
            (row["id"],),
        )
    )


def can_speak(c: Connection, call_id: str, message_id: str) -> bool:
    row = c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
    message = H.message(c, message_id)
    return bool(row and message and _speakers_read(c, row, message))


def _notes_may_keep(c: Connection, row: Row, aid: str | None) -> bool:
    # The per-bot brief is plain text. Only the thread message can carry narrower run provenance.
    if not aid:
        return True
    private_tasks: Iterable[str] = task_privacy.attempt_tasks(c, aid) - {row["task_id"]}
    meetings: set[str] = task_privacy.meeting_sources(c).attempt(aid)
    return not any(H.task_private(c, H.task(c, tid)) for tid in private_tasks) and meetings <= {row["live_meeting_id"]}


def _return(c: Any, row: Row) -> None:
    from . import call_notes

    thread = threads.thread_row(c, row["thread_id"])
    if thread is None or thread.get("deleted_at"):
        c.execute("UPDATE call_follow_ups SET returned_state='done' WHERE task_id=?", (row["task_id"],))
        return
    answer, author, provenance = _answer(c, row)
    answer = answer[:ANSWER_CHARS]
    topic = row["topic"]
    link = f"[Open task](#/task/{row['task_id']})"
    if answer:
        body = f"Back on {topic}: {answer}"
    elif row["status"] in ("done", "closed"):
        body = f"The follow-up on {topic} ended without an answer."
    else:
        body = f"The follow-up on {topic} failed before it could answer."
    refs: Record = {
        "task": row["task_id"],
        "call": {"id": row["id"], "meeting_id": row["live_meeting_id"]},
        "live_meeting": {"meeting_id": row["live_meeting_id"]},
        "quiet": True,
        "no_mentions": True,
        **provenance,
    }
    # This is a server projection of the bot's completed work. It may arrive after the bot leaves the call or its
    # run lease ends. Its task, call and run references keep the original readers on the message.
    message = H._write_message(c, author, None, f"{body}\n\n{link}", thread, "say", refs, None, None)
    state = row["status"] if row["status"] in ("done", "closed") else "failed"
    speak = bool(answer and row["state"] == "live" and not row["ending_at"] and row["speak_replies"])
    c.execute(
        "UPDATE call_follow_ups SET returned_state=?,message_id=?,speech_pending=? WHERE task_id=?",
        (state, message["id"], speak, row["task_id"]),
    )
    c.execute(
        "INSERT INTO changes(topic,kind,subject_id,conversation_id,actor,created_at) "
        "VALUES('threads','call.follow_up',?,?,?,?)",
        (row["id"], row["thread_id"], author, H.now()),
    )
    append = getattr(call_notes, "append", None)
    if answer and append is not None and _notes_may_keep(c, row, provenance.get("turn_id")):
        append(c, row["id"], row["bot_id"], body)


def sweep(calls: Any) -> None:
    """Return terminal work once, even if the task or call survived a server restart."""
    with calls.store.read() as c:
        if not _ready(c):
            return
    with calls.store.transaction() as c:
        for row in _ready(c):
            with batch_work.isolated(c, "call_follow_up", row["task_id"], private=True):
                _return(c, row)


def queue_speech(calls: Any, call_id: str) -> None:
    """Pick up durable answers when a live call has a recorder, including after a server restart."""
    with calls.store.read() as c:
        rows: list[Row] = c.execute(_QUEUE_SPEECH_SQL, (call_id,)).fetchall()
    for row in rows:
        body = row["body"].rsplit("\n\n", 1)[0]
        calls.voice.follow_up(call_id, row["bot_id"], row["message_id"], " ".join(body.split()[:80]))


def finish_speech(c: Connection, call_id: str, message_id: str) -> None:
    c.execute(
        "UPDATE call_follow_ups SET speech_pending=0 WHERE call_id=? AND message_id=? AND speech_pending=1",
        (call_id, message_id),
    )


def notes(c: Connection, auth: Any, who: Identity, call_id: str) -> list[Record]:
    rows: list[Row] = c.execute(
        "SELECT f.task_id,f.message_id,t.title,t.status FROM call_follow_ups f JOIN tasks t ON t.id=f.task_id "
        "WHERE f.call_id=? ORDER BY f.rowid DESC LIMIT ?",
        (call_id, NOTES_LIMIT),
    ).fetchall()
    out: list[Record] = []
    for row in rows:
        message = H.message(c, row["message_id"]) if row["message_id"] else None
        if not H.task_private_readable(c, who.actor, dict(row, id=row["task_id"])):
            continue
        if message and not threads.message_readable(c, threads.viewer(auth, who), message):
            continue
        out.append(
            {
                "task_id": row["task_id"],
                "title": row["title"],
                "status": row["status"],
                "text": message["body"].rsplit("\n\n", 1)[0][:2000] if message else None,
            }
        )
    return out


def install(app: FastAPI, auth: Any, write: Any) -> None:
    @app.post(
        "/api/v2/calls/{call_id}/follow-ups",
        **api_v2("Calls", "createCallFollowUp", "Hand a live call's question to a bot's normal task", "CallFollowUp"),
    )
    def follow_up(request: Request, call_id: str, body: FollowUp) -> Any:
        who: Identity = request.state.identity

        def work(c: Connection) -> dict[str, str]:
            row, bot = may_file(c, auth, who, call_id, body.bot)
            return file_task(c, auth, who, row, bot, body.ask)

        def check(c: Connection) -> None:
            may_file(c, auth, who, call_id, body.bot)

        return write(request, body, work, check=check)
