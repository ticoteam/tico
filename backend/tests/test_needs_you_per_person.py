"""One person's Needs you is read from the tasks that can reach them (waiting_on.candidates), not from every person's
lists. Whatever way an item reaches a person, their list is the one every person's pass gives them, item for item and
in order (Spec "Needs you per person", I39: AC-5 to AC-8).

Synthetic rows only (backend/tests/helpers/engine.py).
"""

import pytest

from backend import task_calls
from backend import task_engine as E
from backend import waiting_on as W
from backend.tests.helpers import engine as X

ANA, BEN, CARA = X.ANA, "human:ben", "human:cara"
PEOPLE = (ANA, BEN, CARA, "human:nobody")
BUILD = (
    {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Review", "owner": "assignee", "requirements": [{"kind": "ok", "person": ANA, "line": "OK the build"}]},
)
NOTES = (
    {"name": "Check", "owner": "assignee", "requirements": [{"kind": "ok", "person": "bot:helper", "line": "OK it"}]},
)
SHIP = ({"name": "Ship", "owner": "bot:builder", "requirements": [X.github("pr_open")]},)


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
        # Needs you shows each ask with its entry alone (task_calls.ask_entry): the one the task's whole view gives.
        open_ids = [found[0] for found in conn.execute("SELECT id FROM tasks WHERE status='open' ORDER BY id")]
        for person in sorted(people | set(every)):
            for task_id in open_ids:
                view = task_calls.task_view(conn, task_id, person) or {}
                entries = {}
                for entry in view.get("to_leave") or ():
                    entries.setdefault(entry["requirement_id"], entry)
                for requirement_id, entry in entries.items():
                    assert task_calls.ask_entry(conn, task_id, requirement_id, person) == entry, (person, task_id)


def _same_for_everyone(c):
    """Each person's list from their own read is the one every person's pass gives them."""
    every = W.needs_by_person(c)
    for person in sorted({*PEOPLE, *every}):
        assert W.needs(c, person) == every.get(person, []), person
    return every


def test_each_persons_list_is_the_one_every_persons_pass_gives_them(c):
    """AC-5: asks, own work, Paused bots, a cause the Owner fixes first, snoozes by a bot and by a person, a private
    task asking someone who may not read it, tasks that fall to a person by the ladder, and a person who left."""
    for slug in ("builder", "reviewer", "manager", "helper", "orphan"):
        X.bot(c, slug)
    X.pipeline(c, manager="bot:manager")
    X.pipeline(c, "eng", "Engineering", BUILD, manager="bot:manager")
    X.pipeline(c, "ops", "Operations", ({"name": "Sort", "requirements": [{"kind": "owner_done"}]},))
    X.pipeline(c, "ship", "Shipping", SHIP)
    X.pipeline(c, "notes", "Notes", NOTES)
    c.execute("UPDATE bot_config SET owner_id='ben' WHERE bot IN ('reviewer', 'helper')")
    c.execute("UPDATE bot_config SET owner_id='cara' WHERE bot='orphan'")
    X.new_task(c, ANA, "Renew the lease", assignee=ANA)
    his = X.new_task(c, ANA, "Call the bank", assignee=BEN)
    X.new_task(c, ANA, "Book the venue", assignee=CARA)
    E.add_requirement(c, his["id"], BEN, "answer", ANA, "Which bank?")
    asked = X.new_task(c, ANA, "Write the launch note", "eng", assignee="bot:builder")
    E.add_requirement(c, asked["id"], "bot:builder", "ok", BEN, "OK the headline", seen_version=asked["version"])
    # Ben's bots: one Paused, holding up a task of Ana's (AC-6); one asked to OK something and Paused too.
    X.new_task(c, ANA, "Check the facts", assignee="bot:reviewer")
    X.new_task(c, ANA, "Draft the FAQ", "notes", assignee="bot:builder")  # its stage's OK names Ben's other bot
    c.execute("UPDATE bots SET state='paused' WHERE id IN ('reviewer', 'helper')")
    # A bot snoozed a task with Ben's OK on it: the OK stays in his list. Ana set one aside: it is not listed.
    held = X.new_task(c, ANA, "Chase the vendor", assignee="bot:builder")
    E.add_requirement(c, held["id"], "bot:builder", "ok", BEN, "OK the reply", seen_version=held["version"])
    held = E.task(c, held["id"])
    E.snooze(c, held["id"], "bot:builder", "Vendor replies by Nov 14", "2026-11-14T00:00:00.000000Z", held["version"])
    aside = X.new_task(c, ANA, "Plan the offsite", assignee="bot:builder")
    E.add_requirement(c, aside["id"], "bot:builder", "ok", BEN, "OK the dates", seen_version=aside["version"])
    aside = E.task(c, aside["id"])
    E.snooze(c, aside["id"], ANA, "After the launch", "2026-11-20T00:00:00.000000Z", aside["version"])
    # A task made private after it asked Cara, who may not read it now: whoever has it shares it first.
    secret = X.new_task(c, ANA, "Price the merger", assignee="bot:builder")
    E.add_requirement(c, secret["id"], "bot:builder", "ok", CARA, "OK the price", seen_version=secret["version"])
    c.execute("UPDATE tasks SET private=1 WHERE id=?", (secret["id"],))
    # Falls to a person: nobody owns it and its pipeline has no manager; the ladder reached a person; an OK names nobody.
    X.new_task(c, BEN, "Sort the shared inbox", "ops")
    stuck = X.new_task(c, ANA, "Fix the footer", "eng", assignee="bot:builder")
    c.execute("UPDATE visits SET level='person' WHERE id=?", (stuck["visit"]["id"],))
    nobody = X.new_task(c, ANA, "Pick a logo", assignee="bot:builder")
    E.add_requirement(c, nobody["id"], "bot:builder", "ok", BEN, "OK a logo", seen_version=nobody["version"])
    c.execute("UPDATE requirements SET person_id=NULL WHERE line='OK a logo'")
    # Connect GitHub: the Owner fixes it first, once for every task it holds up (`Wait.fix`).
    X.new_task(c, ANA, "Open the PR", "ship", assignee="bot:builder")
    # Cara leaves: her bot that cannot run is the company's person's to fix, and her own task falls to its ladder.
    X.new_task(c, ANA, "Update the wiki", assignee="bot:orphan")
    c.execute("UPDATE bots SET state='quarantined' WHERE id='orphan'")
    X.left(c, CARA)

    every = _same_for_everyone(c)
    kinds = {person: sorted({n["kind"] for n in items}) for person, items in every.items()}
    assert kinds[ANA] == ["ask", "paused", "task"] and kinds[BEN] == ["ask", "paused", "task"]
    assert any("href" in n for n in every[ANA] if n["kind"] == "paused")  # Connect GitHub reached the Owner


def test_a_bot_its_owner_fixes_lists_every_task_it_holds_up_though_they_own_none(c):
    """AC-6: Ben owns the bot, not its tasks: one Paused item, every task it holds up."""
    X.bot(c, "reviewer")
    X.pipeline(c, manager=None)
    c.execute("UPDATE bot_config SET owner_id='ben' WHERE bot='reviewer'")
    held = sorted(X.new_task(c, ANA, f"Check the facts {n}", assignee="bot:reviewer")["id"] for n in range(3))
    for n in range(20):
        X.new_task(c, ANA, f"Company task {n}", assignee=ANA)
    c.execute("UPDATE bots SET state='paused' WHERE id='reviewer'")
    [paused] = W.needs(c, BEN)
    assert (paused["kind"], paused["bots"], sorted(paused["tasks"])) == ("paused", ["bot:reviewer"], held)
    _same_for_everyone(c)


def test_a_bot_with_no_row_is_the_company_persons_to_fix(c):
    """A stage's OK names a bot that was removed: only the company's person can fix it, and she does in her own read."""
    X.bot(c, "builder")
    X.pipeline(c, "notes", "Notes", NOTES)
    X.new_task(c, ANA, "Draft the FAQ", "notes", assignee="bot:builder")
    c.execute("UPDATE requirements SET person_id='bot:ghost' WHERE person_id='bot:helper'")
    every = _same_for_everyone(c)
    assert [(n["kind"], n["line"]) for n in every[ANA]] == [("paused", "ghost was removed")]


def test_two_people_reading_together_each_get_their_own_list(c):
    """AC-7: a task made private after it asked Cara falls to Ana, who may read it, to share it; Cara may not read it.
    Read in the same snapshot, one after the other, each list is the one they get reading alone, and only Ana's names
    it."""
    X.bot(c, "builder")
    X.pipeline(c, manager=None)
    secret = X.new_task(c, BEN, "Price the merger", assignee="bot:builder")
    E.add_requirement(c, secret["id"], "bot:builder", "answer", CARA, "Which bank?", seen_version=secret["version"])
    c.execute("UPDATE tasks SET private=1 WHERE id=?", (secret["id"],))
    alone = {person: W.needs(c, person) for person in PEOPLE}
    together = [(person, W.needs(c, person)) for person in (CARA, ANA, BEN, CARA, ANA)]
    assert all(found == alone[person] for person, found in together)
    assert [(n["task_id"], n["line"]) for n in alone[ANA]] == [(secret["id"], "Cara can't see this task")]
    assert alone[CARA] == [] and alone[BEN] == []
    _same_for_everyone(c)


def test_a_new_wait_is_in_the_next_read(c):
    """AC-8: nothing read before a change is reused after it."""
    X.bot(c, "builder")
    X.pipeline(c, manager=None)
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    assert W.needs(c, BEN) == []
    E.add_requirement(c, row["id"], "bot:builder", "ok", BEN, "OK the headline", seen_version=row["version"])
    assert [(n["kind"], n["task_id"]) for n in W.needs(c, BEN)] == [("ask", row["id"])]
    _same_for_everyone(c)


def test_a_person_reads_only_the_tasks_that_can_reach_them(c, monkeypatch):
    """AC-1 and AC-4: Ben, with nothing of his own, works out none of the company's tasks; the company's person works
    out only the ones that can fall to her, not each task Ben's bot or another person has."""
    X.bot(c, "builder")
    X.pipeline(c, manager=None)
    mine = X.new_task(c, ANA, "Call the bank", assignee=BEN)["id"]
    for n in range(30):
        X.new_task(c, ANA, f"Company task {n}", assignee="bot:builder")
    reads: list[str] = []
    of = W.of
    monkeypatch.setattr(W, "of", lambda conn, row, *a, **k: reads.append(row["id"]) or of(conn, row, *a, **k))
    assert [n["task_id"] for n in W.needs(c, BEN)] == [mine]
    assert reads == [mine]
    reads.clear()
    assert W.needs(c, CARA) == [] and reads == []
    _same_for_everyone(c)
