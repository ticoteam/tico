"""How long a task waited on what (backend/wait_spans.py, `task_wait_spans`) and the saved queries over it in tico sql
(backend/sql.py DERIVED): a span opens when the task's headline wait starts and closes when it ends, the engine's own
writes keep it, the never-stuck pass catches a flip no write made, and each saved query answers with numbers."""

import contextlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import answers as A
from backend import never_stuck as NS
from backend import task_engine as E
from backend import wait_spans
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import post
from backend.tests.helpers.assistant import assistant_turn
from backend.tests.helpers.sql import query

ANA = X.ANA
BUILDER, MANAGER = "bot:builder", "bot:manager"
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        X.bot(conn, "builder")
        X.bot(conn, "manager")
        X.pipeline(conn, manager=MANAGER)
        yield conn


def spans(c, task_id):
    """The task's spans, oldest first: (kind, who, whether still open)."""
    return [
        (r[0], r[1], r[2] is None)
        for r in c.execute(
            "SELECT kind, who, closed_at FROM task_wait_spans WHERE task_id=? ORDER BY opened_at, rowid", (task_id,)
        )
    ]


def test_a_span_opens_when_a_wait_starts_and_closes_when_it_ends(c, time_machine):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    assert spans(c, task_id) == [("bot", BUILDER, True)]
    time_machine.shift(timedelta(minutes=5))
    asked = X.ok_ask(c, task_id, BUILDER, "ana", "OK the draft?")
    assert spans(c, task_id) == [("bot", BUILDER, False), ("person", ANA, True)]
    time_machine.shift(timedelta(hours=2))
    A.answer(c, task_id, asked["requirement_id"], ANA, "ok")
    assert spans(c, task_id) == [("bot", BUILDER, False), ("person", ANA, False), ("bot", BUILDER, True)]
    opened, closed = c.execute(
        "SELECT opened_at, closed_at FROM task_wait_spans WHERE task_id=? AND kind='person'", (task_id,)
    ).fetchone()
    assert H.parse_ts(closed) - H.parse_ts(opened) == timedelta(hours=2)
    # The same headline again writes nothing: one row per change.
    changes = c.total_changes
    assert wait_spans.sync(c, task_id) is False and c.total_changes == changes
    # A snooze is a wait of its own, and its end hands the task back.
    E.snooze(c, task_id, ANA, "After the launch")
    assert spans(c, task_id)[-1] == ("snoozed", ANA, True)
    E.unsnooze(c, task_id, ANA)
    assert spans(c, task_id)[-1] == ("bot", BUILDER, True)
    X.say_done(c, E.task(c, task_id))
    E.advance(c, task_id)
    assert E.task(c, task_id)["status"] == "done"
    assert [open_ for *_, open_ in spans(c, task_id)] == [False] * 5


def test_the_never_stuck_pass_catches_what_no_write_changed(company, c):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    # The builder's computer goes: nothing wrote to the task, and its bot is Paused now.
    c.execute("DELETE FROM assignments WHERE bot='builder'")
    assert spans(c, task_id) == [("bot", BUILDER, True)]
    NS.check(c)
    assert spans(c, task_id) == [("bot", BUILDER, False), ("paused", BUILDER, True)]
    NS.check(c)
    assert len(spans(c, task_id)) == 2
    # A span a path left open on a task that ended is closed by the pass.
    X.place(c, "builder")
    done_id = X.new_task(c, ANA, "Post the note", assignee=BUILDER)["id"]
    X.say_done(c, E.task(c, done_id))
    E.advance(c, done_id)
    c.execute(
        "INSERT INTO task_wait_spans (id, task_id, visit_id, kind, who, opened_at) VALUES ('left', ?, NULL, 'bot', ?, ?)",
        (done_id, BUILDER, H.now()),
    )
    NS.check(c)
    assert all(not open_ for *_, open_ in spans(c, done_id))
    # A snoozed task is no task the ladder checks, and its span is still kept right.
    E.snooze(c, task_id, ANA, "After the launch")
    c.execute("UPDATE task_wait_spans SET kind='bot' WHERE task_id=? AND closed_at IS NULL", (task_id,))
    NS.check(c)
    assert spans(c, task_id)[-1] == ("snoozed", ANA, True)


def test_a_pass_with_every_span_current_stays_in_its_read_snapshot(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as c:
        X.bot(c, "builder")
        X.bot(c, "manager")
        X.pipeline(c, manager=MANAGER)
        X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)
        NS.check(c)  # the first pass keeps the visit's progress mark
    holds = []

    @contextlib.contextmanager
    def locked():
        with company.transaction() as c:
            holds.append(1)
            yield c

    NS.check_read_first(company, locked, H.now(), [])
    assert holds == []


def rows(api, sql, token):
    out = query(api, sql, token)
    return [dict(zip(out["columns"], row)) for row in out["rows"]]


WEEK = "2030-01-07"


def test_each_saved_query_answers_with_numbers(api):
    store = api.app.state.store
    # Ben's Assistant turn on his message, and a task it hands to ops.
    _, attempt = assistant_turn(api, "ben-test", "Have ops draft the launch post")
    made = post(
        api, "tasks", {"owner": "ops", "title": "Draft the launch post", "body": "For Oct 1."}, token=attempt["token"]
    )
    task_id = made["id"]
    room = post(api, "chat/ops", {"text": "Morning"})["conversation_id"]
    with store.transaction() as c:
        c.execute(
            "UPDATE attempts SET result=json_set(coalesce(result,'{}'),'$.timings',json(?)) WHERE id=?",
            (json.dumps({"claimed_at": 1, "phases": {"first_text": 1500}, "send_to_claim_ms": 500}), attempt["id"]),
        )
        # Ana answers ops' OK that week.
        asked = X.ok_ask(c, task_id, "bot:ops", "ana", "OK the outline?")["requirement_id"]
        A.answer(c, task_id, asked, ANA, "ok")
        c.execute(
            "UPDATE history_events SET created_at=? WHERE subject_id=? AND kind='answer'",
            (f"{WEEK}T09:30:00.000000Z", task_id),
        )
        # Ops acts once that week; its SQL read and a refusal are no actions.
        for action in ("task.update", "sql.query", "refused"):
            H.event(c, "bot:ops", action, "wait-spans-marker", {})
        c.execute("UPDATE events SET created_at=? WHERE target='wait-spans-marker'", (f"{WEEK}T12:00:00.000000Z",))
        # In Ana's own room with ops, ops asks about the task (its refs a list) and she replies: the reply is a touch on
        # the task the question names.
        chat = H.conversation(c, room)
        question = H._write_message(c, "bot:ops", ANA, "Which outline?", chat, "say", {"task": [task_id]}, None, None)
        reply = H._write_message(c, ANA, "bot:ops", "The short one", chat, "say", {}, question["id"], None)
    touches = rows(
        api, f"SELECT kind, at FROM human_touches_v WHERE task_id='{task_id}' AND person_id='{ANA}'", "ana-test"
    )
    assert {"kind": "answer", "at": f"{WEEK}T09:30:00.000000Z"} in touches
    assert {"kind": "message", "at": reply["created_at"]} in touches
    decided = {w["week"]: w for w in rows(api, "SELECT * FROM human_decisions_weekly", "ana-test")}
    assert decided[WEEK] == {"week": WEEK, "decisions": 1, "agent_actions": 1, "per_1000_actions": 1000.0}
    [first_text] = rows(api, "SELECT turns, p50_ms, p90_ms FROM first_text_weekly", "ben-test")
    assert first_text == {"turns": 1, "p50_ms": 2000, "p90_ms": 2000}


def test_a_span_names_only_a_task_its_reader_may_read(api):
    store = api.app.state.store
    with store.transaction() as c:
        mine = H.task_create(c, "human:ben", "Plan the launch", "Ours.", "human:ben", lint=False)["id"]
        hidden = H.task_create(c, "human:ben", "Price the buyout", "Only me.", "human:ben", private=True, lint=False)[
            "id"
        ]
        c.execute(
            "INSERT INTO task_wait_spans (id, task_id, kind, who, opened_at, closed_at) VALUES ('s1', ?, 'tasks', ?, ?, ?)",
            (mine, hidden, H.now(), H.now()),
        )
    sql = f"SELECT who FROM task_wait_spans WHERE task_id='{mine}' AND kind='tasks'"
    assert rows(api, sql, "ben-test") == [{"who": hidden}]
    assert rows(api, sql, "ana-test") == [{"who": ""}]


def test_a_span_hides_an_agent_its_reader_may_not_see(api):
    from backend.tests.helpers.api import as_member, restrict

    as_member(api, "ben@acme.example")
    store = api.app.state.store
    with store.transaction() as c:
        X.bot(c, "secret")
        restrict(c, "secret", people=["cara"])
        mine = H.task_create(c, "human:ben", "Plan the launch", "Ours.", "human:ben", lint=False)["id"]
        c.execute(
            "INSERT INTO task_wait_spans (id, task_id, kind, who, opened_at, closed_at) VALUES ('s2', ?, 'bot', ?, ?, ?)",
            (mine, "bot:secret", H.now(), H.now()),
        )
    sql = "SELECT who FROM task_wait_spans WHERE id='s2'"
    assert rows(api, sql, "ben-test") == [{"who": ""}]
    assert rows(api, sql, "ana-test") == [{"who": "bot:secret"}]


def test_an_ok_given_to_someone_waits_on_them_from_the_give(c, time_machine):
    from backend import waiting_on as W

    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    asked = X.ok_ask(c, task_id, BUILDER, "ana", "OK the draft?")["requirement_id"]
    time_machine.shift(timedelta(hours=3))
    A.give_to(c, task_id, asked, ANA, "human:ben")
    [head] = [w for w in W.waiting_on(c, task_id) if w.requirement_id == asked]
    assert (head.who, H.parse_ts(head.since)) == ("human:ben", START + timedelta(hours=3))
    assert spans(c, task_id)[-1] == ("person", "human:ben", True)


def test_taking_back_an_ok_that_moved_the_task_is_one_decision(api):
    store = api.app.state.store
    made = post(api, "tasks", {"owner": "ops", "title": "Post the note", "body": "Today."})
    task_id = made["id"]
    with store.transaction() as c:
        X.say_done(c, E.task(c, task_id), "bot:ops")
        asked = X.ok_ask(c, task_id, "bot:ops", "ana", "OK to post?")["requirement_id"]
        answer_id = A.answer(c, task_id, asked, ANA, "ok")["answer_id"]
        assert E.task(c, task_id)["visit"]["entered_how"] == "met"  # the OK moved it on
        A.undo(c, answer_id, ANA)
        kinds = [
            r[0]
            for r in c.execute(
                "SELECT kind FROM history_events WHERE subject_id=? AND actor_id=? ORDER BY created_at", (task_id, ANA)
            )
        ]
        assert kinds[-3:] == ["answer", "answer_undone", "move"]
        c.execute(
            "UPDATE history_events SET created_at=? WHERE subject_id=? AND actor_id=? AND (kind <> 'move' "
            "OR json_extract(data, '$.undone_answer') IS NOT NULL)",
            (f"{WEEK2}T10:00:00.000000Z", task_id, ANA),
        )
    [week] = rows(api, f"SELECT decisions FROM human_decisions_weekly WHERE week='{WEEK2}'", "ana-test")
    assert week == {"decisions": 2}


WEEK2 = "2030-02-04"


def test_an_answer_that_ends_an_agents_snooze_is_one_decision_and_a_person_unsnoozing_is_another(api):
    store = api.app.state.store
    task_id = post(api, "tasks", {"owner": "ops", "title": "Price the plan", "body": "Today."})["id"]
    with store.transaction() as c:
        asked = X.ok_ask(c, task_id, "bot:ops", "ana", "OK the price?")["requirement_id"]
        version = E.task(c, task_id)["version"]
        E.snooze(c, task_id, "bot:ops", "Waiting for Ana", seen_version=version)
        A.answer(c, task_id, asked, ANA, "ok")  # ends the agent's snooze by itself
        assert not E.task(c, task_id)["snooze"]
        E.snooze(c, task_id, "bot:ops", "Waiting for the bank", seen_version=E.task(c, task_id)["version"])
        E.unsnooze(c, task_id, ANA, "Go on")  # her own decision
        c.execute(
            "UPDATE history_events SET created_at=? WHERE subject_id=? AND actor_id=? AND kind <> 'move'",
            (f"{WEEK3}T10:00:00.000000Z", task_id, ANA),
        )
    [week] = rows(api, f"SELECT decisions FROM human_decisions_weekly WHERE week='{WEEK3}'", "ana-test")
    assert week == {"decisions": 2}


def test_an_ok_added_to_the_stage_later_waits_from_when_it_was_added(c, time_machine):
    from backend import waiting_on as W

    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    time_machine.shift(timedelta(hours=2))
    added = X.requirement(c, E.task(c, task_id)["stage_id"], "ok", person="human:ben", line="Ben signs off")
    [head] = [w for w in W.waiting_on(c, task_id) if w.requirement_id == added]
    assert (head.who, H.parse_ts(head.since)) == ("human:ben", START + timedelta(hours=2))


WEEK3 = "2030-03-04"


def test_a_messages_task_is_read_from_its_refs_as_the_server_reads_it():
    """human_touches_v names a message's task (and its parent's) as hubdb.refs_task_id does, whatever shape the refs."""
    import sqlite3

    from backend import sql as S

    shapes = [
        {"task": "t1"},
        {"task": " t1 "},
        {"task": ["", "t1"]},
        {"task": [" ", 7, "t1", "t2"]},
        {"task": ""},
        {"task": [""], "task_id": "t2"},
        {"task_id": ["", "t2"]},
        {"task": 5},
        {"task": "\u00a0t1\u3000"},
        {"task": ["\u2003\u205f", "\tt1\n"]},
        {},
    ]
    conn = sqlite3.connect(":memory:")
    for refs in shapes:
        [got] = conn.execute(
            f"SELECT {S._refs_task_sql('m')} FROM (SELECT ? AS refs) m", (json.dumps(refs),)
        ).fetchone()
        assert got == H.refs_task_id(refs), refs


def test_what_a_close_an_end_or_run_now_makes_by_itself_is_no_decision_of_its_own(api):
    store = api.app.state.store
    with store.transaction() as c:
        parent = H.task_create(c, ANA, "Launch the site", "Soon.", "bot:ops", lint=False)["id"]
        first = H.task_create(c, "bot:ops", "Check the copy", "x", "bot:ops", parent_id=parent, lint=False)["id"]
        E.snooze(c, parent, "bot:ops", "Waiting for the check", seen_version=E.task(c, parent)["version"])
        E.move(c, first, E.CLOSED, ANA, "Not needed")  # the last it filed: ends the snooze that waited for it
        assert not E.task(c, parent)["snooze"]
        second = H.task_create(c, "bot:ops", "Check the links", "x", "bot:ops", parent_id=parent, lint=False)["id"]
        version = E.task(c, parent)["version"]
        E.move(c, parent, E.CLOSED, ANA, "Dropped", seen_version=version)  # closes the second with it
        assert E.task(c, second)["status"] == "closed"
        # Run now on a task its agent snoozed ends the snooze by itself.
        invoice = H.task_create(c, ANA, "Send the invoice", "Today.", "bot:ops", lint=False)["id"]
        E.snooze(c, invoice, "bot:ops", "After the bank", seen_version=E.task(c, invoice)["version"])
        H.task_run_now(c, ANA, invoice)
        assert not E.task(c, invoice)["snooze"]
        ended = c.execute(
            "SELECT json_extract(data, '$.consequence') FROM history_events WHERE kind='unsnooze' AND actor_id=? "
            "AND subject_id IN (?,?) ORDER BY created_at",
            (ANA, parent, invoice),
        ).fetchall()
        assert [r[0] for r in ended] == ["filed_ended", "run_now"]
        c.execute(
            "UPDATE history_events SET created_at=? WHERE actor_id=? AND subject_id IN (?,?,?,?) "
            "AND coalesce(json_extract(data, '$.move'), '') <> 'new'",
            (f"{WEEK4}T10:00:00.000000Z", ANA, parent, first, second, invoice),
        )
    [week] = rows(api, f"SELECT decisions FROM human_decisions_weekly WHERE week='{WEEK4}'", "ana-test")
    assert week == {"decisions": 2}  # her two closes


WEEK4 = "2030-04-01"
