"""Preserve a repository-create operation's target after an ambiguous external result."""
import httpx

from backend import github_app as G
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
    assert first.status_code == 502
    assert gh.created_repositories == {'Acme/tico-recorder'}

    changed = product_repo_request(api, {**body, 'name': 'another-product'}, key='lost-response-key')
    assert changed.status_code == 409, {'status': changed.status_code, 'body': changed.json(),
                                       'created': sorted(gh.created_repositories)}
    assert changed.json()['error']['code'] == 'idempotency_conflict'
    assert gh.created_repositories == {'Acme/tico-recorder'}
