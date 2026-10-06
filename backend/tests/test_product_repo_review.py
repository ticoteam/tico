"""Preserve a repository-create operation's target after an ambiguous external result."""
import httpx
import pytest
from fastapi.testclient import TestClient

from backend import github_app as G
from backend.app import create_app
from backend.tests.test_github_app import api, gh, connect, product_repo_request  # noqa: F401


def test_product_create_lost_response_keeps_key_bound_to_original_name(api, gh, monkeypatch):
    connect(api, administration='true')
    gh.permissions = {'administration': 'write', 'metadata': 'read'}
    dropped = [False]

    def lose_first_creation_response(request):
        response = gh(request)
        if request.method == 'POST' and request.url.path == '/orgs/Acme/repos' and not dropped[0]:
            dropped[0] = True
            raise httpx.ReadTimeout('synthetic response loss after GitHub created repository', request=request)
        return response

    monkeypatch.setattr(G, 'TRANSPORT', httpx.MockTransport(lose_first_creation_response))
    body = {'org': 'Acme', 'name': 'tico-recorder', 'visibility': 'private',
            'auto_init': False, 'confirmed': True}
    first = product_repo_request(api, body, key='lost-response-key')
    assert first.status_code == 409
    assert first.json()['error']['code'] == 'github_create_outcome_unknown'
    assert gh.created_repositories == {'Acme/tico-recorder'}

    changed = product_repo_request(api, {**body, 'name': 'another-product'}, key='lost-response-key')
    assert changed.status_code == 409, {'status': changed.status_code, 'body': changed.json(),
                                       'created': sorted(gh.created_repositories)}
    assert changed.json()['error']['code'] == 'idempotency_conflict'
    assert gh.created_repositories == {'Acme/tico-recorder'}

    same = product_repo_request(api, body, key='lost-response-key')
    assert same.status_code == 409
    assert same.json()['error']['code'] == 'github_create_outcome_unknown'
    assert len([call for call in gh.calls if call[:2] == ('POST', '/orgs/Acme/repos')]) == 1


def test_product_operation_survives_app_restart_cache_cleanup_and_disconnect(api, gh, monkeypatch):
    lost_response = True
    connect(api, administration='true')
    gh.permissions = {'administration': 'write', 'metadata': 'read'}
    body = {'org': 'Acme', 'name': 'tico-recorder', 'visibility': 'private',
            'auto_init': False, 'confirmed': True}
    gh.lose_next_product_create_response = lost_response
    first = product_repo_request(api, body, key='durable-restart')
    assert first.status_code == (409 if lost_response else 200)
    with api.app_state.store.transaction() as c:
        c.execute('DELETE FROM idempotency')
        c.execute('DELETE FROM github_app')

    # A new application and service lock reopen only the disposable fixture database.
    restarted = create_app(api.app_state.store.settings)
    with TestClient(restarted, follow_redirects=False) as retry:
        def forbid_github(request):
            raise AssertionError('Durable replay must not contact GitHub')

        monkeypatch.setattr(G, 'TRANSPORT', httpx.MockTransport(forbid_github))
        same = product_repo_request(retry, body, key='durable-restart')
        assert same.status_code == first.status_code
        if lost_response:
            assert same.json()['error']['code'] == 'github_create_outcome_unknown'
        else:
            assert same.json() == first.json()
        changed = product_repo_request(retry, {**body, 'name': 'another-product'}, key='durable-restart')
        assert changed.status_code == 409
        assert changed.json()['error']['code'] == 'idempotency_conflict'
    assert gh.created_repositories == {'Acme/tico-recorder'}


@pytest.mark.slow
def test_separate_api_service_cannot_create_while_first_operation_is_in_flight(api, gh, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    connect(api, administration='true')
    gh.permissions = {'administration': 'write', 'metadata': 'read'}
    body = {'org': 'Acme', 'name': 'tico-recorder', 'visibility': 'private',
            'auto_init': False, 'confirmed': True}
    started, release = Event(), Event()

    def hold_external_create(request):
        response = gh(request)
        if request.method == 'POST' and request.url.path == '/orgs/Acme/repos':
            started.set()
            assert release.wait(10), 'Synthetic create was not released'
        return response

    monkeypatch.setattr(G, 'TRANSPORT', httpx.MockTransport(hold_external_create))
    separate = create_app(api.app_state.store.settings)
    assert separate.state.github_app.product_repo_create_lock is not api.app_state.github_app.product_repo_create_lock
    with TestClient(separate, follow_redirects=False) as other, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(product_repo_request, api, body, key='separate-service')
        try:
            assert started.wait(10), 'Synthetic create did not start'
            same = product_repo_request(other, body, key='separate-service')
            assert same.status_code == 409
            assert same.json()['error']['code'] == 'github_create_outcome_unknown'
            changed = product_repo_request(other, {**body, 'name': 'another-product'}, key='separate-service')
            assert changed.status_code == 409
            assert changed.json()['error']['code'] == 'idempotency_conflict'
        finally:
            release.set()
        assert pending.result(timeout=10).status_code == 200
    assert len([call for call in gh.calls if call[:2] == ('POST', '/orgs/Acme/repos')]) == 1
    assert gh.created_repositories == {'Acme/tico-recorder'}
