"""A task does not stick on PR bookkeeping: a finished workflow run wakes the builder, and a conflict nobody was told
about is found by the maintenance pass."""

from types import SimpleNamespace

from backend import github as G
from backend import hubdb as H
from backend.tests.helpers.github import PR, _app, _burst, _prs, api, get, hook, pr_event, post  # noqa: F401

HEAD = 'a' * 40


# C: a finished workflow run counts like a check.
def workflow(api, conclusion, sha=HEAD, name='CI', action='completed'):
    return hook(
        api,
        'workflow_run',
        {
            'action': action,
            'repository': {'full_name': 'ticoteam/tico'},
            'workflow_run': {
                'id': 5,
                'name': name,
                'status': 'completed',
                'conclusion': conclusion,
                'head_sha': sha,
                'pull_requests': [],
            },
        },
    )


def test_a_failed_workflow_run_wakes_the_builder_once_per_head(api):
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on CI', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': HEAD}))
    workflow(api, None, action='requested')
    assert _burst(api, task['id']) == []
    workflow(api, 'failure')
    workflow(api, 'failure')  # a redelivery
    assert _burst(api, task['id']) == [f"Checks finished on tico#412: failed (workflow:CI) {PR}/checks at {HEAD[:12]}"]
    link = get(api, 'tasks/' + task['id'] + '/links')['links'][0]
    assert link['checks'] == 'failing'
    workflow(api, 'failure', sha='b' * 40)  # another head than the PR's: ignored
    assert len(_burst(api, task['id'])) == 1


def test_a_workflow_run_on_main_does_not_wake_a_merged_prs_owner(api):
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Shipped work', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': HEAD}))
    hook(api, 'pull_request', pr_event('closed', state='closed', merged=True, head={'sha': HEAD}))
    merged = _burst(api, task['id'])  # the merge's own news
    assert len(merged) == 1 and 'merged' in merged[0]
    workflow(api, 'failure')  # main's run at the same commit: no PR list, and the PR is merged
    assert _burst(api, task['id']) == merged


# D: the maintenance pass finds a conflict no event reported.
def test_the_pass_finds_a_conflict_on_an_unknown_pr_once_per_head(api, monkeypatch):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Improve messages', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': HEAD}))
    service = api.app.state.github_app
    with service.store.transaction() as c:
        c.execute(
            "INSERT INTO repositories(full_name,reachable,enabled,added_by,updated_at,default_branch) VALUES('ticoteam/tico',1,1,'human:ana',?,'main')",
            (H.now(),),
        )
    fake = {'mergeable': False, 'mergeable_state': 'dirty'}
    seen = []

    def fetch(method, path, headers=None, **kw):
        seen.append(path)
        if fake.get('refused'):
            return SimpleNamespace(status_code=500, json=lambda: {})
        body = {'state': 'open', 'merged': False, 'draft': False, 'head': {'sha': HEAD, 'ref': 'b'}, **fake}
        return SimpleNamespace(status_code=200, json=lambda: body)

    monkeypatch.setattr(service, 'row', lambda c=None: {'slug': 'test-app', 'org': 'ticoteam'})
    monkeypatch.setattr(service, 'mint', lambda *a, **k: ('t', 'later'))
    monkeypatch.setattr(service, '_call', fetch)
    monkeypatch.setattr(G, '_conflict_retry', {})

    def age(minutes):
        old = H.shift(H.now(), minutes=-minutes)
        with service.store.transaction() as c:
            c.execute("UPDATE task_links SET updated_at=? WHERE task_id=? AND kind='pr'", (old, task['id']))

    assert G.refresh_conflicts(service) == 0  # just updated: not due
    age(6)
    assert G.refresh_conflicts(service) == 1
    assert _prs(api, task['id'])[412]['mergeable'] == 'conflict'
    assert any('onflict' in i for i in _burst(api, task['id']))
    with service.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key=?", ('github-task-wake:' + task['id'],))
    assert G.refresh_conflicts(service) == 0  # a read resets the clock
    age(31)
    assert G.refresh_conflicts(service) == 1  # stale, read again, same head: no second wake
    assert _burst(api, task['id']) == []
    # A clean unknown result is not read before five minutes, a clean stale one after thirty.
    fake.update(mergeable=True, mergeable_state='clean')
    age(40)
    assert G.refresh_conflicts(service) == 1 and _prs(api, task['id'])[412]['mergeable'] == 'clean'
    # A read GitHub refuses writes nothing, so it waits half an hour instead of being read every 10 s pass.
    fake['refused'] = True
    age(40)
    reads = len(seen)
    assert G.refresh_conflicts(service) == 0 and len(seen) == reads + 1
    assert G.refresh_conflicts(service) == 0 and len(seen) == reads + 1
