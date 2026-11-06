"""Dev tickets do not stick on PR bookkeeping: merged work needs no open PR, a PR naming its ticket links itself, a
finished workflow run wakes the builder, and a conflict nobody was told about is found by the maintenance pass."""

import json
from types import SimpleNamespace

from backend import github as G
from backend import hubdb as H
from backend import review_gate
from backend.tests.test_github import PR, _app, _branch_pr, _burst, _prs, api, get, hook, pr_event, post  # noqa: F401

HEAD = 'a' * 40


def dev_type(api, numbered=False):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(
            c,
            H.KEEPER,
            'Dev tickets',
            [
                {'name': 'In Progress', 'status': 'doing'},
                {'name': 'Review 1', 'status': 'review'},
                {'name': 'Pending Release', 'status': 'doing'},
                {'name': 'Done', 'status': 'done'},
            ],
            numbered=numbered,
        )
        merge = next(s for s in typ['steps'] if s['name'] == 'Pending Release')
        c.execute('UPDATE task_steps SET flow=? WHERE id=?', (json.dumps({'on_merge': True}), merge['id']))
    return typ


def ticket(api, typ, step='In Progress', **fields):
    return post(
        api,
        'tasks',
        {'owner': 'cpo', 'title': 'Improve messages', 'body': 'x', 'type': typ['id'], 'step': step, **fields},
    )


def set_number(api, task, number):
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET number=? WHERE id=?', (number, task['id']))


# A: merged work satisfies a check after the merge, never a review before it.
def test_merged_pr_satisfies_a_check_after_the_merge_but_not_a_review_before_it(api):
    typ = dev_type(api)
    early = ticket(api, typ, links=[PR])
    hook(api, 'pull_request', pr_event('opened', head={'sha': HEAD}))
    hook(
        api,
        'pull_request',
        pr_event('closed', state='closed', merged=True, head={'sha': HEAD}, merge_commit_sha='c' * 40),
    )
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE tasks SET step_id=(SELECT id FROM task_steps WHERE type_id=? AND name='In Progress') WHERE id=?",
            (typ['id'], early['id']),
        )
        row = H.task(c, early['id'])
        assert H.task_links(c, early['id'])[0]['state'] == 'merged'
        refused = review_gate.problem(c, row, row['owner_id'], require_pr=True)
        assert refused and 'no linked PR' in refused[3]  # pre-merge: still wants an open PR
        c.execute(
            "UPDATE tasks SET step_id=(SELECT id FROM task_steps WHERE type_id=? AND name='Pending Release') WHERE id=?",
            (typ['id'], early['id']),
        )
        row = H.task(c, early['id'])
        assert review_gate.problem(c, row, row['owner_id'], require_pr=True) is None


def test_a_ticket_with_only_a_closed_pr_is_still_refused(api):
    typ = dev_type(api)
    task = ticket(api, typ, step='Pending Release', links=[PR])
    hook(api, 'pull_request', pr_event('opened', head={'sha': HEAD}))
    hook(api, 'pull_request', pr_event('closed', state='closed', head={'sha': HEAD}))
    with api.app.state.store.transaction() as c:
        row = H.task(c, task['id'])
        refused = review_gate.problem(c, row, row['owner_id'], require_pr=True)
        assert refused and 'no linked PR' in refused[3]


# B: a PR naming its Dev ticket links itself.
def test_a_pr_naming_its_dev_ticket_links_itself_strictly(api):
    _app(api)
    typ = dev_type(api, numbered=True)
    task = ticket(api, typ)
    set_number(api, task, 665)
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(7, 'fix/x', title='Fix thing (#665)')})
    assert _prs(api, task['id']) == {}  # a bare #665 is not a ticket name
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(8, 'fix/x', title='Dev 665 and Dev 12')})
    assert _prs(api, task['id']) == {}  # two tickets named: none chosen
    hook(
        api,
        'pull_request',
        {'action': 'opened', 'pull_request': _branch_pr(9, 'fix/x', head_repo='a/b', title='Dev 665')},
    )
    assert _prs(api, task['id']) == {}  # a fork
    hook(
        api,
        'pull_request',
        {
            'action': 'opened',
            'pull_request': _branch_pr(10, 'fix/pr-signals', title='Stop sticking', body='Fixes Dev ticket 665.'),
        },
    )
    links = _prs(api, task['id'])
    assert set(links) == {10} and links[10]['added_by'] == 'keeper'
    # Another PR naming it does not add a second open PR; a branch name works for another ticket.
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(11, 'fix/dev-665-again', title='Again')})
    assert set(_prs(api, task['id'])) == {10}
    other = ticket(api, typ)
    set_number(api, other, 700)
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(12, 'fix/dev-700-thing', title='Thing')})
    assert set(_prs(api, other['id'])) == {12}


def test_a_pr_naming_a_non_dev_or_finished_ticket_links_nothing(api):
    _app(api)
    typ = dev_type(api, numbered=True)
    done = ticket(api, typ)
    set_number(api, done, 50)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (done['id'],))
    plain = post(api, 'tasks', {'owner': 'cpo', 'title': 'Plain', 'body': 'x'})
    set_number(api, plain, 51)
    for n, title in ((20, 'Dev 50'), (21, 'Dev 51')):
        hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(n, 'x/y', title=title)})
    assert _prs(api, done['id']) == {} and _prs(api, plain['id']) == {}


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


# D: the maintenance pass finds a conflict no event reported.
def test_the_pass_finds_a_conflict_on_an_unknown_pr_once_per_head(api, monkeypatch):
    typ = dev_type(api)
    task = ticket(api, typ, links=[PR])
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
        body = {'state': 'open', 'merged': False, 'draft': False, 'head': {'sha': HEAD, 'ref': 'b'}, **fake}
        return SimpleNamespace(status_code=200, json=lambda: body)

    monkeypatch.setattr(service, 'row', lambda c=None: {'slug': 'test-app', 'org': 'ticoteam'})
    monkeypatch.setattr(service, 'mint', lambda *a, **k: ('t', 'later'))
    monkeypatch.setattr(service, '_call', fetch)

    def age(minutes):
        old = G._minutes_ago(minutes)
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
