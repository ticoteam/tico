"""A rule's due date with its routine (backend/rule_due_routine.py, docs/rules-plan.md §4): turning it on makes one "Before
a due date" automation linked by routine_id, whose run opens the owner's task on time; completing that task marks the
day Satisfied, a cancel or the keeper's close doesn't, and reopening takes it back; a day marked by hand is skipped; an
edit moves the routine to the new row; removing the due date turns it off; Due soon follows its lead time."""
import json
from datetime import datetime, timedelta, timezone

from backend import rule_due as D
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401  (the api fixture)
from backend.tests.test_automation_api import patch
from backend.tests.test_automation_due import drain, runs_of, tick
from backend.tests.test_automation_runs import sql
from backend.tests.test_org_rules import org, rule

TEXT = "File the Acme sales tax return."


def linked(api, first="2030-03-01", repeats="once", days=14, owner="ops"):
    org(api)
    r = rule(api, {"kind": "internal", "text": TEXT, "who": {"bots": ["ops"]}})
    due = post(api, f"rules/{r['id']}/due", {"first_due": first, "repeats": repeats, "owner": owner,
                                              "timezone": "UTC", "task_days": days})
    return r, due


def automation(api, aid):
    return get(api, f"automations/{aid}")


def tasks(api, aid):
    return sql(api, "SELECT id, title, owner, due, status, occurrence FROM tasks WHERE occurrence LIKE ? ORDER BY due",
               (aid + ":%",))


def write(api, fn, *args, **kw):
    with api.app.state.store.transaction() as c:
        return fn(c, *args, **kw)


def marks(api, due_id):
    return sql(api, "SELECT due_on, satisfied_by, via, task_id FROM rule_due_status WHERE due_date_id=?", (due_id,))


def test_turning_the_routine_on_makes_one_linked_automation_whose_run_opens_the_owners_task_on_time(api):
    _, due = linked(api)
    aid = due["routine_id"]
    assert aid and due["task_days"] == 14
    auto = automation(api, aid)
    assert auto["trigger"] == {"type": "due", "first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC",
                               "rule_due_id": due["id"]}
    assert auto["owner"] == "bot:ops" and auto["steps"][0]["args"]["title"] == TEXT
    assert auto["steps"][0]["args"]["owner"] == "bot:ops"
    assert auto["next_due"] == "2030-03-01"
    assert len(sql(api, "SELECT id FROM automations WHERE flow LIKE ?", (f"%{due['id']}%",))) == 1
    tick(api, datetime(2030, 2, 14, 23, 59, tzinfo=timezone.utc))
    assert tasks(api, aid) == []
    tick(api, datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
    drain(api)
    [task] = tasks(api, aid)
    assert (task["title"], task["owner"], task["due"]) == (TEXT, "bot:ops", "2030-03-01T23:59:59+00:00")


def test_completing_the_task_marks_the_day_and_reopening_takes_it_back(api):
    _, due = linked(api)
    aid = due["routine_id"]
    tick(api, datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
    [task] = tasks(api, aid)
    write(api, H.task_update, "human:ana", task["id"], status="done")       # a mover's or manager's done is not
    assert marks(api, due["id"]) == []
    write(api, H.task_update, "bot:ops", task["id"], status="doing")
    write(api, H.task_update, "bot:ops", task["id"], status="done")
    assert marks(api, due["id"]) == [{"due_on": "2030-03-01", "satisfied_by": "bot:ops", "via": "routine",
                                      "task_id": task["id"]}]
    write(api, H.task_update, "bot:ops", task["id"], status="declined")     # a finished task declined keeps its mark
    assert len(marks(api, due["id"])) == 1
    write(api, H.task_update, "bot:ops", task["id"], status="doing")
    assert marks(api, due["id"]) == []
    actions = sql(api, "SELECT before, after FROM audit_log WHERE object=? AND via='routine'",
                  ("org_rule:" + due["rule_id"],))
    assert len(actions) == 2      # the mark and its removal are both on record


def test_a_keeper_close_or_a_decline_marks_nothing_and_a_persons_close_does(api):
    _, due = linked(api, first="2030-03-01", repeats="monthly", days=0)
    aid = due["routine_id"]
    tick(api, datetime(2030, 4, 1, 0, 0, 5, tzinfo=timezone.utc))
    march, april = tasks(api, aid)
    write(api, H.task_close, H.KEEPER, march["id"])
    write(api, H.task_update, "bot:ops", april["id"], status="declined")
    assert marks(api, due["id"]) == []
    tick(api, datetime(2030, 5, 1, 0, 0, 5, tzinfo=timezone.utc))
    may = tasks(api, aid)[-1]
    write(api, H.task_close, "human:ana", may["id"])
    assert [(m["due_on"], m["satisfied_by"]) for m in marks(api, due["id"])] == [("2030-05-01", "human:ana")]
    write(api, H.task_update, "bot:ops", march["id"], status="done")       # the owner's done after the keeper's close
    assert sorted(m["due_on"] for m in marks(api, due["id"])) == ["2030-03-01", "2030-05-01"]


def test_a_late_completion_of_an_older_occurrence_marks_that_one(api):
    _, due = linked(api, first="2030-01-31", repeats="monthly", days=0)
    aid = due["routine_id"]
    tick(api, datetime(2030, 2, 28, 0, 0, 5, tzinfo=timezone.utc))
    january, february = tasks(api, aid)
    write(api, H.task_update, "bot:ops", january["id"], status="done")
    assert [m["due_on"] for m in marks(api, due["id"])] == ["2030-01-31"]
    assert february["status"] == "open"


def test_a_day_marked_by_hand_is_skipped_and_opens_no_task(api):
    _, due = linked(api, first="2030-01-07", repeats="weekly", days=0)
    aid = due["routine_id"]
    with api.app.state.store.transaction() as c:
        D.satisfy(c, "human:ana", D.one(c, due["id"]), "2030-01-14")
    tick(api, datetime(2030, 1, 14, 0, 0, 5, tzinfo=timezone.utc))
    drain(api)
    assert runs_of(api, aid) == ["due:2030-01-07", "due:2030-01-14"]
    assert [t["occurrence"] for t in tasks(api, aid)] == [f"{aid}:2030-01-07"]
    runs = {r["trigger_ref"]: r["status"] for r in get(api, f"automations/{aid}/runs")["runs"]}
    assert runs["due:2030-01-14"] == "skipped"
    tick(api, datetime(2030, 1, 14, 6, tzinfo=timezone.utc))      # a later pass starts nothing for that day either
    assert runs_of(api, aid) == ["due:2030-01-07", "due:2030-01-14"]


def test_an_edit_of_repeats_moves_the_routine_to_the_new_row_and_keeps_history(api):
    _, due = linked(api, first="2030-03-01", repeats="yearly")
    aid = due["routine_id"]
    edited = post(api, f"rule-due/{due['id']}", {"repeats": "quarterly"})
    assert edited["id"] != due["id"] and edited["routine_id"] == aid and edited["task_days"] == 14
    auto = automation(api, aid)
    assert auto["trigger"]["rule_due_id"] == edited["id"] and auto["trigger"]["repeats"] == "quarterly"
    assert auto["version"] == 2
    assert [link["id"] for link in get(api, f"rule-due/{edited['id']}")["chain"]] == [due["id"], edited["id"]]
    # An owner change in place moves the task's owner and who the routine acts as.
    patch(api, aid, {"enabled": False})
    post(api, f"rule-due/{edited['id']}", {"owner": "ana"})
    auto = automation(api, aid)
    assert auto["owner"] == "person:ana" and auto["steps"][0]["args"]["owner"] == "human:ana"
    assert auto["enabled"] is False                                         # an edit never resumes a paused routine
    # The ended row's id can't remove the routine its successor now holds.
    refused = post(api, f"rule-due/{due['id']}/remove", {}, expected=409)
    assert refused["error"]["code"] == "ended" and refused["error"]["replaced_by"] == edited["id"]
    assert automation(api, aid)["trigger"]["rule_due_id"] == edited["id"]
    assert get(api, f"rule-due/{edited['id']}")["task_days"] == 14


def test_a_task_opened_before_an_edit_marks_the_ended_row_even_when_the_new_schedule_has_its_day(api):
    _, due = linked(api, first="2030-03-01", repeats="yearly", days=0)
    aid = due["routine_id"]
    tick(api, datetime(2030, 3, 1, 0, 0, 5, tzinfo=timezone.utc))
    [task] = tasks(api, aid)
    edited = post(api, f"rule-due/{due['id']}", {"repeats": "quarterly"})
    write(api, H.task_update, "bot:ops", task["id"], status="done")
    assert [m["due_on"] for m in marks(api, due["id"])] == ["2030-03-01"]
    assert marks(api, edited["id"]) == []


def test_a_task_outliving_its_routine_still_marks_its_day(api):
    _, due = linked(api, first="2030-03-01", repeats="yearly", days=0)
    tick(api, datetime(2030, 3, 1, 0, 0, 5, tzinfo=timezone.utc))
    [task] = tasks(api, due["routine_id"])
    post(api, f"rule-due/{due['id']}", {"task_days": None})
    write(api, H.task_update, "bot:ops", task["id"], status="done")
    assert [m["due_on"] for m in marks(api, due["id"])] == ["2030-03-01"]


def test_removing_the_due_date_or_clearing_task_days_turns_the_routine_off(api):
    _, due = linked(api)
    aid = due["routine_id"]
    off = post(api, f"rule-due/{due['id']}", {"task_days": None})
    assert off["routine_id"] is None and off["task_days"] is None
    assert sql(api, "SELECT deleted_at FROM automations WHERE id=?", (aid,))[0]["deleted_at"]
    again = post(api, f"rule-due/{due['id']}", {"task_days": 7})
    assert again["routine_id"] and again["routine_id"] != aid and again["task_days"] == 7
    post(api, f"rule-due/{due['id']}/remove", {})
    assert sql(api, "SELECT deleted_at FROM automations WHERE id=?", (again["routine_id"],))[0]["deleted_at"]


def test_due_soon_follows_the_routines_lead_time(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": TEXT, "who": {"bots": ["ops"]}})
    day = (D.local_today("UTC") + timedelta(days=40)).isoformat()
    plain = post(api, f"rules/{r['id']}/due", {"first_due": day, "repeats": "once", "owner": "ops", "timezone": "UTC"})
    early = post(api, f"rules/{r['id']}/due", {"first_due": day, "repeats": "once", "owner": "ops", "timezone": "UTC",
                                                "task_days": 45})
    assert plain["status"] == "upcoming" and plain["task_days"] is None    # 30 days with no routine
    assert early["status"] == "due_soon"


def prune_runs(api, days_after):
    """automations.prune as the nightly sweep runs it, `days_after` the latest run finished: run detail is gone."""
    from backend import automations
    last = sql(api, "SELECT max(finished_at) AS f FROM automation_runs")[0]["f"]
    with api.app.state.store.transaction() as c:
        automations.prune(c, H.shift(last, days=days_after))
    assert not sql(api, "SELECT id FROM automation_runs WHERE state IS NOT NULL")


def test_a_late_completion_after_run_detail_is_pruned_still_marks_its_day_and_the_ended_row(api):
    _, due = linked(api, first="2030-03-01", repeats="yearly", days=0)
    tick(api, datetime(2030, 3, 1, 0, 0, 5, tzinfo=timezone.utc))
    [task] = tasks(api, due["routine_id"])
    edited = post(api, f"rule-due/{due['id']}", {"repeats": "quarterly"})
    prune_runs(api, 31)
    write(api, H.task_update, "bot:ops", task["id"], status="done")
    assert [m["due_on"] for m in marks(api, due["id"])] == ["2030-03-01"]
    assert marks(api, edited["id"]) == []


def test_a_task_opened_by_run_now_marks_its_day(api):
    _, due = linked(api, first="2030-03-01", repeats="yearly")
    run = post(api, f"automations/{due['routine_id']}/run", {})["run"]
    assert run["trigger_ref"] == "manual"
    [task] = tasks(api, due["routine_id"])
    write(api, H.task_update, "bot:ops", task["id"], status="done")
    assert [m["due_on"] for m in marks(api, due["id"])] == ["2030-03-01"]


def test_a_due_dates_owner_is_the_caller_a_teammate_in_who_or_someone_they_manage(api):
    # The routine makes tasks as the due date's owner, outside task caps: a co-owner of Ops manages Ops's rule, and may
    # name herself or Ops, never the company's owner or a teammate the rule does not name.
    org(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET bot_owners='[\"cara\"]' WHERE bot='ops'")
    r = rule(api, {"kind": "internal", "text": TEXT, "who": {"bots": ["ops"]}})
    body = {"first_due": "2030-03-01", "repeats": "once", "timezone": "UTC", "task_days": 14}
    for owner in ("ana", "ben"):
        post(api, f"rules/{r['id']}/due", {**body, "owner": owner}, token="cara-test", expected=403)
    assert sql(api, "SELECT id FROM automations WHERE flow LIKE '%rule_due_id%'") == []
    mine = post(api, f"rules/{r['id']}/due", {**body, "owner": "cara"}, token="cara-test")
    ops = post(api, f"rules/{r['id']}/due", {**body, "owner": "ops"}, token="cara-test")
    assert automation(api, ops["routine_id"])["owner"] == "bot:ops"
    # Someone below her on the chart is hers to name too.
    with api.app.state.store.transaction() as c:
        raw = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        raw["people"].append({"id": "dan", "email": "dan@acme.example", "reports_to": "cara"})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(raw),))
    post(api, f"rules/{r['id']}/due", {**body, "owner": "dan"}, token="cara-test")
    # The owner names anyone.
    assert post(api, f"rules/{r['id']}/due", {**body, "owner": "ben"})["owner"] in ("human:ben", "ben")
    # An owner change passes the same test; an edit that keeps the owner is not asked again.
    post(api, f"rule-due/{ops['id']}", {"owner": "ana"}, token="cara-test", expected=403)
    assert automation(api, ops["routine_id"])["owner"] == "bot:ops"
    post(api, f"rule-due/{mine['id']}", {"owner": "ops"}, token="cara-test")
    post(api, f"rule-due/{ops['id']}", {"owner": "ops", "task_days": 7}, token="cara-test")


def cara_routine(api, trigger, expected=200):
    """Cara's own "Before a due date" automation, which opens a task for her."""
    body = {"name": "Mine", "owner": "person:cara", "enabled": True, "trigger": {"type": "due", **trigger},
            "steps": [{"id": "open", "type": "action", "do": "create_task", "args": {"title": "Mine", "owner": "cara"}}]}
    return post(api, "automations", body, token="cara-test", expected=expected)


def test_only_the_linked_routine_names_a_due_date_and_a_forged_task_satisfies_nothing(api):
    _, due = linked(api, first="2030-03-01", repeats="yearly", days=0)
    trigger = {"first_due": "2030-03-01", "repeats": "yearly", "open_days": 0, "tz": "UTC"}
    # Cara's routine may not name Ana's due date, on create or on an edit.
    cara_routine(api, {**trigger, "rule_due_id": due["id"]}, expected=403)
    mine = cara_routine(api, trigger)
    patch(api, mine["id"], {"trigger": {"type": "due", **trigger, "rule_due_id": due["id"]}}, token="cara-test",
          expected=403)
    # One saved before that check (written straight to its row) opens its task without the link, whatever its input says.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE automations SET flow=json_set(flow, '$.trigger.rule_due_id', ?) WHERE id=?",
                  (due["id"], mine["id"]))
    post(api, f"automations/{mine['id']}/run", {"input": {"due": {"due_on": "2030-03-01", "tz": "UTC",
                                                                  "rule_due_id": due["id"]}}}, token="cara-test")
    [opened] = tasks(api, mine["id"])
    assert sql(api, "SELECT rule_due_id FROM tasks WHERE id=?", (opened["id"],))[0]["rule_due_id"] is None
    write(api, H.task_update, "human:cara", opened["id"], status="done")
    # A task stamped with Ana's due date by a routine that never was its routine marks nothing either.
    forged = write(api, H.task_create, "human:cara", "Forged", "Forged", "human:cara",
                   occurrence=f"{mine['id']}:2030-03-01", rule_due_id=due["id"])
    write(api, H.task_update, "human:cara", forged["id"], status="done")
    assert marks(api, due["id"]) == []
    # The linked routine's own task still does, and its refs name the due date.
    tick(api, datetime(2030, 3, 1, 0, 0, 5, tzinfo=timezone.utc))
    [task] = tasks(api, due["routine_id"])
    refs = sql(api, "SELECT refs FROM messages WHERE json_extract(refs,'$.automation')=?", (due["routine_id"],))
    assert any(json.loads(ref["refs"]).get("rule_due_id") == due["id"] for ref in refs)
    write(api, H.task_update, "bot:ops", task["id"], status="done")
    assert [(m["due_on"], m["task_id"]) for m in marks(api, due["id"])] == [("2030-03-01", task["id"])]
