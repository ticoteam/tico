"""Answers to what a task needs (backend/answers.py, docs/dev/pipelines-v3.md "Asks" and "What your answer does").

Synthetic rows only (backend/tests/helpers/engine.py): a General pipeline, a two-stage Build pipeline, the fictional
company's two people and three bots.
"""

import pytest

from backend import answers as A
from backend import task_engine as E
from backend import task_requirements as R
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
BEN = "human:ben"
BUILD = (
    {"name": "Build", "owner": "bot:builder", "requirements": [{"kind": "owner_done"}]},
    {
        "name": "Review",
        "owner": "bot:reviewer",
        "requirements": [{"kind": "ok", "person": ANA, "line": "OK the build"}],
    },
)


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


@pytest.fixture
def general(c):
    for slug in ("builder", "reviewer", "manager"):
        X.bot(c, slug)
    return X.pipeline(c, manager="bot:manager")


@pytest.fixture
def build(c, general):
    return X.pipeline(c, "eng", "Engineering", BUILD, manager="bot:manager")


def _refused(rule):
    return pytest.raises(H.Refused, match=rule)


def _bot_ask(c, task_id, bot, kind, person, line, **fields):
    """A bot adds an ask to a task, sending the version it saw."""
    seen = E.task(c, task_id)["version"]
    return E.add_requirement(c, task_id, bot, kind, person, line, seen_version=seen, **fields)


def _owner_done(c, row):
    return R.on_stage(c, row["stage_id"])[0]["id"]


def _in_review(c, build):
    """A task the builder finished: it waits in Review on Ana's OK."""
    row = X.new_task(c, ANA, "Build the importer", "eng")
    moved = A.answer(
        c, row["id"], build["requirements"]["Build"][0], "bot:builder", "done", seen_version=row["version"]
    )
    assert moved["stage"]["name"] == "Review"
    X.settled(c)
    return moved


def _body(c, key):
    return H._one(c, "SELECT * FROM messages WHERE wake_key=?", (key,))["body"]


def _answer_wakes(c, task_id):
    return [(key, who) for key, who in X.wakes(c, task_id) if ":answer:" in key]


# ----------------------------------------------------------------------------- owner says done
def test_the_owner_says_done_and_the_task_moves_in_the_same_step(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    done = A.answer(c, row["id"], _owner_done(c, row), "bot:builder", "done", seen_version=row["version"])
    assert (done["status"], done["stage_id"]) == ("done", "general-done")
    assert done["version"] == row["version"] + 1  # the move raised it; the answer did not
    saved = H._one(c, "SELECT * FROM answers WHERE id=?", (done["answer_id"],))
    assert (saved["visit_id"], saved["by_id"], saved["answer"]) == (row["visit"]["id"], "bot:builder", "done")
    mine = X.new_task(c, ANA, "Call the bank", assignee=ANA)
    assert A.answer(c, mine["id"], _owner_done(c, mine), ANA, "done")["status"] == "done"  # a person sends no version


def test_the_owner_the_manager_or_any_person_says_done_and_nobody_else(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    rid = _owner_done(c, row)
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, "bot:reviewer", "done", seen_version=row["version"])
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, "bot:builder", "ok", seen_version=row["version"])
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, ANA, "pushed_past", "It went out")  # push past is a move
    assert H._one(c, "SELECT COUNT(*) AS n FROM answers")["n"] == 0
    assert A.answer(c, row["id"], rid, "bot:manager", "done", seen_version=row["version"])["status"] == "done"
    other = X.new_task(c, ANA, "Write the release note", assignee="bot:builder")
    assert A.answer(c, other["id"], _owner_done(c, other), BEN, "done")["status"] == "done"


def test_a_bot_sends_the_version_it_saw_and_an_old_one_is_refused(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    rid = _owner_done(c, row)
    with _refused("task_changed"):
        A.answer(c, row["id"], rid, "bot:builder", "done")
    E.reassign(c, row["id"], "bot:reviewer", ANA)
    E.reassign(c, row["id"], "bot:builder", ANA)
    with _refused("task_changed"):
        A.answer(c, row["id"], rid, "bot:builder", "done", seen_version=row["version"])
    assert E.task(c, row["id"])["status"] == "open"
    assert A.answer(c, row["id"], rid, "bot:builder", "done", seen_version=row["version"] + 2)["status"] == "done"


# ----------------------------------------------------------------------------- OK, send back
def test_an_ok_from_the_named_person_moves_the_task_on_and_nobody_else_can_give_it(c, build):
    row = _in_review(c, build)
    rid = build["requirements"]["Review"][0]
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, "bot:reviewer", "ok", seen_version=row["version"])  # a bot it does not name
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, BEN, "ok")  # it names Ana
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, ANA, "maybe")
    done = A.answer(c, row["id"], rid, ANA, "ok", "Looks right")
    assert (done["status"], done["version"]) == ("done", row["version"] + 1)
    assert _answer_wakes(c, row["id"]) == []


def test_send_back_needs_notes_and_wakes_the_bot_once_with_them_word_for_word(c, build):
    row = _in_review(c, build)
    rid = build["requirements"]["Review"][0]
    with _refused("task_why"):
        A.answer(c, row["id"], rid, ANA, "no", "  ")
    notes = "The importer drops the last row.  Keep the header as it was."
    back = A.answer(c, row["id"], rid, ANA, "no", notes)
    assert (back["stage"]["name"], back["version"]) == ("Review", row["version"])
    key = f"{row['id']}:{row['visit']['id']}:answer:{back['answer_id']}"
    assert _answer_wakes(c, row["id"]) == [(key, "bot:reviewer")]
    assert notes in _body(c, key)
    assert W.headline(c, row["id"]).who == "bot:reviewer"  # the bot's turn, with the notes
    again = A.answer(c, row["id"], rid, ANA, "no", notes)  # the same answer twice is one answer
    assert again["answer_id"] == back["answer_id"]
    assert len(_answer_wakes(c, row["id"])) == 1
    assert H._one(c, "SELECT COUNT(*) AS n FROM answers WHERE requirement_id=?", (rid,))["n"] == 1


def test_done_again_after_a_send_back_asks_the_approver_again_and_their_ok_moves_it_on(c, general):
    review = X.pipeline(
        c,
        "retry",
        "Retry",
        stages=[
            {
                "name": "Review",
                "owner": ANA,
                "requirements": [{"kind": "owner_done"}, {"kind": "ok", "person": BEN, "line": "OK the note"}],
            },
            {"name": "Publish", "owner": ANA, "requirements": [{"kind": "owner_done"}]},
        ],
    )
    done, rid = review["requirements"]["Review"]
    row = X.new_task(c, ANA, "Review the launch note", pipeline_id="retry", assignee=ANA)
    A.answer(c, row["id"], done, ANA, "done")
    back = A.answer(c, row["id"], rid, BEN, "no", "Change the headline")
    mine = lambda: [n for n in W.needs(c, BEN) if n.get("task_id") == row["id"]]  # noqa: E731
    assert mine() == []
    with _refused("task_not_met"):
        H.task_move(c, ANA, row["id"], E.NEXT)

    again = H.task_done(c, ANA, row["id"], "New headline.")
    assert again["stage"]["name"] == "Review"
    assert len(mine()) == 1, "back in Ben's Needs you"
    wait = W.headline(c, row["id"])
    assert (wait.state, wait.who) == ("person", BEN)
    history = c.execute(
        "SELECT answer, notes, undone_at FROM answers WHERE requirement_id=? ORDER BY created_at", (rid,)
    )
    assert [tuple(r) for r in history] == [("no", "Change the headline", None), ("again", "New headline.", None)]
    with _refused("task_not_met"):
        H.task_done(c, ANA, row["id"])  # once more: nothing is sent back, and it waits on Ben
    assert A.answer(c, row["id"], rid, BEN, "ok")["stage"]["name"] == "Publish"
    with _refused("task_changed"):
        A.undo(c, back["answer_id"], BEN)  # the send back did not move it


def test_a_stage_ok_that_names_nobody_is_any_persons_and_never_a_bots(c, general):
    stages = ({"name": "Sign", "owner": "bot:builder", "requirements": [{"kind": "ok", "line": "OK the lease"}]},)
    X.pipeline(c, "legal", "Legal", stages)
    row = X.new_task(c, ANA, "Sign the lease", "legal")
    rid = R.on_stage(c, row["stage_id"])[0]["id"]
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, "bot:builder", "ok", seen_version=row["version"])
    assert A.answer(c, row["id"], rid, BEN, "ok")["status"] == "done"


# ----------------------------------------------------------------------------- an OK added to one task
def test_an_ok_on_one_task_answered_while_everything_else_is_met_moves_the_task(c, general):
    row = X.new_task(c, ANA, "Pick the signup plan", assignee="bot:builder")
    rid = X.ok_ask(c, row["id"], "bot:builder", ANA, "OK the Pro trial for new signups?")["requirement_id"]
    stays = A.answer(c, row["id"], _owner_done(c, row), "bot:builder", "done", seen_version=row["version"])
    assert stays["status"] == "open"  # while the OK is open the task stays
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, "bot:builder", "ok", seen_version=row["version"])  # asked of Ana
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, ANA, " ")
    done = A.answer(c, row["id"], rid, ANA, "ok")
    assert done["status"] == "done"
    assert H._one(c, "SELECT answer FROM answers WHERE id=?", (done["answer_id"],))["answer"] == "ok"


def test_an_ok_on_one_task_wakes_the_bot_that_asked_once_with_the_ask_and_the_answer(c, general):
    row = X.new_task(c, ANA, "Pick the signup plan", assignee="bot:builder")
    asked = X.ok_ask(c, row["id"], "bot:builder", ANA, "OK the Pro trial for new signups?")
    X.settled(c)
    out = A.answer(c, row["id"], asked["requirement_id"], ANA, "ok")
    assert out["status"] == "open" and out["version"] == row["version"]  # the builder has not said done
    [(key, who)] = _answer_wakes(c, row["id"])
    assert (key, who) == (f"{row['id']}:{row['visit']['id']}:answer:{out['answer_id']}", "bot:builder")
    assert '"OK the Pro trial for new signups?": ok' in _body(c, key)
    A.answer(c, row["id"], asked["requirement_id"], ANA, "ok")  # the same answer twice is one answer
    assert len(_answer_wakes(c, row["id"])) == 1


def test_a_snoozed_task_takes_the_answer_and_wakes_nobody(c, general):
    row = X.new_task(c, ANA, "Pick the signup plan", assignee="bot:builder")
    asked = X.ok_ask(c, row["id"], "bot:builder", ANA, "OK the Free plan?")
    E.snooze(c, row["id"], ANA)
    out = A.answer(c, row["id"], asked["requirement_id"], ANA, "no", "Make it the Pro trial")
    assert out["answer_id"] and out["snooze"] and _answer_wakes(c, row["id"]) == []


def test_done_on_a_task_its_owner_snoozed_ends_the_snooze_and_moves_it_at_once(c, general):
    # Agent Ops snoozed its setup task to wait for a repository, then said done in the same turn: the task was left open
    # until the snooze ran out.
    row = X.new_task(c, ANA, "Set up the Release Owner", assignee="bot:builder")
    E.snooze(c, row["id"], "bot:builder", "waiting to go live", "2099-01-01T00:00:00Z", seen_version=row["version"])
    seen = E.task(c, row["id"])["version"]
    done = A.answer(c, row["id"], _owner_done(c, row), "bot:builder", "done", seen_version=seen)
    assert (done["status"], done["snooze"]) == ("done", None)
    ended = H._one(c, "SELECT ended_by_id FROM snoozes WHERE task_id=?", (row["id"],))
    assert ended["ended_by_id"] == "bot:builder"
    # A person saying done ends a bot's snooze the same way.
    other = X.new_task(c, ANA, "Set up the Growth Owner", assignee="bot:builder")
    E.snooze(c, other["id"], "bot:builder", "waiting to go live", seen_version=other["version"])
    assert A.answer(c, other["id"], _owner_done(c, other), ANA, "done")["status"] == "done"


def test_a_bot_saying_done_keeps_a_snooze_a_person_set(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    E.snooze(c, row["id"], ANA, "after the board call", "2099-01-01T00:00:00Z")
    seen = E.task(c, row["id"])["version"]
    out = A.answer(c, row["id"], _owner_done(c, row), "bot:builder", "done", seen_version=seen)
    assert out["status"] == "open" and out["snooze"]["by_id"] == ANA
    # Its done stands: when the snooze ends, the task moves on.
    assert E.unsnooze(c, row["id"], ANA) and E.advance(c, row["id"])["status"] == "done"


def test_done_while_something_else_is_missing_keeps_the_owners_snooze(c, general):
    row = X.new_task(c, ANA, "Pick the signup plan", assignee="bot:builder")
    _bot_ask(c, row["id"], "bot:builder", "ok", ANA, "OK the plan?", shows=X.shown(c))  # still waits on Ana
    seen = E.task(c, row["id"])["version"]
    E.snooze(c, row["id"], "bot:builder", "waiting on Ana", seen_version=seen)
    seen = E.task(c, row["id"])["version"]
    out = A.answer(c, row["id"], _owner_done(c, row), "bot:builder", "done", seen_version=seen)
    assert out["status"] == "open" and out["snooze"]


def test_a_bot_that_cannot_run_still_gets_the_wake(c, general):
    row = X.new_task(c, ANA, "Pick the signup plan", assignee="bot:builder")
    asked = X.ok_ask(c, row["id"], "bot:builder", ANA, "OK the Free plan?")
    c.execute("UPDATE bots SET state='paused' WHERE id='builder'")
    A.answer(c, row["id"], asked["requirement_id"], ANA, "no", "Make it the Pro trial")
    assert [who for _, who in _answer_wakes(c, row["id"])] == ["bot:builder"]


def test_an_ok_added_to_one_task_is_never_a_bots_to_answer(c, general):
    """An OK added to one task is asked of a person (docs/dev/pipelines-v3.md, "A requirement on one task"): none is
    asked of a bot now, and one an older company asked of a bot is still no bot's to give, the named one's included."""
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    with _refused("task_requirement"):
        E.add_requirement(c, row["id"], ANA, "ok", "bot:reviewer", "Check the facts")
    asked = X.ok_ask(c, row["id"], ANA, "bot:reviewer", "OK the facts?")
    for bot in ("bot:builder", "bot:reviewer"):
        with _refused("task_requirement"):
            A.answer(c, row["id"], asked["requirement_id"], bot, "ok", seen_version=row["version"])
    assert [s.state for s in R.standings(c, E.task(c, row["id"]))] == [R.NOT_MET, R.WAITING]


# ----------------------------------------------------------------------------- refused
def test_an_answer_on_a_withdrawn_requirement_or_a_stage_the_task_left_is_refused(c, build):
    row = X.new_task(c, ANA, "Build the importer", "eng")
    asked = _bot_ask(c, row["id"], "bot:builder", "ok", ANA, "OK three retries?", shows=X.shown(c))
    E.withdraw_requirement(c, asked["requirement_id"], "bot:builder", "Found it in the docs")
    with _refused("task_requirement"):
        A.answer(c, row["id"], asked["requirement_id"], ANA, "ok")
    build_done = build["requirements"]["Build"][0]
    moved = A.answer(c, row["id"], build_done, ANA, "done")
    assert moved["stage"]["name"] == "Review"
    with _refused("task_changed"):
        A.answer(c, row["id"], build_done, ANA, "done")  # the task left Build
    with _refused("not-found"):
        A.answer(c, row["id"], "no-such-requirement", ANA, "done")
    E.move(c, row["id"], E.CLOSED, ANA)
    with _refused("task_ended"):
        A.answer(c, row["id"], build["requirements"]["Review"][0], ANA, "ok")


# ----------------------------------------------------------------------------- give to
def test_give_a_requirement_added_to_one_task_to_another_person_and_needs_you_follows(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    asked = _bot_ask(c, row["id"], "bot:builder", "ok", ANA, "OK the headline", shows=X.shown(c))
    rid = asked["requirement_id"]
    assert [n["requirement_id"] for n in W.needs(c, ANA)] == [rid]
    with _refused("task_requirement"):
        A.give_to(c, row["id"], rid, ANA, "bot:reviewer")  # only a person is named (C29)
    given = A.give_to(c, row["id"], rid, ANA, BEN)
    assert given["version"] == row["version"] and "answer_id" not in given
    assert R.get(c, rid)["person_id"] == BEN
    assert W.needs(c, ANA) == [] and [n["requirement_id"] for n in W.needs(c, BEN)] == [rid]
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, ANA, "ok")
    A.answer(c, row["id"], rid, BEN, "ok")
    assert W.needs(c, BEN) == []


def test_give_a_stage_ok_to_another_person_and_needs_you_follows(c, build):
    row = _in_review(c, build)
    rid = build["requirements"]["Review"][0]
    assert [n["task_id"] for n in W.needs(c, ANA)] == [row["id"]]
    given = A.give_to(c, row["id"], rid, ANA, BEN)
    assert given["version"] == row["version"]
    saved = H._one(c, "SELECT * FROM answers WHERE id=?", (given["answer_id"],))
    assert (saved["answer"], saved["notes"]) == ("give", BEN)
    assert W.needs(c, ANA) == [] and [n["requirement_id"] for n in W.needs(c, BEN)] == [rid]
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, ANA, "ok")
    assert A.answer(c, row["id"], rid, BEN, "ok")["status"] == "done"
    assert R.get(c, rid)["person_id"] == ANA  # the stage still names Ana for the next task


def test_give_your_own_task_to_someone_else_is_a_reassign(c, general):
    row = X.new_task(c, ANA, "Call the bank", assignee=ANA)
    assert [w.who for w in W.waiting_on(c, row["id"])] == [ANA]
    given = A.give_to(c, row["id"], _owner_done(c, row), ANA, BEN)
    assert (given["owner_id"], given["version"]) == (BEN, row["version"] + 1)
    assert [w.who for w in W.waiting_on(c, row["id"])] == [BEN]  # his turn now, in his Tasks (not Needs you)


def test_an_ask_is_given_or_named_only_to_a_person_who_is_here(c, build):
    review = _in_review(c, build)
    stage_ok = build["requirements"]["Review"][0]
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    rid = _bot_ask(c, row["id"], "bot:builder", "ok", ANA, "OK the headline", shows=X.shown(c))["requirement_id"]
    mine = X.new_task(c, ANA, "Call the bank", assignee=ANA)
    X.left(c, "human:cara")
    for nobody in ("human:missing", "human:cara"):  # never on the roster; on it, and left
        with _refused("task_requirement"):
            A.give_to(c, row["id"], rid, ANA, nobody)
        with _refused("task_requirement"):
            A.give_to(c, review["id"], stage_ok, ANA, nobody)
        with _refused("task_requirement"):
            A.give_to(c, mine["id"], _owner_done(c, mine), ANA, nobody)
        with _refused("task_requirement"):
            E.add_requirement(c, row["id"], ANA, "ok", nobody, "OK the invoice")
    with _refused("task_requirement"):
        E.add_requirement(c, row["id"], ANA, "ok", "bot:nobody", "OK the footer")
    assert R.get(c, rid)["person_id"] == ANA and E.task(c, mine["id"])["owner_id"] == ANA
    assert H._one(c, "SELECT COUNT(*) AS n FROM answers WHERE answer='give'")["n"] == 0


def test_an_ask_that_names_a_person_who_left_is_any_persons(c, general):
    row = X.new_task(c, ANA, "Pick the signup plan", assignee="bot:builder")
    rid = X.ok_ask(c, row["id"], "bot:builder", BEN, "OK the Free plan?")["requirement_id"]
    X.left(c, BEN)
    wait = W.headline(c, row["id"])
    assert (wait.state, wait.who, wait.ask) == ("person", ANA, True)
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, "bot:reviewer", "ok", seen_version=row["version"])  # still never a bot's
    out = A.answer(c, row["id"], rid, ANA, "ok")
    assert [s.state for s in R.standings(c, out)] == [R.NOT_MET, R.MET]


def test_an_answered_ask_is_not_given_away(c, build):
    row = _in_review(c, build)
    rid = build["requirements"]["Review"][0]
    A.answer(c, row["id"], rid, ANA, "no", "Keep the header")
    with _refused("task_requirement"):
        A.give_to(c, row["id"], rid, ANA, BEN)


# ----------------------------------------------------------------------------- undo
def test_undo_before_the_move_takes_the_answer_back(c, general):
    row = X.new_task(c, ANA, "Pick the signup plan", assignee="bot:builder")
    _bot_ask(c, row["id"], "bot:builder", "ok", ANA, "OK the plan?", shows=X.shown(c))
    said = A.answer(c, row["id"], _owner_done(c, row), "bot:builder", "done", seen_version=row["version"])
    with _refused("identity"):
        A.undo(c, said["answer_id"], "bot:reviewer")  # not who answered
    with _refused("identity"):
        A.undo(c, said["answer_id"], ANA)  # a person, but not who answered
    out = A.undo(c, said["answer_id"], "bot:builder")
    assert out["version"] == row["version"] and out["stage_id"] == row["stage_id"]
    assert H._one(c, "SELECT undone_at FROM answers WHERE id=?", (said["answer_id"],))["undone_at"]
    assert [s.state for s in R.standings(c, out)][0] == R.NOT_MET
    assert A.undo(c, said["answer_id"], "bot:builder")["version"] == row["version"]  # twice is once


def test_undo_after_the_move_is_an_ordinary_move_back(c, build):
    row = _in_review(c, build)
    rid = build["requirements"]["Review"][0]
    done = A.answer(c, row["id"], rid, ANA, "ok")
    assert done["status"] == "done"
    back = A.undo(c, done["answer_id"], ANA)
    assert (back["stage"]["name"], back["status"]) == ("Review", "open")
    assert back["version"] == done["version"] + 1
    assert [n["task_id"] for n in W.needs(c, ANA)] == [row["id"]]  # it waits on Ana again


def test_undo_after_the_task_moved_on_again_is_refused(c, build):
    row = X.new_task(c, ANA, "Build the importer", "eng")
    said = A.answer(c, row["id"], build["requirements"]["Build"][0], ANA, "done")
    E.move(c, row["id"], E.DONE, ANA, "Shipped without review")
    with _refused("task_changed"):
        A.undo(c, said["answer_id"], ANA)


def test_undo_of_a_send_back_after_the_task_moved_on_is_refused(c, build):
    row = _in_review(c, build)
    rid = build["requirements"]["Review"][0]
    back = A.answer(c, row["id"], rid, ANA, "no", "Keep the header")
    assert A.answer(c, row["id"], rid, ANA, "ok")["status"] == "done"  # she looked again and OK'd it
    with _refused("task_changed"):
        A.undo(c, back["answer_id"], ANA)  # the send back did not move it


def test_undo_of_an_old_look_again_after_the_task_moved_on_is_refused(c, build):
    row = _in_review(c, build)
    rid = build["requirements"]["Review"][0]
    A.answer(c, row["id"], rid, ANA, "no", "Keep the header")
    # A Look again from before it was retired: kept in answers, and nothing writes one by hand now.
    aid = H.new_id()
    c.execute(
        "INSERT INTO answers (id, visit_id, requirement_id, by_id, answer, created_at) VALUES (?,?,?,?,?,?)",
        (aid, row["visit"]["id"], rid, "bot:reviewer", "again", H.now()),
    )
    assert W.headline(c, row["id"]).who == ANA, "it asks Ana again"
    assert A.answer(c, row["id"], rid, ANA, "ok")["status"] == "done"
    with _refused("task_changed"):
        A.undo(c, aid, "bot:reviewer")  # the Look again did not move it
    assert E.task(c, row["id"])["status"] == "done"


def test_undo_of_a_send_back_cancels_the_wake_no_run_took(c, build):
    row = _in_review(c, build)
    back = A.answer(c, row["id"], build["requirements"]["Review"][0], ANA, "no", "Keep the header")
    A.undo(c, back["answer_id"], ANA)
    state = c.execute(
        "SELECT j.state FROM jobs j JOIN messages m ON m.id = j.message_id WHERE m.wake_key=?",
        (f"{row['id']}:{row['visit']['id']}:answer:{back['answer_id']}",),
    ).fetchone()[0]
    assert state == "canceled"
    assert W.headline(c, row["id"]).who == ANA


def _waiting_for(c, task_id, bot):
    """The bodies of the wakes still queued for a bot about a task."""
    rows = c.execute(
        "SELECT m.body FROM jobs j JOIN messages m ON m.id = j.message_id WHERE j.state='queued' AND j.bot_id=? "
        "AND m.wake_key LIKE ?",
        (H.actor_id(bot), task_id + ":%"),
    ).fetchall()
    return [str(found[0]) for found in rows]


def test_undo_of_one_answer_takes_only_its_words_out_of_the_wake_they_were_folded_into(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    X.settled(c)  # its start ran: the builder still has work
    colour = X.ok_ask(c, row["id"], ANA, BEN, "OK the blue?")
    font = X.ok_ask(c, row["id"], ANA, BEN, "OK the serif font?")
    blue = A.answer(c, row["id"], colour["requirement_id"], BEN, "ok")
    serif = A.answer(c, row["id"], font["requirement_id"], BEN, "ok")
    [both] = _waiting_for(c, row["id"], "bot:builder")  # one run starts, with both answers in order
    assert both.index('Ben answered "OK the blue?": ok') < both.index('Ben answered "OK the serif font?": ok')
    A.undo(c, serif["answer_id"], BEN)
    [rest] = _waiting_for(c, row["id"], "bot:builder")  # the wake stays, with the first answer's words
    assert 'Ben answered "OK the blue?": ok' in rest and 'answered "OK the serif font?"' not in rest
    A.undo(c, blue["answer_id"], BEN)
    assert _waiting_for(c, row["id"], "bot:builder") == []  # nothing is left of it


def test_an_ok_bound_to_exact_content_is_never_a_plain_answer_only_given_from_its_preview(c, general):
    """Through the task call everything answers by (H.task_answer): a plain ok on a bound OK is refused for the person
    it is asked of, for a bot, and for the Assistant (its own actor, or the person's when it acts for them: below the
    routes that is the person's name). Nothing is met and the task does not move. The OK route gives it."""
    from backend import task_asks as K

    X.bot(c, "assistant")
    email = {"to": ["pat@example.com"], "subject": "Your invoice", "body_sha256": "a" * 64}
    asked = K.ask_ok(
        c, "bot:builder", ANA, "Send this email?", shows=["email:e1"], subject="hq_reply:TK-1", content=email
    )
    task_id, rid = asked["task_id"], asked["requirement_id"]
    X.say_done(c, E.task(c, task_id))  # the owner's own work is done: the OK is all the task waits for
    stage = E.task(c, task_id)["stage_id"]
    with pytest.raises(H.Refused, match="task_requirement: " + K.FROM_PREVIEW):
        H.task_answer(c, ANA, task_id, rid, "ok")  # a plain ok, even from the person it is asked of
    for other in ("bot:builder", "bot:assistant"):
        # The task filed for it is private to its parties: a bot not asked does not even find it.
        with _refused("task_requirement|not-found"):
            H.task_answer(c, other, task_id, rid, "ok", seen_version=E.task(c, task_id)["version"])
    row = E.task(c, task_id)
    assert K.standing(c, rid) == K.WAITING and rid not in R.newest_answers(c, row["visit"]["id"])
    assert (row["status"], row["stage_id"]) == ("open", stage)
    shown = K.show(c, ANA, rid, email, own_click=True)
    K.give_ok(c, ANA, rid, shown, own_click=True)  # the OK route, with the hash of what she was shown
    assert R.newest_answers(c, row["visit"]["id"])[rid]["answer"] == "ok"
    assert E.task(c, task_id)["status"] == "done"  # and the task moves on


def test_an_ok_bound_to_nothing_is_a_plain_answer(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    plain = E.add_requirement(c, row["id"], BEN, "ok", ANA, "OK the wording")
    out = H.task_answer(c, ANA, row["id"], plain["requirement_id"], "ok")
    assert R.newest_answers(c, out["visit"]["id"])[plain["requirement_id"]]["answer"] == "ok"
