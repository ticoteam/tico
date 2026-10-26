"""Temporary upstream failures retry briefly and remain distinct from actionable App problems."""
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest

from backend import review_gate as G
from backend.store import Problem
from backend.tests.test_review_gate import api, ticket, move, HEAD

PR = {'id': 'pr', 'repo': 'acme/product', 'number': 1}


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(G, 'RETRY_BACKOFF', 0)
    def read(method, path, **kwargs):
        data = {'head': {'sha': HEAD}} if '/pulls/' in path else {'check_runs': []} if path.endswith('check-runs') else []
        return SimpleNamespace(status_code=200, json=lambda: data)
    return SimpleNamespace(store=SimpleNamespace(read=lambda: nullcontext(None)), row=lambda c: {'org': 'acme'},
                           mint=lambda *args: ('synthetic-token', 'later'), _call=read)


@pytest.mark.parametrize('failure', [httpx.ReadTimeout('untrusted transport details'), httpx.ConnectError('untrusted URL'),
                                     Problem('github_unreachable', 'GitHub could not be reached', 502)])
def test_transport_failure_is_temporary_and_mint_tries_twice(service, failure):
    calls = []
    def mint(*args):
        calls.append(args)
        raise failure
    service.mint = mint
    result = G.snapshot(service, [PR])['pr']
    assert len(calls) == 2
    assert result['why'] == 'GitHub did not answer in time'
    assert result['temporary'] and not result['devops']
    assert 'sign in' not in str(result) and 'permission' not in str(result)
    assert 'untrusted' not in str(result)


@pytest.mark.parametrize('code,detail,status', [
    ('github_app_permissions', 'Checks: read was not granted', 409),
    ('github_repo_not_accessible', 'Repository is outside this installation', 409),
    ('github_app_key', 'GitHub rejected the App key (HTTP 401)', 502),
    ('github_not_installed', 'Install the App on the organization', 409),
    ('github_unavailable', 'The stored GitHub App key could not be decrypted', 503)])
def test_permanent_problem_keeps_code_and_detail_and_never_retries(service, code, detail, status):
    calls = []
    def mint(*args):
        calls.append(args)
        raise Problem(code, detail, status)
    service.mint = mint
    result = G.snapshot(service, [PR])['pr']
    assert len(calls) == 1
    assert result['problem_code'] == code and result['why'] == detail
    assert result['temporary'] is False and result['devops'] is True


@pytest.mark.parametrize('status', [429, 502])
def test_mint_http_outage_then_success_does_not_refuse(service, status):
    calls = []
    def mint(*args):
        calls.append(args)
        if len(calls) == 1:
            raise Problem('github_token', f'GitHub would not issue an installation token (HTTP {status})', 502,
                          extra={'github_status': status})
        return 'synthetic-token', 'later'
    service.mint = mint
    assert G.snapshot(service, [PR])['pr']['head_sha'] == HEAD
    assert len(calls) == 2


def test_read_503_exhaustion_names_status_and_is_temporary(service):
    calls = []
    def read(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(status_code=503)
    service._call = read
    result = G.snapshot(service, [PR])['pr']
    assert len(calls) == 2
    assert result['why'] == 'GitHub answered 503' and result['upstream_status'] == 503
    assert result['temporary'] and not result['devops']


def test_read_429_then_success_and_transport_retry_preserve_full_snapshot(service):
    original, calls = service._call, {}
    def read(method, path, **kwargs):
        calls[path] = calls.get(path, 0) + 1
        if calls[path] == 1:
            if path.endswith('/statuses'):
                raise httpx.ConnectError('untrusted request URL')
            return SimpleNamespace(status_code=429)
        return original(method, path, **kwargs)
    service._call = read
    result = G.snapshot(service, [PR])['pr']
    assert result['head_sha'] == HEAD and result['checks'] == {}
    assert all(count >= 2 for count in calls.values())


def test_unexpected_error_keeps_type_without_transport_details_or_signin_claim(service):
    def mint(*args):
        raise ValueError('untrusted secret URL')
    service.mint = mint
    result = G.snapshot(service, [PR])['pr']
    assert result['why'] == 'GitHub read failed: ValueError'
    assert not result['temporary'] and not result['devops']
    assert 'untrusted' not in str(result) and 'sign in' not in str(result)


@pytest.mark.parametrize("stage", ["mint", "read"])
def test_temporary_refusal_is_retryable_keeps_notice_and_creates_no_ask(api, monkeypatch, stage):
    task = ticket(api, f'Tests: head {HEAD}: 9 passed')
    service = api.app.state.github_app
    monkeypatch.setattr(G, 'RETRY_BACKOFF', 0)
    monkeypatch.setattr(service, 'row', lambda c: {'org': 'acme'})
    calls, notices = [], []
    def mint(*args):
        calls.append(args)
        raise httpx.ReadTimeout('untrusted transport URL')
    original_notify = G.notify
    def notify(c, actor, notice):
        notices.append(notice)
        return original_notify(c, actor, notice)
    if stage == 'mint':
        monkeypatch.setattr(service, 'mint', mint)
    else:
        monkeypatch.setattr(service, 'mint', lambda *args: ('synthetic-token', 'later'))
        def read(*args, **kwargs):
            calls.append(args)
            return SimpleNamespace(status_code=503)
        monkeypatch.setattr(service, '_call', read)
    monkeypatch.setattr(G, 'notify', notify)
    with service.store.read() as c:
        before = c.execute('SELECT count(*) FROM tasks').fetchone()[0]
    response = move(api, task)
    assert response.status_code == 422
    error = response.json()['error']
    assert len(calls) == 2 and error['retryable'] and error['temporary'] and not error['devops']
    assert ('GitHub did not answer in time' if stage == 'mint' else 'GitHub answered 503') in error['detail']
    assert 'will retry' in error['detail']
    assert 'sign in' not in error['detail'] and 'permission' not in error['detail']
    assert notices[0]['temporary'] and not notices[0]['devops']
    with service.store.read() as c:
        assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == before


def test_permission_code_and_detail_reach_the_refusal(api, monkeypatch):
    task = ticket(api, f'Tests: head {HEAD}: 9 passed')
    service = api.app.state.github_app
    monkeypatch.setattr(service, 'row', lambda c: {'org': 'acme'})
    def mint(*args):
        raise Problem('github_app_permissions', 'Checks: read is not accepted on this installation', 409)
    monkeypatch.setattr(service, 'mint', mint)
    response = move(api, task)
    error = response.json()['error']
    assert error['problem_code'] == 'github_app_permissions' and error['devops'] and not error['retryable']
    assert 'Checks: read is not accepted on this installation' in error['detail']


def test_success_after_transient_mint_allows_the_review_move(api, monkeypatch):
    task = ticket(api, f'Tests: head {HEAD}: 9 passed')
    service = api.app.state.github_app
    monkeypatch.setattr(service, 'row', lambda c: {'org': 'acme'})
    monkeypatch.setattr(G, 'RETRY_BACKOFF', 0)
    calls = []
    def mint(*args):
        calls.append(args)
        if len(calls) == 1:
            raise Problem('github_token', 'GitHub would not issue an installation token (HTTP 502)', 502)
        return 'synthetic-token', 'later'
    def read(method, path, **kwargs):
        data = {'head': {'sha': HEAD}} if '/pulls/' in path else {'check_runs': []} if path.endswith('check-runs') else []
        return SimpleNamespace(status_code=200, json=lambda: data)
    monkeypatch.setattr(service, 'mint', mint)
    monkeypatch.setattr(service, '_call', read)
    assert move(api, task).status_code == 200
    assert len(calls) == 2


@pytest.mark.parametrize('status', [429, 502])
def test_actual_mint_retains_upstream_status_for_the_gate(status):
    from backend.github_app import GitHubApp
    service = SimpleNamespace(installation=lambda: 77, _jwt=lambda: 'synthetic-jwt',
        _call=lambda *args, **kwargs: httpx.Response(status, json={'message': 'Synthetic upstream outage'}))
    with pytest.raises(Problem) as caught:
        GitHubApp._mint(service, (), ['acme/product'], {'checks': 'read'}, diagnose=False)
    assert caught.value.code == 'github_token'
    assert caught.value.extra['github_status'] == status
    assert G.github_failure(caught.value)['temporary']
