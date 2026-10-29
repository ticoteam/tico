"""Production invariants (backend/invariants.py): each check fires on what it should and not on what it should not;
one alert task per episode, closed when the check passes; Health lists the firing checks for owners and admins only;
the scheduler runs the sweep with no model; every check reads through an index. Synthetic fixtures only (Ana owns the
team, Cara is a member; the bots are those of backend/tests/test_api.py)."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend import invariants as I
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_api import api, get, post, setup_attempt  # noqa: F401

NOW = datetime.now(timezone.utc)
EPOCH = int(NOW.timestamp())


def stamp(at):
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def ago(**delta):
    return stamp(NOW - timedelta(**delta))


def sweep(api, at=NOW):
    store = api.app.state.store
    with store.transaction() as c:
        return I.sweep(c, api.app.state.execution.auth, store.settings, at)


def examples(api, check):
    with api.app.state.store.read() as c:
        return c.execute("SELECT examples_json FROM invariant_state WHERE check_id=?", (check,)).fetchone()[0]


def alerts(api, check):
    """Alert tasks raised for one check that are still open."""
    title = "Production check: " + I.BY_ID[check].label
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    with api.app.state.store.read() as c:
        return [row["id"] for row in c.execute(f"SELECT id FROM tasks WHERE title=? AND status IN ({marks}) "
                                               "ORDER BY created", (title, *H.ACTIVE_STATUSES))]


def flight_event(c, at, site="backend/app.py:4100 in mutate", route="PATCH /api/v2/tasks/{tid}", hold_s=6.5):
    c.execute("INSERT INTO flight_events(ts,kind,detail_json) VALUES(?,?,?)",
              (at, "long_hold", json.dumps({"route": route, "site": site, "hold_s": hold_s, "wait_ms": 10.0})))


def flight_request(c, ts, route, n, errors, caller="runner"):
    c.execute("INSERT INTO flight_requests(ts,span,route,caller,n,errors,bytes,total_ms,p50,p95,max_ms,hist) "
              "VALUES(?,60,?,?,?,?,0,0,0,0,0,'{}')", (ts, route, caller, n, errors))


def flight_minute(c, ts, requests, dropped):
    c.execute("INSERT INTO flight_process(ts,cpu,rss,threads,fds,sockets,lag_p95,lag_max,requests,queries,sql_ms,txns,"
              "wait_max,hold_max,hold_p95,locked,dropped) VALUES(?,0,0,0,0,0,0,0,?,0,0,0,0,0,0,0,?)",
              (ts, requests, dropped))


def test_a_run_past_its_lease_by_ten_minutes_fires_and_a_recent_or_finished_one_does_not(api):
    _, _, attempt = setup_attempt(api)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE attempts SET state='running', lease_until=? WHERE id=?", (ago(minutes=20), attempt["id"]))
    assert sweep(api)["lease_overdue"] == 1
    with store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (ago(minutes=5), attempt["id"]))
    assert sweep(api)["lease_overdue"] == 0, "five minutes past the lease is a renewal still in flight"
    with store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed', lease_until=? WHERE id=?", (ago(hours=3), attempt["id"]))
    assert sweep(api)["lease_overdue"] == 0, "a finished run has no lease to lose"


def test_a_bot_task_in_progress_with_no_run_for_an_hour_fires_and_one_with_a_queued_run_does_not(api):
    stuck = post(api, "tasks", {"owner": "ops", "title": "Draft the notes", "body": "x"})["id"]
    waiting = post(api, "tasks", {"owner": "ops", "title": "Pick a venue", "body": "x"})["id"]
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE tasks SET status='doing', updated=? WHERE id IN (?,?)", (ago(hours=2), stuck, waiting))
        # Finish the first task's run and leave the second task's run queued.
        c.execute("UPDATE jobs SET state='done' WHERE id=(SELECT id FROM jobs ORDER BY created LIMIT 1)")
    assert sweep(api)["task_stuck"] == 1
    assert stuck in examples(api, "task_stuck")


def test_a_question_to_a_person_who_has_since_left_fires_and_one_to_a_present_person_does_not(api):
    tid = post(api, "tasks", {"owner": "ops", "title": "Pick a vendor", "body": "x"})["id"]
    store = api.app.state.store
    with store.transaction() as c:
        task = H.task(c, tid)
        H.say(c, "bot:ops", "human:ana", "Which vendor?", kind="ask", conversation_id=task["conversation_id"],
              refs={"task": tid})
        left = c.execute("SELECT id FROM messages WHERE kind='ask' AND to_actor='human:ana' "
                         "ORDER BY created DESC LIMIT 1").fetchone()[0]
        H.say(c, "bot:ops", "human:ana", "Which budget?", kind="ask", conversation_id=task["conversation_id"],
              refs={"task": tid})
        # Ana left after the first question was asked: its recipient is now a person not on the roster.
        c.execute("UPDATE messages SET to_actor='human:gone' WHERE id=?", (left,))
    assert sweep(api)["owner_unreachable"] == 1
    assert left in examples(api, "owner_unreachable"), "the example is the message id, never the person"
    with store.transaction() as c:
        c.execute("UPDATE messages SET answered_by='human:ana' WHERE to_actor='human:gone'")
    assert sweep(api)["owner_unreachable"] == 0


def test_the_same_rule_refusing_one_bot_more_than_twenty_times_in_a_day_fires(api):
    store = api.app.state.store
    with store.transaction() as c:
        for n in range(I.REFUSALS_PER_DAY + 1):
            c.execute("INSERT INTO refusals(id,ts,actor,rule,detail_json,severity) VALUES(?,?,?,?,'{}','warn')",
                      (f"ref-ops-{n}", ago(minutes=n + 1), "bot:ops", "lint"))
        for n in range(I.REFUSALS_PER_DAY):
            c.execute("INSERT INTO refusals(id,ts,actor,rule,detail_json,severity) VALUES(?,?,?,?,'{}','warn')",
                      (f"ref-cpo-{n}", ago(minutes=n + 1), "bot:cpo", "lint"))
        for n in range(50):
            c.execute("INSERT INTO refusals(id,ts,actor,rule,detail_json,severity) VALUES(?,?,?,?,'{}','warn')",
                      (f"ref-old-{n}", ago(days=2), "bot:finance", "lint"))
    assert sweep(api)["refused_again"] == 1, "twenty-one refusals fire; twenty and old refusals do not"


def test_a_bot_held_for_a_provider_sign_in_failure_fires_and_other_holds_do_not(api):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO run_backoff(bot,fails,held_at,error_class,error) VALUES('ops',5,?,'sign_in','401')",
                  (ago(minutes=3),))
        c.execute("INSERT INTO run_backoff(bot,fails,held_at,error_class,error) VALUES('cpo',5,?,'other','boom')",
                  (ago(minutes=3),))
    assert sweep(api)["sign_in_held"] == 1


def test_a_webhook_send_still_sending_long_after_its_claim_fires(api):
    store = api.app.state.store
    with store.transaction() as c:
        for send, claimed, state in (("wh_stuck", ago(minutes=30), "sending"), ("wh_fresh", ago(seconds=20), "sending"),
                                     ("wh_done", ago(hours=5), "delivered")):
            c.execute("INSERT INTO webhook_sends(id,event_id,webhook_id,type,body,state,next_attempt,created,claimed_at) "
                      "VALUES(?,?,?,?,'{}',?,?,?,?)", (send, "we_" + send, "ep_1", "task.done", state, ago(hours=5),
                                                       ago(hours=5), claimed))
    assert sweep(api)["webhook_stuck"] == 1


def test_a_scheduled_automation_thirty_minutes_past_due_fires_and_a_paused_one_does_not(api):
    store = api.app.state.store
    flow = '{"trigger": {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"}}'
    with store.transaction() as c:
        for aid, enabled, due in (("au_late", 1, ago(hours=1)), ("au_paused", 0, ago(hours=1)),
                                  ("au_next", 1, stamp(NOW + timedelta(hours=1)))):
            c.execute("INSERT INTO automations(id,name,owner,trigger_type,enabled,flow_json,version,created,created_by,"
                      "updated,next_due) VALUES(?,?,?,?,?,?,1,?,?,?,?)",
                      (aid, aid, "human:ana", "schedule", enabled, flow, ago(days=2), "human:ana", ago(days=2), due))
    assert sweep(api)["automation_overdue"] == 1


def test_a_relay_no_pull_has_reached_for_half_an_hour_fires_and_a_recent_pull_does_not(api):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO trigger_relays(relay_id,trigger_id,address_prefix,created_by,created) "
                  "VALUES('rl_1','trg_1','https://hooks.tico.dev/rl_1','human:ana',?)", (ago(hours=2),))
        c.execute("UPDATE hook_relay_state SET last_pull_at=NULL WHERE singleton=1")
    assert sweep(api)["relay_silent"] == 1
    with store.transaction() as c:
        c.execute("UPDATE hook_relay_state SET last_pull_at=? WHERE singleton=1", (NOW.timestamp() - 60,))
    assert sweep(api)["relay_silent"] == 0


def test_more_than_thirty_write_lock_holds_of_five_seconds_in_an_hour_fire_and_name_the_site(api):
    store = api.app.state.store
    with store.transaction() as c:
        for _ in range(I.LONG_HOLDS_PER_HOUR + 1):
            flight_event(c, EPOCH - 60)
        flight_event(c, EPOCH - 60, site="backend/other.py:9 in tidy", route=None, hold_s=5.2)
    assert sweep(api)["long_holds"] == 2
    assert "mutate" in examples(api, "long_holds")


def test_thirty_holds_in_the_hour_and_forty_older_ones_do_not_fire(api):
    store = api.app.state.store
    with store.transaction() as c:
        for _ in range(I.LONG_HOLDS_PER_HOUR):
            flight_event(c, EPOCH - 60)
        for _ in range(40):
            flight_event(c, EPOCH - 7200)
    assert sweep(api)["long_holds"] == 0


def test_a_runner_route_failing_over_two_percent_of_two_hundred_requests_fires(api):
    store = api.app.state.store
    with store.transaction() as c:
        flight_request(c, EPOCH - 120, "POST /api/v2/jobs/claim", 200, 5)            # 2.5%: fires
        flight_request(c, EPOCH - 120, "POST /api/v2/files/uploads", 1000, 10)      # 1%: no
        # The same route from another caller is counted in the route's total.
        flight_request(c, EPOCH - 120, "POST /api/v2/jobs/claim", 150, 150, caller="other")
    assert sweep(api)["runner_errors"] == 1
    assert "POST /api/v2/jobs/claim" in examples(api, "runner_errors")


def test_a_runner_route_under_two_hundred_requests_or_under_two_percent_does_not_fire(api):
    store = api.app.state.store
    with store.transaction() as c:
        flight_request(c, EPOCH - 120, "POST /api/v2/attempts/{aid}/renew", 199, 100)       # too few requests
        flight_request(c, EPOCH - 120, "POST /api/v2/attempts/{aid}/events", 400, 4)        # 1%
        flight_request(c, EPOCH - 7200, "POST /api/v2/files/uploads", 1000, 900)            # an hour and more ago
    assert sweep(api)["runner_errors"] == 0


def test_the_recorder_dropping_ten_percent_of_its_samples_in_an_hour_fires(api):
    store = api.app.state.store
    with store.transaction() as c:
        flight_minute(c, EPOCH - 120, requests=900, dropped=100)
    assert sweep(api)["recorder_drops"] == 1
    assert "UTC: 100 dropped" in examples(api, "recorder_drops")


def test_a_few_drops_among_many_samples_or_drops_older_than_an_hour_do_not_fire(api):
    store = api.app.state.store
    with store.transaction() as c:
        flight_minute(c, EPOCH - 120, requests=100000, dropped=10)     # under DROPPED_MIN and under 1%
        flight_minute(c, EPOCH - 7200, requests=900, dropped=100)      # older than the hour
    assert sweep(api)["recorder_drops"] == 0


def test_a_rehearsal_or_demo_copy_checks_nothing_and_raises_nothing(api):
    store = api.app.state.store
    with store.transaction() as c:
        for n in range(I.REFUSALS_PER_DAY + 1):
            c.execute("INSERT INTO refusals(id,ts,actor,rule,detail_json,severity) VALUES(?,?,?,?,'{}','warn')",
                      (f"ref-copy-{n}", ago(minutes=1), "bot:ops", "lint"))
        assert I.sweep(c, api.app.state.execution.auth, SimpleNamespace(rehearsal=True, demo=False), NOW) == {}
        assert I.sweep(c, api.app.state.execution.auth, SimpleNamespace(rehearsal=False, demo=True), NOW) == {}
        assert c.execute("SELECT count(*) FROM invariant_state").fetchone()[0] == 0


def test_one_alert_task_per_episode_and_it_closes_when_the_check_passes(api):
    _, _, attempt = setup_attempt(api)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE attempts SET state='running', lease_until=? WHERE id=?", (ago(minutes=30), attempt["id"]))
    sweep(api)
    first = alerts(api, "lease_overdue")
    assert len(first) == 1
    sweep(api)
    sweep(api, NOW + timedelta(minutes=15))
    assert alerts(api, "lease_overdue") == first, "still firing: no second alert while the first is open"
    with store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE id=?", (attempt["id"],))
    sweep(api, NOW + timedelta(minutes=30))
    assert alerts(api, "lease_overdue") == []
    with store.read() as c:
        assert H.task(c, first[0])["status"] not in H.ACTIVE_STATUSES
    with store.transaction() as c:
        c.execute("UPDATE attempts SET state='running' WHERE id=?", (attempt["id"],))
    sweep(api, NOW + timedelta(minutes=45))
    assert len(alerts(api, "lease_overdue")) == 1, "a new episode raises a new alert"


def test_a_closed_alert_is_not_reopened_while_the_check_still_fires(api):
    _, _, attempt = setup_attempt(api)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE attempts SET state='running', lease_until=? WHERE id=?", (ago(minutes=30), attempt["id"]))
    sweep(api)
    tid = alerts(api, "lease_overdue")[0]
    with store.transaction() as c:
        H.task_close(c, H.KEEPER, tid, note="Known; leave it.")
    sweep(api, NOW + timedelta(minutes=15))
    assert alerts(api, "lease_overdue") == [], "a person closed it; the same episode does not raise it again"


def test_health_lists_the_firing_check_for_an_owner_and_hides_it_from_a_member(api):
    _, _, attempt = setup_attempt(api)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE attempts SET state='running', lease_until=? WHERE id=?", (ago(minutes=30), attempt["id"]))
    sweep(api)
    owner = {x["id"]: x for x in get(api, "health")["checks"]}
    assert owner["invariant:lease_overdue"]["status"] == "warn"
    assert attempt["id"] in owner["invariant:lease_overdue"]["summary"]
    assert owner["invariant:lease_overdue"]["fixes"], "the alert's task is one click away"
    member = {x["id"] for x in get(api, "health", token="cara-test")["checks"]}
    assert "invariant:lease_overdue" not in member


def test_the_scheduler_runs_the_checks_every_fifteen_minutes_and_records_the_run(api):
    store = api.app.state.store
    scheduler = Scheduler(store, api.app.state.execution)
    scheduler.tick(NOW)
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='invariant_checks'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM invariant_state").fetchone()[0] == len(I.CHECKS)
    scheduler.tick(NOW + timedelta(minutes=5))
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='invariant_checks'").fetchone()[0] == 1, \
            "not again before fifteen minutes"


def test_every_check_reads_through_an_index_not_a_scan_of_a_big_table(api):
    """EXPLAIN QUERY PLAN for each statement a sweep runs: no full scan of a table that grows with use. A scan of
    messages_open_ask_to is allowed: it is a partial index holding only the open questions."""
    store = api.app.state.store
    big = {"attempts", "tasks", "messages", "jobs", "approvals", "refusals", "automations", "webhook_sends",
           "conversations", "flight_events", "flight_requests", "flight_process"}
    bounded = {"messages_open_ask_to"}
    statements = []
    with store.transaction() as c:
        c.set_trace_callback(lambda sql: statements.append(sql) if sql.lstrip().upper().startswith("SELECT") else None)
        I.sweep(c, api.app.state.execution.auth, store.settings, NOW)
        c.set_trace_callback(None)
        plans = []
        for sql in dict.fromkeys(statements):
            for row in c.execute("EXPLAIN QUERY PLAN " + sql).fetchall():
                plans.append((sql[:70], row["detail"]))
    scans = [(sql, detail) for sql, detail in plans
             if detail.startswith("SCAN ") and detail.split()[1] in big and detail.split()[-1] not in bounded]
    assert not scans, scans
