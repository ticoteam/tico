"""The ask metrics in tico sql (backend/sql.py DERIVED): repeat_ask_weekly, keyed by the requirement a person answers
again, and ask_answer_weekly, from an ask (or its give) to its first answer and from a ❓ to the reply that cleared it.
Synthetic rows only (backend/tests/helpers/engine.py), on a clock the test moves."""

from datetime import datetime, timedelta, timezone

import pytest

from backend import answers as A
from backend import question_marks as QM
from backend import sql as S
from backend import task_engine as E
from backend.auth import Auth, Identity
from backend.store import H, Problem
from backend.tests.helpers import engine as X

ANA, BEN = X.ANA, "human:ben"
BUILDER = "bot:builder"
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)
STAGES = (
    {
        "name": "Draft",
        "owner": "assignee",
        "requirements": [{"kind": "owner_done"}, {"kind": "ok", "person": ANA, "line": "Ana OKs the draft"}],
    },
)


@pytest.fixture
def drafts(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        X.bot(conn, "builder")
        X.bot(conn, "manager")
        X.pipeline(conn, "drafts", "Drafts", STAGES, manager="bot:manager")
    return company


def view(company, sql, who=Identity(ANA, "owner")):
    """`sql` in tico sql, read as `who` (backend/sql.py), after what the test wrote was committed."""
    with company.read() as c:
        conn = S.connect(company.settings.db_path, c, Auth(company), who)
        try:
            out = S.run(conn, sql, {}, 100, 20)
        finally:
            conn.close()
    return [dict(zip(out["columns"], row)) for row in out["rows"]]


def requirement(c, row, kind):
    return c.execute(
        "SELECT id FROM requirements WHERE stage_id=? AND kind=? AND withdrawn_at IS NULL", (row["stage_id"], kind)
    ).fetchone()[0]


def round_trip(c, task_id, answers, clock):
    """The stage's OK answered by each of `answers` in turn, (person, value), the owner saying done before each: after
    a send back, saying done again asks the OK again."""
    for person, value in answers:
        clock.shift(timedelta(minutes=1))
        row = E.task(c, task_id)
        A.answer(c, task_id, requirement(c, row, "owner_done"), BUILDER, "done", seen_version=row["version"])
        A.answer(c, task_id, requirement(c, row, "ok"), person, value, "Not yet" if value == "no" else None)


def test_a_repeat_ask_is_the_same_requirement_answered_again_by_the_same_person(drafts, time_machine):
    with drafts.transaction() as c:
        _repeats(c, time_machine)
    assert view(drafts, "SELECT answers, repeats, repeat_pct FROM repeat_ask_weekly") == [
        {"answers": 6, "repeats": 2, "repeat_pct": 33.3}
    ]


def _repeats(c, clock):
    # Ana sends the draft back, then OKs it when asked again: one repeat.
    again = X.new_task(c, ANA, "Draft the launch note", "drafts", assignee=BUILDER)["id"]
    round_trip(c, again, [(ANA, "no"), (ANA, "ok")], clock)
    # Given to Ben: he sends it back and OKs it the second time; Ana never answered it. One repeat, his.
    given = X.new_task(c, ANA, "Draft the price note", "drafts", assignee=BUILDER)["id"]
    row = E.task(c, given)
    A.give_to(c, given, requirement(c, row, "ok"), ANA, BEN)
    round_trip(c, given, [(BEN, "no"), (BEN, "ok")], clock)
    # An OK taken back is no answer: answering it again is not a repeat.
    undone = X.new_task(c, ANA, "Draft the FAQ", "drafts", assignee=BUILDER)["id"]
    row = E.task(c, undone)
    first = A.answer(c, undone, requirement(c, row, "ok"), ANA, "ok")["answer_id"]
    clock.shift(timedelta(minutes=1))
    A.undo(c, first, ANA)
    clock.shift(timedelta(minutes=1))
    A.answer(c, undone, requirement(c, row, "ok"), ANA, "ok")
    # An ask added to the task, answered once, and a ❓ (a new ask every time): no repeat.
    asked = X.ok_ask(c, undone, BUILDER, "ana", "OK the headline?")["requirement_id"]
    A.answer(c, undone, asked, ANA, "ok")


def test_ask_to_answer_counts_from_the_ask_or_its_give_and_the_median_of_an_even_count_is_the_middle_two(
    drafts, time_machine
):
    with drafts.transaction() as c:
        _asks(c, time_machine)
    rows = view(drafts, "SELECT source, answers, median_s FROM ask_answer_weekly ORDER BY source")
    assert rows == [
        {"source": "ask", "answers": 2, "median_s": 2700},
        {"source": "question", "answers": 1, "median_s": 1200},
    ]


def _asks(c, time_machine):
    task_id = X.new_task(c, ANA, "Draft the launch note", "drafts", assignee=BUILDER)["id"]
    asked = X.ok_ask(c, task_id, BUILDER, "ana", "OK the outline?")["requirement_id"]
    time_machine.shift(timedelta(hours=1))
    A.answer(c, task_id, asked, ANA, "ok")  # an hour
    given = X.ok_ask(c, task_id, BUILDER, "ana", "OK the headline?")["requirement_id"]
    time_machine.shift(timedelta(hours=5))
    A.give_to(c, task_id, given, ANA, BEN)
    time_machine.shift(timedelta(minutes=30))
    A.answer(c, task_id, given, BEN, "no", "Shorter")  # half an hour after the give, not five and a half
    # A ❓, answered by Ana's reply in the task's chat 20 minutes later.
    QM.ask(c, BUILDER, task_id, ANA, "Which photo goes on top?", checked=False)
    time_machine.shift(timedelta(minutes=20))
    H.task_comment(c, ANA, task_id, "The red one")
    # An ask in Product's Validate waits for its page to pass first: it is left out.
    held = X.new_task(c, ANA, "Validate the launch", assignee=BUILDER)["id"]
    H._task_event(c, held, BUILDER, "move", None, "product-validate", None, data={"move": "forward"})
    H._task_event(c, held, BUILDER, "requirement", None, "r-held", "OK it?", data={"kind": "ok", "person": ANA})
    H._task_event(c, held, ANA, "answer", None, "ok", None, data={"requirement": "r-held", "answer_id": "a-held"})


def test_a_question_mark_is_read_with_its_message_and_never_its_words(drafts):
    company = drafts
    with company.transaction() as c:
        mine = H.task_create(c, BEN, "Price the buyout", "Only me.", BEN, private=True, lint=False)["id"]
        QM.ask(c, BUILDER, mine, BEN, "Which price?", checked=False)
    sql = f"SELECT person_id, task_id FROM question_marks WHERE task_id='{mine}'"
    assert view(company, sql, Identity(BEN, "human")) == [{"person_id": BEN, "task_id": mine}]
    assert view(company, sql) == []  # Ana may not read Ben's private task or its chat
    with pytest.raises(Problem):
        view(company, "SELECT line FROM question_marks", Identity(BEN, "human"))
