"""Only fixture computers and synthetic allowance reports; no provider requests."""
from backend import hubdb as H
from backend.tests.test_api import api, get, headers, post, runner  # noqa: F401


def setup(api):
    r = runner(api)
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'profiles': [{'name': 'sample', 'runtimes': {
        'codex': {'signed_in': True}, 'claude': {'signed_in': True}}}]}, token=r['token'])
    return r, {'runner_id': r['runner_id'], 'profile': 'sample', 'runtime': 'codex'}


def reading(percent=40):
    return {'used_percent': percent, 'reported_at': H.now(), 'resets_at': H.shift(H.now(), days=5)}


def state(api):
    return next(profile['runtimes']['codex'] for computer in get(api, 'subscriptions')['profiles_by_computer']
                for profile in computer['profiles'] if profile['name'] == 'sample')


def test_refresh_coalesces_and_keeps_auth_separate(api):
    r, body = setup(api)
    request = post(api, 'subscriptions/refresh', body)
    assert post(api, 'subscriptions/refresh', body)['id'] == request['id']
    assert get(api, 'runner-subscription-refreshes', r['token'])['refreshes'][0]['id'] == request['id']
    post(api, f'runner-subscription-refreshes/{request["id"]}/report', {
        'profile': 'sample', 'runtime': 'codex', 'state': 'succeeded', 'weekly': reading(100)}, token=r['token'])
    current = state(api)
    assert current['signed_in'] is True
    assert current['weekly']['used_percent'] == 100
    assert current['refresh']['state'] == 'succeeded'
    assert get(api, 'runner-subscription-refreshes', r['token'])['refreshes'] == []


def test_refresh_access_and_unsupported_without_runner_command(api):
    r, body = setup(api)
    response = api.post('/api/v2/subscriptions/refresh', json=body, headers=headers('cara-test'))
    assert response.status_code == 403
    response = api.post('/api/v2/subscriptions/refresh', json=body, headers=headers(r['token']))
    assert response.status_code == 403
    result = post(api, 'subscriptions/refresh', {**body, 'runtime': 'claude'})
    assert result['state'] == 'unavailable'
    assert get(api, 'runner-subscription-refreshes', r['token'])['refreshes'] == []


def test_signin_invalidates_report_and_rejects_older_inflight(api):
    r, body = setup(api)
    request = post(api, 'subscriptions/refresh', body)
    post(api, f'runners/{r["runner_id"]}/logins', {'runtime': 'codex', 'profile': 'sample'})
    response = api.post(f'/api/v2/runner-subscription-refreshes/{request["id"]}/report', json={
        'profile': 'sample', 'runtime': 'codex', 'state': 'succeeded', 'weekly': reading()}, headers=headers(r['token']))
    assert response.status_code == 409
    assert state(api)['weekly'] is None


def test_wrong_runner_cannot_report_and_unknown_never_success(api):
    r, body = setup(api)
    other = runner(api, label='Other')
    request = post(api, 'subscriptions/refresh', body)
    path = f'/api/v2/runner-subscription-refreshes/{request["id"]}/report'
    report = {'profile': 'sample', 'runtime': 'codex', 'state': 'succeeded', 'weekly': reading()}
    assert api.post(path, json=report, headers=headers(other['token'])).status_code == 404
    assert api.post(path, json={**report, 'weekly': None}, headers=headers(r['token'])).status_code == 422
    post(api, path.removeprefix('/api/v2/'), {**report, 'state': 'failed', 'weekly': None}, token=r['token'])
    assert state(api)['refresh']['state'] == 'failed'
    assert state(api)['weekly'] is None
