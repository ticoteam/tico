"""A task's Human (`tasks.human_id`, hubdb.task_human): the one person accountable for a task whatever owns it. The task
falls to them first (waiting_on.person_for), so an OK that names nobody is theirs and is in their Needs you; set and
cleared on the task's create and edit."""

import pytest

from backend import task_engine as E
from backend import waiting_on as W
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, post  # noqa: F401  (fixtures)

ANA, BEN = X.ANA, "human:ben"
SPEC = (
    {
        "name": "Spec",
        "owner": "bot:builder",
        "requirements": [
            {"kind": "owner_done"},
            {"kind": "ok", "who_oks": "people_only", "line": "OK the spec"},
        ],
    },
)


@pytest.fixture
def c(company):
    """After each test, every person's own Needs you is the one every person's pass gives them: a Human whose task
    `waiting_on.candidates` misses fails here."""
    with company.transaction() as conn:
        yield conn
        every = W.needs_by_person(conn)
        people = {f"human:{found[0]}" for found in conn.execute("SELECT id FROM humans").fetchall()}
        for person in sorted(people | set(every)):
            assert W.needs(conn, person) == every.get(person, []), person


@pytest.fixture
def spec(c):
    for slug in ("builder", "manager"):
        X.bot(c, slug)
    X.pipeline(c, manager="bot:manager")
    return X.pipeline(c, "dev", "Development", SPEC, manager="bot:manager")


def _ok_wait(c, task_id):
    return next(w for w in W.waiting_on(c, task_id) if w.ask)


def test_an_ok_that_names_nobody_goes_to_the_tasks_human_first(c, spec):
    row = X.new_task(c, ANA, "Add CSV export", "dev", assignee="bot:builder")
    assert (_ok_wait(c, row["id"]).state, _ok_wait(c, row["id"]).who) == ("person", ANA)  # no Human: the ladder
    X.H.task_update(c, ANA, row["id"], human_id="ben")
    task = E.task(c, row["id"])
    assert task["human_id"] == BEN and task["owner_id"] == "bot:builder"  # the bot still owns it
    assert W.person_for(c, task) == BEN
    wait = _ok_wait(c, row["id"])
    assert (wait.state, wait.who, wait.line) == ("person", BEN, "OK the spec")
    assert [(n["kind"], n["task_id"], n["line"]) for n in W.needs(c, BEN)] == [("ask", row["id"], "OK the spec")]
    assert not [n for n in W.needs(c, ANA) if n.get("task_id") == row["id"]]
    # The bot keeps the work; only what falls to a person moves.
    assert ("bot", "bot:builder") in [(w.state, w.who) for w in W.waiting_on(c, row["id"])]
    # Stuck: the ladder reaches the Human, not the company's owner.
    c.execute("UPDATE visits SET level='person', person_id=? WHERE task_id=?", (ANA, row["id"]))
    assert W.headline(c, row["id"]).who == BEN
    # Taken off: the ladder as before.
    X.H.task_update(c, ANA, row["id"], human_id="")
    assert E.task(c, row["id"])["human_id"] is None
    assert _ok_wait(c, row["id"]).who == ANA


def test_a_human_who_left_or_cannot_read_a_private_task_is_passed_over(c, spec):
    row = X.new_task(c, ANA, "Add CSV export", "dev", assignee="bot:builder")
    X.H.task_update(c, ANA, row["id"], human_id="ben")
    assert _ok_wait(c, row["id"]).who == BEN
    c.execute("UPDATE tasks SET private=1 WHERE id=?", (row["id"],))  # Ben is not on it and does not read it
    assert _ok_wait(c, row["id"]).who == ANA
    c.execute("UPDATE tasks SET private=0 WHERE id=?", (row["id"],))
    X.left(c, BEN)
    assert _ok_wait(c, row["id"]).who == ANA
    assert W.needs(c, BEN) == []


def test_the_human_is_set_on_create_changed_and_cleared_on_edit_and_only_ever_a_person(api):
    t = post(api, "tasks", {"owner": "ops", "title": "Add CSV export", "body": "x", "human": "ben"}, token="ana-test")
    assert t["human_id"] == BEN and t["owner_id"] == "bot:ops"
    t = post(api, "tasks/" + t["id"], {"version": t["version"], "human": "cara"}, token="ana-test")
    assert t["human_id"] == "human:cara"
    t = post(api, "tasks/" + t["id"], {"version": t["version"], "human": ""}, token="ana-test")
    assert t["human_id"] is None
    history = {(e["field"], e["old"], e["new"]) for e in get(api, "tasks/" + t["id"])["events"]}
    assert {("human_id", None, BEN), ("human_id", BEN, "human:cara"), ("human_id", "human:cara", None)} <= history
    # A bot, nobody known, or a person who cannot sign in is refused, and nothing changes.
    for wrong in ("ops", "nobody-here"):
        post(api, "tasks/" + t["id"], {"version": t["version"], "human": wrong}, token="ana-test", expected=422)
        post(api, "tasks", {"owner": "ops", "title": "Add PDF export", "body": "x", "human": wrong}, expected=422)
    # Someone who may not change the task's fields may not name its Human either.
    post(api, "tasks/" + t["id"], {"version": t["version"], "human": "cara"}, token="cara-test", expected=403)
    with api.app.state.store.transaction() as c:
        X.no_sign_in(c, "human:cara")
    post(api, "tasks/" + t["id"], {"version": t["version"], "human": "cara"}, token="ana-test", expected=422)
    assert get(api, "tasks/" + t["id"])["task"]["human_id"] is None


def test_a_private_tasks_human_must_already_read_it(api):
    """Naming a Human never adds a reader: one who cannot read the private task is refused, on create and on edit."""
    body = {"owner": "ops", "title": "Payroll fix", "body": "x", "private": True}
    refused = post(api, "tasks", {**body, "human": "ben"}, token="ana-test", expected=422)
    assert "Ben" in str(refused) and "can't read this private task" in str(refused)
    t = post(api, "tasks", {**body, "human": "ben", "readers": ["human:ben"]}, token="ana-test")
    assert t["human_id"] == BEN
    t = post(api, "tasks", {**body, "title": "Payroll report"}, token="ana-test")
    refused = post(api, "tasks/" + t["id"], {"version": t["version"], "human": "ben"}, token="ana-test", expected=422)
    assert "can't read this private task" in str(refused)
    assert get(api, "tasks/" + t["id"])["task"]["human_id"] is None
    # Made private in the same edit that names a Human who cannot read it: judged as it lands.
    t = post(api, "tasks", {**body, "title": "Payroll export", "private": False}, token="ana-test")
    edit = {"version": t["version"], "human": "ben", "private": True}
    assert "can't read this private task" in str(post(api, "tasks/" + t["id"], edit, token="ana-test", expected=422))
    t = post(api, "tasks/" + t["id"], {"version": t["version"], "human": "ana"}, token="ana-test")
    assert t["human_id"] == ANA  # the one who asked for it reads it


def test_the_owner_bots_wake_says_to_ask_the_human_while_they_can_act(c, spec):
    from backend import task_wakes as TW

    row = X.new_task(c, ANA, "Add CSV export", "dev", assignee="bot:builder")
    said = "is this task's Human, so ask them the questions and OKs it needs from a person (--to human:ben)."
    assert "this task's Human" not in TW.body(c, E.task(c, row["id"]), to="bot:builder")
    X.H.task_update(c, ANA, row["id"], human_id="ben")
    lines = TW.body(c, E.task(c, row["id"]), to="bot:builder").splitlines()
    assert [line for line in lines if "this task's Human" in line] == [f"{X.H.actor_name(c, BEN)} {said}"]
    X.left(c, BEN)  # passed over by the ladder: the wake names nobody
    assert "this task's Human" not in TW.body(c, E.task(c, row["id"]), to="bot:builder")
