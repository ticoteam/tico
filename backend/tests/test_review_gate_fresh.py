"""All-green hosted checks at the current head count for the review gate only from a fresh GitHub read: a bot's
question titled Review 1 or Review 2 reads GitHub as a review child does, and a review-gate hold clears
once a fresh read finds every hosted check passing (PR 1603)."""
import uuid
from types import SimpleNamespace

import pytest

from backend import hubdb as H, task_blocked
from backend.tests.test_github import api, headers, hook, post, pr_event, PR  # noqa: F401
from backend.tests.test_review_gate import HEAD, OLD, BASE, ticket, get, move
from backend.tests.test_api import hold

STALE = f'Tests: head {OLD}: 9 passed, 0 failed'
GREEN = {f'check {i}': 'success' for i in range(16)}


def github(api, monkeypatch, checks):
    """A configured GitHub App whose PR #412 is at HEAD with these check-run conclusions; returns the paths read."""
    service = api.app.state.github_app
    monkeypatch.setattr(service, 'row', lambda c: {'org': 'ticoteam'})
    monkeypatch.setattr(service, 'mint', lambda *a: ('synthetic', 'later'))
    reads = []
    def fetch(method, path, **kw):
        reads.append(path)
        if '/pulls/' in path:
            data = {'head': {'sha': HEAD}}
        elif path.endswith('check-runs'):
            data = {'check_runs': [{'id': i, 'name': name, 'conclusion': result}
                                   for i, (name, result) in enumerate(checks.items(), 1)]}
        else:
            data = []
        return SimpleNamespace(status_code=200, json=lambda: data)
    monkeypatch.setattr(service, '_call', fetch)
    return reads


def bot_token(api, slug='cpo'):
    """A leased bot identity: enroll a runner, assign the bot, send it a message, claim the turn."""
    code = post(api, 'enrollments', {'owner_id': 'ana'})['code']
    runner = post(api, 'runners/enroll', {'code': code, 'label': 'Mac', 'platform': 'test'})
    post(api, 'bots/' + slug + '/assignment', {'runner_id': runner['runner_id'], 'expected_generation': 0})
    as_runner = {'Authorization': 'Bearer ' + runner['token'], 'Idempotency-Key': str(uuid.uuid4())}
    r = api.post('/api/v2/runners/heartbeat', headers=as_runner,
                 json={'version': 'test', 'platform': 'test', 'readiness': {slug: True}})
    assert r.status_code == 200, r.text
    post(api, 'chat/' + slug, {'text': 'Please look at your queue.'})
    r = api.post('/api/v2/jobs/claim', headers={**as_runner, 'Idempotency-Key': str(uuid.uuid4())}, json={'bot': slug})
    assert r.status_code == 200, r.text
    return r.json()['attempt']['token']


def ask(api, token, task, title='Review 2: PR #412 is ready for its second review'):
    return api.post('/api/v2/task-questions', headers={'Authorization': 'Bearer ' + token,
                                                       'Idempotency-Key': str(uuid.uuid4())},
                    json={'on': 'ana', 'parent': task['id'], 'title': title,
                          'question': 'Should Review 2 start on the current head?'})


def held(api, task):
    """A review-gate hold with no remembered move: a Review 2 child refused while GitHub was not configured."""
    r = api.post('/api/v2/tasks', headers=headers(), json={'owner': 'cmo', 'title': 'Review 2: PR #412',
        'body': f'Check PR #412 base {BASE} head {HEAD}', 'relations': [{'task': task['id'], 'kind': 'parent'}]})
    assert r.status_code == 422 and 'newest receipt head: ' + OLD in r.text, r.text
    with api.app.state.store.read() as c:
        assert H.task(c, task['id'])['blocked_kind'] == 'gate'
    current = get(api, 'tasks/' + task['id'])['task']
    assert current['step']['name'] == 'In Progress'
    return current


def test_renamed_review_step_reads_fresh_ci_before_refusing_move(api, monkeypatch):
    task = ticket(api, f'Tests: head {HEAD}: 9 passed, 0 failed')
    with api.app.state.store.transaction() as c:
        typ = H.type_get(c, task['type_id'])
        H.type_update(c, H.KEEPER, typ['id'], steps=[
            {'id': s['id'], 'name': 'Code review' if s['name'] == 'Review 1' else s['name'],
             'status': s['status']} for s in typ['steps']])
    reads = github(api, monkeypatch, {'python': 'failure'})
    response = move(api, task, 'Code review')
    assert any('/commits/' + HEAD + '/check-runs' in p for p in reads), 'the renamed step reads GitHub afresh'
    assert response.status_code == 422 and 'CI failing at head ' + HEAD in response.text, response.text
    assert 'python' in response.text
    current = get(api, 'tasks/' + task['id'])['task']
    assert current['step']['name'] == 'In Progress' and 'CI failing' in hold(current)['reason']


def test_review_question_is_filed_when_every_hosted_check_passes_at_the_current_head(api, monkeypatch):
    task = ticket(api, STALE)
    token = bot_token(api)
    reads = github(api, monkeypatch, GREEN)
    r = ask(api, token, task)
    assert r.status_code == 200, r.text
    assert any('/commits/' + HEAD + '/check-runs' in p for p in reads), 'the question read GitHub afresh'
    question = r.json()['task']
    assert question['status'] == 'waiting' and question['waiting_on'] == 'human:ana'
    # This is the builder's decision question, not a blocked judging check. The minute sweep agrees.
    with api.app.state.store.transaction() as c:
        task_blocked.sweep(c, H.now())
    current = get(api, 'tasks/' + task['id'])['task']
    assert current['step']['name'] == 'In Progress' and not hold(current).get('since')


def test_fresh_green_checks_do_not_clear_a_waiting_reviewers_hold(api, monkeypatch):
    task = ticket(api, STALE)
    github(api, monkeypatch, GREEN)
    check = post(api, 'tasks', {'owner_id': 'cmo', 'title': 'Review 1: PR #412',
        'body': 'Review the candidate.', 'relations': [{'task': task['id'], 'kind': 'parent'}]})
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'bot:cmo', check['id'], status='waiting', note='Review runner unavailable')
        held_row = H.task(c, task['id'])
        assert held_row['blocked_kind'] == 'check'
        since = held_row['blocked_since']
    assert task_blocked.fresh(api.app.state.github_app, force=True) == []
    current = get(api, 'tasks/' + task['id'])['task']
    assert current['step']['name'] == 'In Progress' and hold(current)['since'] == since
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'bot:cmo', check['id'], status='doing')
    current = get(api, 'tasks/' + task['id'])['task']
    assert current['step']['name'] == 'In Progress' and not hold(current).get('since')


@pytest.mark.parametrize('result,refusal', [('failure', 'CI failing at head ' + HEAD),
                                            (None, 'newest receipt head: ' + OLD)])
def test_review_question_is_refused_unless_every_hosted_check_passes(api, monkeypatch, result, refusal):
    """A failing check is named first (the gate reads CI before receipts); a pending one leaves the stale receipt."""
    task = ticket(api, STALE)
    token = bot_token(api)
    github(api, monkeypatch, {**GREEN, 'check 3': result})
    r = ask(api, token, task)
    assert r.status_code == 422 and refusal in r.text, r.text


def test_a_question_titled_otherwise_reads_no_github(api, monkeypatch):
    task = ticket(api, STALE)
    token = bot_token(api)
    reads = github(api, monkeypatch, GREEN)
    assert ask(api, token, task, title='Which reviewer should take PR #412?').status_code == 200
    assert reads == []


def test_blocked_gate_hold_clears_on_a_fresh_all_green_read(api, monkeypatch):
    task = ticket(api, STALE)
    parked = held(api, task)
    service = api.app.state.github_app
    with api.app.state.store.transaction() as c:
        task_blocked.reconcile(c, task['id'])
        assert H.task(c, task['id'])['blocked_since'], 'the cached read alone keeps the hold'
    reads = github(api, monkeypatch, GREEN)
    assert task_blocked.fresh(service, force=True) == [task['id']]
    assert any('/commits/' + HEAD + '/check-runs' in p for p in reads)
    current = get(api, 'tasks/' + task['id'])['task']
    assert current['step']['name'] == 'In Progress' and not hold(current).get('since')
    assert current['version'] > parked['version']


@pytest.mark.parametrize('result', ['failure', None])
def test_blocked_gate_hold_stays_unless_every_hosted_check_passes_and_reads_once_per_change(api, monkeypatch, result):
    task = ticket(api, STALE)
    held(api, task)
    service = api.app.state.github_app
    reads = github(api, monkeypatch, {**GREEN, 'check 3': result})
    assert task_blocked.fresh(service, force=True) == []
    assert get(api, 'tasks/' + task['id'])['task']['step']['name'] == 'In Progress'
    count = len(reads)
    assert count and task_blocked.fresh(service, force=True) == []
    assert len(reads) == count, 'nothing the gate reads changed, so GitHub is not read again'
    with api.app.state.store.transaction() as c:
        H.task_update(c, task['owner_id'], task['id'], note='Checks rerunning.')
    github(api, monkeypatch, GREEN)
    assert task_blocked.fresh(service, force=True) == [task['id']]


def test_stale_receipt_review_moves_without_passing_checks_are_still_refused(api, monkeypatch):
    task = ticket(api, STALE)
    github(api, monkeypatch, {**GREEN, 'check 3': None})
    child = api.post('/api/v2/tasks', headers=headers(), json={'owner': 'cmo', 'title': 'Review 2: PR #412',
        'body': f'Check PR #412 base {BASE} head {HEAD}', 'relations': [{'task': task['id'], 'kind': 'parent'}]})
    assert child.status_code == 422 and 'newest receipt head: ' + OLD in child.text, child.text
    r = move(api, task, 'Review 2')
    assert r.status_code == 422 and 'newest receipt head: ' + OLD in r.text, r.text
    # With no hosted check passing at all, an all-skipped read is no pass either.
    github(api, monkeypatch, {'check 1': 'skipped'})
    assert move(api, task, 'Review 2').status_code == 422


def test_a_same_head_failure_landing_during_the_read_keeps_the_hold(api, monkeypatch):
    """A check webhook changes the PR link but not the task version: a green read it supersedes must not clear."""
    from backend import review_gate
    from backend.tests.test_github import _suite
    task = ticket(api, STALE)
    held(api, task)
    service = api.app.state.github_app
    github(api, monkeypatch, GREEN)
    read = review_gate.snapshot
    def raced(service, prs):
        found = read(service, prs)
        _suite(api, 'github-actions', 'completed', 'failure', sha=HEAD)
        return found
    monkeypatch.setattr(review_gate, 'snapshot', raced)
    assert task_blocked.fresh(service, force=True) == []
    assert get(api, 'tasks/' + task['id'])['task']['step']['name'] == 'In Progress'


def test_reads_that_keep_failing_take_turns_with_the_other_holds(api, monkeypatch):
    from backend import review_gate
    first = ticket(api, STALE)
    second = post(api, 'tasks', {'owner_id': 'cpo', 'title': 'Improve replies', 'body': 'Build replies.',
                                 'type': first['type_id'], 'step': 'In Progress', 'links': [PR]})
    hook(api, 'pull_request', pr_event('synchronize', head={'sha': HEAD}))
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET note=? WHERE id=?', (STALE, second['id']))
    second = get(api, 'tasks/' + second['id'])['task']
    held(api, first)
    held(api, second)
    service = api.app.state.github_app
    github(api, monkeypatch, GREEN)
    with api.app.state.store.read() as c:
        broken = {p['id'] for p in review_gate.links(c, H.task(c, first['id']))}
    read, tried = review_gate.snapshot, []
    def snapshot(service, prs):
        tried.append({p['id'] for p in prs} <= broken)
        if {p['id'] for p in prs} <= broken:
            return {p['id']: {'error': True, 'why': 'GitHub answered 404'} for p in prs}
        return read(service, prs)
    monkeypatch.setattr(review_gate, 'snapshot', snapshot)
    monkeypatch.setattr(task_blocked, 'FRESH_PER_PASS', 1)
    passes = [task_blocked.fresh(service, force=True) for _ in range(2)]
    assert len(tried) == 2 and sorted(tried) == [False, True], 'one read a pass, and the failing hold does not take both'
    assert [second['id']] in passes
    assert get(api, 'tasks/' + first['id'])['task']['step']['name'] == 'In Progress'
    broken.clear()          # GitHub answers again: the failed hold is read on the next pass and clears
    assert task_blocked.fresh(service, force=True) == [first['id']]


def test_a_held_review_question_sent_anyway_reads_github_afresh_and_is_filed(api, monkeypatch):
    """Send anyway on a held Review 2 question is the same gate as a new one: all-green hosted checks at the current
    head count from a fresh read, with no receipt at that head."""
    from backend.tests.test_org_rule_check import COLOR, Judge
    task = ticket(api, STALE)
    token = bot_token(api)
    api.app.state.judge = Judge(api.app.state.store)
    post(api, 'rules', {'kind': 'internal', 'text': COLOR, 'who': {'bots': ['cpo']}, 'if_broken': 'escalate',
                        'reviewer': 'ana'})
    reads = github(api, monkeypatch, GREEN)
    r = api.post('/api/v2/task-questions', headers={'Authorization': 'Bearer ' + token,
                                                     'Idempotency-Key': str(uuid.uuid4())},
                 json={'on': 'ana', 'parent': task['id'], 'title': 'Review 2: PR #412 is ready for its second review',
                       'question': 'Should Review 2 start on the current head?',
                       'body': 'Our favorite color is blue.'})
    assert r.status_code == 200 and r.json().get('held'), r.text
    hold = r.json()['held']['id']
    reads.clear()
    view = get(api, 'rule-holds/' + hold)
    sent = post(api, 'rule-holds/' + hold + '/send', {'nonce': view['nonce']})
    assert sent['hold']['state'] == 'sent', sent
    assert any('/commits/' + HEAD + '/check-runs' in p for p in reads), 'Send anyway read GitHub afresh'
    question = sent['result']['task']
    assert question['status'] == 'waiting' and question['waiting_on'] == 'human:ana'
