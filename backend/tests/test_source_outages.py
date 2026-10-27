"""Outside failures preserve work and send one source transition, never one escalation per turn."""
import json

import pytest

from backend import login_holds, source_outages
from backend.store import H
from backend.tests.test_api import api, assign, claim, expire, get, post, ready, runner  # noqa: F401
from backend.tests.test_login_holds import at, events, limit, done, signed_in


def notices(c):
    return [dict(r) for r in c.execute("SELECT * FROM messages WHERE json_extract(refs_json,'$.source_outage') IS NOT NULL")]


def managers(c):
    from backend.health_monitor import SLUG
    c.execute("INSERT INTO bots(slug,display_name,runtime,state) VALUES(?,?,?,'active')", (SLUG, 'Health', 'fake'))
    c.execute("UPDATE task_types SET manager='coo',role='engineering' WHERE id='general'")


def test_source_start_and_end_are_once_across_repeated_observations_and_restarts(api, monkeypatch):
    at(monkeypatch, '2026-10-27T01:00:00.000000Z')
    with api.app.state.store.transaction() as c:
        managers(c)
        for count in (1, 20, 100):
            source_outages.observe(c, 'private-login-name', 'Claude subscription quota', waiting=count,
                                   until='2026-10-27T07:00:00.000000Z')
        rows = notices(c)
        assert len(rows) == 2 and {r['to_actor'] for r in rows} == {'bot:coo', 'bot:health-monitor'}
        assert not any('private-login-name' in r['body'] or 'private-login-name' in r['refs_json'] for r in rows)
        assert not c.execute("SELECT 1 FROM jobs WHERE message_id IN (?,?)", tuple(r['id'] for r in rows)).fetchone()
        assert not c.execute("SELECT 1 FROM tasks").fetchone()
    # No process-local latch: a fresh transaction observes the saved episode.
    with api.app.state.store.transaction() as c:
        source_outages.observe(c, 'private-login-name', 'Claude subscription quota', waiting=100,
                               until='2026-10-27T07:00:00.000000Z')
        source_outages.observe(c, 'private-login-name', 'Claude subscription quota', active=False)
        source_outages.observe(c, 'private-login-name', 'Claude subscription quota', active=False)
        assert len(notices(c)) == 4
        assert all('available again' in r['body'] for r in notices(c)[2:])
        source_outages.observe(c, 'another-login', 'Claude subscription quota', waiting=1)
        assert len(notices(c)) == 6


def test_a_six_hour_hold_does_not_lease_again_or_hold_another_subscription(api, monkeypatch):
    at(monkeypatch, '2026-10-27T01:00:00.000000Z')
    from backend.tests.test_login_holds import providers_on
    providers_on(api)
    machine = runner(api)
    for bot in ('ops', 'finance'):
        assign(api, machine, bot)
    signed_in(api, machine, ['ops', 'finance'], ('codex',))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.fallback.off',json('true'))")
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.runtime','codex') WHERE bot='finance'")
    task = post(api, 'tasks', {'owner': 'ops', 'title': 'Prepare the deployment plan', 'body': 'Review existing code.'})
    first = claim(api, machine)
    limit(api, machine, first, errors=('Usage limit; usage window resets at 2026-10-27T07:00:00Z',))
    other = post(api, 'tasks', {'owner': 'finance', 'title': 'Review the invoice', 'body': 'Read it.'})
    with api.app.state.store.transaction() as c:
        managers(c)
    for hour in range(2, 7):
        at(monkeypatch, f'2026-10-27T{hour:02}:00:00.000000Z', api)
        ready(api, machine, ['ops', 'finance'])
        if hour == 2:
            work = claim(api, machine)
            assert work and work['bot'] == 'finance'
            done(api, machine, work)
        assert claim(api, machine) is None
        with api.app.state.store.transaction() as c:
            source_outages.sync(c, api.app.state.store.settings)
            assert c.execute('SELECT count(*) FROM attempts WHERE job_id=?', (first['job_id'],)).fetchone()[0] == 1
            assert not c.execute("SELECT 1 FROM jobs WHERE state='uncertain'").fetchone()
            assert not c.execute("SELECT 1 FROM bots WHERE state='quarantined'").fetchone()
            assert not c.execute("SELECT 1 FROM tasks WHERE blocked_since IS NOT NULL").fetchone()
    at(monkeypatch, '2026-10-27T07:01:00.000000Z', api)
    ready(api, machine, ['ops', 'finance'])
    resumed = claim(api, machine)
    assert resumed and resumed['job_id'] == first['job_id'] and resumed['task']['id'] == task['id']
    done(api, machine, resumed)
    with api.app.state.store.transaction() as c:
        source_outages.sync(c, api.app.state.store.settings)
        assert len(notices(c)) == 4
        assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == 2


@pytest.mark.parametrize('outcome', ['failed', 'interrupted'])
def test_mid_turn_fallback_quota_is_saved_and_resumes_at_its_own_reset(api, monkeypatch, outcome):
    from backend.tests.test_login_holds import two_bots
    machine = two_bots(api, monkeypatch)
    post(api, 'tasks', {'owner': 'ops', 'title': 'Read the startup instructions', 'body': 'Continue the task.'})
    first = claim(api, machine)
    post(api, f"attempts/{first['id']}/started", {'thread_id': 'saved-primary'}, machine['token'])
    seq = events(api, machine, first, [
        ('diagnostic', 'fake unavailable; running this turn on codex'),
        ('tool', 'Read'), ('error', 'Usage limit; usage window resets at 2026-10-19T07:00:00Z')])
    result = post(api, f"attempts/{first['id']}/complete", {'outcome': outcome, 'last_seq': seq,
                  'fallback': 'codex', 'primary_held': True, 'limited': True, 'text': 'Checkpoint: read the files.'}, machine['token'])
    assert result['outcome'] == 'interrupted'
    with api.app.state.store.read() as c:
        saved = json.loads(c.execute('SELECT result_json FROM attempts WHERE id=?', (first['id'],)).fetchone()[0])
        assert saved['retryable'] and saved['source_pause'] == 'quota'
        assert c.execute('SELECT state FROM jobs WHERE id=?', (first['job_id'],)).fetchone()[0] == 'queued'
        hold = login_holds.get(c, machine['runner_id'], '', 'codex')
        assert hold['until'].startswith('2026-10-19T07:00:00')
        assert not login_holds.get(c, machine['runner_id'], '', 'fake')
    at(monkeypatch, '2026-10-19T07:01:00.000000Z', api)
    ready(api, machine, ['ops', 'finance'])
    again = claim(api, machine)
    assert again and again['job_id'] == first['job_id']


def test_an_absolute_reset_is_not_extended_by_a_later_status_refresh(api, monkeypatch):
    from backend.execution import cooling_off, release_limits
    at(monkeypatch, '2026-10-27T01:00:00.000000Z')
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    post(api, 'tasks', {'owner': 'ops', 'title': 'Review the startup rules', 'body': 'Read them.'})
    first = claim(api, machine)
    limit(api, machine, first, errors=('Usage limit; usage window resets at 2026-10-27T07:00:00Z',))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_status SET since='2026-10-27T06:59:00.000000Z' WHERE bot='ops'")
    at(monkeypatch, '2026-10-27T07:01:00.000000Z', api)
    with api.app.state.store.transaction() as c:
        assert not cooling_off(c, 'ops')
        release_limits(c)
        assert H.status(c, 'ops')['state'] == 'idle'
        assert 'resumes' in H.status(c, 'ops')['focus']
    ready(api, machine, ['ops'])
    again = claim(api, machine)
    assert again and again['job_id'] == first['job_id']


def test_thirty_human_closures_make_one_note_and_no_wakes_for_a_paused_bot(api):
    with api.app.state.store.transaction() as c:
        tasks = [H.task_create(c, 'bot:coo', f'Review change {i}', 'Review the existing change.', 'bot:finance', lint=False)
                 for i in range(30)]
        c.execute("UPDATE jobs SET state='completed'")
        c.execute("UPDATE bots SET state='paused' WHERE slug IN ('coo','finance')")
        for task in tasks:
            H.task_close(c, 'human:ana', task['id'], note='Board cleanup: finished and shipped.')
        assert not c.execute("SELECT 1 FROM jobs WHERE state='queued'").fetchone()
        for target in ('bot:coo', 'bot:finance'):
            notes = [dict(r) for r in c.execute('SELECT * FROM notes WHERE to_actor=?', (target,))]
            assert len(notes) == 1 and '30 tasks' in notes[0]['body']
            assert not any(task['title'] in notes[0]['body'] for task in tasks)
        assert c.execute("SELECT count(*) FROM messages WHERE body LIKE 'Closed:%'").fetchone()[0] == 60


def test_twenty_minutes_of_busy_server_renewals_keep_the_running_lease_and_thread(api, monkeypatch):
    from backend.tests.test_api import headers, setup_attempt
    from backend.store import Problem
    machine, _, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'keep-this-session'}, machine['token'])
    store, execution = api.app.state.store, api.app.state.execution
    last = H.now()
    execution.served_at = None  # prove per-attempt arrival protection, independently of global stall grace
    original = execution.renew
    def busy(*args, **kwargs):
        raise Problem('unavailable', 'Synthetic write lock is busy', 503, retryable=True)
    monkeypatch.setattr(execution, 'renew', busy)
    for minute in range(1, 21):
        at(monkeypatch, H.shift(last, seconds=minute * 60), api)
        refused = api.post(f"/api/v2/attempts/{attempt['id']}/renew", json={}, headers=headers(machine['token']))
        assert refused.status_code == 503
        with store.transaction() as c:
            execution.expire(c)
            assert c.execute('SELECT state FROM attempts WHERE id=?', (attempt['id'],)).fetchone()[0] == 'running'
            assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'running'
    monkeypatch.setattr(execution, 'renew', original)
    assert post(api, f"attempts/{attempt['id']}/renew", {}, machine['token'])
    with store.read() as c:
        assert c.execute('SELECT thread_id FROM attempts WHERE id=?', (attempt['id'],)).fetchone()[0] == 'keep-this-session'
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Continued the same thread.'}, machine['token'])


def test_a_safe_lost_lease_resumes_once_then_waits_without_a_botops_task(api, monkeypatch):
    from backend.tests.test_api import setup_attempt
    from backend.execution import STALL_MAX
    machine, _, first = setup_attempt(api)
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {'ops': True},
                                     'capabilities': ['capacity_continue_v1']}, machine['token'])
    post(api, f"attempts/{first['id']}/started", {'thread_id': 'same-session'}, machine['token'])
    post(api, f"attempts/{first['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'tool_events': True}}]}, machine['token'])
    clock = H.now()
    store, execution = api.app.state.store, api.app.state.execution
    for attempt in (first, None):
        if attempt is None:
            post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {'ops': True},
                                             'capabilities': ['capacity_continue_v1']}, machine['token'])
            attempt = claim(api, machine)
            assert attempt and attempt['job_id'] == first['job_id'] and attempt['recovery']['resume_session']
            assert attempt['continue_thread'] == 'same-session'
            post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'same-session'}, machine['token'])
            post(api, f"attempts/{attempt['id']}/events", {'events': [
                {'seq': 1, 'kind': 'status', 'payload': {'tool_events': True}}]}, machine['token'])
        expire(api, attempt['id'])
        # No server-stall grace: the computer alone lost contact.
        execution.served_at = None
        with store.transaction() as c:
            execution.expire(c)
            assert H.status(c, 'ops')['state'] == 'idle' and 'Resuming' in H.status(c, 'ops')['focus']
        clock = H.shift(clock, seconds=STALL_MAX + 1)
        at(monkeypatch, clock, api)
        with store.transaction() as c:
            execution.auto_reconcile(c)
    with store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (first['job_id'],)).fetchone()[0] == 'uncertain'
        assert c.execute('SELECT count(*) FROM attempts WHERE job_id=?', (first['job_id'],)).fetchone()[0] == 2
        assert not c.execute("SELECT 1 FROM events WHERE action='job.review_requested'").fetchone()
        assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0
        assert 'second lost lease' in H.status(c, 'ops')['focus']


def test_a_hundred_queued_notices_restore_as_one_carrier_with_their_history(api, monkeypatch):
    at(monkeypatch, '2026-10-27T01:00:00.000000Z')
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, 'human:ana', 'Review the change', 'Review it.', 'bot:ops', lint=False)
        c.execute("UPDATE jobs SET state='completed'")
        c.execute("UPDATE tasks SET status='doing' WHERE id=?", (task['id'],))
        for n in range(100):
            from backend.rules import Decision, WAKE
            sent = H._write_message(c, H.KEEPER, 'bot:ops', f'Waiting update {n}',
                                     H.conversation(c, task['conversation_id']), 'notice', {'task': task['id']},
                                     None, None, decided=Decision(WAKE, 'assignment'))
            # IDs sort opposite to arrival, so the clock tie cannot accidentally make this guard pass.
            c.execute('UPDATE jobs SET id=? WHERE message_id=?', (f'waiting-{100-n:03}', sent['id']))
        source_outages.batch_waiters(c, 'ops')
        pending = [dict(r) for r in c.execute("SELECT * FROM jobs WHERE state='queued'")]
        assert len(pending) == 1
        message = H.message(c, pending[0]['message_id'])
        assert message['refs']['wake_digest'] == 100
        assert c.execute('SELECT count(*) FROM jobs WHERE wake_carrier=?', (pending[0]['id'],)).fetchone()[0] == 100
        assert c.execute("SELECT count(*) FROM messages WHERE body LIKE 'Waiting update %'").fetchone()[0] == 100


def test_quota_after_a_named_external_action_stays_held(api, monkeypatch):
    at(monkeypatch, '2026-10-27T01:00:00.000000Z')
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    post(api, 'chat/ops', {'text': 'Continue the saved work.'})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'external-action'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'tool', 'payload': {'name': 'Bash', 'status': 'started',
                                            'outside_actions': ['merge pull request']}},
        {'seq': 2, 'kind': 'error', 'payload': {'error': 'Usage limit; resets at 2026-10-27T07:00:00Z', 'limit': True}}]}, machine['token'])
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {'ops': True},
                                  'capabilities': ['capacity_continue_v1', 'source_continue_v1']}, machine['token'])
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'failed', 'limited': True, 'fallback': 'codex',
          'source_continuation': {'thread_id': 'side-session', 'runtime': 'codex', 'harness': 'codex',
                                  'model': 'synthetic', 'profile': '', 'epoch': None},
          'last_seq': 2, 'text': 'Stopped after the merge request.'}, machine['token'])
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'uncertain'
        result = json.loads(c.execute('SELECT result_json FROM attempts WHERE id=?', (attempt['id'],)).fetchone()[0])
        assert not result.get('source_pause') and not result.get('source_continue')


@pytest.mark.parametrize('evidence', ['unknown_shell', 'final_answer'])
def test_strict_lease_recovery_preserves_unknown_effects_and_late_final_answers(api, monkeypatch, evidence):
    from backend.execution import retry_facts
    from backend.tests.test_api import setup_attempt
    machine, _, attempt = setup_attempt(api)
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {'ops': True},
                                     'capabilities': ['capacity_continue_v1']}, machine['token'])
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'kept'}, machine['token'])
    payload = ({'seq': 2, 'kind': 'tool', 'payload': {'name': 'shell'}} if evidence == 'unknown_shell' else
               {'seq': 2, 'kind': 'message', 'payload': {'text': 'Done.', 'final': True}})
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'tool_events': True}}, payload]}, machine['token'])
    expire(api, attempt['id'])
    execution = api.app.state.execution
    execution.served_at = None
    with api.app.state.store.transaction() as c:
        execution.expire(c)
        c.execute('UPDATE attempts SET finished=? WHERE id=?',
                  (H.shift(H.now(), minutes=-16), attempt['id']))
        row = c.execute('SELECT * FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()
        run = c.execute('SELECT started,finished FROM attempts WHERE id=?', (attempt['id'],)).fetchone()
        facts = retry_facts(c, row, run['started'], run['finished'])
        assert not facts['lease_lost']
        execution.auto_reconcile(c)
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'uncertain'
        assert not c.execute("SELECT 1 FROM events WHERE action='job.auto_reconcile' AND target=?",
                             (attempt['id'],)).fetchone()



def test_mid_turn_primary_quota_uses_strict_saved_session_even_with_a_ready_fallback(api, monkeypatch):
    from backend.tests.test_login_holds import two_bots
    machine = two_bots(api, monkeypatch)
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {'ops': True},
                                     'capabilities': ['capacity_continue_v1']}, machine['token'])
    post(api, 'chat/ops', {'text': 'Continue reading the saved work.'})
    first = claim(api, machine)
    post(api, f"attempts/{first['id']}/started", {'thread_id': 'quota-session'}, machine['token'])
    seq = events(api, machine, first, [('tool', 'Read'),
                 ('error', 'Usage limit; resets at 2026-10-19T07:00:00Z')])
    post(api, f"attempts/{first['id']}/complete", {'outcome': 'failed', 'limited': True,
                 'last_seq': seq, 'text': 'Files read; continue after the reset.'}, machine['token'])
    assert claim(api, machine) is None  # the other login is ready, but it does not hold this session's work
    at(monkeypatch, '2026-10-19T07:01:00.000000Z', api)
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {'ops': True},
                                     'capabilities': ['capacity_continue_v1']}, machine['token'])
    again = claim(api, machine)
    assert again and again['job_id'] == first['job_id'] and again['continue_thread'] == 'quota-session'
    post(api, f"attempts/{again['id']}/started", {'thread_id': 'quota-session'}, machine['token'])
    post(api, f"attempts/{again['id']}/complete", {'outcome': 'failed', 'last_seq': 0,
                 'text': 'The saved session is missing.'}, machine['token'])
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (first['job_id'],)).fetchone()[0] == 'uncertain'
    assert claim(api, machine) is None



def test_same_timestamp_backlog_keeps_the_newest_notice_when_ids_sort_backwards(api, monkeypatch):
    from backend.rules import Decision, WAKE
    at(monkeypatch, '2026-10-27T01:00:00.000000Z')
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, 'human:ana', 'Read the latest update', 'Read it.', 'bot:ops', lint=False)
        c.execute("UPDATE jobs SET state='completed'")
        messages = []
        for n in range(3):
            message = H._write_message(c, H.KEEPER, 'bot:ops', f'Update {n}',
                          H.conversation(c, task['conversation_id']), 'notice', {'task': task['id']},
                          None, None, decided=Decision(WAKE, 'assignment'))
            c.execute('UPDATE jobs SET id=? WHERE message_id=?', (f'waiting-{3-n}', message['id']))
            messages.append(message['id'])
        source_outages.batch_waiters(c, 'ops')
        [carrier] = c.execute("SELECT id,message_id FROM jobs WHERE state='queued'").fetchall()
        assert carrier['message_id'] == messages[-1]
        assert c.execute('SELECT count(*) FROM jobs WHERE wake_carrier=?', (carrier['id'],)).fetchone()[0] == 2
        assert all(H.message(c, message) for message in messages)


def test_negotiated_fallback_continuation_keeps_its_provider_and_refuses_an_old_runner(api, monkeypatch):
    from backend.tests.test_login_holds import two_bots
    machine = two_bots(api, monkeypatch)
    caps = ['capacity_continue_v1', 'source_continue_v1']
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {'ops': True},
                                  'capabilities': caps}, machine['token'])
    post(api, 'chat/ops', {'text': 'Read the saved work.'})
    first = claim(api, machine)
    post(api, f"attempts/{first['id']}/started", {'thread_id': 'primary-thread'}, machine['token'])
    seq = events(api, machine, first, [('tool', 'Read'), ('error', 'Usage limit; resets at 2026-10-19T07:00:00Z')])
    saved = {'thread_id': 'fallback-thread', 'runtime': 'codex', 'harness': 'codex', 'model': 'synthetic',
             'reasoning_effort': '', 'profile': '', 'epoch': None}
    post(api, f"attempts/{first['id']}/complete", {'outcome': 'failed', 'limited': True, 'fallback': 'codex',
          'source_continuation': saved, 'last_seq': seq}, machine['token'])
    assert claim(api, machine) is None
    # A later setting stops new fallback turns, not continuation of work already saved there.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.fallback.off',json('true')) WHERE bot='ops'")
        login_holds.record(c, machine['runner_id'], '', 'fake', H.now(), reset='2026-10-19T09:00:00.000000Z')
    at(monkeypatch, '2026-10-19T07:01:00.000000Z', api)
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {'ops': True},
                                  'capabilities': ['capacity_continue_v1']}, machine['token'])
    assert claim(api, machine) is None  # old runners know only primary-session continuation
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {'ops': True},
                                  'capabilities': caps}, machine['token'])
    again = claim(api, machine)
    assert again and again['job_id'] == first['job_id']
    assert again['continue_thread'] == 'fallback-thread' and again['source_continuation'] == saved
    with api.app.state.store.read() as c:
        assert c.execute('SELECT thread_id FROM attempts WHERE id=?', (first['id'],)).fetchone()[0] == 'primary-thread'
        assert not c.execute("SELECT 1 FROM jobs WHERE state='uncertain'").fetchone()
