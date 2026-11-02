"""Core failure/stall flow and bounded private fallback export, using only synthetic API fixtures."""
import json

import pytest

from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner, setup_attempt  # noqa: F401


def start(api):
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    post(api, 'tasks', {'owner': 'ops', 'title': 'Inspect runs', 'body': 'Please summarize the current work.'})
    attempt = claim(api, machine, 'ops')
    msg = attempt['message']
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'thread'}, machine['token'])
    return machine, msg, attempt


def listed(api, aid):
    return next(r for r in get(api, 'bots/ops/turns') if r['id'] == aid)


@pytest.mark.parametrize(('cause', 'expected'), [
    ({'error': 'killed', 'exit_code': -15, 'process_exit': True}, 'Process exited (signal 15)'),
    ({'error': 'out of memory', 'exit_code': -9, 'process_exit': True}, 'Process exited (signal 9)'),
    ({'exit_code': -9, 'process_exit': True, 'oom_killed': True}, 'OOM-killed (reported by the runner)'),
    ({'end_reason': 'Timeout: max_run_minutes (60 minutes)'}, 'Timeout: max_run_minutes (60 minutes)'),
    ({'error': {'code': 'server_error', 'message': 'upstream failed'}},
     'Model error: {"code": "server_error", "message": "upstream failed"}'),
])
def test_kill_timeout_and_model_error_record_end_reason_and_last_tool(api, cause, expected):
    machine, _, a = start(api)
    events = [{'seq': 1, 'kind': 'tool', 'payload': {'tool': 'exec_command', 'status': 'started'}},
              {'seq': 2, 'kind': 'diagnostic' if 'end_reason' in cause else 'error', 'payload': cause}]
    post(api, f"attempts/{a['id']}/events", {'events': events}, machine['token'])
    post(api, f"attempts/{a['id']}/complete", {'outcome': 'failed', 'last_seq': 2}, machine['token'])
    row = listed(api, a['id'])
    assert row['end_reason'] == expected
    assert row['failure_reason'] == expected
    assert row['ended_at'] and row['last_event_at'] and row['last_event'] in ('error', 'diagnostic')
    # Metadata uses the existing run gate, including a bot reading its own run.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE run_backoff SET wait_until=NULL")  # the failure's wait is over
    post(api, 'tasks', {'title': 'Inspect previous run', 'body': 'Read the previous run reason.', 'owner': 'ops'})
    next_run = claim(api, machine, 'ops')
    assert next_run
    assert next(r for r in get(api, 'bots/ops/turns', next_run['token']) if r['id'] == a['id'])['end_reason'] == expected


def test_lost_heartbeat_records_end_reason_even_before_start(api):
    machine, _, a = setup_attempt(api)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE attempts SET lease_until=? WHERE id=?', (H.shift(H.now(), seconds=-1), a['id']))
        api.app.state.execution.expire(c)
    row = listed(api, a['id'])
    assert row['end_reason'] == 'Computer lost or heartbeat stopped (attempt lease expired)'
    assert row['ended_at'] and row['exit'] == 'expired'


def test_stall_sweep_warns_without_stopping_and_renewal_clears_it(api):
    machine, _, a = start(api)
    post(api, f"attempts/{a['id']}/events", {'events': [
        {'seq': 1, 'kind': 'tool', 'payload': {'tool': 'exec_command', 'status': 'started'}}]}, machine['token'])
    base = H.now()
    # A long lease isolates the diagnostic sweeper from the existing expiry/recovery policy.
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE attempts SET lease_until=? WHERE id=?', (H.shift(base, hours=1), a['id']))
    from backend.scheduler import Scheduler
    Scheduler(api.app.state.store, api.app.state.execution).tick(H.parse_ts(H.shift(base, seconds=299)))
    assert not listed(api, a['id'])['stalled']
    at = H.shift(base, seconds=301)
    Scheduler(api.app.state.store, api.app.state.execution).tick(H.parse_ts(at))
    row = listed(api, a['id'])
    assert row['stalled'] and row['stall_minutes'] == 5 and row['last_event'] == 'exec_command'
    health = get(api, 'health')
    assert any(x['id'] == 'stalled_runs' and x['status'] == 'warn' for x in health['checks'])
    assert health['stalled_runs'][0]['run'] == a['id']
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM attempts WHERE id=?', (a['id'],)).fetchone()[0] == 'running'
    post(api, f"attempts/{a['id']}/renew", {}, machine['token'])
    assert not listed(api, a['id'])['stalled']
    assert not get(api, 'health')['stalled_runs']


@pytest.mark.parametrize('finished', [False, True])
def test_stderr_export_is_capped_redacted_private_and_survives_missing_stream(api, tmp_path, finished):
    from clients.hubtools import write_fixture
    machine, msg, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {'thread_id': 'stderr'}, machine['token'])
    raw = '\n'.join(f'line {n}' for n in range(300)) + '\nAPI key: sk-test_abcdefghijklmnopqrstuvwxyz0123456789\nfinal failure'
    post(api, f"attempts/{a['id']}/diagnostics", {'tail': raw}, machine['token'])
    found = get(api, f"attempts/{a['id']}/capture")
    assert not found['stream_captured'] and found['transcript'] == ''
    assert len(found['stderr_tail'].splitlines()) == 200 and len(found['stderr_tail'].encode()) <= 65536
    assert 'line 0\n' not in found['stderr_tail'] and found['stderr_tail'].endswith('final failure')
    assert 'abcdefghijklmnopqrstuvwxyz' not in json.dumps(found)
    summary = get(api, f"attempts/{a['id']}/capture?full=false")
    assert summary['stderr_bytes'] > 0 and 'stderr_tail' not in summary
    write_fixture(found, tmp_path)
    assert (tmp_path / ('replay-' + a['id']) / 'stderr.log').read_text().endswith('final failure\n')
    # Raw stderr retains the capture endpoint's stricter access; run metadata never carries it.
    get(api, f"attempts/{a['id']}/capture", 'ben-test', expected=403)
    assert 'final failure' not in json.dumps(listed(api, a['id']))
    assert 'final failure' not in json.dumps(get(api, f"turns/{a['id']}/steps"))
    assign(api, machine, 'finance')
    ready(api, machine, ['ops', 'finance'])
    post(api, 'chat/finance', {'text': 'Check the numbers'})
    other = claim(api, machine, 'finance')
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET team='engineering' WHERE bot='finance'")
    assert get(api, 'bots/ops/turns', other['token']) == []
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE attempt_diagnostics SET stalled_at=? WHERE attempt_id=?', (H.now(), a['id']))
    assert not get(api, 'health', other['token'])['stalled_runs']
    response = api.get(f"/api/v2/attempts/{a['id']}/capture", headers=headers(other['token']))
    assert response.status_code in (403, 404)
    unicode_tail = ('🚀' * 9000 + '\n') * 2 + 'last failure'
    post(api, f"attempts/{a['id']}/diagnostics", {'tail': unicode_tail}, machine['token'])
    assert len(get(api, f"attempts/{a['id']}/capture")['stderr_tail'].encode()) <= 65536
    alien = runner(api)
    post(api, f"attempts/{a['id']}/diagnostics", {'tail': 'wrong computer'}, alien['token'], expected=403)
    post(api, f"attempts/{a['id']}/events", {'events': [
        {'seq': 1, 'kind': 'error', 'payload': {'error': 'Process exited (signal 15)',
                                             'exit_code': -15, 'process_exit': True}}]}, machine['token'])
    if finished:
        post(api, f"attempts/{a['id']}/complete", {'outcome': 'failed', 'last_seq': 1}, machine['token'])
        assert 'final failure' not in api.get(f"/api/runs/{a['id']}/log", headers=headers()).text
        assert 'final failure' not in json.dumps(get(api, f"turns/{a['id']}/steps"))
    # Removing a secret removes the tail and prevents a later runner retry from putting it back.
    post(api, f"messages/{msg['id']}/redact", {'span': 'summarize the current', 'label': 'removed'})
    assert not post(api, f"attempts/{a['id']}/diagnostics", {'tail': raw}, machine['token'])['kept']
    get(api, f"attempts/{a['id']}/capture", expected=404)


@pytest.mark.parametrize(('outcome', 'text', 'stopped', 'expected'), [
    ('completed', 'done', False, 'Finished'),
    ('interrupted', 'Runner restarted during execution', False, 'Runner restarted during execution'),
    ('interrupted', '', True, 'Cancelled by human:ana'),
])
def test_finished_restart_and_cancelled_reasons(api, outcome, text, stopped, expected):
    machine, _, a = start(api)
    if stopped:
        post(api, 'bots/ops/stop', {'attempt_id': a['id']})
    post(api, f"attempts/{a['id']}/complete", {'outcome': outcome, 'text': text, 'last_seq': 0}, machine['token'])
    assert listed(api, a['id'])['end_reason'] == expected


def test_failed_reply_text_never_becomes_list_reason(api):
    machine, _, a = start(api)
    post(api, f"attempts/{a['id']}/complete",
         {'outcome': 'failed', 'last_seq': 0, 'text': 'private reply text'}, machine['token'])
    assert listed(api, a['id'])['end_reason'] == 'Process failed (no exit status reported)'


def silent_since(api, aid, started):
    """A run started at `started`, with no heartbeat and no event since: its computer went quiet at that moment."""
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE attempts SET started=?,created=?,lease_until=? WHERE id=?',
                  (started, started, H.shift(H.now(), hours=2), aid))
        c.execute('UPDATE attempt_diagnostics SET heartbeat_at=NULL,last_event_at=? WHERE attempt_id=?', (started, aid))


def tick(api, at):
    from backend.scheduler import Scheduler
    Scheduler(api.app.state.store, api.app.state.execution).tick(H.parse_ts(at))


def test_silent_run_is_marked_stalled_then_ended_by_the_server_at_its_limit_plus_grace(api):
    """No heartbeat, no events, no computer answer: the server marks it stalled, and ends it at max_run_minutes (60)
    plus the five-minute grace, on its own clock. The computer never reports."""
    machine, _, a = start(api)
    t0 = H.now()
    silent_since(api, a['id'], t0)
    tick(api, H.shift(t0, minutes=6))
    row = listed(api, a['id'])
    assert row['stalled'] and row['stall_minutes'] == 5
    tick(api, H.shift(t0, minutes=64))
    assert listed(api, a['id'])['stalled']
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM attempts WHERE id=?', (a['id'],)).fetchone()[0] == 'running'
    tick(api, H.shift(t0, minutes=66))
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM attempts WHERE id=?', (a['id'],)).fetchone()[0] == 'expired'
    row = listed(api, a['id'])
    assert row['end_reason'].startswith('Ended by the server: no heartbeat for 5 minutes')
    assert 'max_run_minutes (60 minutes)' in row['end_reason']


def test_capped_run_is_not_extended_by_stall_grace_or_a_call_in_flight(api, monkeypatch):
    """The server's cap ends the run through expiry: neither the lease grace from a server stall nor a call from the
    computer still in flight extends it."""
    machine, _, a = start(api)
    t0 = H.now()
    silent_since(api, a['id'], H.shift(t0, minutes=-70))
    far = H.shift(t0, hours=3)
    monkeypatch.setattr(api.app.state.execution, 'stall_recovery', lambda now: ((now, now, far),))
    api.app.state.execution.calls[a['id']] = {object(): (machine['runner_id'], H.now())}
    tick(api, t0)
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM attempts WHERE id=?', (a['id'],)).fetchone()[0] == 'expired'
    assert listed(api, a['id'])['end_reason'].startswith('Ended by the server')


def test_a_run_still_heartbeating_past_its_limit_is_left_to_the_runner(api):
    machine, _, a = start(api)
    t0 = H.now()
    silent_since(api, a['id'], H.shift(t0, minutes=-70))
    post(api, f"attempts/{a['id']}/renew", {}, machine['token'])
    tick(api, H.now())
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM attempts WHERE id=?', (a['id'],)).fetchone()[0] == 'running'
    assert not listed(api, a['id'])['stalled']


def test_a_server_capped_run_does_not_come_back_on_renewal_and_its_late_result_is_still_filed(api):
    """The computer comes back after the cap with the same assignment: its renewal is refused, the run stays ended,
    and a late result from the turn is still filed as the turn's outcome."""
    machine, _, a = start(api)
    post(api, f"attempts/{a['id']}/events", {'events': [
        {'seq': 1, 'kind': 'tool', 'payload': {'tool': 'exec_command', 'status': 'started'}}]}, machine['token'])
    t0 = H.now()
    silent_since(api, a['id'], H.shift(t0, minutes=-70))
    tick(api, t0)
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM attempts WHERE id=?', (a['id'],)).fetchone()[0] == 'expired'
    renewal = api.post(f"/api/v2/attempts/{a['id']}/renew", json={}, headers=headers(machine['token']))
    assert renewal.status_code == 409
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM attempts WHERE id=?', (a['id'],)).fetchone()[0] == 'expired'
    late = api.post(f"/api/v2/attempts/{a['id']}/complete", json={'outcome': 'completed', 'last_seq': 1},
                    headers=headers(machine['token']))
    assert late.status_code == 200
    assert listed(api, a['id'])['end_reason'] == 'Finished'
