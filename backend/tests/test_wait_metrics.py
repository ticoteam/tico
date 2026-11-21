"""The ask metrics in tico sql (backend/sql.py DERIVED): repeat_ask_weekly, one decision for each time an ask is
asked of a person and a repeat only after a recorded re-ask, and ask_answer_weekly, from an ask added to a task (or its
give) to its first answer. Synthetic rows only (backend/tests/helpers/engine.py), on a clock the test moves."""

from datetime import datetime, timedelta, timezone

import pytest

from backend import answers as A
from backend import sql as S
from backend import task_engine as E
from backend.auth import Auth, Identity
from backend.store import H
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
    for n, (person, value) in enumerate(answers):
        if clock:
            clock.shift(timedelta(minutes=1))
        row = E.task(c, task_id)
        done = requirement(c, row, "owner_done")
        A.answer(c, task_id, done, BUILDER, "done", f"Round {n + 1}", seen_version=row["version"])
        if clock:
            clock.shift(timedelta(minutes=1))
        A.answer(c, task_id, requirement(c, row, "ok"), person, value, "Not yet" if value == "no" else None)


def test_a_repeat_is_a_decision_on_an_ask_asked_again_after_a_recorded_re_ask(drafts, time_machine):
    with drafts.transaction() as c:
        clock = time_machine
        # Ana sends the draft back, then OKs it when the owner's done asks it again: one repeat.
        again = X.new_task(c, ANA, "Draft the launch note", "drafts", assignee=BUILDER)["id"]
        round_trip(c, again, [(ANA, "no"), (ANA, "ok")], clock)
        # Given to Ben: he sends it back and OKs it the second time; Ana never decided it. One repeat, his.
        given = X.new_task(c, ANA, "Draft the price note", "drafts", assignee=BUILDER)["id"]
        A.give_to(c, given, requirement(c, E.task(c, given), "ok"), ANA, BEN)
        round_trip(c, given, [(BEN, "no"), (BEN, "ok")], clock)
        # Ana OKs, then OKs again with a note on the same open ask: one decision, no repeat.
        revised = X.new_task(c, ANA, "Draft the FAQ", "drafts", assignee=BUILDER)["id"]
        ok = requirement(c, E.task(c, revised), "ok")
        clock.shift(timedelta(minutes=1))
        A.answer(c, revised, ok, ANA, "ok")
        clock.shift(timedelta(minutes=1))
        A.answer(c, revised, ok, ANA, "ok", "Ship it with the new photo")
        # An OK taken back is no decision: answering it again is the first.
        undone = X.new_task(c, ANA, "Draft the guide", "drafts", assignee=BUILDER)["id"]
        ok = requirement(c, E.task(c, undone), "ok")
        clock.shift(timedelta(minutes=1))
        first = A.answer(c, undone, ok, ANA, "ok")["answer_id"]
        clock.shift(timedelta(minutes=1))
        A.undo(c, first, ANA)
        clock.shift(timedelta(minutes=1))
        A.answer(c, undone, ok, ANA, "ok")
        # Done, reopened, and OK'd again on the new visit: one repeat.
        reopened = X.new_task(c, ANA, "Draft the press note", "drafts", assignee=BUILDER)["id"]
        round_trip(c, reopened, [(ANA, "ok")], clock)
        assert E.task(c, reopened)["status"] == "done"
        clock.shift(timedelta(minutes=1))
        E.move(c, reopened, E.REOPEN, ANA, "One more pass", seen_version=E.task(c, reopened)["version"])
        round_trip(c, reopened, [(ANA, "ok")], clock)
    assert view(drafts, "SELECT answers, repeats, repeat_pct FROM repeat_ask_weekly") == [
        {"answers": 8, "repeats": 3, "repeat_pct": 37.5}
    ]


def test_two_answers_to_one_ask_at_the_same_moment_are_one_decision(drafts):
    # The clock does not move: Ana OKs, then OKs again with a note, on the same open ask.
    with drafts.transaction() as c:
        task_id = X.new_task(c, ANA, "Draft the launch note", "drafts", assignee=BUILDER)["id"]
        ok = requirement(c, E.task(c, task_id), "ok")
        A.answer(c, task_id, ok, ANA, "ok")
        A.answer(c, task_id, ok, ANA, "ok", "With the new photo")
    assert view(drafts, "SELECT answers, repeats FROM repeat_ask_weekly") == [{"answers": 1, "repeats": 0}]


def test_ask_to_answer_counts_from_the_ask_or_its_give_and_the_median_of_an_even_count_is_the_middle_two(
    drafts, time_machine
):
    with drafts.transaction() as c:
        task_id = X.new_task(c, ANA, "Draft the launch note", "drafts", assignee=BUILDER)["id"]
        asked = X.ok_ask(c, task_id, BUILDER, "ana", "OK the outline?")["requirement_id"]
        time_machine.shift(timedelta(hours=1))
        A.answer(c, task_id, asked, ANA, "ok")  # an hour
        given = X.ok_ask(c, task_id, BUILDER, "ana", "OK the headline?")["requirement_id"]
        time_machine.shift(timedelta(hours=5))
        A.give_to(c, task_id, given, ANA, BEN)
        time_machine.shift(timedelta(minutes=30))
        A.answer(c, task_id, given, BEN, "no", "Shorter")  # half an hour after the give, not five and a half
    assert view(drafts, "SELECT answers, median_s FROM ask_answer_weekly") == [{"answers": 2, "median_s": 2700}]


def test_the_median_keeps_fractions_of_a_second(drafts, time_machine):
    with drafts.transaction() as c:
        task_id = X.new_task(c, ANA, "Draft the launch note", "drafts", assignee=BUILDER)["id"]
        for seconds in (1, 2):
            asked = X.ok_ask(c, task_id, BUILDER, "ana", f"OK part {seconds}?")["requirement_id"]
            time_machine.shift(timedelta(seconds=seconds))
            A.answer(c, task_id, asked, ANA, "ok")
    assert view(drafts, "SELECT median_s FROM ask_answer_weekly") == [{"median_s": 1.5}]


def test_an_answer_taken_back_does_not_end_the_ask(drafts, time_machine):
    with drafts.transaction() as c:
        task_id = X.new_task(c, ANA, "Draft the launch note", "drafts", assignee=BUILDER)["id"]
        asked = X.ok_ask(c, task_id, BUILDER, "ana", "OK the outline?")["requirement_id"]
        time_machine.shift(timedelta(hours=1))
        first = A.answer(c, task_id, asked, ANA, "ok")["answer_id"]
        time_machine.shift(timedelta(hours=1))
        A.undo(c, first, ANA)
        time_machine.shift(timedelta(hours=1))
        A.answer(c, task_id, asked, ANA, "ok")
    assert view(drafts, "SELECT answers, median_s FROM ask_answer_weekly") == [{"answers": 1, "median_s": 3 * 3600}]


def test_an_ask_added_in_product_validate_is_left_out_by_its_visits_stage(drafts, time_machine):
    """There an agent's OK waits for its page to pass before it is asked: its ask's time is not when it was added."""
    with drafts.transaction() as c:
        X.pipeline(c, "product", "Product", ({"name": "Validate", "owner": "assignee", "requirements": []},))
        held = X.new_task(c, ANA, "Validate the launch", "product", assignee=BUILDER)["id"]
        assert E.task(c, held)["visit"]["stage_id"] == "product-validate"
        asked = X.ok_ask(c, held, BUILDER, "ana", "OK it?")["requirement_id"]
        time_machine.shift(timedelta(hours=1))
        H._task_event(c, held, ANA, "answer", None, "ok", None, data={"requirement": asked, "answer_id": "a-held"})
    assert view(drafts, "SELECT * FROM ask_answer_weekly") == []


def test_a_new_page_for_the_ask_asks_again_and_only_stored_oks_and_pages_count(drafts, time_machine):
    """Ana sends a page back and OKs the replacement in the same visit: two decisions, one repeat. An ok or no on
    something that is no stored OK or page (an old structured question's reply) is not a decision."""

    def say(c, task_id, actor, kind, old, new, data):
        time_machine.shift(timedelta(minutes=1))
        H._task_event(c, task_id, actor, kind, old, new, None, data=data)

    with drafts.transaction() as c:
        task_id = X.new_task(c, ANA, "Write the spec", "drafts", assignee=BUILDER)["id"]
        page = X.requirement(c, E.task(c, task_id)["stage_id"], "page", person=ANA, line="Ana OKs the spec")
        say(c, task_id, BUILDER, "page_attached", None, "page:f1@1", {"requirement": page})
        say(c, task_id, ANA, "answer", None, "no", {"requirement": page, "answer_id": "a1"})
        say(c, task_id, BUILDER, "page_attached", "page:f1@1", "page:f1@2", {"requirement": page})
        say(c, task_id, ANA, "answer", None, "ok", {"requirement": page, "answer_id": "a2"})
        say(c, task_id, ANA, "answer", None, "ok", {"requirement": "an-old-question", "answer_id": "a3"})
    assert view(drafts, "SELECT answers, repeats FROM repeat_ask_weekly") == [{"answers": 2, "repeats": 1}]
