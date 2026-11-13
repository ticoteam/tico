"""Whose turn a task is (backend/waiting_on.py, docs/pipelines-v3.md "Whose turn it is").

Synthetic rows only (backend/tests/helpers/engine.py).
"""

import pytest

from backend import task_engine as E
from backend import waiting_on as W
from backend.tests.helpers import engine as X

ANA = X.ANA
BUILD = (
    {"name": "Ideas", "by_hand": True, "requests_move_on": True},
    {"name": "On deck"},
    {"name": "Build", "owner": "bot:builder", "task_limit": 1, "requirements": [{"kind": "owner_done"}]},
    {
        "name": "Review",
        "owner": "bot:reviewer",
        "requirements": [{"kind": "ok", "person": ANA, "line": "OK the build"}],
    },
)


@pytest.fixture
def c(company):
    """After each test, every person's own Needs you is the one every person's pass gives them: a new way a wait
    reaches a person that `waiting_on.candidates` misses fails here."""
    with company.transaction() as conn:
        yield conn
        every = W.needs_by_person(conn)
        people = {f"human:{found[0]}" for found in conn.execute("SELECT id FROM humans").fetchall()}
        for person in sorted(people | set(every)):
            assert W.needs(conn, person) == every.get(person, []), person


@pytest.fixture
def general(c):
    for slug in ("builder", "reviewer", "manager"):
        X.bot(c, slug)
    return X.pipeline(c, manager="bot:manager")


@pytest.fixture
def build(c, general):
    return X.pipeline(c, "eng", "Engineering", BUILD, manager="bot:manager")


def _states(c, task_id):
    return [(w.state, w.who) for w in W.waiting_on(c, task_id)]


def test_a_task_a_bot_owns_is_the_bots_turn_and_a_persons_task_is_theirs(c, general):
    mine = X.new_task(c, ANA, "Call the bank", assignee=ANA)
    bots = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    assert _states(c, mine["id"]) == [("person", ANA)]
    assert _states(c, bots["id"]) == [("bot", "bot:builder")]
    wait = W.headline(c, bots["id"])
    assert wait.requirement_id == general["requirements"]["To do"][0] and wait.since == bots["visit"]["entered_at"]


def test_a_task_with_no_owner_is_the_manager_bots_and_with_no_manager_a_persons(c, general):
    unowned = ({"name": "Sort", "requirements": [{"kind": "owner_done"}]},)
    X.pipeline(c, "intake", "Intake", unowned, manager="bot:manager")
    X.pipeline(c, "ops", "Operations", unowned)
    assert _states(c, X.new_task(c, ANA, "Sort the shared inbox", "intake")["id"]) == [("bot", "bot:manager")]
    assert _states(c, X.new_task(c, ANA, "Renew the domain", "ops")["id"]) == [("person", ANA)]  # whoever asked


def test_a_bot_that_cannot_run_shows_paused_with_the_cause(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    c.execute("UPDATE bots SET state='quarantined' WHERE id='builder'")
    wait = W.headline(c, row["id"])
    assert (wait.state, wait.who, wait.line) == ("paused", "bot:builder", "builder is quarantined")
    c.execute("UPDATE bots SET state='active' WHERE id='builder'")
    assert W.headline(c, row["id"]).state == "bot"  # it carries on by itself


def test_a_task_waiting_for_a_worktree_is_paused_and_nobody_is_woken_for_it(c, general):
    from backend import worktree_waits

    worktree_waits.ensure(c)
    row = X.new_task(c, ANA, "Fix the login page", assignee="bot:builder")
    other = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    c.execute(
        "INSERT INTO worktree_waits VALUES('w1',?,'bot:builder','acme/web',1,NULL,'waiting',NULL,'',?,?,NULL)",
        (row["id"], X.H.now(), X.H.now()),
    )
    wait = W.headline(c, row["id"])
    assert (wait.state, wait.who, wait.line) == ("paused", "bot:builder", "builder is waiting for a free worktree")
    assert W.turn(c, E.task(c, row["id"])) is None  # the wait wakes it once the worktree is ready, not never-stuck
    assert W.headline(c, other["id"]).state == "bot"  # the bot itself can run: its other tasks carry on
    c.execute("UPDATE worktree_waits SET state='reserved', link_id='l1' WHERE id='w1'")
    assert W.headline(c, row["id"]).line == "builder is waiting for its worktree to be set up"
    c.execute("UPDATE worktree_waits SET state='served', finished_at=updated_at WHERE id='w1'")
    assert W.headline(c, row["id"]).state == "bot"


def test_the_ladder_hands_the_turn_to_the_manager_bot_and_then_to_a_person(c, general):
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    c.execute("UPDATE visits SET level='manager' WHERE id=?", (row["visit"]["id"],))
    assert _states(c, row["id"]) == [("bot", "bot:manager")]
    c.execute("UPDATE visits SET level='person' WHERE id=?", (row["visit"]["id"],))
    waits = W.waiting_on(c, row["id"])  # the manager bot's owner has it, and the first thing it says is why
    assert [(w.state, w.who, w.line) for w in waits] == [
        ("person", ANA, "Stuck in To do"),
        ("person", ANA, "Say when the work is done"),
    ]
    assert [(n["kind"], n["line"]) for n in W.needs(c, ANA)] == [("task", "Stuck in To do")]


def test_snoozed_is_the_headline_and_the_rest_is_still_listed(c, general):
    row = X.new_task(c, ANA, "Chase the vendor", assignee="bot:builder")
    E.snooze(c, row["id"], "bot:builder", "Vendor replies by Nov 14", "2026-11-14T00:00:00.000000Z", row["version"])
    waits = W.waiting_on(c, row["id"])
    assert [w.state for w in waits] == ["snoozed", "bot"]
    assert (waits[0].who, waits[0].line, waits[0].until) == (
        "bot:builder",
        "Vendor replies by Nov 14",
        "2026-11-14T00:00:00.000000Z",
    )


def test_by_hand_room_and_a_named_person_follow_the_task_through_its_stages(c, build):
    first = X.new_task(c, "bot:builder", "Build the importer", "eng")  # a bot's idea: held by hand
    second = X.new_task(c, "bot:builder", "Build the exporter", "eng")
    wait = W.headline(c, first["id"])
    assert (wait.state, wait.who, wait.line) == ("by_hand", "eng-ideas", "Leaves Ideas by hand")
    asked = X.new_task(c, ANA, "Build the report", "eng")  # a person's request is not held: it waits for room
    wait = W.headline(c, asked["id"])
    assert (wait.state, wait.who, wait.line) == ("room", "eng-on-deck", "Moving to On deck")
    for row in (first, second):
        E.move(c, row["id"], E.NEXT, ANA)  # picked by hand: Ideas needs nothing
    assert W.all_met(W.waiting_on(c, first["id"]))  # On deck needs nothing: it waits only for a slot
    assert W.headline(c, first["id"]).line == "Moving to Build"
    E.fill_open_slots(c)
    wait = W.headline(c, second["id"])
    assert (wait.state, wait.who, wait.line) == ("room", "eng-build", "No room in Build")
    assert _states(c, first["id"]) == [("bot", "bot:builder")]
    X.say_done(c, E.task(c, first["id"]))
    E.advance(c, first["id"])
    wait = W.headline(c, first["id"])
    assert (wait.state, wait.who, wait.line) == ("person", ANA, "OK the build")
    assert wait.requirement_id == build["requirements"]["Review"][0]


def test_a_task_waits_on_the_tasks_it_filed_and_on_a_question_a_bot_asked(c, general):
    stages = ({"name": "In development", "owner": "bot:manager", "requirements": [{"kind": "linked_done"}]},)
    X.pipeline(c, "product", "Product", stages)
    parent = X.new_task(c, ANA, "Checkout flow", "product")
    assert _states(c, parent["id"]) == [("bot", "bot:manager")]  # nothing filed yet: the owner's to do
    child = X.new_task(c, ANA, "Build the cart", assignee="bot:builder", parent=parent["id"])
    assert _states(c, parent["id"]) == [("tasks", child["id"])]
    asked = E.add_requirement(
        c,
        child["id"],
        "bot:builder",
        "answer",
        ANA,
        "Card or invoice?",
        choices=["Card", "Invoice"],
        seen_version=child["version"],
    )
    waits = W.waiting_on(c, child["id"])
    assert [(w.state, w.who) for w in waits] == [("person", ANA)]  # the bot waits for the answer: it has no turn
    assert waits[0].requirement_id == asked["requirement_id"] and waits[0].line == "Card or invoice?"


def test_an_ended_task_waits_on_nothing_and_an_open_one_never_does(c, build):
    row = X.new_task(c, "bot:builder", "Build the importer", "eng")
    stops = []
    for step in (E.NEXT, E.NEXT, "Review", E.DONE):
        assert W.waiting_on(c, row["id"]), "an open task always waits on something"
        stops.append(W.headline(c, row["id"]).state)
        row = E.move(c, row["id"], step, ANA, "Moved by hand for the test")
    assert stops == ["by_hand", "room", "bot", "person"]
    assert W.waiting_on(c, row["id"]) == [] and W.headline(c, row["id"]) is None
    assert W.waiting_on(c, "no-such-task") == []


def test_needs_you_lists_what_is_a_persons_to_act_on_oldest_first_and_leaves_out_snoozed(c, build):
    mine = X.new_task(c, ANA, "Call the bank", assignee=ANA)
    bots = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    asked = E.add_requirement(c, bots["id"], "bot:builder", "ok", ANA, "OK the headline", seen_version=bots["version"])
    set_aside = X.new_task(c, ANA, "Renew the lease", assignee=ANA)
    E.snooze(c, set_aside["id"], ANA)
    listed = W.needs(c, ANA)
    assert [(n["kind"], n["task_id"], n["line"]) for n in listed] == [
        ("task", mine["id"], "Say when the work is done"),
        ("ask", bots["id"], "OK the headline"),
    ]
    assert listed[1]["requirement_id"] == asked["requirement_id"]
    assert W.needs(c, "human:nobody") == []


def test_a_persons_own_task_is_listed_once_however_much_is_missing(c, general):
    stages = ({"name": "Plan", "owner": "assignee", "requirements": [{"kind": "owner_done"}, {"kind": "linked_done"}]},)
    X.pipeline(c, "plans", "Plans", stages)
    row = X.new_task(c, ANA, "Plan the launch", "plans", assignee=ANA)
    assert [w.state for w in W.waiting_on(c, row["id"])] == ["person", "person"]
    listed = W.needs(c, ANA)
    assert [(n["kind"], n["task_id"]) for n in listed] == [("task", row["id"])]
    assert listed[0]["missing"] == ["Say when the work is done", "No task filed yet"]


def test_a_person_who_left_or_was_removed_is_never_chosen(c, general):
    ben = "human:ben"
    c.execute("UPDATE bot_config SET owner_id='ben' WHERE bot='reviewer'")
    his = X.new_task(c, ANA, "Call the bank", assignee=ben)["id"]
    asked = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    E.add_requirement(c, asked["id"], "bot:builder", "ok", ben, "OK the headline", seen_version=asked["version"])
    stuck = X.new_task(c, ANA, "Fix the footer", assignee="bot:builder")["id"]
    c.execute("UPDATE visits SET level='person', person_id='human:nobody' WHERE task_id=?", (stuck,))  # removed
    paused = X.new_task(c, ANA, "Check the facts", assignee="bot:reviewer")["id"]
    c.execute("UPDATE bots SET state='paused' WHERE id='reviewer'")
    assert {(n["kind"], n.get("task_id")) for n in W.needs(c, ben)} == {
        ("task", his),
        ("ask", asked["id"]),
        ("paused", None),
    }
    X.left(c, ben)
    assert W.needs(c, ben) == []
    # Each falls through to the next person who is here: the first person above the manager bot, Ana.
    assert _states(c, his) == [("person", ANA)]
    assert [(w.state, w.who, w.ask) for w in W.waiting_on(c, asked["id"])] == [("person", ANA, True)]
    assert _states(c, stuck) == [("person", ANA), ("person", ANA)]
    listed = {(n["kind"], n.get("task_id")) for n in W.needs(c, ANA)}
    assert {("task", his), ("ask", asked["id"]), ("task", stuck)} <= listed
    assert [n["tasks"] for n in W.needs(c, ANA) if n["kind"] == "paused"] == [[paused]]  # the company owner fixes it


def test_every_persons_needs_you_comes_from_one_pass_over_the_tasks(c, general, monkeypatch):
    """The never-stuck check compares each person's turns with their Needs you. One pass builds every person's list:
    each task is read once, not once a person, and each list is the one `needs` gives that person."""
    ben = "human:ben"
    c.execute("UPDATE bot_config SET owner_id='ben' WHERE bot='reviewer'")
    his = X.new_task(c, ANA, "Call the bank", assignee=ben)["id"]
    hers = X.new_task(c, ANA, "Renew the lease", assignee=ANA)["id"]
    asked = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    E.add_requirement(c, asked["id"], "bot:builder", "ok", ben, "OK the headline", seen_version=asked["version"])
    E.add_requirement(c, his, ANA, "answer", ANA, "Which bank?")
    paused = X.new_task(c, ANA, "Check the facts", assignee="bot:reviewer")["id"]
    c.execute("UPDATE bots SET state='paused' WHERE id='reviewer'")
    reads: list[str] = []
    task = E.task
    monkeypatch.setattr(E, "task", lambda conn, task_id, *a, **k: reads.append(task_id) or task(conn, task_id, *a, **k))
    every = W.needs_by_person(c)
    assert sorted(reads) == sorted({his, hers, asked["id"], paused})  # each open task once
    assert set(every) == {ANA, ben}
    assert {(n["kind"], n.get("task_id")) for n in every[ben]} == {
        ("task", his),
        ("ask", asked["id"]),
        ("paused", None),
    }
    assert {(n["kind"], n.get("task_id")) for n in every[ANA]} == {("task", hers), ("ask", his)}
    for person in (ANA, ben, "human:nobody"):
        assert W.needs(c, person) == every.get(person, [])


def test_an_ok_that_names_a_bot_is_that_bots_turn(c, general):
    """A stage's OK may name a bot (docs/pipelines-v3.md, kind 2); an OK added to one task is asked of a person."""
    from backend.hubdb import Refused

    ok = {"kind": "ok", "person": "bot:reviewer", "line": "OK the wording"}
    stages = ({"name": "Check", "owner": ANA, "requirements": [{"kind": "owner_done"}, ok]},)
    X.pipeline(c, "notes", "Notes", stages)
    row = X.new_task(c, ANA, "Write the launch note", "notes", assignee=ANA)
    with pytest.raises(Refused, match="An OK is asked of a person"):
        E.add_requirement(c, row["id"], ANA, "ok", "bot:reviewer", "OK the wording")
    waits = W.waiting_on(c, row["id"])
    assert [(w.state, w.who, w.ask) for w in waits] == [("person", ANA, False), ("bot", "bot:reviewer", True)]
    assert [n["kind"] for n in W.needs(c, ANA)] == ["task"]  # the reviewer's OK is not in Ana's list


def test_a_person_who_cannot_sign_in_is_never_chosen_and_someone_who_can_always_is(c, general, monkeypatch):
    from backend import never_stuck as NS
    from backend import task_requirements as R

    ben = "human:ben"
    X.pipeline(c, "plans", "Plans", ({"name": "Plan", "owner": ben, "requirements": [{"kind": "owner_done"}]},))
    c.execute("UPDATE pipelines SET owner_id=?, manager_id=? WHERE id='plans'", (ben, ben))
    c.execute("UPDATE bot_config SET owner_id='ben' WHERE bot='builder'")
    task_id = X.new_task(c, "bot:builder", "Plan the offsite", "plans")["id"]
    assert _states(c, task_id) == [("person", ben)]
    X.no_sign_in(c, ben)  # on the chart, but sign-in is off: he cannot act
    assert not R.active_person(c, ben)
    # The stage, the pipeline and the bot that asked all fall to Ben, and the company owner does not resolve: the
    # person who has been here longest has it.
    monkeypatch.setattr(W.pipeline_rights, "company_owner", lambda conn: "")
    who = W.person_for(c, E.task(c, task_id))
    assert who and who != ben and R.active_person(c, who)
    assert _states(c, task_id) == [("person", who)]
    assert [n["task_id"] for n in W.needs(c, who) if n["kind"] == "task"] == [task_id]
    assert NS.rules_hold(c) == []
    monkeypatch.setattr(W, "person_for", lambda conn, row: None)  # a turn that names nobody is a broken rule
    assert NS.rules_hold(c) == [f"3: task {task_id} is a person's turn that names nobody"]


def test_the_top_of_the_ladder_is_the_first_person_above_the_manager_bot_on_the_org_chart(c, general):
    """C16: the person the ladder reaches is the first person above the pipeline's manager bot on the org chart, not
    the person whose computer it runs on. Someone on the way who cannot act is passed over to the next one up; with
    nobody above it, the company's owner."""
    from backend import access as Access
    from backend.views import roster

    for slug, name in (("bo", "Bo"), ("cy", "Cy"), ("di", "Di")):
        c.execute("INSERT INTO humans(id,name,email) VALUES(?,?,?)", (slug, name, f"{slug}@acme.example"))
    whole = roster(c)
    people = [
        *whole["people"],
        {"id": "bo", "name": "Bo", "reports_to": "di"},
        {"id": "cy", "name": "Cy"},
        {"id": "di", "name": "Di"},
    ]
    Access.save_roster(c, {**whole, "people": people})
    c.execute("UPDATE pipelines SET owner_id=NULL WHERE id='general'")
    # The manager reports to Bo and runs on Cy's computer; the builder runs on Cy's too.
    c.execute("UPDATE bot_config SET reports_to='human:bo', owner_id='cy' WHERE bot='manager'")
    c.execute("UPDATE bot_config SET reports_to='bot:manager', owner_id='cy' WHERE bot='builder'")
    task_id = X.new_task(c, "bot:reviewer", "Write the launch note", assignee="bot:builder")["id"]
    c.execute("UPDATE visits SET level='person', person_id=NULL WHERE task_id=?", (task_id,))
    assert W.person_for(c, E.task(c, task_id)) == "human:bo"
    X.no_sign_in(c, "human:bo")  # passed over: the next person up
    assert W.person_for(c, E.task(c, task_id)) == "human:di"
    c.execute("UPDATE bot_config SET reports_to=NULL WHERE bot='manager'")  # nobody above it: the company's owner
    assert W.person_for(c, E.task(c, task_id)) == W.company_person(c)


def test_priming_bots_reads_their_run_back_off_once_for_all_of_them(c, general):
    # Perf I38: `prime_bots` read each bot's state, settings, computer and onboarding together, but `cannot_run` still
    # read each bot's run_backoff row on its own (about 300 point reads a pass). One read for all now, misses included,
    # and a bot held for failing runs still says so.
    from backend import run_backoff, task_wakes

    bots = [X.bot(c, f"worker-{k}") for k in range(8)]
    c.execute("INSERT INTO run_backoff(bot, fails, held_at) VALUES('worker-3', 5, ?)", (X.H.now(),))
    reads = []
    c.set_trace_callback(lambda sql: reads.append(sql) if "FROM run_backoff" in sql else None)
    try:
        task_wakes.prime_bots(c, bots)
        causes = [task_wakes.cannot_run(c, b) for b in bots]
    finally:
        c.set_trace_callback(None)
    assert causes == [task_wakes.RUNS_FAILING if b == "bot:worker-3" else None for b in bots]
    assert len(reads) == 1, reads  # eight before: one a bot
