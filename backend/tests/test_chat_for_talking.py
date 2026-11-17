"""The chat is for talking (spec "Flow, simplified", change 1). What Tico tells a bot reaches it through its run: the
notice is the message its run starts from and stays in its room history, but no person's read of the room shows it,
and it moves no unread mark. A run woken only by such a notice posts its text only when it is for a person: the notice
is the task's request, a person's act is behind it, the bot named someone, or the run finished the task. Otherwise the
text stays on the run.

Synthetic rows only: the fictional company's bots `ops` and `cpo`, and Ana."""

import importlib
import json
from contextlib import contextmanager
from pathlib import Path

from backend import task_engine as E
from backend import task_wakes
from backend import threads
from backend import wake_rules
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import assign, claim, get, post, ready, runner
from backend.tests.helpers.events import live  # noqa: F401 (the fixture)
from backend.tests.helpers.events import ready as ready_stream

ANA = "human:ana"
STOPPED = "You stopped before this task was finished. Try 2 of 3."


def _start(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    return machine


def _task(api):
    """Ana's task for ops, filed from her chat: its runs' replies reach her in the task's own room."""
    task = post(api, "tasks", {"owner": "ops", "title": "Add the export button", "body": "On the reports page."})
    with api.app.state.store.transaction() as c:
        H.event(c, H.KEEPER, "task.room", task["id"], {})
        room = E.task(c, task["id"])["conversation_id"]
    return task, room


def _finish(api, machine, attempt, text):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "fixture-thread"}, machine["token"])
    body = {"outcome": "completed", "last_seq": 0, "text": text}
    return post(api, f"attempts/{attempt['id']}/complete", body, machine["token"], key="done")["message"]


def _settle(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")


def _poke(api, task_id, text=STOPPED):
    """Tico's own notice to ops on the task, nobody's act behind it: the never-stuck ladder's second try."""
    with api.app.state.store.transaction() as c:
        row = E.task(c, task_id)
        return task_wakes.add(c, row, "bot:ops", row["visit"], text, try_n=1)


def _people_read(api, task_id, room):
    """Every read of the room a person has: the thread, the chat page and the task page."""
    with api.app.state.store.read() as c:
        timeline = threads.timeline(c, threads.actor_viewer(c, ANA), room, flat=True)["messages"]
    return {
        "thread": [m.get("body") for m in timeline],
        "chat": [m["body"] for m in get(api, f"conversations/{room}/messages")],
        "task": [m["body"] for m in get(api, f"tasks/{task_id}")["messages"]],
    }


def _counters(api, room):
    with api.app.state.store.read() as c:
        return _counters_of(c, room)


def test_a_notice_reaches_the_bot_in_its_run_and_no_persons_read_of_the_room(api):
    machine = _start(api)
    task, room = _task(api)
    _settle(api)
    before = _counters(api, room)
    notice = _poke(api, task["id"])
    assert _counters(api, room) == before  # nothing new for anyone to read
    for read in _people_read(api, task["id"], room).values():
        assert not any(STOPPED in (body or "") for body in read)
    attempt = claim(api, machine)
    assert attempt["message"]["id"] == notice
    assert STOPPED in attempt["message"]["body"]
    assert any(STOPPED in m["body"] for m in attempt["history"])  # its room history, as before


def test_a_bare_acknowledgement_of_a_notice_is_kept_on_the_run_not_posted(api):
    machine = _start(api)
    task, room = _task(api)
    _settle(api)
    _poke(api, task["id"])
    attempt = claim(api, machine)
    assert _finish(api, machine, attempt, "Noted. Picking it up again.") is None
    for read in _people_read(api, task["id"], room).values():
        assert "Noted. Picking it up again." not in read
    with api.app.state.store.read() as c:
        kept = c.execute("SELECT final_text FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
    assert kept == "Noted. Picking it up again."
    # Why it acted is one tap away, on the run.
    runs = get(api, f"tasks/{task['id']}/run-view")["runs"]
    assert STOPPED in next(r for r in runs if r["id"] == attempt["id"])["told"]


def test_a_reply_that_names_a_person_still_shows_and_quotes_nothing_hidden(api):
    machine = _start(api)
    task, room = _task(api)
    _settle(api)
    _poke(api, task["id"])
    attempt = claim(api, machine)
    said = "@ana which reports page: the old one or the new one?"
    reply = _finish(api, machine, attempt, said)
    assert reply and reply["body"] == said
    reads = _people_read(api, task["id"], room)
    assert said in reads["thread"] and said in reads["chat"]
    with api.app.state.store.read() as c:
        timeline = threads.timeline(c, threads.actor_viewer(c, ANA), room, flat=True)["messages"]
    shown = next(m for m in timeline if m.get("body") == said)
    assert shown["reply_to"] is None  # never "Message not shown"


def test_the_reply_to_a_persons_own_act_still_shows(api):
    """Ana filed the task: the run that answers its request (its first wake) answers her."""
    machine = _start(api)
    task, room = _task(api)
    attempt = claim(api, machine)
    reply = _finish(api, machine, attempt, "On it: adding the button next to Export PDF.")
    assert reply
    assert "On it: adding the button next to Export PDF." in _people_read(api, task["id"], room)["thread"]


def test_the_run_that_finishes_the_task_still_reports_to_the_person(api):
    machine = _start(api)
    task, room = _task(api)
    _settle(api)
    _poke(api, task["id"])
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "fixture-thread"}, machine["token"])
    post(api, f"tasks/{task['id']}/done", {"version": attempt["task"]["version"]}, attempt["token"])
    body = {"outcome": "completed", "last_seq": 0, "text": "Done: the button is live."}
    assert post(api, f"attempts/{attempt['id']}/complete", body, machine["token"], key="done")["message"]
    assert "Done: the button is live." in _people_read(api, task["id"], room)["thread"]


def test_the_live_stream_brings_a_person_neither_the_notice_nor_a_quote_of_it(api, live):
    machine = _start(api)
    task, room = _task(api)
    _settle(api)
    ana = live("ana-test", "?topics=messages")
    ready_stream(ana)
    _poke(api, task["id"])
    attempt = claim(api, machine)
    said = "@ana should the button sit left of Export PDF?"
    _finish(api, machine, attempt, said)
    shown = ana.until(lambda e: e["event"] == "messages" and (e["data"].get("message") or {}).get("body") == said)
    assert shown["data"]["message"]["reply_to"] is None
    assert STOPPED not in json.dumps(ana.seen)


# ----------------------------------------------------------------------------- what was already written
MIGRATION = "backend.migrations.20261116234549_run_notices_out_of_chats"
BASELINE = Path(__file__).resolve().parents[1] / "migrations" / "baseline.sql"


def _old_stamp(c):
    """The stamp trigger as it was before the migration: every message but a carrier gets a seq."""
    line = next(x for x in BASELINE.read_text().splitlines() if x.startswith("CREATE TRIGGER threads_messages_stamp "))
    c.execute("DROP TRIGGER threads_messages_stamp")
    c.execute(line)


def _answer(c, room, notice, text, *, turn=True, finished=False, task_id=None):
    """The bot's answer to `notice` as the completion posted it (refs.turn_id, its run's id), or as the bot sent it
    itself (`turn=False`, no run named)."""
    run, now = H.new_id(), H.now()
    if turn:
        c.execute(
            "INSERT INTO attempts (id, bot_id, runner_id, generation, token_hash, lease_until, state, created_at, "
            "started_at, finished_at, task_id, final_text) VALUES (?,?,?,0,?,?,'completed',?,?,?,?,?)",
            (run, "cpo", "r1", H.new_id(), now, now, now, now, task_id, text),
        )
    if finished:  # during the run, before its completion posts the answer
        E.move(c, task_id, E.DONE, "bot:cpo", "Shipped", seen_version=E.task(c, task_id)["version"])
    sent = H.say(
        c,
        "bot:cpo",
        ANA,
        text,
        conversation_id=room,
        in_reply_to=notice,
        refs={"turn_id": run, "task": task_id} if turn else {"task": task_id},
    )
    return sent["id"]


def _counters_of(c, room):
    return tuple(c.execute("SELECT last_seq, last_unread_seq FROM conversations WHERE id=?", (room,)).fetchone())


@contextmanager
def _caused_by(actor):
    """Tico's notices written while `actor` acts: each carries who caused it (refs.caused_by)."""
    token = wake_rules.CAUSE.set(actor)
    try:
        yield
    finally:
        wake_rules.CAUSE.reset(token)


def _shown(c, room):
    rows = threads.timeline(c, threads.actor_viewer(c, ANA), room, flat=True)["messages"]
    return [m.get("body") for m in rows if not m.get("hidden")]


def test_the_migration_takes_notices_and_bare_answers_out_of_peoples_timelines(company):
    """Given a task room written the old way (every notice with a seq, the bot's answers to them posted), when the
    migration runs, then the notices and the answer that named nobody leave Ana's timeline and the unread mark, and
    her own request stays, and every answer that was for her: the one to the task's request, one naming her, one the bot sent itself, one
    to her own act, the one that finished the task. A notice written after it has no seq. Running it again changes nothing."""
    with company.transaction() as c:
        X.bot(c, "cpo")
        X.place(c, "cpo")
        _old_stamp(c)
        task = X.new_task(c, ANA, "Add the export button", assignee="bot:cpo")
        room = task["conversation_id"]
        request = c.execute("SELECT id FROM messages WHERE wake_key LIKE ?", (task["id"] + ":%",)).fetchone()[0]
        real = _answer(c, room, request, "Which page?", task_id=task["id"])
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
        poke = task_wakes.add(c, task, "bot:cpo", task["visit"], STOPPED, try_n=1)
        bare = _answer(c, room, poke, "Noted, back on it.", task_id=task["id"])
        named = _answer(c, room, poke, "@ana the old reports page or the new one?", task_id=task["id"])
        sent = _answer(c, room, poke, "I sent this one myself.", turn=False, task_id=task["id"])
        with _caused_by(ANA):
            asked = task_wakes.add(c, task, "bot:cpo", task["visit"], "Ana moved it back.", try_n=2)
        hers = _answer(c, room, asked, "Back on it, Ana: fixing the label first.", task_id=task["id"])
        last = task_wakes.add(c, task, "bot:cpo", task["visit"], "Still open.", try_n=3)
        done = _answer(c, room, last, "Done: the button is live.", finished=True, task_id=task["id"])
        quiet = task_wakes.add(c, task, "bot:cpo", task["visit"], "Late in To do.", try_n=4)
        bare_seq = c.execute("SELECT seq FROM messages WHERE id=?", (bare,)).fetchone()[0]
        assert {STOPPED, "Noted, back on it.", "Late in To do."} <= {b.split("\n")[0] for b in _shown(c, room)}
        assert _counters_of(c, room)[1] > bare_seq  # unread, with notices to read

        migration = importlib.import_module(MIGRATION)
        migration.up(c)
        migration.up(c)  # again: nothing more

        kept = [m for m in _shown(c, room) if m]
        assert kept == [
            "What to do and how to tell it is done.",  # Ana's own request, the task's first message
            "Which page?",
            "@ana the old reports page or the new one?",
            "I sent this one myself.",
            "Back on it, Ana: fixing the label first.",
            "Done: the button is live.",
        ]
        newest = c.execute("SELECT seq FROM messages WHERE id=?", (done,)).fetchone()[0]
        assert _counters_of(c, room) == (newest, newest)  # the last notice no longer counts
        states = {s for (s,) in c.execute("SELECT state FROM message_deliveries WHERE message_id=?", (bare,))}
        assert states <= {"moot", "read"}
        assert c.execute("SELECT final_text FROM attempts WHERE final_text=?", ("Noted, back on it.",)).fetchone()
        for notice in (poke, asked, last, quiet):  # still stored: each is what its run started from
            assert c.execute("SELECT seq FROM messages WHERE id=?", (notice,)).fetchone()[0] is None
        assert {real, named, sent, hers, done} == {
            r[0] for r in c.execute("SELECT id FROM messages WHERE conversation_id=? AND seq IS NOT NULL", (room,))
        }
        fresh = task_wakes.add(c, task, "bot:cpo", task["visit"], "One more.", try_n=5)
        assert c.execute("SELECT seq FROM messages WHERE id=?", (fresh,)).fetchone()[0] is None
        assert _counters_of(c, room) == (newest, newest)


def _old_room(c):
    """cpo's task from Ana, written with the old stamp trigger, and its first notice already run."""
    X.bot(c, "cpo")
    X.place(c, "cpo")
    _old_stamp(c)
    task = X.new_task(c, ANA, "Add the export button", assignee="bot:cpo")
    c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    return task, task["conversation_id"]


def _seq(c, message_id):
    return c.execute("SELECT seq FROM messages WHERE id=?", (message_id,)).fetchone()[0]


def test_the_report_of_a_run_that_finished_its_task_stays_after_the_task_is_reopened(company):
    """Whether a run finished its task is read from the task's moves (its visits), not its latest done or closed time,
    which a reopen clears."""
    with company.transaction() as c:
        task, room = _old_room(c)
        poke = task_wakes.add(c, task, "bot:cpo", task["visit"], STOPPED, try_n=1)
        done = _answer(c, room, poke, "Done: the real deliverable is ready.", finished=True, task_id=task["id"])
        stage = c.execute("SELECT id FROM stages WHERE kind='work' ORDER BY position LIMIT 1").fetchone()[0]
        E.move(c, task["id"], stage, ANA, "One more thing", seen_version=E.task(c, task["id"])["version"])
        assert E.task(c, task["id"])["done_at"] is None
        importlib.import_module(MIGRATION).up(c)
        assert _seq(c, done) is not None


def test_an_answer_to_a_persons_message_carried_into_the_run_stays(company):
    """A person's message folded into a notice's run through a carrier (the keeper's copy: refs.carries, refs.from) is
    a person's words in the run, as the completion reads it (H.in_person_name_sql)."""
    with company.transaction() as c:
        task, room = _old_room(c)
        poke = task_wakes.add(c, task, "bot:cpo", task["visit"], STOPPED, try_n=1)
        asked = H.say(c, ANA, "bot:cpo", "What is the answer?", conversation_id=room)["id"]
        reply = _answer(c, room, poke, "The answer to your folded-in question is 42.", task_id=task["id"])
        run = H.message(c, reply)["refs"]["turn_id"]
        carrier = H.new_id()
        c.execute(
            "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at,revision,restricted) "
            "VALUES(?,?,?,?,'notice','What is the answer?',?,?,0,1)",
            (carrier, room, H.KEEPER, "bot:cpo", json.dumps({"carries": asked, "from": ANA}), H.now()),
        )
        c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (run, carrier))
        importlib.import_module(MIGRATION).up(c)
        assert _seq(c, reply) is not None


def test_the_unread_mark_keeps_a_bot_post_people_were_delivered(company):
    """A bot's post in a group DM names nobody but is delivered to its people: once a later notice leaves, the room's
    unread mark still covers the post, so the sidebar's summary and the thread's own count agree."""
    with company.transaction() as c:
        X.bot(c, "cpo")
        X.place(c, "cpo")
        _old_stamp(c)
        ana = threads.actor_viewer(c, ANA)
        room = threads.dm(c, ana, ["human:ben", "bot:cpo"], title="Launch")["id"]
        post = threads.post(c, threads.actor_viewer(c, "bot:cpo"), room, "The report is ready.")
        H.say(c, H.KEEPER, "bot:cpo", "Late in To do.", conversation_id=room, kind="notice")
        importlib.import_module(MIGRATION).up(c)
        assert _counters_of(c, room)[1] == _seq(c, post["id"])
        ben = threads.actor_viewer(c, "human:ben")
        assert threads.subtree_summaries(c, ben, [room])[room]["unread"] == threads.unread(c, ben, room)["unread"] == 1


def test_the_answers_query_follows_the_reply_index_whatever_the_statistics(company):
    """Under the migration's write lock the answers are found from each notice through messages_reply_kind, never by
    walking every message of the notice's bot, with no statistics and with statistics that favour the sender index."""
    migration = importlib.import_module(MIGRATION)
    with company.transaction() as c:

        def plan():
            return " | ".join(r[-1] for r in c.execute("EXPLAIN QUERY PLAN " + migration.ANSWERS))

        c.execute("ANALYZE sqlite_schema")  # makes sqlite_stat1 where there is none, and loads what it holds
        c.execute("DELETE FROM sqlite_stat1")
        c.execute("ANALYZE sqlite_schema")
        assert "m USING INDEX messages_reply_kind" in plan(), plan()
        c.execute(
            "INSERT INTO sqlite_stat1(tbl,idx,stat) VALUES ('messages','messages_reply_kind','1000000 500000 500000'),"
            "('messages','messages_from_kind','1000000 1 1')"
        )
        c.execute("ANALYZE sqlite_schema")  # stale statistics that favour the sender index
        assert "m USING INDEX messages_reply_kind" in plan(), plan()
