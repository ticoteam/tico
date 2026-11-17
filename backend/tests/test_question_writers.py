"""Every writer asks a question as a message marked ❓ (backend/question_marks.py): the engine refuses a question ask, a
bot's OK shows what it approves, and a question still open from before is taken back and its bot woken to ask again
(backend/old_questions.py). Synthetic rows only: the fictional company's people and bots (backend/tests/helpers/engine.py)."""

import json

import pytest

from backend import old_questions as OQ
from backend import question_marks as Q
from backend import task_engine as E
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
BEN = "human:ben"
BUILDER = "bot:builder"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


@pytest.fixture
def general(c):
    for slug in ("builder", "reviewer", "manager"):
        X.bot(c, slug)
    return X.pipeline(c, manager="bot:manager")


def _seen(c, task_id):
    return E.task(c, task_id)["version"]


def test_a_question_is_refused_as_an_ask_and_an_ok_is_the_only_kind(c, general):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    with pytest.raises(H.Refused, match="tico task ask"):
        E.add_requirement(c, task_id, BUILDER, "answer", ANA, "Which tone?", seen_version=_seen(c, task_id))
    with pytest.raises(H.Refused, match="Only an OK"):
        E.add_requirement(c, task_id, ANA, "page", BEN, "Look at this", seen_version=_seen(c, task_id))
    assert c.execute("SELECT count(*) FROM requirements WHERE visit_id IS NOT NULL").fetchone()[0] == 0


def test_a_bots_ok_shows_what_it_approves_and_a_persons_need_not(c, general):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    with pytest.raises(H.Refused, match="--show"):
        E.add_requirement(c, task_id, BUILDER, "ok", ANA, "OK the note?", seen_version=_seen(c, task_id))
    # A link, a page that is not there, a version that is not there, or a page on a private task the bot cannot read
    # approves nothing Tico holds: it is a question.
    page = X.shown(c, bot="builder")[0]
    private = H.task_create(c, "human:cara", "Private plan", "Secret", "human:cara", private=True, lint=False)
    hidden = X.shown(c, bot="reviewer", task_id=private["id"])[0]
    for shows in (
        ["https://example.com/launch-note"],
        ["page:file-not-there"],
        [page + "@7"],
        ["file:" + page[5:]],
        [hidden],
    ):
        with pytest.raises(H.Refused, match="--show"):
            E.add_requirement(
                c, task_id, BUILDER, "ok", ANA, "OK the note?", shows=shows, seen_version=_seen(c, task_id)
            )
    shows = ["https://example.com/launch-note", "file:" + page[5:] + "@1"]
    ok = E.add_requirement(c, task_id, BUILDER, "ok", ANA, "OK the note?", shows=shows, seen_version=_seen(c, task_id))
    assert (
        json.loads(c.execute("SELECT shows FROM requirements WHERE id=?", (ok["requirement_id"],)).fetchone()[0])
        == shows
    )
    rid = E.add_requirement(c, task_id, ANA, "ok", BEN, "OK the note", seen_version=_seen(c, task_id))["requirement_id"]
    row = c.execute("SELECT kind, person_id FROM requirements WHERE id=?", (rid,)).fetchone()
    assert (row[0], row[1]) == ("ok", BEN)


def _old_question(c, task_id, by=BUILDER, person=ANA):
    """A question the older engine added: a `requirements` row of kind `answer` on the task's visit."""
    visit = E.task(c, task_id)["visit"]["id"]
    rid = H.new_id()
    c.execute(
        "INSERT INTO requirements (id, visit_id, kind, who_oks, person_id, added_by_id, by_bot, line, choices, settings, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            rid,
            visit,
            "answer",
            "people_with_exceptions",
            person,
            by,
            1,
            "Which tone?",
            json.dumps(["A", "B"]),
            "{}",
            H.now(),
        ),
    )
    c.execute("UPDATE visits SET level='person', tries=3, person_id=?, next_try_at=NULL WHERE id=?", (person, visit))
    return rid, visit


def _queued(c, task_id):
    return c.execute(
        "SELECT j.bot_id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued' "
        "AND json_extract(m.refs, '$.task')=?",
        (task_id,),
    ).fetchall()


def _withdrawn(c, rid):
    return c.execute("SELECT withdrawn_at FROM requirements WHERE id=?", (rid,)).fetchone()[0]


def _wakes(c, task_id):
    """The bodies of the wakes the take-back left on a task, oldest first."""
    return [
        row[0]
        for row in c.execute(
            "SELECT body FROM messages WHERE json_extract(refs, '$.task')=? AND body LIKE 'Your question on this task%' "
            "ORDER BY created_at, rowid",
            (task_id,),
        )
    ]


def test_an_open_question_from_before_is_taken_back_and_its_bot_woken_to_ask_again(c, general):
    asked = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    open_rid, _ = _old_question(c, asked)
    answered = X.new_task(c, ANA, "Pick the colour", assignee=BUILDER)
    done_rid, _ = _old_question(c, answered["id"])
    X.answer(c, answered, done_rid, "A", ANA)
    closed = X.new_task(c, ANA, "Order the paper", assignee=BUILDER)["id"]
    _old_question(c, closed)
    H.task_close(c, ANA, closed, "Not needed.", _seen(c, closed))
    X.settled(c)

    assert OQ.carry_over(c) == 1

    assert _withdrawn(c, open_rid) and not _withdrawn(c, done_rid)
    took = "SELECT count(*) FROM history_events WHERE subject_id=? AND note=?"
    assert c.execute(took, (closed, OQ.NOTE)).fetchone()[0] == 0 and _wakes(c, closed) == []
    assert Q.on_task(c, asked) == [], "nothing is converted: the bot asks again"
    note = c.execute(
        "SELECT actor_id, old, note FROM history_events WHERE subject_id=? AND kind='requirement_withdrawn'", (asked,)
    ).fetchall()
    assert [(row[0], row[1], row[2]) for row in note] == [(H.KEEPER, open_rid, OQ.NOTE)]
    [wake] = _wakes(c, asked)
    assert "Which tone?\n- A\n- B\n" in wake and f"tico task ask {asked}" in wake
    assert [tuple(r) for r in _queued(c, asked)] == [("builder",)]
    assert [n for n in W.needs(c, ANA) if n.get("task_id") == asked] == []

    before = c.execute("SELECT count(*) FROM history_events").fetchone()[0]
    assert OQ.carry_over(c) == 0, "a second run changes nothing"
    assert c.execute("SELECT count(*) FROM history_events").fetchone()[0] == before


def test_the_wake_is_direct_on_a_by_hand_stage_or_under_the_bots_snooze_and_a_persons_snooze_stays(c, general):
    X.pipeline(
        c,
        pid="manual",
        name="Manual review",
        manager="bot:manager",
        stages=[{"name": "Review", "owner": "assignee", "by_hand": True, "requirements": [{"kind": "owner_done"}]}],
    )
    manual = X.new_task(c, ANA, "Choose what to review", pipeline_id="manual", assignee=BUILDER)["id"]
    _old_question(c, manual)
    snoozed = X.new_task(c, ANA, "Wait for the answer", assignee=BUILDER)["id"]
    _old_question(c, snoozed, person="bot:reviewer")
    E.snooze(c, snoozed, BUILDER, "Waiting for the reviewer", None, seen_version=_seen(c, snoozed))
    held = X.new_task(c, ANA, "Hold until Monday", assignee=BUILDER)["id"]
    _old_question(c, held)
    E.snooze(c, held, ANA, "Monday", H.shift(H.now(), days=3), seen_version=_seen(c, held))
    X.settled(c)

    assert OQ.carry_over(c) == 3

    for task_id in (manual, snoozed):
        assert [tuple(r) for r in _queued(c, task_id)] == [("builder",)], task_id
        assert len(_wakes(c, task_id)) == 1, task_id
    assert len(_wakes(c, held)) == 1
    assert E.task(c, held).get("snooze"), "a snooze a person set stays"


def test_a_persons_old_question_is_taken_back_with_no_bot_to_wake_and_a_long_one_is_cut(c, general):
    by_person = X.new_task(c, ANA, "Plan the launch", assignee=BUILDER)["id"]
    person_rid, _ = _old_question(c, by_person, by=BEN)
    gone = X.new_task(c, ANA, "Ask a bot that left", assignee=BUILDER)["id"]
    gone_rid, _ = _old_question(c, gone, by="bot:left-already")
    long = X.new_task(c, ANA, "Name the release", assignee=BUILDER)["id"]
    long_rid, _ = _old_question(c, long)
    c.execute(
        "UPDATE requirements SET line=?, choices=? WHERE id=?",
        ("Which name? " + "x" * 2000, json.dumps(["y" * 500] * 20), long_rid),
    )
    X.settled(c)

    assert OQ.carry_over(c) == 3

    assert _withdrawn(c, person_rid) and _withdrawn(c, gone_rid)
    assert _queued(c, by_person) == [] and _wakes(c, by_person) == [], "a person's own question has no bot to ask again"
    assert _wakes(c, gone) == [], "a bot that no longer exists is not woken"
    [wake] = _wakes(c, long)
    lines = wake.splitlines()
    assert lines[1].startswith("Which name? ") and len(lines[1]) == OQ.LINE_MAX
    picks = [row for row in lines if row.startswith("- ")]
    assert len(picks) == OQ.CHOICES_MAX + 1 and picks[-1] == f"- (and {20 - OQ.CHOICES_MAX} more)"
    assert all(len(row) <= OQ.CHOICE_MAX + 2 for row in picks)
    assert len(wake) < 2000


def test_the_open_questions_read_their_answers_by_visit(c, general):
    plan = " ".join(str(row[-1]) for row in c.execute("EXPLAIN QUERY PLAN " + OQ.OPEN))
    assert "answers_visit" in plan and "SCAN a" not in plan, plan
