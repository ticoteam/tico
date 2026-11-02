"""Busy-turns v3: synthetic workers, real transactional claims, no provider calls."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_attempt_contention import fleet
from backend.tests.test_runner_load import point


def waiters(api, n=37, running=3):
    computer = fleet(api, attempts=n + running, trees=0)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=? WHERE id=?", (n + running, computer['runner_id']))
        for i in range(n):
            c.execute("UPDATE attempts SET state='leased' WHERE id=?", (f'attempt-{i}',))
            c.execute("UPDATE jobs SET state='leased' WHERE attempt_id=?", (f'attempt-{i}',))
            from backend import start_places
            start_places.claim(c, f'attempt-{i}')
    for i in range(n):
        post(api, f'attempts/attempt-{i}/start-place', {'waiting': 'shared login'}, computer['token'])
        post(api, f'attempts/attempt-{i}/events', {'events': [
            {'seq': 1, 'kind': 'status', 'payload': {'waiting': 'Waiting for the shared ChatGPT login', 'login': 'ChatGPT'}}
        ]}, computer['token'])
    return computer


def sent_by(api, message_id, actor):
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE messages SET from_actor=? WHERE id=?', (actor, message_id))


def overload(api, computer, cpu=95, mem=.5):
    """Six one-minute samples over 90%: performance.load_status calls the computer overloaded."""
    import time
    now = int(time.time()) // 60 * 60
    post(api, 'runners/performance', {'samples': [point(now - 300 + i * 60, cpu=cpu, mem=mem) for i in range(6)]},
         computer['token'])


def test_four_counts_separate_37_login_waiters(api):
    """Spec 1/2: the original 40-of-40 case leaves room for a different login."""
    computer = waiters(api)
    rid = computer['runner_id']
    machine = next(m for m in get(api, 'operations')['machines'] if m['id'] == rid)
    assert {k: machine.get('turn_counts', {}).get(k) for k in ('running', 'starting', 'waiting', 'queued')} == {
        'running': 3, 'starting': 0, 'waiting': 37, 'queued': 0}
    assert 'shared login' in machine['turn_summary'] and 'oldest' in machine['turn_summary']
    health = get(api, 'health')
    assert next(c for c in health['checks'] if c['id'] == 'turns:' + rid)['summary'].endswith(machine['turn_summary'])


def test_health_below_warning_counts_queued_work_without_asking_each_bot_why(api, monkeypatch):
    """Below the company warning, Health's Turns line counts queued work without claim_refusal for each queued bot
    (its stuck and queue checks already say why). A paused bot's work still reads as waiting, not queued; at
    warning the line names each wait again."""
    from backend import execution
    computer = runner(api)
    for bot in ('finance', 'doc-updater'):
        assign(api, computer, bot)
        post(api, 'chat/' + bot, {'text': 'Queued while healthy'})
    ready(api, computer, ['finance', 'doc-updater'])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE slug='doc-updater'")
    real = execution.claim_refusal

    def refused(*args, **kwargs):
        raise AssertionError('claim_refusal on the Health Turns line below warning')

    monkeypatch.setattr(execution, 'claim_refusal', refused)
    line = next(m for m in get(api, 'health')['computers'] if m['id'] == computer['runner_id'])
    assert {k: line['turn_counts'][k] for k in ('waiting', 'queued')} == {'waiting': 1, 'queued': 1}
    assert [w['reason'] for w in line['turn_counts']['waits']] == ['bot paused']
    asked = []
    monkeypatch.setattr(execution, 'claim_refusal', lambda *args, **kwargs: asked.append(args[3]) or real(*args, **kwargs))
    from backend import org_turns
    with api.app.state.store.transaction() as c:
        org_turns.save(c, org_turns.LEVELS, {'warning_per_person': 0, 'stop_per_person': 100})
    line = next(m for m in get(api, 'health')['computers'] if m['id'] == computer['runner_id'])
    # At warning the running bot's queue is asked why again; the paused one never needs asking.
    assert asked == ['finance']
    assert line['turn_counts']['waiting'] + line['turn_counts']['queued'] == 2


def test_unrelated_work_and_free_turn_ignore_37_login_waiters(api):
    """Spec 2: a different login starts immediately on an otherwise idle computer."""
    computer = waiters(api)
    rid = computer['runner_id']
    assign(api, computer, 'finance')
    ready(api, computer, ['ops', 'finance'])
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET capacity=40 WHERE id=?', (rid,))
    post(api, 'chat/finance', {'text': 'Independent work on another login'})
    attempt = claim(api, computer, 'finance')
    assert attempt is not None and attempt['bot'] == 'finance'
    with api.app.state.store.read() as c:
        from backend.views import free_turn
        assert free_turn(c, 'ops', {'machine': {'runner_id': rid, 'label': 'Test Mac'}}) == ''


def test_two_ready_waiters_claim_last_start_place_atomically(api):
    """Spec 3: renewable delivery leases give neither waiter a start place."""
    computer = waiters(api, n=2, running=0)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET capacity=1 WHERE id=?', (computer['runner_id'],))

    def reserve(i):
        return post(api, f'attempts/attempt-{i}/start-place', {}, computer['token'])

    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(reserve, range(2)))
    assert sum(a['reserved'] for a in answers) == 1
    winner = next(i for i, a in enumerate(answers) if a['reserved'])
    post(api, f'attempts/attempt-{winner}/started', {'thread_id': 'synthetic-thread'}, computer['token'])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE state='running'").fetchone()[0] == 1


def test_start_reservation_expires_despite_renewals_and_leaves_task_untouched(api, monkeypatch):
    """Spec 4: three minutes is a hard preparation deadline, not another renewable lease."""
    from backend.tests.test_task_lanes import task_bot, new_task
    computer = task_bot(api, lanes=True)
    task = new_task(api, "Work whose preparation times out")
    attempt = claim(api, computer)
    assert attempt['task']['id'] == task['id']
    store = api.app.state.store
    aid = attempt['id']
    now = H.now()
    with store.read() as c:
        before = [tuple(r) for r in c.execute('SELECT id,status,version,note,updated FROM tasks ORDER BY id')]
    for elapsed in (60, 120):
        monkeypatch.setattr(H, 'now', lambda elapsed=elapsed: H.shift(now, seconds=elapsed))
        post(api, f'attempts/{aid}/renew', {}, computer['token'])
    monkeypatch.setattr(H, 'now', lambda: H.shift(now, seconds=181))
    with store.transaction() as c:
        # Keep the delivery lease long: only the reservation has expired.
        c.execute('UPDATE attempts SET lease_until=? WHERE id=?', (H.shift(now, seconds=600), aid))
        from backend import start_places
        start_places.waiting(c, aid, 'shared login')
        assert aid in start_places.overdue(c)  # A late wait report cannot erase the deadline.
        api.app.state.execution.expire(c)
    with store.read() as c:
        assert c.execute('SELECT state FROM attempts WHERE id=?', (aid,)).fetchone()[0] == 'expired'
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'queued'
        assert [tuple(r) for r in c.execute('SELECT id,status,version,note,updated FROM tasks ORDER BY id')] == before
    denied = api.post(f'/api/v2/attempts/{aid}/started', json={'thread_id': 'too-late'}, headers=headers(computer['token']))
    assert denied.status_code == 409


@pytest.mark.parametrize('cpu,mem', [(95, .5), (20, .95)])
def test_overload_holds_only_new_starts_and_calm_load_resumes_them(api, cpu, mem):
    """Spec 14: the existing sampler's five-minute CPU/memory signal brakes this computer only."""
    import time
    computer = fleet(api, attempts=1, trees=0)
    assign(api, computer, 'finance')
    ready(api, computer, ['ops', 'finance'])
    sent_by(api, post(api, 'chat/finance', {'text': 'New work'})['id'], 'bot:ops')
    overload(api, computer, cpu=cpu, mem=mem)
    assert claim(api, computer, 'finance') is None
    machine = next(m for m in get(api, 'operations')['machines'] if m['id'] == computer['runner_id'])
    assert any(w['reason'] == 'computer busy' for w in machine['turn_counts']['waits'])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state,stop_requested FROM attempts WHERE id='attempt-0'").fetchone()[:] == ('running', None)
    post(api, 'runners/performance', {'samples': [point(int(time.time()) // 60 * 60 + 1, cpu=20)]}, computer['token'])
    assert claim(api, computer, 'finance') is not None


def test_stop_on_waiter_is_final_even_after_capacity_returns(api):
    """Spec 12 on the candidate: Stop still wins when a waiter releases its computer place."""
    computer = waiters(api, n=1, running=0)
    post(api, 'bots/ops/stop', {'attempt_id': 'attempt-0'})
    answer = post(api, 'attempts/attempt-0/renew', {}, computer['token'])
    assert answer['stop'] is True
    denied = post(api, 'attempts/attempt-0/start-place', {}, computer['token'])
    assert denied['reserved'] is False and denied['stop'] is True
    post(api, 'attempts/attempt-0/complete', {'outcome': 'interrupted', 'text': '', 'last_seq': 1}, computer['token'])
    assert claim(api, computer, 'ops') is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE attempt_id='attempt-0'").fetchone()[0] == 'cancelled'


def test_upgrade_preserves_legacy_waits_and_only_unstarted_places_expire(api, monkeypatch):
    from backend import login_waits, start_places
    monkeypatch.setattr(login_waits, 'harness_caps', lambda _: {'one_turn_per_login': True})
    computer = waiters(api, n=2, running=1)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM start_places")
        old = H.shift(H.now(), seconds=-7200)
        c.execute("UPDATE attempts SET created=?", (old,))
        # One legacy worker cleared its wait and really is preparing; the other still waits.
        c.execute("INSERT INTO attempt_events(attempt_id,seq,kind,payload_json,created) "
                  "VALUES('attempt-1',2,'status',?,?)", ('{"waiting":""}', H.now()))
        start_places.restore_waits(c)
        assert start_places.overdue(c) == []  # An older worker never agreed to a hard preparation deadline.
        assert sum(start_places.held(c, computer['runner_id']).values()) == 3
        counts = start_places.summary(c, computer['runner_id'], api.app.state.store.settings)
        assert counts['running'] == 1 and counts['waiting'] == 1
        assert counts['waits'][0]['oldest'] == old


def test_repeated_overdue_starts_stop_at_the_pre_start_retry_limit(api, monkeypatch):
    from backend import execution
    from backend.tests.test_api import setup_attempt
    monkeypatch.setattr(execution, 'PRE_START_TRIES', 2)
    machine, message, first = setup_attempt(api)
    store = api.app.state.store
    attempt = first
    for i in range(2):
        with store.transaction() as c:
            c.execute('UPDATE start_places SET start_until=? WHERE attempt_id=?',
                      (H.shift(H.now(), seconds=-1), attempt['id']))
            # Expiry is caused only by the hard start window, with a fresh delivery lease.
            c.execute('UPDATE attempts SET lease_until=? WHERE id=?', (H.shift(H.now(), seconds=600), attempt['id']))
            api.app.state.execution.expire(c)
        ready(api, machine, ['ops'])
        attempt = claim(api, machine)
        if i == 0:
            assert attempt['job_id'] == first['job_id']
    assert attempt is None
    with store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (first['job_id'],)).fetchone()[0] == 'failed'
    replies = get(api, f"conversations/{message['conversation_id']}/messages")
    assert [m['body'] for m in replies if m['from_actor'] == 'bot:ops'] == [
        "Your ops couldn't start on Test Mac; check that Computer"]


def test_legacy_runner_preparation_survives_upgrade_and_three_minutes(api, monkeypatch):
    from backend import start_places
    from backend.tests.test_api import runner
    computer = runner(api)
    assign(api, computer, 'ops')
    assert ready(api, computer, ['ops'])['start_places'] is True
    post(api, 'chat/ops', {'text': 'Work claimed by a worker before start-place support'})
    # Its old claim omits the opt-in even on a new server.
    old = post(api, 'jobs/claim', {}, computer['token'])['attempt']
    assert 'start_reservation_seconds' not in old
    now = H.now()
    monkeypatch.setattr(H, 'now', lambda: H.shift(now, seconds=181))
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE attempts SET lease_until=? WHERE id=?', (H.shift(H.now(), seconds=600), old['id']))
        start_places.restore_waits(c)
        assert start_places.overdue(c) == []
        assert sum(start_places.held(c, computer['runner_id']).values()) == 1
        api.app.state.execution.expire(c)
    post(api, f"attempts/{old['id']}/started", {'thread_id': 'legacy-thread'}, computer['token'])
    post(api, f"attempts/{old['id']}/complete", {'outcome': 'completed', 'text': '', 'last_seq': 0}, computer['token'])
    post(api, 'chat/ops', {'text': 'The upgraded worker now opts in'})
    ready(api, computer, ['ops'])
    from backend.tests.test_api import settled
    settled(api, computer)  # The worker kept heartbeating throughout the simulated preparation.
    fresh = claim(api, computer)
    assert fresh['start_reservation_seconds'] == 180
    with api.app.state.store.read() as c:
        assert start_places.managed(c, fresh['id'])


def test_legacy_wait_keeps_capacity_until_its_unchecked_start_finishes(api):
    """A worker without start-place support cannot release capacity then start over the limit."""
    from backend.tests.test_api import runner
    computer = runner(api)
    assign(api, computer, 'ops')
    assign(api, computer, 'finance')
    ready(api, computer, ['ops', 'finance'])
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET capacity=1 WHERE id=?', (computer['runner_id'],))
    post(api, 'chat/ops', {'text': 'Legacy work'})
    old = post(api, 'jobs/claim', {'bot': 'ops'}, computer['token'])['attempt']
    post(api, f"attempts/{old['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'waiting': 'shared login', 'login': 'ChatGPT'}}
    ]}, computer['token'])
    post(api, 'chat/finance', {'text': 'New protocol contender'})
    assert claim(api, computer, 'finance') is None
    post(api, f"attempts/{old['id']}/started", {'thread_id': 'legacy'}, computer['token'])
    assert claim(api, computer, 'finance') is None
    post(api, f"attempts/{old['id']}/complete", {'outcome': 'completed', 'text': '', 'last_seq': 1}, computer['token'])
    assert claim(api, computer, 'finance') is not None


def test_a_turn_waiting_for_a_docker_turn_user_gives_its_place_back(api):
    """A Gateway turn whose turn users are all busy waits like one at a busy login rather than failing at the server."""
    from backend import start_places
    from backend.tests.test_task_lanes import task_bot, new_task
    computer = task_bot(api, lanes=True)
    new_task(api, 'Work that waits for a turn user')
    aid = claim(api, computer)['id']
    post(api, f'attempts/{aid}/start-place', {'waiting': 'Docker turn user'}, computer['token'])
    with api.app.state.store.read() as c:
        assert start_places.is_waiting(c, aid)
    assert post(api, f'attempts/{aid}/start-place', {}, computer['token'])['reserved'] is True


def test_finished_task_stops_a_new_pre_start_wait_before_capacity_returns(api):
    from backend.tests.test_task_lanes import task_bot, new_task
    computer = task_bot(api, lanes=True)
    task = new_task(api, 'Work finished while its turn waits')
    attempt = claim(api, computer)
    aid = attempt['id']
    post(api, f'attempts/{aid}/start-place', {'waiting': 'pre-start requirements'}, computer['token'])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done',done_at=? WHERE id=?", (H.now(), task['id']))
    assert post(api, f'attempts/{aid}/renew', {}, computer['token'])['stop'] is True
    # The grant and started endpoints must fence the race even without another renewal.
    assert post(api, f'attempts/{aid}/start-place', {}, computer['token']) == {'reserved': False, 'stop': True}
    post(api, f'attempts/{aid}/started', {'thread_id': 'must-not-start'}, computer['token'], expected=409)
    post(api, f'attempts/{aid}/complete', {'outcome': 'interrupted', 'text': '', 'last_seq': 0}, computer['token'])
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state,started FROM attempts WHERE id=?', (aid,)).fetchone()[:] == ('interrupted', None)
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'cancelled'
        assert c.execute('SELECT status FROM tasks WHERE id=?', (task['id'],)).fetchone()[0] == 'done'


def test_shared_computer_counts_are_neutral_when_a_members_bot_list_is_empty(api):
    from backend.tests.test_api import restrict
    computer = waiters(api)
    with api.app.state.store.transaction() as c:
        restrict(c, 'ops', people=['ana'])
        c.execute('UPDATE runners SET accepts_member_bots=1 WHERE id=?', (computer['runner_id'],))
    row = next(x for x in get(api, 'computers', token='cara-test')['computers'] if x['id'] == computer['runner_id'])
    assert row['bots'] == []
    assert row['turn_counts']['waiting'] == 37 and row['turn_counts']['running'] == 3
    assert row['turn_counts']['waits'][0]['reason'] == 'shared login'
    for secret in ('ops', 'ChatGPT', 'task-', 'room-', 'ana'):
        assert secret not in str(row['turn_counts']) + row['turn_summary']


def test_live_legacy_login_clear_preserves_preparation_after_task_finishes(api, monkeypatch):
    from backend import login_waits, start_places
    from backend.tests.test_task_lanes import task_bot, new_task
    caps = {'one_turn_per_login': True}
    monkeypatch.setattr(login_waits, 'harness_caps', lambda _: caps)
    computer = task_bot(api, lanes=True)
    task = new_task(api, 'Work finished during legacy preparation')
    attempt = post(api, 'jobs/claim', {}, computer['token'])['attempt']
    aid = attempt['id']

    def status(seq, waiting):
        post(api, f'attempts/{aid}/events', {'events': [
            {'seq': seq, 'kind': 'status', 'payload': {'waiting': waiting, 'login': 'ChatGPT'}}
        ]}, computer['token'])

    status(1, 'shared login')
    with api.app.state.store.read() as c:
        assert start_places.is_waiting(c, aid)
    status(2, '')
    with api.app.state.store.transaction() as c:
        assert not start_places.is_waiting(c, aid)
        assert c.execute('SELECT waiting_since FROM start_places WHERE attempt_id=?', (aid,)).fetchone()[0] is None
        c.execute("UPDATE tasks SET status='done',done_at=? WHERE id=?", (H.now(), task['id']))
    assert not post(api, f'attempts/{aid}/renew', {}, computer['token']).get('stop')
    # An obsolete login-queue report cannot reintroduce a wait after this capability opts out.
    caps['one_turn_per_login'] = False
    status(3, 'obsolete shared login')
    with api.app.state.store.transaction() as c:
        assert not start_places.is_waiting(c, aid)
        counts = start_places.summary(c, computer['runner_id'], api.app.state.store.settings)
        assert counts['starting'] == 1 and counts['waiting'] == 0
        c.execute('DELETE FROM start_places WHERE attempt_id=?', (aid,))
        start_places.restore_waits(c)
        assert not start_places.is_waiting(c, aid)
    assert post(api, f'attempts/{aid}/started', {'thread_id': 'legacy-preparation'}, computer['token'])['started']


def test_delayed_managed_login_events_cannot_release_a_new_start_grant(api, monkeypatch):
    from backend import login_waits
    monkeypatch.setattr(login_waits, 'harness_caps', lambda _: {'one_turn_per_login': True})
    computer = waiters(api, n=1, running=0)
    aid = 'attempt-0'
    grant = post(api, f'attempts/{aid}/start-place', {}, computer['token'])
    assert grant['reserved']
    post(api, f'attempts/{aid}/events', {'events': [
        {'seq': 2, 'kind': 'status', 'payload': {'waiting': 'late login wait', 'login': 'ChatGPT'}}
    ]}, computer['token'])
    with api.app.state.store.read() as c:
        assert c.execute('SELECT start_until,wait_reason,waiting_since FROM start_places WHERE attempt_id=?',
                         (aid,)).fetchone()[:] == (grant['start_until'], '', None)
    assert post(api, f'attempts/{aid}/started', {'thread_id': 'managed-grant'}, computer['token'])['started']


def test_overload_rechecks_even_a_previously_granted_start(api):
    computer = waiters(api, n=1, running=0)
    sent_by(api, 'message-0', H.KEEPER)
    grant = post(api, 'attempts/attempt-0/start-place', {}, computer['token'])
    assert grant['reserved']
    overload(api, computer)
    post(api, 'attempts/attempt-0/started', {'thread_id': 'braked'}, computer['token'], expected=409)
    assert post(api, 'attempts/attempt-0/start-place', {}, computer['token']) == {
        'reserved': False, 'waiting': 'computer busy'}


def test_a_persons_own_message_starts_on_an_overloaded_computer_and_nothing_else_does(api):
    """Bots' own load held every start, a person's chat to a bot included, behind a hundred bot turns. A person's
    message still starts, ahead of the turns waiting there; a keeper notice (routine, sweep, rule wake) or a bot's
    message still waits."""
    computer = waiters(api, n=3, running=0)
    sent_by(api, 'message-0', H.KEEPER)
    sent_by(api, 'message-1', 'bot:finance')
    overload(api, computer)
    for i in (0, 1):
        assert post(api, f'attempts/attempt-{i}/start-place', {}, computer['token']) == {
            'reserved': False, 'waiting': 'computer busy'}
    # attempt-2 answers a person and is claimed after the two now waiting "computer busy".
    assert post(api, 'attempts/attempt-2/start-place', {}, computer['token'])['reserved']
    assert post(api, 'attempts/attempt-2/started', {'thread_id': 'person'}, computer['token'])['started']


def test_a_persons_message_on_an_overloaded_computer_still_needs_a_free_turn(api):
    computer = waiters(api, n=1, running=1)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET capacity=1 WHERE id=?', (computer['runner_id'],))
    overload(api, computer)
    assert post(api, 'attempts/attempt-0/start-place', {}, computer['token']) == {
        'reserved': False, 'waiting': 'computer busy'}


def test_an_overloaded_computer_hands_out_a_persons_chat_but_not_a_bots_message(api):
    """The claim holds the queue on an overloaded computer, or one with turns waiting to start there, except for a
    person's own message: before, a person's chat never reached a start place to be let through."""
    computer = fleet(api, attempts=1, trees=0)
    for bot in ('finance', 'cpo'):
        assign(api, computer, bot)
    ready(api, computer, ['ops', 'finance', 'cpo'])
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET capacity=5 WHERE id=?', (computer['runner_id'],))
    sent_by(api, post(api, 'chat/cpo', {'text': 'Review the contract'})['id'], 'bot:ops')
    post(api, 'chat/finance', {'text': 'Close the books'})
    overload(api, computer)
    attempt = claim(api, computer)
    assert attempt is not None and attempt['bot'] == 'finance'
    assert post(api, f"attempts/{attempt['id']}/start-place", {}, computer['token'])['reserved']
    assert claim(api, computer) is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT j.state FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "WHERE m.from_actor='bot:ops'").fetchone()[0] == 'queued'


def test_operations_and_computers_share_one_company_default_across_runners(api, monkeypatch):
    """Each computer's summary used to rebuild company pools/groups; one Default memo covers the listing."""
    from backend import usage_limits
    from backend.tests.test_api import runner

    first = waiters(api, n=0, running=0)
    second = runner(api, label='Other synthetic computer')
    assign(api, second, 'finance')
    ready(api, second, ['finance'])
    post(api, 'chat/ops', {'text': 'Queue on first computer'})
    post(api, 'chat/finance', {'text': 'Queue on second computer'})
    calls = []
    real = usage_limits.company

    def once(c):
        calls.append(1)
        return real(c)

    monkeypatch.setattr(usage_limits, 'company', once)
    machines = get(api, 'operations')['machines']
    ops_calls = len(calls)
    calls.clear()
    computers = get(api, 'computers')['computers']
    computer_calls = len(calls)
    ids = {first['runner_id'], second['runner_id']}
    assert ids <= {m['id'] for m in machines}
    assert ids <= {m['id'] for m in computers}
    assert ops_calls == 1, ops_calls
    assert computer_calls == 1, computer_calls


def test_held_totals_match_each_computers_held_counts(api):
    """Health's capacity fence used one held() query per computer; the fleet pass must agree."""
    from backend import start_places
    from backend.tests.test_api import runner

    first = waiters(api, n=2, running=1)
    second = runner(api, label='Other capacity computer')
    assign(api, second, 'finance')
    ready(api, second, ['finance'])
    with api.app.state.store.read() as c:
        totals = start_places.held_totals(c)
        for rid in (first['runner_id'], second['runner_id']):
            assert totals.get(rid, 0) == sum(start_places.held(c, rid).values())


def memory(api, computer, mem, at=None):
    """One synthetic sample at this minute (or at `at`), memory at `mem` of the computer's total."""
    import time
    at = at or int(time.time()) // 60 * 60
    post(api, 'runners/performance', {'samples': [point(at, cpu=20, mem=mem)]}, computer['token'])


def waits_of(api, computer):
    machine = next(m for m in get(api, 'operations')['machines'] if m['id'] == computer['runner_id'])
    return machine['turn_counts']['waits']


def bot_waiting_for_finance(api, sender='bot:ops'):
    computer = fleet(api, attempts=1, trees=0)
    assign(api, computer, 'finance')
    ready(api, computer, ['ops', 'finance'])
    sent_by(api, post(api, 'chat/finance', {'text': 'New work'})['id'], sender)
    return computer


def test_memory_at_85_holds_new_starts_and_they_start_again_below_80(api):
    """Memory at 85% or more holds a bot's new start; between 80 and 85 the hold stays while a start waits on it."""
    computer = bot_waiting_for_finance(api)
    memory(api, computer, .86)
    assert claim(api, computer, 'finance') is None
    assert any(w['reason'] == 'computer busy: memory' for w in waits_of(api, computer))
    memory(api, computer, .82)
    assert claim(api, computer, 'finance') is None
    memory(api, computer, .79)
    assert claim(api, computer, 'finance') is not None


def test_memory_between_80_and_85_admits_a_start_when_none_waits(api):
    computer = bot_waiting_for_finance(api)
    memory(api, computer, .82)
    assert claim(api, computer, 'finance') is not None


def test_hold_words_name_the_memory_percent_and_the_stale_sample(api):
    computer = bot_waiting_for_finance(api)
    memory(api, computer, .86)
    from backend.start_places import computer_hold
    with api.app.state.store.read() as c:
        assert computer_hold(c, computer['runner_id']) == ('computer busy: memory', 'computer busy: memory 86%')
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runner_load_samples SET ts=ts-300,sampled_at=sampled_at-300 WHERE runner_id=?',
                  (computer['runner_id'],))
    with api.app.state.store.read() as c:
        assert computer_hold(c, computer['runner_id']) == ('computer not reporting', 'computer not reporting')


def test_a_computer_whose_sample_is_stale_refuses_bot_starts_and_admits_a_persons_message(api):
    """A frozen computer stops sampling; with diagnostics on its last sample is old and it admits no bot's start."""
    import time
    computer = bot_waiting_for_finance(api)
    memory(api, computer, .3, at=int(time.time()) // 60 * 60 - 300)
    assert claim(api, computer, 'finance') is None
    machine = next(m for m in get(api, 'operations')['machines'] if m['id'] == computer['runner_id'])
    assert any(w['reason'] == 'computer not reporting' for w in machine['turn_counts']['waits'])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET from_actor='human:chris' WHERE from_actor='bot:ops'")
    assert claim(api, computer, 'finance') is not None


def test_stale_sample_admits_again_once_diagnostics_are_off(api):
    """Diagnostics off keeps the start rules as they were: a stale sample holds nothing."""
    import time
    computer = bot_waiting_for_finance(api)
    memory(api, computer, .3, at=int(time.time()) // 60 * 60 - 300)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET performance='off' WHERE id=?", (computer['runner_id'],))
    assert claim(api, computer, 'finance') is not None


def test_a_computer_that_never_reported_is_not_held(api):
    """Older runners never sent samples; holding them for silence would stop every start on them."""
    computer = bot_waiting_for_finance(api)
    assert claim(api, computer, 'finance') is not None


def batch(api, computer, readings):
    """One delivery of samples [(minute offset in seconds from now, memory fraction)], as the runner posts them."""
    import time
    now = int(time.time()) // 60 * 60
    post(api, 'runners/performance', {'samples': [point(now + off, cpu=20, mem=mem) for off, mem in readings]},
         computer['token'])


def test_a_batch_that_climbs_through_85_then_82_holds_from_clear(api):
    """A recovered delivery of readings is replayed in order: 86% then 82% holds, though the last one is in the band."""
    computer = bot_waiting_for_finance(api)
    batch(api, computer, [(-60, .86), (0, .82)])
    assert claim(api, computer, 'finance') is None


def test_a_batch_that_falls_to_79_then_rises_to_82_releases_a_held_computer(api):
    computer = bot_waiting_for_finance(api)
    import time
    now = int(time.time()) // 60 * 60
    memory(api, computer, .86, at=now - 120)
    batch(api, computer, [(-60, .79), (0, .82)])
    assert claim(api, computer, 'finance') is not None


def test_an_older_delivery_cannot_move_a_hold_set_by_a_newer_reading(api):
    """A late batch from before the newest stored reading is ignored, as the per-minute upsert already ignores it."""
    computer = bot_waiting_for_finance(api)
    memory(api, computer, .3)
    batch(api, computer, [(-300, .9)])
    assert claim(api, computer, 'finance') is not None


def test_a_computer_that_reported_then_went_silent_past_retention_is_not_reporting_until_it_samples_again(api):
    """Samples past the 48-hour window are pruned, but the computer still reported before: it stays held until it
    sends a fresh sample, and a computer that never reported is not held (above)."""
    import time
    computer = bot_waiting_for_finance(api)
    memory(api, computer, .3)
    with api.app.state.store.read() as c:
        marked = c.execute('SELECT last_sampled_at FROM runners WHERE id=?', (computer['runner_id'],)).fetchone()[0]
    assert marked == int(time.time()) // 60 * 60
    with api.app.state.store.transaction() as c:
        c.execute('DELETE FROM runner_load_samples WHERE runner_id=?', (computer['runner_id'],))
        c.execute('UPDATE runners SET last_sampled_at=last_sampled_at-200000 WHERE id=?', (computer['runner_id'],))
    assert claim(api, computer, 'finance') is None
    memory(api, computer, .3)
    assert claim(api, computer, 'finance') is not None


def test_an_upgraded_computer_stays_held_after_its_samples_are_pruned_and_startups_keep_its_mark(api):
    """A database from before the mark: starting the server backfills it from the samples the computer kept. Retention
    then prunes those samples with nothing new ingested, and a second start keeps the mark, so the computer is still
    not reporting until a fresh sample arrives."""
    import time
    from backend import performance as P
    store = api.app.state.store
    computer = bot_waiting_for_finance(api)
    rid = computer['runner_id']
    memory(api, computer, .3)
    now = int(time.time()) // 60 * 60
    with store.transaction() as c:
        c.execute('ALTER TABLE runners DROP COLUMN last_sampled_at')
    store.initialize()
    with store.read() as c:
        assert c.execute('SELECT last_sampled_at FROM runners WHERE id=?', (rid,)).fetchone()[0] == now
    P.sweep(store, now=int(time.time()) + 49 * 3600)
    with store.read() as c:
        assert c.execute('SELECT count(*) FROM runner_load_samples WHERE runner_id=?', (rid,)).fetchone()[0] == 0
    store.initialize()
    with store.read() as c:
        assert c.execute('SELECT last_sampled_at FROM runners WHERE id=?', (rid,)).fetchone()[0] == now
    assert claim(api, computer, 'finance') is None
    memory(api, computer, .3)
    assert claim(api, computer, 'finance') is not None
