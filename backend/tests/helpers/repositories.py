"""Helpers that other tests share, moved out of test_repositories.py so they import no test file."""

import uuid
from backend.tests.helpers.github_app import auth, connect


def put(api, path, body, token='owner-test'):
    response = api.put('/api/v2/' + path, json=body, headers={**auth(token), 'Idempotency-Key': uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    return response.json()


def catalog(api, gh):
    connect(api)
    gh.repositories = [
        {'full_name': 'Acme/' + name, 'default_branch': 'main'} for name in ('product', 'docs', 'bot-sales', 'emp-cpo')
    ]
    response = api.post('/api/v2/repositories/refresh', headers=auth())
    assert response.status_code == 200, response.text
    return response.json()
