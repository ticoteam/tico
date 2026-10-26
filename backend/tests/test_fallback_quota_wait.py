"""Named fallback resets wait durably; recorded writes still require review."""
import json

import pytest

from backend import execution, login_holds
from backend.store import H
from backend.tests.test_api import api, claim, get, post  # noqa: F401
from backend.tests.test_execution_review import job_state, sweep
from backend.tests.test_login_holds import at, signed_in, two_bots

NOTICE = "Model error: You've hit your session limit · resets 11:50am (UTC)"
HIT = "2026-10-26T10:49:20.000000Z"
RESET = "2026-10-26T11:50:00.000000Z"


def setup(api, monkeypatch):
    machine = two_bots(api, monkeypatch, runtimes=('claude', 'musecode'))
    at(monkeypatch, HIT, api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.fallback',json(?))",
                  (json.dumps({'harness': 'musecode', 'model': 'muse-spark-1.3'}),))
        login_holds.record(c, machine['runner_id'], '', 'fake', HIT,
                           '2026-10-26T13:00:00.000000Z', error='Primary quota')
    post(api, 'tasks', {'owner': 'ops', 'title': 'Review the change', 'body': 'Please review it.'})
    attempt = claim(api, machine, bot='ops')
    assert attempt['config']['primary_held']
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'quota'}, machine['token'])
    return machine, attempt


def finish(api, machine, attempt, notice=NOTICE, *, writes=0, intermediate=False, outcome='interrupted', **extra):
    events = [('diagnostic', {'text': 'fake login held; running this turn on ' + ('claude' if intermediate else 'musecode')}),
              ('error', {'error': notice, 'limit': False})]
    if intermediate:
        events += [('diagnostic', {'text': 'claude hit its usage limit; running this turn on musecode'}),
                   ('diagnostic', {'text': 'APIError: fallback harness interrupted; inspect local runner'})]
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': n, 'kind': kind, 'payload': payload} for n, (kind, payload) in enumerate(events, 1)]}, machine['token'])
    if writes is not None:
        with api.app.state.store.transaction() as c:
            c.execute('UPDATE turns SET writes=? WHERE id=?', (writes, attempt['id']))
    post(api, f"attempts/{attempt['id']}/complete", {
        'outcome': outcome, 'last_seq': len(events), 'fallback': 'musecode', 'primary_held': True, **extra}, machine['token'])


@pytest.mark.parametrize('outcome', ['interrupted', 'failed'])
def test_named_fallback_limit_requeues_until_reset_and_shared_login_takes_one_probe(api, monkeypatch, outcome):
    machine, attempt = setup(api, monkeypatch)
    finish(api, machine, attempt, outcome=outcome)
    assert job_state(api, attempt['job_id']) == 'queued'
    assert get(api, 'bots/ops/execution-review')['jobs'] == []
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine['runner_id'], '', 'musecode')
        assert hold['until'] == RESET
        assert login_holds.get(c, machine['runner_id'], '', 'fake')['until'].startswith('2026-10-26T13:00')
        status = H.status(c, 'ops')
        assert status['state'] == 'limited' and 'resumes ~11:50 UTC' in status['focus']
        result = json.loads(c.execute('SELECT result_json FROM attempts WHERE id=?', (attempt['id'],)).fetchone()[0])
        assert result['not_before'] == RESET
        assert result['limit_source']['runtime'] == 'musecode'
    post(api, 'tasks', {'owner': 'finance', 'title': 'Another review', 'body': 'Please review it.'})
    at(monkeypatch, '2026-10-26T11:49:59.000000Z', api)
    signed_in(api, machine, ['ops', 'finance'], ['claude', 'musecode'])
    assert claim(api, machine, bot='finance') is None
    assert claim(api, machine, bot='ops') is None
    health = get(api, 'health')
    row = next(r for r in health['stuck'] if r['bot'] == 'ops')
    assert 'quota reset ~11:50 UTC' in row['why']
    at(monkeypatch, RESET, api)
    signed_in(api, machine, ['ops', 'finance'], ['claude', 'musecode'])
    probe = claim(api, machine, bot='ops')
    assert probe and probe['job_id'] == attempt['job_id']
    assert claim(api, machine, bot='finance') is None


def test_quota_wait_is_a_job_deadline_even_if_another_lane_clears_status_and_primary_hold(api, monkeypatch):
    machine, attempt = setup(api, monkeypatch)
    finish(api, machine, attempt)
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.KEEPER, 'ops', state='idle', focus='')
        login_holds.clear(c, machine['runner_id'], '', 'fake')
    assert claim(api, machine, bot='ops') is None
    at(monkeypatch, RESET, api)
    signed_in(api, machine, ['ops', 'finance'], ['claude', 'musecode'])
    assert claim(api, machine, bot='ops')['job_id'] == attempt['job_id']


@pytest.mark.parametrize('extra', [{}, {'limited': True}, {'outcome': 'failed'}])
def test_fallback_limit_with_writes_keeps_review_and_names_error_and_reset(api, monkeypatch, extra):
    machine, attempt = setup(api, monkeypatch)
    finish(api, machine, attempt, writes=5, **extra)
    assert job_state(api, attempt['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert NOTICE in review['reason'] and 'quota resets ~11:50 UTC' in review['reason']
    assert review['stopped'] == 'changed'
    with api.app.state.store.read() as c:
        from backend import run_backoff
        assert not run_backoff.get(c, 'ops').get('fails')
        assert H.status(c, 'ops')['state'] != 'crashed'
        assert login_holds.get(c, machine['runner_id'], '', 'musecode')['until'] == RESET
    at(monkeypatch, '2026-10-26T14:00:00.000000Z', api)
    assert sweep(api) == []
    assert job_state(api, attempt['job_id']) == 'uncertain'


def test_intermediate_fallback_limit_keeps_its_runtime_and_never_holds_later_fallback(api, monkeypatch):
    machine, attempt = setup(api, monkeypatch)
    finish(api, machine, attempt, intermediate=True)
    assert job_state(api, attempt['job_id']) == 'queued'
    with api.app.state.store.read() as c:
        assert login_holds.get(c, machine['runner_id'], '', 'claude')['until'] == RESET
        assert not login_holds.get(c, machine['runner_id'], '', 'musecode')
        result = json.loads(c.execute('SELECT result_json FROM attempts WHERE id=?', (attempt['id'],)).fetchone()[0])
        assert result['limit_source']['runtime'] == 'claude'


@pytest.mark.parametrize('effect', ['hub_write', 'push', 'approval'])
def test_real_write_receipts_pushes_and_consumed_approvals_are_never_requeued(api, monkeypatch, effect):
    machine, attempt = setup(api, monkeypatch)
    extra = {}
    if effect == 'hub_write':
        with api.app.state.store.read() as c:
            task_id = c.execute('SELECT task_id FROM turns WHERE id=?', (attempt['id'],)).fetchone()[0]
        post(api, f'tasks/{task_id}/comments', {'text': 'I changed the report.'}, attempt['token'])
        with api.app.state.store.read() as c:
            assert c.execute('SELECT writes FROM turns WHERE id=?', (attempt['id'],)).fetchone()[0] > 0
    elif effect == 'push':
        extra = {'pushes': [{'path': 'tasks/example/repository', 'count': 1}]}
    else:
        with api.app.state.store.transaction() as c:
            message_id = c.execute('SELECT message_id FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0]
            c.execute('INSERT INTO approvals(id,kind,message_id,requested_by,created,consumed_at) VALUES(?,?,?,?,?,?)',
                      (H.new_id(), 'send', message_id, 'bot:ops', HIT, HIT))
    finish(api, machine, attempt, writes=None, **extra)
    assert job_state(api, attempt['job_id']) == 'uncertain'


def test_a_later_fallback_completing_does_not_wait_on_an_earlier_named_limit(api, monkeypatch):
    machine, attempt = setup(api, monkeypatch)
    finish(api, machine, attempt, intermediate=True, outcome='completed')
    assert job_state(api, attempt['job_id']) == 'completed'


@pytest.mark.parametrize('notice', ["Model error: session unavailable", "You've hit your session limit · resets tomorrow",
                                  "You've hit your session limit · resets 11:50am (Europe/Paris)"])
def test_nonmatching_fallback_error_keeps_existing_interruption_behaviour(api, monkeypatch, notice):
    machine, attempt = setup(api, monkeypatch)
    finish(api, machine, attempt, notice)
    assert job_state(api, attempt['job_id']) == 'uncertain'
    with api.app.state.store.read() as c:
        assert not login_holds.get(c, machine['runner_id'], '', 'musecode')
        assert H.status(c, 'ops')['state'] == 'crashed'
