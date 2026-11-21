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


def test_each_saved_query_answers_with_numbers(api):
    store = api.app.state.store
    # Ben hands work to ops through his Assistant; ops finishes it, and his Assistant reports back.
    _, attempt = assistant_turn(api, "ben-test", "Have ops draft the launch post")
    made = post(
        api, "tasks", {"owner": "ops", "title": "Draft the launch post", "body": "For Oct 1."}, token=attempt["token"]
    )
    with store.transaction() as c:
        c.execute(
            "UPDATE attempts SET result=json_set(coalesce(result,'{}'),'$.timings',json(?)) WHERE id=?",
            (json.dumps({"claimed_at": 1, "phases": {"first_text": 1500}, "send_to_claim_ms": 500}), attempt["id"]),
        )
        row = E.task(c, made["id"])
        # Ops asks Ana twice: she answers the first; the second ends without her (ops takes it back).
        first = X.ok_ask(c, made["id"], "bot:ops", "ana", "OK the outline?")["requirement_id"]
        A.answer(c, made["id"], first, ANA, "ok")
        second = X.ok_ask(c, made["id"], "bot:ops", "ana", "OK the headline?")["requirement_id"]
        E.withdraw_requirement(c, second, "bot:ops", "Not needed")
        spans_ = c.execute(
            "SELECT id FROM task_wait_spans WHERE task_id=? AND kind='person' ORDER BY opened_at", (made["id"],)
        ).fetchall()
        # The first wait lasted an hour, the second ten minutes.
        for (span_id,), (opened, closed) in zip(
            spans_,
            (
                ("2030-01-07T09:00:00.000000Z", "2030-01-07T10:00:00.000000Z"),
                ("2030-01-07T11:00:00.000000Z", "2030-01-07T11:10:00.000000Z"),
            ),
        ):
            c.execute("UPDATE task_wait_spans SET opened_at=?, closed_at=? WHERE id=?", (opened, closed, span_id))
        c.execute(
            "UPDATE history_events SET created_at='2030-01-07T09:30:00.000000Z' WHERE subject_id=? AND kind='answer'",
            (made["id"],),
        )
        row = E.task(c, made["id"])
        X.say_done(c, row, "bot:ops")
        E.move(c, made["id"], E.DONE, "bot:ops", seen_version=row["version"])
    touches = rows(
        api, f"SELECT kind FROM human_touches_v WHERE task_id='{made['id']}' AND person_id='{ANA}'", "ana-test"
    )
    assert {"kind": "answer"} in touches
    decided = {w["week"]: w for w in rows(api, "SELECT * FROM human_decisions_weekly", "ana-test")}
    assert decided["2030-01-07"]["decisions"] == 1
    assert any(w["agent_actions"] and w["per_1000_actions"] is not None for w in decided.values())
    [waits] = rows(api, "SELECT * FROM person_wait_weekly", "ana-test")
    assert waits == {
        "week": "2030-01-07",
        "person_waits": 2,
        "answered": 1,
        "ask_answer_median_s": 3600,
        "repeats": 1,
        "repeat_ask_pct": 50.0,
        "avoidable": 1,
        "avoidable_pct": 50.0,
    }
    [first_text] = rows(api, "SELECT turns, p50_ms, p90_ms FROM first_text_weekly", "ben-test")
    assert first_text == {"turns": 1, "p50_ms": 2000, "p90_ms": 2000}
    [reported] = rows(api, "SELECT ended, reported, report_back_pct FROM report_back_weekly", "ben-test")
    assert reported == {"ended": 1, "reported": 1, "report_back_pct": 100.0}
    # Ana handed nothing off: her report-back rate has no weeks.
    assert rows(api, "SELECT * FROM report_back_weekly", "ana-test") == []


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
