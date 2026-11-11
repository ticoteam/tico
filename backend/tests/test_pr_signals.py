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


# E: a base that moves has the other PRs on it read at once (Acme rc.5 run G: the conflict was seen 3 minutes late).
def _moved_setup(api, monkeypatch):
    """Four tracked PRs: 412 and 413 on ticoteam/tico main, 414 on ticoteam/tico release, 9 on ticoteam/other main.
    Returns (service, tasks by number, GitHub's answers by number, the paths read)."""
    service = api.app.state.github_app
    with service.store.transaction() as c:
        for repo in ('ticoteam/tico', 'ticoteam/other'):
            c.execute(
                "INSERT INTO repositories(full_name,reachable,enabled,added_by,updated_at,default_branch) "
                "VALUES(?,1,1,'human:ana',?,'main')",
                (repo, H.now()),
            )
    prs = {
        412: ('ticoteam/tico', 'main'),
        413: ('ticoteam/tico', 'main'),
        414: ('ticoteam/tico', 'release'),
        9: ('ticoteam/other', 'main'),
    }
    tasks, answers = {}, {}
    for number, (repo, base) in prs.items():
        url = f'https://github.com/{repo}/pull/{number}'
        tasks[number] = post(api, 'tasks', {'owner': 'cpo', 'title': f'PR {number}', 'body': 'x', 'links': [url]})
        payload = pr_event('opened', html_url=url, number=number, head={'sha': HEAD, 'ref': f'b{number}'})
        payload['pull_request']['base'] = {'ref': base}
        payload['pull_request']['mergeable'] = True
        payload['repository'] = {'full_name': repo, 'default_branch': 'main'}
        hook(api, 'pull_request', payload)
        answers[number] = {'mergeable': False, 'mergeable_state': 'dirty'}
    seen = []

    def fetch(method, path, headers=None, **kw):
        seen.append(path)
        number = int(path.rsplit('/', 1)[1])
        body = {'state': 'open', 'merged': False, 'draft': False, 'head': {'sha': HEAD, 'ref': f'b{number}'}}
        return SimpleNamespace(status_code=200, json=lambda: {**body, **answers[number]})

    monkeypatch.setattr(service, 'row', lambda c=None: {'slug': 'test-app', 'org': 'ticoteam'})
    monkeypatch.setattr(service, 'mint', lambda *a, **k: ('t', 'later'))
    monkeypatch.setattr(service, '_call', fetch)
    monkeypatch.setattr(G, '_conflict_retry', {})
    return service, tasks, answers, seen


def _merge(api, number, sha, repo='ticoteam/tico', base='main'):
    url = f'https://github.com/{repo}/pull/{number}'
    payload = pr_event(
        'closed', html_url=url, number=number, state='closed', merged=True, merge_commit_sha=sha, head={'sha': HEAD}
    )
    payload['pull_request']['base'] = {'ref': base}
    payload['repository'] = {'full_name': repo, 'default_branch': 'main'}
    hook(api, 'pull_request', payload)


def _push(api, branch, sha, repo='ticoteam/tico'):
    hook(
        api,
        'push',
        {
            'ref': 'refs/heads/' + branch,
            'after': sha,
            'repository': {'full_name': repo, 'default_branch': 'main'},
            'commits': [],
        },
    )


def test_a_merge_has_the_other_prs_on_its_base_read_at_once(api, monkeypatch):
    service, tasks, _, seen = _moved_setup(api, monkeypatch)
    assert G.refresh_conflicts(service) == 0  # all just read clean: nothing due
    _merge(api, 412, 'c' * 40)
    _push(api, 'main', 'c' * 40)  # the same move, from the push: no second read
    assert G.refresh_conflicts(service) == 1
    assert seen == ['/repos/ticoteam/tico/pulls/413']  # not the merged one, another base or another repository
    assert _prs(api, tasks[413]['id'])[413]['mergeable'] == 'conflict'
    assert any('onflict' in i for i in _burst(api, tasks[413]['id']))
    assert 'recheck' not in _prs(api, tasks[413]['id'])[413]['detail']
    assert G.refresh_conflicts(service) == 0 and len(seen) == 1
    # A push to another branch reads the PRs based on it, and only those.
    _push(api, 'release', 'd' * 40)
    assert G.refresh_conflicts(service) == 1 and seen[-1] == '/repos/ticoteam/tico/pulls/414'
    # Tico's own merge call (a repository with neither a queue nor auto-merge) does the same before the webhook.
    with service.store.transaction() as c:
        assert G.merged_by_tico(c, 'ticoteam/other', 8) == 1
    assert G.refresh_conflicts(service) == 1 and seen[-1] == '/repos/ticoteam/other/pulls/9'


def test_a_base_move_retries_githubs_unknown_a_bounded_number_of_times(api, monkeypatch):
    service, tasks, answers, seen = _moved_setup(api, monkeypatch)
    answers[413] = {'mergeable': None, 'mergeable_state': 'unknown'}  # GitHub is still computing

    def due_now():
        with service.store.transaction() as c:
            c.execute(
                "UPDATE task_links SET detail=json_set(detail,'$.recheck.due',?) "
                "WHERE json_extract(detail,'$.recheck') IS NOT NULL",
                (H.shift(H.now(), seconds=-1),),
            )

    _merge(api, 412, 'c' * 40)
    assert G.refresh_conflicts(service) == 1
    assert G.refresh_conflicts(service) == 0  # read again only after a short wait
    due_now()
    assert G.refresh_conflicts(service) == 1 and len(seen) == 2
    answers[413] = {'mergeable': False, 'mergeable_state': 'dirty'}  # computed: the conflict is acted on
    due_now()
    assert G.refresh_conflicts(service) == 1
    assert _prs(api, tasks[413]['id'])[413]['mergeable'] == 'conflict'
    assert any('onflict' in i for i in _burst(api, tasks[413]['id']))
    assert 'recheck' not in _prs(api, tasks[413]['id'])[413]['detail']
    # A move GitHub never answers is read once and then once per RECHECK_AFTER_S step, no more.
    answers[413] = {'mergeable': None, 'mergeable_state': 'unknown'}
    _push(api, 'main', 'e' * 40)
    reads = len(seen)
    for _ in range(len(G.RECHECK_AFTER_S) + 3):
        G.refresh_conflicts(service)
        due_now()
    assert len(seen) - reads == 1 + len(G.RECHECK_AFTER_S)
    assert 'recheck' not in _prs(api, tasks[413]['id'])[413]['detail']
