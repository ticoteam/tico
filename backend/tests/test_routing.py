"""Routing (backend/routing.py), through `POST /api/v2/tasks` with `route: true`: the person is picked before the task
is filed (the recent helper on the relationship, then its owner, async only when the work is not urgent, then anyone on
in the owner's team, then the inbox fallback), and the task is filed as theirs, owner and assignee. A private task goes
only to someone who may read it, or is refused. Availability is on, async or off: set (ended by `until` at read), else
Calendar's effective working hours. Routed work lands on the Support pipeline, whose timers tell its manager: a day
quiet in the hourly batch, an hour quiet on an urgent task at once.
"""

import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import routing
from backend import stage_rule_timers as T
from backend.entities import store as ES
from backend.store import H, encode
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.company import bot
from backend.tests.helpers.member_bots import agent_ops, turn  # noqa: F401
from backend.tests.test_jira_intake import _create, jira  # noqa: F401

START = datetime(2030, 1, 7, 10, 0, tzinfo=timezone.utc)  # a Monday, inside Calendar's default hours (UTC)
TITLES = itertools.cycle(["Acme", "Globex", "Initech", "Umbrella", "Hooli", "Stark", "Wayne", "Wonka", "Soylent"])


@pytest.fixture
def app(api, time_machine):
    time_machine.move_to(START, tick=False)
    return api


def _db(api):
    return api.app.state.store.transaction()


def _route(api, token="ana-test", expected=200, **body):
    """A routed task's id (or the refusal), each with its own title."""
    r = api.post(
        "/api/v2/tasks",
        json={"title": f"{next(TITLES)} cannot sign in", "route": True, **body},
        headers=headers(token),
    )
    assert r.status_code == expected, r.text
    data = r.json()
    return data["task"]["id"] if expected == 200 else data


def _held(api, task_id):
    with _db(api) as c:
        row = H.task(c, task_id)
        return row["owner_id"], row["assignee_id"]


def _set(api, value, *pids):
    with _db(api) as c:
        for pid in pids:
            c.execute("UPDATE humans SET availability=?, availability_until=NULL WHERE id=?", (value, pid))


def _account(api, owner="human:ben", domain="acme.example"):
    with _db(api) as c:
        entity = ES.upsert(c, "human:ana", "organization", match={"domain": domain}, name=domain)["id"]
        ES.set_owner(c, "human:ana", entity, owner)
        return entity


def _team(api, team, *pids):
    with _db(api) as c:
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for person in roster["people"]:
            if person["id"] in pids:
                person["team"] = team
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(roster),))


def _support(api, **fields):
    with _db(api) as c:
        sid = routing.support_pipeline(c)
        for key, value in fields.items():
            c.execute(f"UPDATE pipelines SET {key}=? WHERE id=?", (value, sid))
        return sid


def test_a_routed_task_is_filed_as_its_persons_on_the_support_pipeline(app):
    task_id = _route(app)
    with _db(app) as c:
        row = H.task(c, task_id)
        assert c.execute("SELECT preset FROM pipelines WHERE id=?", (row["pipeline_id"],)).fetchone()[0] == "support"
        assert H.is_human(row["owner_id"]) and row["assignee_id"] == row["owner_id"]
        # Filed as theirs: no "no owner" pick, no reassign after the fact.
        kinds = [r[0] for r in c.execute("SELECT kind FROM history_events WHERE subject_id=?", (task_id,))]
        assert "reassign" not in kinds
        assert not c.execute("SELECT 1 FROM messages WHERE body LIKE '%has no owner%'").fetchone()
        rules = {r[0] for r in c.execute("SELECT id FROM stage_rules WHERE pipeline_id=?", (row["pipeline_id"],))}
        assert rules == {"sr_support_waits", "sr_urgent_support"}
    assert "names no owner" in str(_route(app, expected=422, owner="ops"))
    assert "goes to Support" in json.dumps(_route(app, expected=422, pipeline="general"))
    with _db(app) as c:  # Support has a manager to wake even with no General Manager bot: the company's person
        assert c.execute("SELECT manager_id FROM pipelines WHERE id=?", (row["pipeline_id"],)).fetchone()[0]
    named = _route(app, pipeline=row["pipeline_id"])  # naming Support itself is fine
    assert H.is_human(_held(app, named)[0])


def test_routing_is_refused_when_support_is_changed_to_be_moved_by_hand_or_retired(app):
    sid = _support(app)
    with _db(app) as c:  # moved by hand: no timer would ever look at it
        c.execute("UPDATE stages SET by_hand=1 WHERE pipeline_id=? AND kind='work'", (sid,))
    assert "moved by hand" in json.dumps(_route(app, expected=422))
    _support(app, retired_at=H.now())
    assert "retired" in json.dumps(_route(app, expected=422))


def test_a_routed_create_a_company_rule_would_hold_is_refused_not_held(app):
    from backend.tests.helpers.org_rules import org
    from backend.tests.helpers.org_rule_check import NEEDLE, Judge, color_rule

    org(app)
    app.app.state.judge = Judge(app.app.state.store)
    color_rule(app)
    bot(app, "ops")
    refused = _route(app, token="ops-test", expected=422, message=f"Their {NEEDLE} red.")
    assert "Held by a company rule" in json.dumps(refused)
    with _db(app) as c:
        assert not c.execute("SELECT 1 FROM org_rule_holds").fetchone()  # nothing held to Send anyway later


def test_everyone_off_goes_to_the_inbox_fallback(app):
    _set(app, "off", "ana", "ben", "cara")
    first = _route(app)
    assert _held(app, first) == ("human:ana", "human:ana")  # the company's person
    _support(app, manager_id="human:cara")
    second = _route(app)
    assert _held(app, second) == ("human:cara", "human:cara")


def test_an_async_owner_takes_routine_work_but_not_urgent_work(app):
    acme = _account(app, owner="human:ben")
    _set(app, "off", "ana")
    _set(app, "async", "ben")
    _team(app, "support", "ben", "cara")
    assert _held(app, _route(app, entity=acme))[0] == "human:ben"
    urgent = _route(app, entity=acme, labels=["urgent"])
    assert _held(app, urgent) == ("human:cara", "human:cara")  # anyone on in the owner's team


def test_the_recent_helper_on_the_relationship_wins_while_on(app):
    acme = _account(app, owner="human:ben")
    earlier = _route(app, entity=acme, private=False)
    with _db(app) as c:
        H._task_event(c, earlier, "human:cara", "comment", None, None, "Asked them for a screenshot")
    assert _held(app, _route(app, entity=acme))[0] == "human:cara"
    _set(app, "off", "cara")
    assert _held(app, _route(app, entity=acme))[0] == "human:ben"


def test_help_older_than_two_days_does_not_count(app, time_machine):
    acme = _account(app, owner="human:ben")
    earlier = _route(app, entity=acme, private=False)
    with _db(app) as c:
        H._task_event(c, earlier, "human:cara", "comment", None, None, "Looked into it")
    time_machine.move_to(START + timedelta(days=7), tick=False)  # the next Monday
    assert _held(app, _route(app, entity=acme))[0] == "human:ben"


def test_private_work_is_not_routed(app):
    bot(app, "ops")
    refused = _route(app, token="ops-test", expected=422, private=True, readers=["human:ana"])
    assert "Private tasks aren't routed" in json.dumps(refused)
    made = post(app, "tasks", {"title": "Contract review for Acme", "owner": "ben", "private": True})
    parent = (made.get("task") or made)["id"]
    under = _route(app, expected=422, parent=parent, private=False)  # its owner would read the private parent
    assert "Private tasks aren't routed" in json.dumps(under)
    with _db(app) as c:
        assert not c.execute("SELECT 1 FROM pipelines WHERE preset='support'").fetchone()  # nothing was made


def test_a_stage_rule_that_holds_the_person_out_files_nothing(app):
    sid = _support(app)
    with _db(app) as c:
        now = H.now()
        c.execute(
            "INSERT INTO stage_rules (id, name, pipeline_id, stages, event, timer, cond, jev, then_do, then_params, "
            "reason, status, builtin, created_by, created_at, updated_at) VALUES ('sr_people_out', 'No people', ?, "
            "'[]', 'enters', '{}', ?, '', 'block', '{}', 'Bots only here', 'active', '', 'keeper', ?, ?)",
            (sid, json.dumps({"owner_kind": "human"}), now, now),
        )
        before = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    refused = _route(app, expected=422)
    assert "routed work needs a named owner" in json.dumps(refused)
    with _db(app) as c:
        assert c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == before


def test_a_routed_create_for_a_filed_item_returns_its_task_before_routing(app, jira):
    link = f"jira:issue:PROJ-77@{jira.tools['acme.atlassian.net']}"
    first = _create(app, link)
    again = post(app, "tasks", {"title": "Fix the export", "route": True, "private": True, "primary_link": link})
    assert again.get("found") is True and (again.get("task") or again)["id"] == (first.get("task") or first)["id"]


def test_the_dry_run_names_the_person_routing_would_pick(app):
    shown = post(app, "tasks/dry-run", {"title": "Refund question from Acme", "route": True})
    assert shown["ok"] is True and shown["owner"] == "human:ana", shown


def test_availability_ends_at_read_and_working_hours_decide(app):
    with _db(app) as c:
        ben = routing.person_row(c, "ben")
        assert routing.effective(c, ben, START) == "on"
        assert routing.effective(c, ben, datetime(2030, 1, 6, 12, 0, tzinfo=timezone.utc)) == "async"  # a Sunday
        shown = routing.set_availability(c, "human:ben", "ben", "off", "2030-01-07T11:00:00Z", START)
        assert shown["availability"] == "off" and shown["from"] == "set"
        ben = routing.person_row(c, "ben")
    with _db(app) as c:
        assert routing.effective(c, ben, START + timedelta(minutes=59)) == "off"
        later = routing.view(c, ben, START + timedelta(hours=2))
    assert (later["availability"], later["set"], later["from"]) == ("on", None, "working_hours")
    assert ben["availability"] == "off"  # nothing rewrote the row: the end is computed at read


def test_a_tag_added_to_a_quiet_task_is_noticed(app, time_machine):
    _support(app, manager_id="bot:ops")
    task_id = _route(app, private=False)
    time_machine.move_to(START + timedelta(minutes=61), tick=False)
    with _db(app) as c:
        T.sweep(c)  # quiet an hour, not urgent: judged and passed over
    assert _wakes(app, "bot:ops") == []
    assert post(app, f"tasks/{task_id}", {"labels": ["urgent"]})
    time_machine.move_to(START + timedelta(minutes=65), tick=False)
    with _db(app) as c:
        T.sweep(c)
    wakes = _wakes(app, "bot:ops")
    assert len(wakes) == 1 and _title(app, task_id) in wakes[0]


def test_both_support_timers_on_one_task_give_its_manager_one_wake(app, time_machine):
    _support(app, manager_id="bot:ops")
    task_id = _route(app, labels=["urgent"], private=False)
    time_machine.move_to(START + timedelta(hours=25), tick=False)  # a day quiet and urgent: both rules match
    with _db(app) as c:
        T.sweep(c)
    wakes = _wakes(app, "bot:ops")
    assert len(wakes) == 1 and wakes[0].count(_title(app, task_id)) == 1


def test_a_due_date_late_label_does_not_quiet_the_support_timers(app, time_machine):
    _support(app, manager_id="bot:ops")
    task_id = _route(app, labels=["urgent"], private=False, due="2030-01-07T12:00:00Z")
    time_machine.move_to(START + timedelta(hours=25), tick=False)
    with _db(app) as c:  # Late by its due date: a person's task gets the label alone, and wakes nobody
        c.execute("UPDATE visits SET late_told_at=? WHERE task_id=? AND left_at IS NULL", (H.now(), task_id))
        T.sweep(c)
    wakes = _wakes(app, "bot:ops")
    assert len(wakes) == 1 and _title(app, task_id) in wakes[0]


def test_the_dry_run_reports_a_filed_item_as_found_without_routing(app, jira):
    link = f"jira:issue:PROJ-78@{jira.tools['acme.atlassian.net']}"
    first = _create(app, link)
    shown = post(
        app, "tasks/dry-run", {"title": "Fix the export", "route": True, "private": True, "primary_link": link}
    )
    assert shown["found"] is True and shown["task"] == (first.get("task") or first)["id"], shown


def test_a_private_support_pipeline_refuses_routing(app):
    _support(app, private=1)
    assert "Private tasks aren't routed" in json.dumps(_route(app, expected=422))


def test_a_support_manager_who_owns_the_task_is_still_told(app, time_machine):
    _set(app, "off", "ana", "ben", "cara")
    _support(app, manager_id="human:ana")
    task_id = _route(app, private=False)
    assert _held(app, task_id)[0] == "human:ana"  # the fallback, and Support's manager too
    time_machine.move_to(START + timedelta(hours=25), tick=False)
    with _db(app) as c:
        T.sweep(c)
    wakes = _wakes(app, "human:ana")
    assert len(wakes) == 1 and _title(app, task_id) in wakes[0]


def test_one_urgent_quiet_period_wakes_the_manager_once(app, time_machine):
    _support(app, manager_id="bot:ops")
    task_id = _route(app, labels=["urgent"], private=False)
    time_machine.move_to(START + timedelta(hours=2), tick=False)
    with _db(app) as c:
        T.sweep(c)  # Urgent support waits tells the manager
    time_machine.move_to(START + timedelta(hours=25), tick=False)
    with _db(app) as c:
        T.sweep(c)  # Support waits: the same quiet period, told already
    wakes = _wakes(app, "bot:ops")
    assert len(wakes) == 1 and _title(app, task_id) in wakes[0]


def test_the_recent_helper_is_read_as_the_person_whose_request_is_carried(app, agent_ops):
    """Agent Ops filing a person's request (request_id turn) routes as that person: a task they cannot read, here one
    held by Ana's own inbox bot, names no helper for Cara's request, though Agent Ops itself reads it."""
    from backend.tests.helpers.api import restrict

    acme = _account(app, owner="human:ben")
    with _db(app) as c:  # Ana's inbox bot: Ana and Agent Ops read its work, Cara does not
        restrict(c, "inbox", people=["ana"], bots=["botops"])
    made = post(app, "tasks", {"title": "Acme's renewal", "owner": "inbox", "entity": acme, "private": False})
    inbox_task = (made.get("task") or made)["id"]
    with _db(app) as c:
        H._task_event(c, inbox_task, "human:ana", "comment", None, None, "Called them back")
    attempt = turn(app, agent_ops, person="cara-test", text="Route Acme's question to whoever is on")
    routed = _route(app, token=attempt["token"], entity=acme, request_id="turn")
    assert _held(app, routed)[0] == "human:ben"  # its owner: Ana's work on a task Cara cannot read does not count


def _wakes(api, manager):
    with _db(api) as c:
        return [
            r[0]
            for r in c.execute(
                "SELECT body FROM messages WHERE json_extract(refs, '$.wake') = 'stage_rules' AND recipient_id=?",
                (manager,),
            )
        ]


def _title(api, task_id):
    with _db(api) as c:
        return H.task(c, task_id)["title"]


def test_urgent_support_waits_wake_the_manager_once_at_once_and_not_after_untagging(app, time_machine):
    _support(app, manager_id="bot:ops")
    first = _route(app, labels=["urgent"], private=False)
    second = _route(app, labels=["urgent"], private=False)
    untagged = _route(app, labels=["urgent"], private=False)
    calm = _route(app, private=False)
    assert post(app, f"tasks/{untagged}", {"labels": []})
    time_machine.move_to(START + timedelta(minutes=61), tick=False)
    with _db(app) as c:  # the manager's hourly wake went ten minutes ago: routine matches would wait
        earlier = H.shift(H.now(), minutes=-10)
        c.execute(
            "INSERT INTO stage_rule_marks(rule_id,task_id,mark,outcome,manager_id,matched_at,told_at) "
            "VALUES('r','t','m','told','bot:ops',?,?)",
            (earlier, earlier),
        )
        T.sweep(c)
    wakes = _wakes(app, "bot:ops")
    assert len(wakes) == 1
    assert _title(app, first) in wakes[0] and _title(app, second) in wakes[0]
    assert _title(app, untagged) not in wakes[0] and _title(app, calm) not in wakes[0]
    with _db(app) as c:  # a routine tell waiting for the manager's hour is still waiting
        c.execute(
            "INSERT INTO stage_rule_marks(rule_id,task_id,mark,outcome,manager_id,matched_at) "
            "VALUES('sr_support_waits',?,'routine','pending','bot:ops',?)",
            (calm, H.now()),
        )
        T.sweep(c)
    assert len(_wakes(app, "bot:ops")) == 1


def test_routine_support_work_held_by_a_person_goes_to_the_manager_after_a_quiet_day(app, time_machine):
    _support(app, manager_id="bot:ops")
    task_id = _route(app, private=False)
    assert H.is_human(_held(app, task_id)[0])
    time_machine.move_to(START + timedelta(hours=23), tick=False)
    with _db(app) as c:
        T.sweep(c)
    assert _wakes(app, "bot:ops") == []
    time_machine.move_to(START + timedelta(hours=25), tick=False)
    with _db(app) as c:
        T.sweep(c)
    wakes = _wakes(app, "bot:ops")
    assert len(wakes) == 1 and _title(app, task_id) in wakes[0] and "Support waits" in wakes[0]


def test_a_person_sets_their_own_availability_and_an_admin_anyones(app):
    mine = app.put(
        "/api/v2/humans/cara/availability",
        json={"availability": "async", "until": "2030-01-07T20:00:00Z"},
        headers=headers("cara-test"),
    )
    assert mine.status_code == 200, mine.text
    assert mine.json()["availability"] == "async"
    others = app.put("/api/v2/humans/ben/availability", json={"availability": "off"}, headers=headers("cara-test"))
    assert others.status_code == 403
    admin = app.put("/api/v2/humans/cara/availability", json={"availability": "off"}, headers=headers("ben-test"))
    assert admin.status_code == 200 and admin.json()["set"] == "off"
    cleared = app.put("/api/v2/humans/cara/availability", json={"availability": None}, headers=headers("ben-test"))
    assert cleared.json()["from"] == "working_hours"
    past = app.put(
        "/api/v2/humans/cara/availability",
        json={"availability": "off", "until": "2029-01-01T00:00:00Z"},
        headers=headers("cara-test"),
    )
    assert past.status_code == 422
    read = app.get("/api/v2/humans/cara/availability", headers=headers("ben-test"))
    assert read.status_code == 200 and read.json()["availability"] == "on"
    assert app.get("/api/v2/humans/nobody/availability", headers=headers("ben-test")).status_code == 404
