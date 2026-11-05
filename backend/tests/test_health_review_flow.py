"""Health follows the factory's check evidence at the current PR head, with set reads and private children hidden."""
import json

from backend import health, hubdb as H
from backend.tests.test_health import add_bot, environment, signed_in  # noqa: F401

HEAD, OLD = 'a' * 40, 'b' * 40


def ticket(api, number=206, step='Second look'):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, 'Factory ' + str(number), [
            {'name': 'Build', 'status': 'doing'},
            {'name': 'First look', 'status': 'review', 'flow': {'check': 'review', 'on_pass': 'Build', 'on_fail': 'Build'}},
            {'name': 'Second look', 'status': 'review', 'flow': {'check': 'review', 'on_pass': 'Build', 'on_fail': 'Build'}},
            {'name': 'In QA', 'status': 'review', 'flow': {'check': 'qa', 'on_pass': 'merge', 'on_fail': 'QA Fixes'}},
            {'name': 'QA Fixes', 'status': 'doing', 'flow': {'after_green': 'merge'}},
        ])
        task = H.task_create(c, 'human:morgan', f'Build change {number}', '', 'bot:builder', type=typ['id'], step=step, private=False, mover=True)
        link = H.task_link(c, 'human:morgan', task['id'], f'https://github.com/Acme/atlas/pull/{number}')
        detail = {'head_sha': HEAD, 'health_wait': {'reason': 'review', 'head': HEAD, 'since': H.shift(H.now(), hours=-24)}}
        c.execute("UPDATE task_links SET state='open',checks='passing',mergeable='clean',detail=? WHERE id=?",
                  (json.dumps(detail), link['id']))
        c.execute('UPDATE task_steps SET check_pool=? WHERE id=?', (json.dumps(['reviewer']), task['step_id']))
    return task


def child(api, task, title='Second look: PR #206', verdict='', head=HEAD, owner='reviewer', private=False, status='closed'):
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, 'human:morgan', title, '', 'bot:' + owner, parent_id=task['id'], private=private, lint=False)
        c.execute('UPDATE tasks SET status=?,verdict=?,verdict_head=? WHERE id=?', (status, verdict or None, head if verdict else None, row['id']))
        H._task_event(c, row['id'], H.KEEPER, 'review_head', None, head, '')
    return row


def items(api):
    response = api.get('/api/v2/health', headers=signed_in())
    assert response.status_code == 200, response.text
    return next(c['items'] for c in response.json()['checks'] if c['id'] == 'aged_prs')


def setup(environment):
    api = environment()
    for slug in ('builder', 'reviewer', 'hidden-reviewer'):
        add_bot(api, slug)
    return api


def test_closed_changes_at_head_name_the_builder(environment):
    api = setup(environment)
    task = ticket(api)
    child(api, task, title='First look: PR #206', verdict='changes')
    child(api, task, verdict='changes')
    [item] = items(api)
    assert item['whose_move'] == 'bot:builder'
    assert 'address review changes on PR #206' in item['fix']
    assert 'without review' not in item['text']


def test_approval_that_satisfies_merge_ready_names_the_builder_to_merge(environment):
    api = setup(environment)
    task = ticket(api, step='In QA')
    child(api, task, title='QA: PR #206', verdict='pass')
    [item] = items(api)
    assert item['whose_move'] == 'bot:builder'
    assert 'bot:builder: merge PR #206' in item['fix']


def test_approval_on_a_feedback_step_names_the_builder_to_move_on(environment):
    api = setup(environment)
    task = ticket(api)
    child(api, task, verdict='approve')
    [item] = items(api)
    assert item['whose_move'] == 'bot:builder'
    assert 'move PR #206 on' in item['fix']


def test_old_head_verdict_needs_re_review_from_the_step_pool(environment):
    api = setup(environment)
    task = ticket(api)
    child(api, task, verdict='changes', head=OLD)
    [item] = items(api)
    assert item['whose_move'] == 'Second look pool'
    assert 're-review needed at the new head' in item['fix']
    assert 'no reviewer filed yet' in item['fix'] and 'online' in item['fix']


def test_waiting_re_review_names_the_pool_and_its_wait_reason(environment):
    from backend import check_pools
    api = setup(environment)
    task = ticket(api)
    child(api, task, verdict='approve', head=OLD)
    with api.app.state.store.transaction() as c:
        step = dict(c.execute('SELECT * FROM task_steps WHERE id=?', (task['step_id'],)).fetchone())
        check_pools._wait(c, task, step, 'Second look: PR #206', [HEAD], 'every reviewer in the pool is at its limit of open checks')
    [item] = items(api)
    assert item['whose_move'] == 'Second look pool'
    assert 're-review' in item['fix'] and 'waiting for a free reviewer' in item['fix']
    assert 'at its limit' in item['fix']


def test_open_checks_name_their_owner_for_renamed_review_qa_and_security(environment):
    api = setup(environment)
    for number, step, title in ((206, 'Second look', 'Second look: PR #206'),
                                (207, 'In QA', 'QA: PR #207'), (208, 'Second look', 'Security: PR #208')):
        task = ticket(api, number=number, step=step)
        child(api, task, title=title, status='open')
    assert len(items(api)) == 3
    assert all(item['whose_move'] == 'bot:reviewer' for item in items(api))


def test_pool_filing_still_identifies_a_check_after_its_title_changes(environment):
    api = setup(environment)
    task = ticket(api)
    check = child(api, task, title='Notes on the patch', verdict='changes')
    with api.app.state.store.transaction() as c:
        H._task_event(c, task['id'], H.KEEPER, 'check_filed', None, check['id'], '')
    assert items(api)[0]['whose_move'] == 'bot:builder'


def test_private_children_supply_no_owner_or_verdict_to_health(environment):
    api = setup(environment)
    task = ticket(api)
    child(api, task, title='Security: PR #206', owner='hidden-reviewer', status='open', private=True)
    child(api, task, owner='hidden-reviewer', verdict='changes', private=True)
    [item] = items(api)
    assert item['whose_move'] == 'Second look pool'
    assert 'hidden-reviewer' not in json.dumps(item)
    assert 'address review changes' not in item['fix']


def test_review_flow_keeps_its_clock_after_a_github_review(environment):
    from backend import github
    api = setup(environment)
    task = ticket(api)
    child(api, task, verdict='approve')
    with api.app.state.store.transaction() as c:
        github.pr_signal(c, 'pull_request_review', {'action': 'submitted',
                         'pull_request': {'html_url': 'https://github.com/Acme/atlas/pull/206'},
                         'review': {'id': 1, 'state': 'approved', 'commit_id': HEAD, 'user': {'login': 'checker'}}})
    [item] = items(api)
    assert item['whose_move'] == 'bot:builder' and item['age_minutes'] == 24 * 60


def test_aged_review_reads_stay_constant_as_tickets_grow(environment):
    api = setup(environment)
    def measured():
        queries = []
        api.app.state.store.roster_rows.clear()
        with api.app.state.store.read_transaction() as c:
            who = api.app.state.auth.owner_identity(c)
            c.set_trace_callback(queries.append)
            checks = health._aged_pr_checks(c, who, api.app.state.auth, True, 'human')
            c.set_trace_callback(None)
        return len(queries), len(checks[0]['items'])
    for number in (206, 207):
        task = ticket(api, number=number)
        child(api, task, verdict='changes')
    few, count = measured()
    assert count == 2
    for number in range(208, 226):
        task = ticket(api, number=number)
        child(api, task, verdict='changes')
    many, count = measured()
    assert count == 20
    assert many == few, (few, many)


def test_a_qa_pass_cannot_clear_a_failure_at_the_same_head(environment):
    api = setup(environment)
    task = ticket(api, step='In QA')
    child(api, task, title='QA: PR #206', verdict='fail')
    child(api, task, title='QA again: PR #206', verdict='pass')
    [item] = items(api)
    assert item['whose_move'] == 'bot:builder'
    assert 'address review changes' in item['fix']


def test_green_new_head_at_a_fixes_step_uses_merge_ready(environment):
    api = setup(environment)
    task = ticket(api, step='In QA')
    child(api, task, title='QA: PR #206', verdict='fail', head=OLD)
    with api.app.state.store.transaction() as c:
        build = dict(c.execute("SELECT * FROM task_steps WHERE type_id=? AND name='QA Fixes'", (task['type_id'],)).fetchone())
        c.execute('UPDATE task_steps SET flow=? WHERE id=?', (json.dumps({'after_green': 'merge'}), build['id']))
        c.execute('UPDATE tasks SET step_id=?,status=? WHERE id=?', (build['id'], 'doing', task['id']))
        H._task_event(c, task['id'], H.KEEPER, 'step', task['step_id'], build['id'], '')
    [item] = items(api)
    assert item['whose_move'] == 'bot:builder'
    assert 'bot:builder: merge PR #206' in item['fix']
