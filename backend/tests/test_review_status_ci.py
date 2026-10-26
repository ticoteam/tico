"""Tico's own `tico/reviews` commit status is a review result, never CI: a failure it posts for a missing review must
not refuse the review request that clears it, while a real failed CI status still does (Dev 567)."""
import json
from types import SimpleNamespace

import pytest

from backend import hubdb as H
from backend.tests.test_github import api, headers, hook  # noqa: F401
from backend.tests.test_review_gate import HEAD, BASE, ticket

RECEIPT = f'Tests: base {BASE} head {HEAD}: 9 passed, 0 failed'


def github(api, monkeypatch, statuses):
    """A configured GitHub App: PR #412 at HEAD with one passing check run and these commit statuses (newest first)."""
    service = api.app.state.github_app
    monkeypatch.setattr(service, 'row', lambda c: {'org': 'ticoteam'})
    monkeypatch.setattr(service, 'mint', lambda *a, **k: ('synthetic', 'later'))
    def fetch(method, path, **kw):
        if '/pulls/' in path:
            data = {'head': {'sha': HEAD}}
        elif path.endswith('check-runs'):
            data = {'check_runs': [{'id': 1, 'name': 'build', 'conclusion': 'success'}]}
        else:
            data = statuses
        return SimpleNamespace(status_code=200, json=lambda: data)
    monkeypatch.setattr(service, '_call', fetch)


def review_child(api, task):
    return api.post('/api/v2/tasks', headers=headers(), json={
        'owner': 'cmo', 'title': 'Review 1: PR #412', 'body': f'Check PR #412 base {BASE} head {HEAD}',
        'relations': [{'task': task['id'], 'kind': 'parent'}]})


@pytest.mark.parametrize('statuses,allowed', [
    ([{'context': 'tico/reviews', 'state': 'failure'}], True),
    # Failure, then success at the same head: GitHub lists the newest first and the gate keeps earlier failures.
    ([{'context': 'tico/reviews', 'state': 'success'}, {'context': 'tico/reviews', 'state': 'failure'}], True),
    ([{'context': 'tico/reviews', 'state': 'failure'}, {'context': 'ci/build', 'state': 'failure'}], False),
])
def test_the_review_status_never_refuses_a_review_request_but_real_ci_does(api, monkeypatch, statuses, allowed):
    task = ticket(api, RECEIPT)
    github(api, monkeypatch, statuses)
    r = review_child(api, task)
    assert (r.status_code == 200) is allowed, r.text
    if not allowed:
        assert 'ci/build' in r.text


def test_a_review_status_webhook_is_not_stored_as_a_check(api):
    task = ticket(api, RECEIPT)
    hook(api, 'status', {'repository': {'full_name': 'ticoteam/tico'}, 'sha': HEAD, 'context': 'tico/reviews',
                         'state': 'failure'})
    hook(api, 'status', {'repository': {'full_name': 'ticoteam/tico'}, 'sha': HEAD, 'context': 'ci/build',
                         'state': 'success'})  # control: a real status is stored
    with api.app.state.store.read() as c:
        link = H.task_links(c, task['id'])[0]
    checks = json.loads(link['detail_json']).get('checks') or {}
    assert 'ci/build' in checks and 'tico/reviews' not in checks and link['checks'] != 'failing'
