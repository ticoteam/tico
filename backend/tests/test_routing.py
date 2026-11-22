"""Routing (backend/routing.py): availability on, async or off (set, ended by `until` at read, else working hours), and
route(task): the recent helper, then the relationship or task owner (async only when not urgent), then anyone on in
the owner's team, then the inbox fallback, so routed work always has a named person as owner and assignee. Routed
work with no pipeline goes on the Support pipeline, whose urgent stage rule tells the manager at once.
"""

import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import routing
from backend import stage_rule_timers as T
from backend import task_engine as E
from backend.store import H, encode
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers

START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)  # a Monday


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        X.bot(conn, "filer")
        yield conn


def _set(c, pid, value, until=None):
    c.execute("UPDATE humans SET availability=?, availability_until=? WHERE id=?", (value, until, pid))


def _all(c, value):
    for pid in ("ana", "ben", "cara"):
        _set(c, pid, value)


CUSTOMERS = itertools.cycle(["Acme", "Globex", "Initech", "Umbrella", "Hooli", "Stark", "Wayne", "Wonka", "Soylent"])


def _task(c, owner="bot:filer", labels=()):
    """A company-visible task on Support, filed by a bot and held by `owner`; each for another customer."""
    title = f"{next(CUSTOMERS)} can't sign in"
    row = X.new_task(c, "bot:filer", title, routing.support_pipeline(c), assignee=owner)
    c.execute("UPDATE tasks SET private=0, labels=? WHERE id=?", (json.dumps(list(labels)), row["id"]))
    return row["id"]


def _held(c, task_id):
    row = H.task(c, task_id)
    return row["owner_id"], row["assignee_id"]


def _team(c, team, *pids):
    found = c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()
    roster = json.loads(found[0])
    for person in roster["people"]:
        if person["id"] in pids:
            person["team"] = team
    c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(roster),))


def test_everyone_off_routes_to_the_inbox_fallback_as_owner_and_assignee(c):
    _all(c, "off")
    task_id = _task(c, owner="human:ben")
    c.execute("UPDATE pipelines SET manager_id='human:cara' WHERE id=?", (H.task(c, task_id)["pipeline_id"],))
    assert routing.route(c, task_id) == "human:cara"
    assert _held(c, task_id) == ("human:cara", "human:cara")
    # No person manages it: the company's default person, still a named owner.
    other = _task(c, owner="human:ben")
    c.execute("UPDATE pipelines SET manager_id=NULL WHERE id=?", (H.task(c, other)["pipeline_id"],))
    assert routing.route(c, other) == "human:" + H.default_human(c)
    assert _held(c, other)[1] == "human:" + H.default_human(c)


def test_an_async_owner_takes_routine_work_but_not_urgent_work(c):
    _all(c, "off")
    _set(c, "ben", "async")
    _set(c, "cara", "on")
    _team(c, "support", "ben", "cara")
    calm = _task(c, owner="human:ben")
    assert routing.route(c, calm) == "human:ben"
    urgent = _task(c, owner="human:ben", labels=["urgent"])
    assert routing.route(c, urgent) == "human:cara"  # anyone on in the owner's team
    assert _held(c, urgent) == ("human:cara", "human:cara")


def test_the_recent_helper_wins_while_on(c):
    _all(c, "on")
    task_id = _task(c, owner="human:ben")
    H._task_event(c, task_id, "human:cara", "comment", None, None, "Asked them for a screenshot")
    assert routing.route(c, task_id) == "human:cara"
    # Off, the helper is passed over: its owner takes it.
    again = _task(c, owner="human:ben")
    H._task_event(c, again, "human:cara", "comment", None, None, "Asked them for a screenshot")
    _set(c, "cara", "off")
    assert routing.route(c, again) == "human:ben"


def test_help_older_than_two_days_does_not_count(c, time_machine):
    _all(c, "on")
    task_id = _task(c, owner="human:ben")
    H._task_event(c, task_id, "human:cara", "comment", None, None, "Looked into it")
    time_machine.move_to(START + timedelta(days=3), tick=False)
    assert routing.route(c, task_id) == "human:ben"


def test_the_relationship_owner_comes_from_the_tasks_entity(c):
    """Read defensively: with tasks.entity_id (the relationship work) the entity's person owns the routing."""
    if "entity_id" not in routing._columns(c, "tasks"):
        c.execute("ALTER TABLE tasks ADD COLUMN entity_id TEXT NOT NULL DEFAULT ''")
    c.execute(
        "INSERT INTO entities(id, type, name, owner_type, owner_id, created_by, created_at, updated_at) "
        "VALUES('acme', 'organization', 'Acme', 'human', 'cara', 'test', ?, ?)",
        (H.now(), H.now()),
    )
    _all(c, "on")
    task_id = _task(c, owner="human:ben")
    c.execute("UPDATE tasks SET entity_id='acme' WHERE id=?", (task_id,))
    assert routing.route(c, task_id) == "human:cara"


def test_availability_until_ends_at_read_and_working_hours_decide(c, time_machine):
    person = routing.person_row(c, "ben")
    assert routing.effective(person, START) == "on"  # no working hours saved: on
    c.execute("UPDATE humans SET work_starts='08:00', work_ends='17:00', time_zone='UTC' WHERE id='ben'")
    person = routing.person_row(c, "ben")
    assert routing.effective(person, START) == "on"
    assert routing.effective(person, START.replace(hour=20)) == "async"
    shown = routing.set_availability(c, "human:ben", "ben", "off", "2030-01-07T10:00:00Z", START)
    assert shown["availability"] == "off" and shown["from"] == "set"
    person = routing.person_row(c, "ben")
    assert routing.effective(person, START + timedelta(minutes=59)) == "off"
    later = START + timedelta(hours=2)
    assert routing.view(person, later) | {"person": None} == {
        "person": None,
        "availability": "on",
        "set": None,
        "until": None,
        "from": "working_hours",
    }
    assert person["availability"] == "off"  # nothing rewrote the row: the end is computed at read


def test_urgent_support_waits_tell_the_manager_at_once(c, time_machine):
    lead = X.bot(c, "lead")
    task_id = _task(c, owner="human:ben", labels=["urgent"])
    calm = _task(c, owner="human:ben")
    c.execute("UPDATE pipelines SET manager_id=? WHERE id=?", (lead, H.task(c, task_id)["pipeline_id"]))
    time_machine.move_to(START + timedelta(minutes=61), tick=False)
    # The manager had its hourly wake ten minutes ago: a routine match waits for the next hour.
    earlier = H.shift(H.now(), minutes=-10)
    c.execute(
        "INSERT INTO stage_rule_marks(rule_id,task_id,mark,outcome,manager_id,matched_at,told_at) "
        "VALUES('r','t','m','told',?,?,?)",
        (lead, earlier, earlier),
    )
    T.sweep(c)
    wakes = c.execute(
        "SELECT body FROM messages WHERE json_extract(refs, '$.wake') = 'stage_rules' AND recipient_id=?", (lead,)
    ).fetchall()
    assert len(wakes) == 1 and H.task(c, task_id)["title"] in wakes[0][0]
    assert H.task(c, calm)["title"] not in wakes[0][0]  # the rule is for urgent work only


def test_a_routed_task_lands_on_support_with_a_person(api):
    from backend.tests.helpers.api import post

    made = post(api, "tasks", {"title": "Refund question from Acme", "route": True})
    task = made.get("task", made)
    with api.app.state.store.read() as c:
        row = E.task(c, task["id"])
        assert row["pipeline"]["preset"] == routing.SUPPORT
        assert row["stage"]["by_hand"] == 0
        assert H.is_human(row["owner_id"]) and row["assignee_id"] == row["owner_id"]
        rule = c.execute("SELECT pipeline_id FROM stage_rules WHERE id='sr_urgent_support'").fetchone()
        assert rule[0] == row["pipeline_id"]
    refused = api.post("/api/v2/tasks", json={"title": "Two owners", "route": True, "owner": "ops"}, headers=headers())
    assert refused.status_code == 422 and "names no owner" in refused.text


def test_a_person_sets_their_own_availability_and_an_admin_anyones(api, time_machine):
    time_machine.move_to(START, tick=False)
    mine = api.put(
        "/api/v2/humans/cara/availability",
        json={"availability": "async", "until": "2030-01-07T12:00:00Z"},
        headers=headers("cara-test"),
    )
    assert mine.status_code == 200, mine.text
    assert mine.json()["availability"] == "async"
    others = api.put("/api/v2/humans/ben/availability", json={"availability": "off"}, headers=headers("cara-test"))
    assert others.status_code == 403
    admin = api.put("/api/v2/humans/cara/availability", json={"availability": "off"}, headers=headers("ben-test"))
    assert admin.status_code == 200 and admin.json()["set"] == "off"
    cleared = api.put("/api/v2/humans/cara/availability", json={"availability": None}, headers=headers("ben-test"))
    assert cleared.json()["from"] == "working_hours"
    past = api.put(
        "/api/v2/humans/cara/availability",
        json={"availability": "off", "until": "2029-01-01T00:00:00Z"},
        headers=headers("cara-test"),
    )
    assert past.status_code == 422
    read = api.get("/api/v2/humans/cara/availability", headers=headers("ben-test"))
    assert read.status_code == 200 and read.json()["availability"] == "on"
