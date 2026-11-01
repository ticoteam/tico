"""Lost handoffs recover once; execution holds stay visible without bypassing them."""
import json

import pytest

from backend import assigned_start as A, health, health_monitor, queue_refusals
from backend.store import H
from backend.tests.test_api import api, claim, get, post  # noqa: F401
from backend.tests.test_auto_tasks import deck, finish, set_cap, take
from backend.tests.test_profiles import put
from backend.tests.test_task_lanes import new_task, task_bot


@pytest.fixture
def clock(monkeypatch):
    value = ['2026-10-20T00:00:00.000000Z']
    monkeypatch.setattr(H, 'now', lambda: value[0])
    return value


def advance(api, clock, hours):
    clock[0] = H.shift(clock[0], hours=hours)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET last_seen_at=?', (H.now(),))


def sweep(api):
    with api.app.state.store.transaction() as c:
        return A.sweep(c)


def cancel(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled'")


@pytest.mark.parametrize('profile', ['lead', 'doer'])
def test_cancelled_assignment_rewakes_once_at_one_hour_and_can_start(api, clock, profile):
    machine = task_bot(api, lanes=True)
    with api.app.state.store.transaction() as c:
        put(c, 'ops', profile)
    task = new_task(api, 'Lost assignment')
    cancel(api)
    advance(api, clock, 0.999)
    assert sweep(api) == []
    advance(api, clock, 0.001)
    assert sweep(api) == [task['id']]
    assert sweep(api) == []
    attempt = claim(api, machine)
    assert attempt['task']['id'] == task['id']
    assert get(api, 'tasks/' + task['id'])['task']['status'] == 'doing'
    advance(api, clock, 3)
    with api.app.state.store.read() as c:
        assert list(A.overdue(c)) == []


@pytest.mark.parametrize('hold', ['waiting', 'snoozed', 'blocked', 'question', 'attempt'])
def test_intentional_waits_and_any_attempt_are_not_rewoken(api, clock, hold):
    machine = task_bot(api, lanes=True)
    task = new_task(api, 'Held assignment')
    if hold == 'attempt':
        assert claim(api, machine)
        # Even a failed pre-start attempt is recovery work, never a lost assignment.
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE tasks SET status='open' WHERE id=?", (task['id'],))
    else:
        with api.app.state.store.transaction() as c:
            if hold == 'waiting':
                c.execute("UPDATE tasks SET waiting_on='human:ana' WHERE id=?", (task['id'],))
            elif hold == 'snoozed':
                c.execute('UPDATE tasks SET snoozed_until=? WHERE id=?', (H.shift(H.now(), days=1), task['id']))
            elif hold == 'blocked':
                blocker = H.task_create(c, 'human:ana', 'Prerequisite', 'Finish first.', 'human:ana', lint=False)
                H.TR.add_blocker(c, H.KEEPER, task['id'], blocker['id'])
            else:
                H.task_ask(c, 'bot:ops', task['id'], 'Which date should I use?')
    cancel(api)
    advance(api, clock, 4)
    assert sweep(api) == []


def test_still_unstarted_health_and_manager_needs_you_show_task_and_actual_reason(api, clock):
    task_bot(api, lanes=True)
    with api.app.state.store.transaction() as c:
        put(c, 'ops', 'lead')
        c.execute("UPDATE bot_config SET reports_to='human:ana' WHERE bot='ops'")
    task = new_task(api, 'Publish the daily report')
    cancel(api)
    advance(api, clock, 1)
    assert sweep(api) == [task['id']]
    set_cap(api, 0)
    advance(api, clock, 1.999)
    assert not any(i.get('assigned_not_started') for i in get(api, 'needs-you')['items'])
    advance(api, clock, 0.001)
    page = get(api, 'health')
    check = next(x for x in page['checks'] if x['id'] == 'assigned_not_started')
    assert 'Publish the daily report' in check['summary'] and 'auto_start_off' in check['summary']
    card = next(x for x in get(api, 'needs-you')['items'] if x['id'] == task['id'])
    assert card['assigned_not_started'] and 'auto_start_off' in card['note']
    assert card['title'].startswith('Assigned, not started: ')
    assert get(api, 'tasks/' + task['id'])['task']['title'] == 'Publish the daily report'
    assert task['id'] not in {i['id'] for i in get(api, 'needs-you', token='ben-test')['items']}
    # A successful claim clears both read-time lists immediately.
    set_cap(api, 1)
    with api.app.state.store.transaction() as c:
        H._set_status(c, H.KEEPER, H.task(c, task['id']), 'doing')
    assert not any(x['id'] == 'assigned_not_started' for x in get(api, 'health')['checks'])


def test_private_assignment_reaches_the_bots_manager_and_no_one_else(api, clock):
    task_bot(api, lanes=True)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
    task = post(api, 'tasks', {'owner_id': 'ops', 'title': 'Private assigned work', 'body': 'Keep this private.', 'private': True})
    cancel(api)
    advance(api, clock, 1)
    assert sweep(api) == [task['id']]
    advance(api, clock, 2)
    assert get(api, f"tasks/{task['id']}", token='ben-test')['task']['title'] == 'Private assigned work'
    get(api, f"tasks/{task['id']}", token='cara-test', expected=404)
    assert task['id'] not in {i['id'] for i in get(api, 'needs-you', token='cara-test')['items']}
    assert 'Private assigned work' not in str(get(api, 'health', token='cara-test'))


def test_reassignment_gets_its_own_hour_not_the_last_comment_time(api, clock):
    task_bot(api, lanes=True)
    task = new_task(api, 'Reassigned work')
    cancel(api)
    advance(api, clock, 1)
    assert sweep(api) == [task['id']]
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'human:ana', task['id'], owner='bot:finance')
    cancel(api)
    assert sweep(api) == []
    advance(api, clock, 1)
    assert sweep(api) == [task['id']]


def test_refused_recovery_is_recorded_once_instead_of_retried_each_tick(api, clock, monkeypatch):
    task_bot(api, lanes=True)
    task = new_task(api, 'Assignment whose delivery refuses')
    cancel(api)
    advance(api, clock, 1)
    calls = []

    def refused(*args, **kwargs):
        calls.append(True)
        raise H.Refused('cap', 'Synthetic delivery cap')

    monkeypatch.setattr(H, '_wake', refused)
    assert sweep(api) == [] and sweep(api) == [] and len(calls) == 1
    advance(api, clock, 2)
    with api.app.state.store.read() as c:
        [row] = A.overdue(c)
        assert row['id'] == task['id'] and row['rewake_refusal'] == 'cap'


def test_scheduler_restart_recovers_once_and_rehearsal_never_recovers(api, clock):
    from backend.scheduler import Scheduler
    task_bot(api, lanes=True)
    task = new_task(api, 'Scheduler assignment')
    cancel(api)
    advance(api, clock, 1)
    store = api.app.state.store
    store.settings.rehearsal = True
    Scheduler(store, api.app.state.execution).tick(H.parse_ts(H.now()))
    with store.read() as c:
        assert not c.execute('SELECT 1 FROM events WHERE action=?', (A.EVENT,)).fetchone()
    store.settings.rehearsal = False
    for _ in range(2):
        Scheduler(store, api.app.state.execution).tick(H.parse_ts(H.now()))
    with store.read() as c:
        assert c.execute('SELECT count(*) FROM events WHERE action=? AND target=?', (A.EVENT, task['id'])).fetchone()[0] == 1


def test_recovery_selection_is_bounded_and_rechecks_waits_under_the_write_lock(api, clock):
    task_bot(api, lanes=True)
    tasks = [new_task(api, 'Assigned item ' + str(n)) for n in range(4)]
    cancel(api)
    advance(api, clock, 1)
    store = api.app.state.store
    with store.read() as c:
        selected = A.due(c, H.now(), limit=2)
    assert len(selected) == 2
    with store.transaction() as c:
        c.execute("UPDATE tasks SET waiting_on='human:ana' WHERE id=?", (selected[0],))
        assert A.sweep(c, H.now(), selected) == [selected[1]]
        assert A.sweep(c, H.now(), []) == []
    with store.read() as c:
        assert len(A.due(c, H.now())) == 2


def test_five_deck_tickets_progress_under_three_task_cap_without_lead_turns(api):
    machine = task_bot(api, lanes=True, capacity=8)
    tasks = deck(api, 5)
    set_cap(api, 3)
    first = [take(api, machine) for _ in range(3)]
    assert len({a['task']['id'] for a in first}) == 3 and take(api, machine) is None
    by_id = {t['id']: t for t in tasks}
    reached = {a['task']['id'] for a in first}
    for a in first[:2]:
        finish(api, machine, a, by_id[a['task']['id']])
        reached.add(take(api, machine)['task']['id'])
    assert reached == set(by_id)
    with api.app.state.store.read() as c:
        assert {r[0] for r in c.execute('SELECT DISTINCT bot_id FROM attempts')} == {'ops'}


@pytest.mark.parametrize('refusal', ['auto_start_off', 'task_wait', 'check_model', 'backoff'])
def test_running_lane_does_not_mask_other_lanes_refusal_and_twelve_hour_finding(api, clock, refusal):
    machine = task_bot(api, lanes=True)
    new_task(api, 'Running task')
    assert claim(api, machine)
    task = new_task(api, 'Unstarted task')
    if refusal == 'auto_start_off':
        set_cap(api, 0)
    with api.app.state.store.transaction() as c:
        if refusal == 'task_wait':
            c.execute("UPDATE tasks SET waiting_on='human:ana' WHERE id=?", (task['id'],))
        elif refusal == 'check_model':
            from backend import check_models
            check_models.record(c, 'bot:ops', task['id'], {'runtime': 'codex', 'model': 'gpt-6.1',
                                'harness': 'codex', 'effort': '', 'role': 'check'})
            check_models.hold(c, task['id'], 'codex', H.shift(H.now(), days=1))
        elif refusal == 'backoff':
            c.execute("INSERT INTO run_backoff(bot,fails,wait_until) VALUES('ops',1,?)", (H.shift(H.now(), days=1),))
    with api.app.state.store.transaction() as c:
        queue_refusals.observe(c, api.app.state.store.settings)
    observe_for(api, clock, 13)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE state='queued'", (H.shift(H.now(), hours=-13),))
        online = {r['id'] for r in health._online_runners(c)}
        rows = [r for r in health._waiting(c, online, api.app.state.store.settings)[2] if r['bot'] == 'ops']
        assert [r['reason'] for r in rows] == [refusal]
        assert 'queue_refused' in {f['check_id'] for f in health_monitor.findings(c, api.app.state.store.settings)}
    check = next(x for x in get(api, 'health')['checks'] if x['id'] == 'queue_refused')
    assert check['items'][0]['reason'] == refusal
    # A newer claimable chat cannot hide the old refused task in the aged-job list.
    post(api, 'chat/ops', {'text': 'A quick question.'})
    with api.app.state.store.read() as c:
        assert health.refused_queues(c, api.app.state.store.settings)[0]['reason'] == refusal
    assert get(api, 'tasks/' + task['id'])['task']['status'] == 'open'


def test_old_queue_only_ages_refusal_from_observation_and_clearing_resets_it(api, clock):
    task_bot(api, lanes=True)
    new_task(api, 'Old queued task')
    set_cap(api, 0)
    advance(api, clock, 13)
    store = api.app.state.store
    with store.transaction() as c:
        queue_refusals.observe(c, store.settings)
        assert health.refused_queues(c, store.settings) == []
    observe_for(api, clock, 12)
    with store.transaction() as c:
        assert health.refused_queues(c, store.settings)
    set_cap(api, 1)
    with store.transaction() as c:
        queue_refusals.observe(c, store.settings)
        assert health.refused_queues(c, store.settings) == []
    set_cap(api, 0)
    with store.transaction() as c:
        queue_refusals.observe(c, store.settings)
        assert health.refused_queues(c, store.settings) == []


def observe_for(api, clock, hours):
    # Refresh at the maximum documented gap, without HTTP traffic or long wall-clock timers.
    store = api.app.state.store
    until = H.shift(H.now(), hours=hours)
    with store.transaction() as c:
        while clock[0] < until:
            clock[0] = min(until, H.shift(clock[0], seconds=queue_refusals.MAX_GAP_SECONDS))
            c.execute('UPDATE runners SET last_seen_at=?', (H.now(),))
            queue_refusals.observe(c, store.settings)


def test_older_same_lane_followup_does_not_hide_free_lane_refusal(api, clock):
    machine = task_bot(api, lanes=True)
    running = new_task(api, 'Running work')
    assert claim(api, machine)
    post(api, 'tasks/' + running['id'] + '/comments', {'text': 'Please include the summary.'})
    advance(api, clock, 1)
    task = new_task(api, 'Refused independent work')
    set_cap(api, 0)
    advance(api, clock, 1)
    store = api.app.state.store
    with store.transaction() as c:
        jobs = c.execute(f"SELECT j.id,{H.MESSAGE_TASK_SQL} task FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "JOIN conversations cv ON cv.id=m.conversation_id WHERE j.state='queued'").fetchall()
        by_task = {r['task']: r['id'] for r in jobs}
        assert set(by_task) == {running['id'], task['id']}
        c.execute('UPDATE jobs SET created_at=? WHERE id=?',
                  (H.shift(H.now(), hours=-3), by_task[running['id']]))
        queued = by_task[task['id']]
        online = {r['id'] for r in health._online_runners(c)}
        rows = [r for r in health._waiting(c, online, store.settings)[2] if r['bot_id'] == 'ops']
        assert [r['reason'] for r in rows] == ['auto_start_off']
        queue_refusals.observe(c, store.settings)
        assert queue_refusals.state(c, 'ops')['job'] == queued
    observe_for(api, clock, 12)
    with store.read() as c:
        assert health.refused_queues(c, store.settings)[0]['job'] == queued


@pytest.mark.parametrize('gap', ['paused', 'quarantined', 'held', 'older_job', 'no_pass'])
def test_unobserved_gap_restarts_refusal_age(api, clock, gap):
    task_bot(api, lanes=True)
    task = new_task(api, 'Refused task')
    set_cap(api, 0)
    store = api.app.state.store
    with store.transaction() as c:
        queue_refusals.observe(c, store.settings)
        original = queue_refusals.state(c, 'ops')
    advance(api, clock, 1)
    with store.transaction() as c:
        if gap in ('paused', 'quarantined'):
            c.execute('UPDATE bots SET state=? WHERE slug=?', (gap, 'ops'))
        elif gap == 'held':
            c.execute('UPDATE jobs SET held=1 WHERE id=?', (original['job'],))
        elif gap == 'older_job':
            H._wake(c, H.task(c, task['id']), 'bot:ops', 'Older work', {'needs_action': True})
            c.execute("UPDATE jobs SET created_at=? WHERE id<>? AND state='queued'",
                      (H.shift(H.now(), hours=-10), original['job']))
        if gap != 'no_pass':
            queue_refusals.observe(c, store.settings)
    advance(api, clock, 13)
    with store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug='ops'")
        c.execute('UPDATE jobs SET held=0 WHERE id=?', (original['job'],))
        c.execute("UPDATE jobs SET state='canceled' WHERE id<>? AND state='queued'", (original['job'],))
        assert health.refused_queues(c, store.settings) == []
        queue_refusals.observe(c, store.settings)
        assert queue_refusals.state(c, 'ops')['since'] == H.now()
        assert health.refused_queues(c, store.settings) == []


def test_reason_change_starts_a_new_clock_and_unchanged_pass_does_not_write(api, clock):
    task_bot(api, lanes=True)
    new_task(api, 'Refused work')
    set_cap(api, 0)
    store = api.app.state.store
    with store.transaction() as c:
        queue_refusals.observe(c, store.settings)
        count = c.total_changes
        queue_refusals.observe(c, store.settings)
        assert c.total_changes == count
    observe_for(api, clock, 12)
    with store.transaction() as c:
        c.execute("INSERT INTO run_backoff(bot,fails,wait_until) VALUES('ops',1,?)", (H.shift(H.now(), days=1),))
        queue_refusals.observe(c, store.settings)
        assert queue_refusals.state(c, 'ops')['since'] == H.now()
        assert queue_refusals.state(c, 'ops')['reason'] == 'backoff'
        assert health.refused_queues(c, store.settings) == []


def test_scheduler_selection_failure_does_not_skip_later_steps_and_other_bots(api, clock, monkeypatch):
    from backend.scheduler import Scheduler
    from backend import milestone_waits
    task_bot(api, lanes=True, others=('finance',))
    new_task(api, 'Ops refusal')
    new_task(api, 'Finance refusal', owner='finance')
    set_cap(api, 0)
    store = api.app.state.store
    original = queue_refusals.current
    later = []

    def broken(*args, **kwargs):
        raise RuntimeError('Synthetic assignment selection failure')

    def one_bot(c, settings, bot):
        assert not c.write_transaction, 'claim evaluation must not take the writer lock'
        if bot == 'finance':
            raise RuntimeError('Synthetic one bot failure')
        return original(c, settings, bot)

    monkeypatch.setattr(A, 'due', broken)
    monkeypatch.setattr(queue_refusals, 'current', one_bot)
    monkeypatch.setattr(milestone_waits, 'reconcile', lambda *args: later.append(True))
    result = Scheduler(store, api.app.state.execution).tick(H.parse_ts(H.now()))
    assert {'assigned_start': 'selection', 'error': 'RuntimeError'} in result['failures']
    assert {'queue_refusals': 'finance', 'error': 'RuntimeError'} in result['failures']
    assert later
    with store.read() as c:
        assert queue_refusals.state(c, 'ops')['reason'] == 'auto_start_off'
        assert c.execute("SELECT 1 FROM service_health WHERE service='scheduler'").fetchone()


def test_orphan_and_failed_delivery_are_recorded_once_and_do_not_starve_next_batch(api, clock, monkeypatch):
    task_bot(api, lanes=True)
    tasks = [new_task(api, 'Recovery item ' + str(n)) for n in range(3)]
    cancel(api)
    advance(api, clock, 1)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute('UPDATE tasks SET conversation_id=NULL WHERE id=?', (tasks[0]['id'],))
    original = H._wake

    def one_bad(c, row, *args, **kwargs):
        if row['id'] == tasks[1]['id']:
            raise RuntimeError('Synthetic bad delivery')
        return original(c, row, *args, **kwargs)

    monkeypatch.setattr(H, '_wake', one_bad)
    with store.transaction() as c:
        for task in tasks:
            A.sweep(c, H.now(), [task['id']])
        assert A.due(c, H.now(), limit=2) == []
    advance(api, clock, 2)
    with store.read() as c:
        rows = {r['id']: r for r in A.overdue(c)}
        assert rows[tasks[0]['id']]['rewake_refusal'] == 'missing_conversation'
        assert rows[tasks[1]['id']]['rewake_refusal'] == 'recovery_error'
        assert rows[tasks[2]['id']]['rewake_refusal'] is None


def test_queue_refusal_checks_the_observed_jobs_message_privacy_for_bot_readers(api, clock):
    from backend.auth import Identity
    task_bot(api, lanes=True)
    post(api, 'tasks', {'owner_id': 'ops', 'title': 'Private refused work', 'body': 'Synthetic private task.', 'private': True})
    set_cap(api, 0)
    store = api.app.state.store
    store.settings.test_identities['finance-observer'] = Identity('bot:finance', 'bot', agent='hermes')
    with store.transaction() as c:
        c.execute("INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES('finance','hermes','x',?,'human:ana')", (H.now(),))
        queue_refusals.observe(c, store.settings)
    observe_for(api, clock, 12)
    assert 'queue_refused' in {x['id'] for x in get(api, 'health')['checks']}
    assert 'queue_refused' not in {x['id'] for x in get(api, 'health', token='finance-observer')['checks']}


@pytest.mark.parametrize('chat_minutes,task_minutes,expected', [(3, 10, True), (1, 20, False)])
def test_busy_health_ages_claim_priority_instead_of_oldest_free_job(api, clock, chat_minutes, task_minutes, expected):
    machine = task_bot(api, lanes=True, capacity=8)
    new_task(api, 'Running lane')
    assert claim(api, machine)
    new_task(api, 'Free lane')
    post(api, 'chat/ops', {'text': 'Please answer this question.'})
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE state='queued'", (H.shift(H.now(), minutes=-task_minutes),))
        c.execute("UPDATE jobs SET created_at=? WHERE state='queued' AND message_id IN "
                  "(SELECT id FROM messages WHERE sender_id LIKE 'human:%')",
                  (H.shift(H.now(), minutes=-chat_minutes),))
        online = {r['id'] for r in health._online_runners(c)}
        _, slow, stuck = health._waiting(c, online, store.settings)
        assert bool([r for r in stuck if r['bot_id'] == 'ops']) is expected
        assert bool([r for r in slow if r['bot_id'] == 'ops']) is expected


def test_recovery_scan_advances_past_a_full_page_of_held_tasks_and_wraps(api, clock, monkeypatch):
    task_bot(api, lanes=True)
    tasks = [new_task(api, 'Held page ' + str(n)) for n in range(5)]
    cancel(api)
    advance(api, clock, 1)
    store = api.app.state.store
    ordered = sorted(t['id'] for t in tasks)
    monkeypatch.setattr(A, 'SCAN_LIMIT', 2)
    with store.transaction() as c:
        c.executemany("UPDATE tasks SET waiting_on='human:ana' WHERE id=?", [(tid,) for tid in ordered[:4]])
    cursor = ['']
    with store.read() as c:
        assert A.due(c, H.now(), cursor=cursor) == [] and cursor == [ordered[1]]
        assert A.due(c, H.now(), cursor=cursor) == [] and cursor == [ordered[3]]
        assert A.due(c, H.now(), cursor=cursor) == [ordered[4]]
        assert A.due(c, H.now(), cursor=cursor) == [] and cursor == ['']


def test_overdue_scopes_reader_before_its_row_budget(api, clock, monkeypatch):
    task_bot(api, lanes=True)
    tasks = [new_task(api, 'Overdue page ' + str(n)) for n in range(3)]
    cancel(api)
    advance(api, clock, 1)
    assert len(sweep(api)) == 3
    advance(api, clock, 2)
    wanted = max(t['id'] for t in tasks)
    monkeypatch.setattr(A, 'SCAN_LIMIT', 1)
    with api.app.state.store.read() as c:
        rows = list(A.overdue(c, visible="id='" + wanted + "'"))
        assert [r['id'] for r in rows] == [wanted]


def test_overdue_checks_the_readers_rule_per_candidate_and_never_lists_every_task(api, clock):
    """Needs you and Health read this page with the reader's task rule: checked per open task by key, not as a list
    of every task the rule allows (a scan of all tasks, 55 ms a call on TIDY)."""
    task_bot(api, lanes=True)
    new_task(api, 'Overdue plan')
    seen = []
    with api.app.state.store.read() as c:
        visible = api.app.state.auth.task_sql(c, api.app.state.auth.authenticate(
            {"authorization": "Bearer ana-test"}, "/api/v2/tasks", "GET"))
        c.set_trace_callback(seen.append)
        try:
            list(A.overdue(c, visible=visible))
        finally:
            c.set_trace_callback(None)
        [page] = [s for s in seen if s.startswith('SELECT t.* FROM tasks t JOIN bots b')]
        plan = " ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + page))
    assert "SCAN tasks" not in plan and "SCAN t " not in plan and "LIST SUBQUERY" not in plan, plan


def test_assignment_backlog_bounds_provenance_work_and_reuses_each_attempt(api, clock, monkeypatch):
    machine = task_bot(api, lanes=True)
    first = new_task(api, 'Earlier attempted assignment')
    assert claim(api, machine)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE tasks SET status='open' WHERE id=?", (first['id'],))
        # Large synthetic backlog of old open bot tasks, unlike the unrelated lock fixture.
        c.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<40000) "
                  "INSERT INTO tasks(id,title,body,requester_id,owner_id,status,lane,rank,created_at,updated_at,conversation_id) "
                  "SELECT printf('synthetic-%06d',x),'Synthetic assignment','','human:ana','bot:ops','open',"
                  "'company',x,?,?,? FROM n", (H.now(), H.now(), first['conversation_id']))
    advance(api, clock, 1)
    calls = []
    original = A.privacy.attempt_tasks

    def counted(c, aid):
        calls.append(aid)
        return original(c, aid)

    monkeypatch.setattr(A.privacy, 'attempt_tasks', counted)
    with store.read() as c:
        assert len(A.due(c, H.now(), limit=A.SCAN_LIMIT)) == A.SCAN_LIMIT - 1
        assert len(calls) == 1


def test_folded_attempt_provenance_still_excludes_unstarted_recovery(api, clock):
    machine = task_bot(api, lanes=True)
    new_task(api, 'Attempt carrier')
    attempt = claim(api, machine)
    folded = new_task(api, 'Folded work')
    with api.app.state.store.transaction() as c:
        H.event(c, H.KEEPER, 'task.next-run.carried', attempt['id'], {'tasks': [folded['id']]})
    cancel(api)
    advance(api, clock, 1)
    assert sweep(api) == []


def test_bad_candidate_does_not_pin_recovery_cursor_or_hide_later_tasks(api, clock, monkeypatch):
    task_bot(api, lanes=True)
    tasks = [new_task(api, 'Row isolation ' + str(n)) for n in range(3)]
    cancel(api)
    advance(api, clock, 1)
    ordered = sorted(t['id'] for t in tasks)
    monkeypatch.setattr(A, 'SCAN_LIMIT', 2)
    original = A.auto_tasks.waiting

    def one_bad(c, row, *args, **kwargs):
        if row['id'] == ordered[0]:
            raise ValueError('Synthetic malformed task wait')
        return original(c, row, *args, **kwargs)

    monkeypatch.setattr(A.auto_tasks, 'waiting', one_bad)
    # Every row is a wait suspect here, so waiting() runs for each one and its failure is what is isolated.
    monkeypatch.setattr(A.auto_tasks, 'maybe_waiting', lambda c, rows: {row['id'] for row in rows})
    cursor, failures = [''], []
    with api.app.state.store.read() as c:
        assert A.due(c, H.now(), cursor=cursor, failures=failures) == [ordered[1]]
        assert cursor == [ordered[1]]
        assert failures == [{'assigned_start': ordered[0], 'error': 'ValueError'}]
        assert A.due(c, H.now(), cursor=cursor, failures=failures) == [ordered[2]]


@pytest.mark.parametrize('excluded', ['attempted', 'waiting'])
def test_overdue_pages_past_more_than_two_hundred_ineligible_recoveries(api, clock, monkeypatch, excluded):
    machine = task_bot(api, lanes=True)
    carrier = new_task(api, 'Provenance carrier')
    attempt = claim(api, machine)
    wanted = new_task(api, 'Still unstarted behind the filtered page')
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<250) "
                  "INSERT INTO tasks(id,title,body,requester_id,owner_id,status,lane,rank,created_at,updated_at,conversation_id) "
                  "SELECT printf('00000000-0000-4000-8000-%012d',x),'Earlier recovery','','human:ana','bot:ops',"
                  "'open','company',x,?,?,? FROM n", (H.now(), H.now(), carrier['conversation_id']))
        ids = [r[0] for r in c.execute("SELECT id FROM tasks WHERE id LIKE '00000000-%'")]
        if excluded == 'attempted':
            H.event(c, H.KEEPER, 'task.next-run.carried', attempt['id'], {'tasks': ids})
        else:
            c.executemany("UPDATE tasks SET waiting_on='human:ana' WHERE id=?", [(tid,) for tid in ids])
    advance(api, clock, 1)
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ana' WHERE bot='ops'")
        for tid in ids + [wanted['id']]:
            row = H.task(c, tid)
            H.event(c, H.KEEPER, A.EVENT, tid, {'assignment': row['owner_id'] + ':' + row['created_at'],
                    'assigned_at': row['created_at'], 'rewoken_at': H.now(), 'refusal': None})
    advance(api, clock, 2)
    calls = []
    original = A.privacy.attempt_tasks

    def counted(c, aid):
        calls.append(aid)
        return original(c, aid)

    monkeypatch.setattr(A.privacy, 'attempt_tasks', counted)
    with store.read() as c:
        assert [r['id'] for r in A.overdue(c, limit=1)] == [wanted['id']]
        assert len(calls) == 1
    assert wanted['id'] in {r['id'] for r in get(api, 'needs-you')['items']}
    check = next(x for x in get(api, 'health')['checks'] if x['id'] == 'assigned_not_started')
    assert [r['task'] for r in check['items']] == [wanted['id']]


def traced_overdue(api):
    """The rows `overdue` yields, and every statement it ran (expanded values, as sqlite reports them)."""
    seen = []
    with api.app.state.store.read() as c:
        c.set_trace_callback(seen.append)
        try:
            found = list(A.overdue(c))
        finally:
            c.set_trace_callback(None)
    return found, seen


@pytest.mark.parametrize('n', [3, 30])
def test_overdue_reads_its_page_in_fixed_statements_not_per_task(api, clock, n):
    task_bot(api, lanes=True)
    tasks = [new_task(api, f'Lost {i}') for i in range(n)]
    cancel(api)
    advance(api, clock, 1)
    assert sorted(sweep(api)) == sorted(t['id'] for t in tasks)
    advance(api, clock, 2)
    found, seen = traced_overdue(api)
    assert sorted(row['id'] for row in found) == sorted(t['id'] for t in tasks)
    # The page's assignment and ledger rows come in one statement each, not one per task.
    assert [s for s in seen if s.startswith('SELECT id,created_at FROM task_events WHERE task_id=')] == []
    assert [s for s in seen if s.startswith('SELECT detail FROM events WHERE target=')] == []
    # A short page is the last one: no second, empty page query.
    assert sum(s.startswith('SELECT t.* FROM tasks t JOIN bots b') for s in seen) == 1


def fleet(store, bots, tasks, attempts, *, days=30):
    """A synthetic fleet for statement counts: `bots` active bots with `tasks` open tasks between them and `attempts`
    runs spread over `days` days (every 5th with an input, every 4th with a run-task event, every 10th task carried)."""
    now = H.now()
    back = lambda hours: H.shift(now, hours=-hours)  # noqa: E731
    slugs = [f'fleet-{b:02d}' for b in range(bots)]
    with store.transaction() as c:
        c.execute("INSERT INTO conversations(id,kind,subject,created_at,last_message_at) VALUES('fleet-conv','task','',?,?)",
                  (now, now))
        c.execute("INSERT INTO runners(id,label,owner_id,token_hash,created_at,last_seen_at,readiness) "
                  "VALUES('fleet-runner','fleet','ana','fleet-token',?,?,'{}')", (now, now))
        for slug in slugs:
            c.execute("INSERT INTO bots(id,slug,display_name,runtime,state,created_at) VALUES(?,?,?,'fake','active',?)",
                      (slug, slug, slug, now))
            c.execute("INSERT INTO bot_config(bot,config,owner_id) VALUES(?,'{}','ana')", (slug,))
        for k in range(tasks):
            c.execute("INSERT INTO tasks(id,title,body,requester_id,owner_id,status,rank,created_at,updated_at,conversation_id) "
                      "VALUES(?,?,'','human:ana',?,'open',?,?,?,'fleet-conv')",
                      (f'fleet-t-{k:05d}', f'Fleet task {k}', 'bot:' + slugs[k % bots], k,
                       back(24 * (days - 2) - k), now))
        ids_by_bot = {b: [f'fleet-t-{k:05d}' for k in range(tasks) if k % bots == b] for b in range(bots)}
        for i in range(attempts):
            b = i % bots
            at = back(24 * days - 24 * days * i / attempts)
            task = ids_by_bot[b][i // bots % len(ids_by_bot[b])] if ids_by_bot[b] else None
            c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at) "
                      "VALUES(?, 'fleet-conv','human:ana',?,'task','',?,?)",
                      (f'fleet-m-{i}', 'bot:' + slugs[b], json.dumps({'task': task}) if task and i % 3 == 0 else '{}', at))
            # A message to a bot queues its job under the message's id (store.QUEUE_TRIGGER).
            c.execute("UPDATE jobs SET state='completed' WHERE id=?", (f'fleet-m-{i}',))
            c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
                      "VALUES(?,?,?,'fleet-runner',1,?,'completed',?,?)",
                      (f'fleet-a-{i}', f'fleet-m-{i}', slugs[b], f'fleet-h-{i}', now, at))
            if i % 5 == 0 and task:
                c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at) "
                          "VALUES(?, 'fleet-conv','bot:' || ?,'human:ana','chat','',?,?)",
                          (f'fleet-in-{i}', slugs[b], json.dumps({'task': task}), at))
                c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (f'fleet-a-{i}', f'fleet-in-{i}'))
            if i % 4 == 0 and ids_by_bot[b]:
                H.event(c, H.KEEPER, 'task.next-run.carried', f'fleet-a-{i}',
                        {'tasks': ids_by_bot[b][:3]})
        for k in range(0, tasks, 10):
            b = k % bots
            j = (k // 10) % max(1, attempts // bots)
            if b + bots * j < attempts:
                c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (f'fleet-a-{b + bots * j}', f'fleet-t-{k:05d}'))
    return slugs


def test_recovery_selection_primes_each_bots_attempt_provenance_in_fixed_statements(api):
    """The scheduler's selection runs in one snapshot; each bot's runs are primed together, so no run costs its own
    queries (the per-run statements of task_privacy._attempt_tasks)."""
    store = api.app.state.store
    fleet(store, 3, 30, 90)
    seen = []
    with store.read_transaction() as c:
        c.set_trace_callback(seen.append)
        try:
            A.due(c, H.now(), cursor=[""])
        finally:
            c.set_trace_callback(None)
    per_run = ('SELECT m.* FROM attempts a JOIN jobs', 'SELECT id FROM tasks WHERE carried_by=?',
               'SELECT detail FROM events WHERE action IN')
    assert [s for s in seen if s.startswith(per_run)] == []
    assert any(s.startswith('SELECT a.id AS primed_attempt') for s in seen)


def traced_due(store, at):
    """The unstarted tasks `due` picks, and every statement it ran (expanded values, as sqlite reports them)."""
    seen = []
    with store.read_transaction() as c:
        c.set_trace_callback(seen.append)
        try:
            found = A.due(c, at, limit=500, cursor=[""])
        finally:
            c.set_trace_callback(None)
    return found, seen


def test_repeated_selection_stops_reading_attempts_once_each_assignment_is_settled(api):
    """The scheduler's tick calls `due` forever. Once every picked task has its ledger entry (what sweep writes) and
    each attempted task is remembered, a later tick reads no attempts for the bots: the work is per new event, not per tick."""
    store = api.app.state.store
    fleet(store, 3, 200, 900)
    at = H.now()
    found, _ = traced_due(store, at)
    assert found
    with store.transaction() as c:
        A.sweep(c, at, found)
    found, second = traced_due(store, at)
    assert found == []
    attempt_reads = [s for s in second if 'FROM attempts WHERE bot_id' in s or s.startswith('SELECT a.id AS primed_attempt')]
    assert attempt_reads == []
    # Only the page's own reads remain (its rows, ledger, owners and waits): 12 statements, 41 before this change.
    assert len(second) <= 15
