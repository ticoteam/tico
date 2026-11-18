"""A call hands work to a normal task and gets its answer back in its own thread."""

from fastapi import Request
from pydantic import Field

from . import read_cache, reach, task_caps, task_privacy, threads
from .api_contract import api_v2
from .models import Contract
from .store import H, Problem

TRANSCRIPT_LINES = 5
NOTES_LIMIT = 20
ANSWER_CHARS = 8000


class FollowUp(Contract):
    ask: str = Field(min_length=1, max_length=4000)
    bot: str | None = Field(default=None, min_length=1, max_length=200)


def source_thread(c, task_id):
    if not read_cache.one(c, "SELECT 1 FROM call_follow_ups LIMIT 1"):
        return None
    return read_cache.one(
        c, "SELECT s.thread_id FROM call_follow_ups f JOIN calls s ON s.id=f.call_id WHERE f.task_id=?", (task_id,)
    )


def _viewer(c, actor):
    from .calls import AUTH

    if H.is_human(actor) and AUTH is not None:
        try:
            return threads.viewer(AUTH, AUTH.identity_for_actor(c, actor))
        except Problem:
            return None
    return threads.actor_viewer(c, actor)


def thread_reader(c, actor, task_id):
    source = source_thread(c, task_id)
    v = _viewer(c, actor) if source else None
    return v is not None and threads.readable(c, v, source[0])


def reader_sql(c, actor):
    """Use the source thread's current standing, including membership changes after the call."""
    from .auth import A

    ids = read_cache.value(
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
        f"WHERE s.thread_id IN {A.qlist(sorted(visible))})"
    )


def may_file(c, auth, who, call_id, bot=None):
    from . import calls

    auth.domain(who)
    row = calls._call(c, call_id)
    attached = {
        r["bot"]: r["joined_by"]
        for r in c.execute("SELECT bot,joined_by FROM live_meeting_bots WHERE meeting_id=?", (row["live_meeting_id"],))
    }
    if who.role == "bot":
        chosen = H.actor_id(who.actor)
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


def file_task(c, auth, who, row, bot, ask):
    owner = auth.target(c, who, f"bot:{bot}", need="write")
    task_caps.check(c, who.actor, owner)
    reach.check(c, who, owner, "task")
    auth.require_bot_contact(c, who, owner, kind="task")
    say = c.execute(
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
        instructions="Answer the call's request. Put the answer in your task completion note or final reply.",
    )
    c.execute("INSERT INTO call_follow_ups(task_id,call_id,bot_id) VALUES(?,?,?)", (task["id"], row["id"], bot))
    return {"task_id": task["id"]}


def _ready(c):
    # Done can be written by a tool call before its bot's final reply is stored. A terminal run failure is
    # reported only once the job has stopped retrying. Closed tasks are already terminal.
    return c.execute(
        "SELECT f.task_id,f.bot_id,t.status,t.title AS topic,s.* FROM call_follow_ups f "
        "JOIN tasks t ON t.id=f.task_id JOIN calls s ON s.id=f.call_id "
        "WHERE f.returned_state IS NOT 'done' AND (f.returned_state IS NULL OR t.status='done') AND (t.status='closed' OR "
        "(NOT EXISTS (SELECT 1 FROM attempts a WHERE a.task_id=t.id AND a.bot_id=f.bot_id "
        "AND a.state IN ('leased','running','input')) AND (t.status='done' OR EXISTS ("
        "SELECT 1 FROM attempts a JOIN jobs j ON j.attempt_id=a.id WHERE a.task_id=t.id AND a.bot_id=f.bot_id "
        "AND a.state='failed' AND j.state IN ('failed','uncertain') AND a.id=("
        "SELECT latest.id FROM attempts latest WHERE latest.task_id=t.id AND latest.bot_id=f.bot_id "
        "ORDER BY latest.created_at DESC,latest.rowid DESC LIMIT 1))))) "
        "ORDER BY f.rowid LIMIT 20"
    ).fetchall()


def _answer(c, row):
    actor = f"bot:{row['bot_id']}"
    if row["status"] != "done":
        return "", None
    done = c.execute(
        "SELECT a.notes,a.created_at FROM answers a JOIN visits v ON v.id=a.visit_id "
        "JOIN requirements r ON r.id=a.requirement_id WHERE v.task_id=? AND a.by_id=? AND a.answer='done' "
        "AND r.kind='owner_done' AND a.undone_at IS NULL AND trim(a.notes)<>'' "
        "ORDER BY a.created_at DESC,a.rowid DESC LIMIT 1",
        (row["task_id"], actor),
    ).fetchone()
    run = c.execute(
        "SELECT id,final_text,started_at,finished_at,result FROM attempts WHERE task_id=? AND bot_id=? "
        "ORDER BY created_at DESC,rowid DESC LIMIT 1",
        (row["task_id"], row["bot_id"]),
    ).fetchone()
    if done:
        aid = (
            run["id"]
            if run and run["started_at"] and run["started_at"] <= done["created_at"] <= (run["finished_at"] or "")
            else None
        )
        return done["notes"].strip(), aid
    if run and run["final_text"] and (H._json(run["result"], {}) or {}).get("outcome") == "completed":
        verdict = H._json(run["result"], {}) or {}
        if not verdict.get("held_reply"):
            return run["final_text"].strip(), run["id"]
    return "", None


def _speakers_read(c, row, message):
    return all(
        task_privacy.message_readable(c, p[0], message)
        for p in c.execute(
            "SELECT actor FROM call_participants WHERE call_id=? AND in_call=1 AND left_at IS NULL AND removed_at IS NULL",
            (row["id"],),
        )
    )


def can_speak(c, call_id, message_id):
    row = c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
    message = H.message(c, message_id)
    return bool(row and message and _speakers_read(c, row, message))


def _notes_may_keep(c, row, aid):
    # The per-bot brief is plain text. Only the thread message can carry narrower run provenance.
    if not aid:
        return True
    private_tasks = task_privacy.attempt_tasks(c, aid) - {row["task_id"]}
    meetings = task_privacy.meeting_sources(c).attempt(aid)
    return not any(H.task_private(c, H.task(c, tid)) for tid in private_tasks) and meetings <= {row["live_meeting_id"]}


def _return(c, calls, row):
    from . import call_notes

    thread = threads.thread_row(c, row["thread_id"])
    if thread is None or thread.get("deleted_at"):
        c.execute("UPDATE call_follow_ups SET returned_state='done' WHERE task_id=?", (row["task_id"],))
        return
    answer, aid = _answer(c, row)
    answer = answer[:ANSWER_CHARS]
    topic = row["topic"]
    link = f"[Open task](#/task/{row['task_id']})"
    if answer:
        body = f"Back on {topic}: {answer}"
    elif row["status"] in ("done", "closed"):
        body = f"The follow-up on {topic} ended without an answer."
    else:
        body = f"The follow-up on {topic} failed before it could answer."
    refs = {
        "task": row["task_id"],
        "call": {"id": row["id"], "meeting_id": row["live_meeting_id"]},
        "live_meeting": {"meeting_id": row["live_meeting_id"]},
        "quiet": True,
        "no_mentions": True,
        **({"turn_id": aid} if aid else {}),
    }
    # This is a server projection of the bot's completed work. It may arrive after the bot leaves the call or its
    # run lease ends. Its task, call and run references keep the original readers on the message.
    message = H._write_message(c, f"bot:{row['bot_id']}", None, f"{body}\n\n{link}", thread, "say", refs, None, None)
    state = row["status"] if row["status"] in ("done", "closed") else "failed"
    c.execute(
        "UPDATE call_follow_ups SET returned_state=?,message_id=? WHERE task_id=?",
        (state, message["id"], row["task_id"]),
    )
    c.execute(
        "INSERT INTO changes(topic,kind,subject_id,conversation_id,actor,created_at) "
        "VALUES('threads','call.follow_up',?,?,?,?)",
        (row["id"], row["thread_id"], f"bot:{row['bot_id']}", H.now()),
    )
    append = getattr(call_notes, "append", None)
    if answer and append is not None and _notes_may_keep(c, row, aid):
        append(c, row["id"], row["bot_id"], body)
    if answer and row["state"] == "live" and not row["ending_at"] and row["speak_replies"]:
        text = " ".join(body.split()[:80])
        c.after_commit.append(lambda: calls.voice.follow_up(row["id"], row["bot_id"], message["id"], text))


def sweep(calls):
    """Return terminal work once, even if the task or call survived a server restart."""
    with calls.store.read() as c:
        if not _ready(c):
            return
    from .batch_work import isolated

    with calls.store.transaction() as c:
        for row in _ready(c):
            with isolated(c, "call_follow_up", row["task_id"], private=True):
                _return(c, calls, row)


def notes(c, auth, who, call_id):
    rows = c.execute(
        "SELECT f.task_id,f.message_id,t.title,t.status FROM call_follow_ups f JOIN tasks t ON t.id=f.task_id "
        "WHERE f.call_id=? ORDER BY f.rowid DESC LIMIT ?",
        (call_id, NOTES_LIMIT),
    ).fetchall()
    out = []
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


def install(app, auth, write):
    @app.post(
        "/api/v2/calls/{call_id}/follow-ups",
        **api_v2("Calls", "createCallFollowUp", "Hand a live call's question to a bot's normal task", "CallFollowUp"),
    )
    def follow_up(request: Request, call_id: str, body: FollowUp):
        who = request.state.identity

        def work(c):
            row, bot = may_file(c, auth, who, call_id, body.bot)
            return file_task(c, auth, who, row, bot, body.ask)

        return write(request, body, work, check=lambda c: may_file(c, auth, who, call_id, body.bot))
