"""Token failures retain safe diagnostic causes and retryability through a refused review move."""
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest
from jwt.exceptions import InvalidKeyError

from backend import github_app, review_gate
from backend.store import Problem
from backend.tests.test_review_gate import api, get, HEAD, move, ticket


@pytest.mark.parametrize('exc,cause,retryable,auth', [
    (httpx.ReadTimeout('synthetic-secret'), 'timed out', True, False),
    (TimeoutError('synthetic-secret'), 'timed out', True, False),
    (Problem('github_unreachable', 'synthetic-secret', 502, extra={'timed_out': True}), 'github_unreachable; timed out', True, False),
    (Problem('github_unreachable', 'synthetic-secret', 502, retryable=True), 'github_unreachable', True, False),
    (Problem('github_token', 'synthetic-secret', 503), 'github_token; HTTP 503', True, False),
    (Problem('github_token', 'synthetic-secret', 429), 'github_token; HTTP 429', True, False),
    (Problem('github_token', 'synthetic-secret', 502, extra={'github_status': 429}), 'HTTP 429', True, False),
    (Problem('github_token', 'synthetic-secret', 502, extra={'github_status': 422}), 'HTTP 422', False, False),
    (Problem('github_auth', 'synthetic-secret', 401), 'github_auth; HTTP 401', False, True),
    (Problem('github_auth', 'synthetic-secret', 403, retryable=True), 'HTTP 403', False, True),
    (Problem('github_app_permissions', 'synthetic-secret', 409), 'github_app_permissions', False, True),
    (Problem('github_app_key', 'synthetic-secret', 502), 'github_app_key; HTTP 502', False, True),
    (Problem('github_key_missing', 'synthetic-secret', 409), 'github_key_missing', False, True),
    (Problem('github_not_installed', 'synthetic-secret', 409), 'github_not_installed', False, True),
    (Problem('github_not_connected', 'synthetic-secret', 409), 'github_not_connected', False, True),
    (Problem('github_unavailable', github_app.MISSING_KEY, 503), 'github_unavailable', False, True),
    (Problem('github_unavailable', 'The stored GitHub App key could not be decrypted', 503), 'HTTP 503', False, True),
    (Problem('github_repo_not_accessible', 'synthetic-secret', 409), 'github_repo_not_accessible', False, False),
    (Problem('github_busy', 'synthetic-secret', 409, retryable=True), 'github_busy', True, False),
    (InvalidKeyError('synthetic-secret'), 'InvalidKeyError', False, True),
    (RuntimeError('synthetic-secret'), 'RuntimeError', False, False),
    (ValueError('synthetic-secret'), 'ValueError', False, False),
])
def test_snapshot_mint_failure_keeps_safe_cause(exc, cause, retryable, auth):
    def mint(*args):
        raise exc
    service = SimpleNamespace(store=SimpleNamespace(read=lambda: nullcontext(None)),
                              row=lambda c: True, mint=mint)
    result = review_gate.snapshot(service, [{'id': 'pr', 'repo': 'acme/widget', 'number': 1}])['pr']
    assert result['error'] is True
    assert cause in result['why']
    assert ('could not sign in' in result['why']) is auth
    assert result['retryable'] is retryable
    assert result['auth'] is auth
    assert 'synthetic-secret' not in str(result)
    assert 'head_sha' not in result
    if isinstance(exc, Problem) and exc.code == 'github_unreachable':
        assert 'HTTP' not in result['why'], 'a transport failure has no GitHub response status'


@pytest.mark.parametrize('status,retryable,auth', [(503, True, False), (429, True, False), (401, False, True), (403, False, True)])
def test_snapshot_http_status_exception(status, retryable, auth):
    request = httpx.Request('POST', 'https://example.test/synthetic-secret')
    exc = httpx.HTTPStatusError('synthetic-secret', request=request, response=httpx.Response(status, request=request))
    result = review_gate.mint_failure(exc, 'acme/widget')
    assert f'HTTP {status}' in result['why']
    assert result['retryable'] is retryable
    assert result['auth'] is auth
    assert ('could not sign in' in result['why']) is auth
    assert 'synthetic-secret' not in str(result)


@pytest.mark.parametrize('failure,retryable,auth', [
    (503, True, False),
    (429, True, False),
    (401, False, True),
    (403, False, True),
    (422, False, False),
    ('timeout', True, False),
    ('boom', False, False),
])
def test_snapshot_read_failure_keeps_cause(failure, retryable, auth):
    def mint(*args):
        return ('synthetic-token', '2030-01-01T00:00:00Z')
    def call(method, path, **kw):
        if failure == 'timeout':
            raise TimeoutError('synthetic-secret')
        if failure == 'boom':
            raise RuntimeError('synthetic-secret')
        return SimpleNamespace(status_code=failure, json=lambda: {})
    service = SimpleNamespace(store=SimpleNamespace(read=lambda: nullcontext(None)),
                              row=lambda c: True, mint=mint, _call=call)
    result = review_gate.snapshot(service, [{'id': 'pr', 'repo': 'acme/widget', 'number': 1}])['pr']
    assert result['error'] is True
    assert result['retryable'] is retryable
    assert result['auth'] is auth
    assert 'synthetic-secret' not in str(result)
    assert 'head_sha' not in result


@pytest.mark.parametrize('failure,cause,retryable,auth', [
    ('timeout', 'timed out', True, False),
    (503, 'github_token; HTTP 503', True, False),
    (429, 'github_token; HTTP 429', True, False),
    (401, 'github_app_key; HTTP 401', False, True),
    (403, 'github_app_permissions; HTTP 403', False, True),
    ('repo-403', 'github_repo_not_accessible; HTTP 403', False, True),
    ('repo-404', 'github_repo_not_accessible; HTTP 404', False, False),
    ('installation-401', 'github_installations; HTTP 401', False, True),
    ('installation-403', 'github_installations; HTTP 403', False, True),
    ('installation-503', 'github_installations; HTTP 503', True, False),
    ('installation-429', 'github_installations; HTTP 429', True, False),
    ('transport', 'github_unreachable', True, False),
    ('installation', 'github_not_installed', False, True),
])
def test_real_mint_failure_reaches_review_refusal(api, monkeypatch, failure, cause, retryable, auth):
    task = ticket(api, f'Tests: head {HEAD}: 9 passed')
    service = api.app.state.github_app
    monkeypatch.setattr(service, 'row', lambda c=None: {'org': 'ticoteam', 'installation_id': None})
    discover = isinstance(failure, str) and failure.startswith('installation-')
    if not discover:
        monkeypatch.setattr(service, 'installation', lambda: None if failure == 'installation' else 123)
    monkeypatch.setattr(service, '_jwt', lambda: 'synthetic-jwt')
    requests = []
    def respond(request):
        requests.append(request)
        assert request.url.path in {'/app/installations/123/access_tokens', '/app/installations', '/app/installations/123'}
        if failure == 'timeout':
            raise httpx.ReadTimeout('synthetic-secret', request=request)
        if failure == 'transport':
            raise httpx.ConnectError('synthetic-secret', request=request)
        status = int(failure.rsplit('-', 1)[1]) if isinstance(failure, str) else failure
        return httpx.Response(status, json={'message': 'permission synthetic-secret' if failure == 403 else 'synthetic-secret'})
    monkeypatch.setattr(github_app, '_client', lambda: httpx.Client(
        base_url='https://example.test', transport=httpx.MockTransport(respond)))

    response = move(api, task)
    assert response.status_code == 422, response.text
    error = response.json()['error']
    assert error['code'] == 'review_gate'
    assert error['retryable'] is retryable
    assert cause in error['detail']
    assert ('could not sign in' in error['detail']) is auth
    assert 'synthetic-secret' not in response.text and 'synthetic-jwt' not in response.text
    assert len(requests) == (0 if failure == 'installation' else 3 if failure == 'repo-404' else 1)
    if discover:
        assert requests[0].url.path == '/app/installations'
    if failure in ('timeout', 'transport'):
        assert 'HTTP' not in error['detail']
    current = get(api, 'tasks/' + task['id'])['task']
    assert current['step_id'] == task['step_id'] and current['version'] == task['version']
    assert not current.get('blocked_since') and not current.get('blocked_gate')
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == 1
        assert not c.execute("SELECT 1 FROM jobs WHERE state='uncertain'").fetchone()
