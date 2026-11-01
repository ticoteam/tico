"""A manager inherits a party's task rights, with current chart and original attribution."""
import json

import pytest

from backend import hubdb as H, task_relations as TR
from backend.tests.test_tasks_board import api, bot_token, get, headers, post  # noqa: F401


@pytest.fixture(params=['bot', 'person'])
def manager(api, request):
    with api.app.state.store.transaction() as c:
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for p in roster['people']:
            p['reports_to'] = ''
        roster['org_groups'] = {'product': {'name': 'Product', 'reports_to': 'ben'}}
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))
        c.execute("UPDATE humans SET teams='[\"sales\"]' WHERE id='ben'")  # not a mover
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='ops'")
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='cpo'")
        c.execute("UPDATE bot_config SET reports_to='human:priya' WHERE bot='cmo'")
    token = bot_token(api, 'cpo') if request.param == 'bot' else 'ben-test'
    actor = 'bot:cpo' if request.param == 'bot' else 'human:ben'
    return token, actor


def make(api, *, owner='bot:ops', requester='human:ana', private=True, **kwargs):
    with api.app.state.store.transaction() as c:
        number = c.execute('SELECT count(*) FROM tasks').fetchone()[0]
        return H.task_create(c, requester, f'Check the permission packet {number}', 'Review the packet.', owner,
                             private=private, lint=False, **kwargs)


def change(api, task, token, **fields):
    current = get(api, 'tasks/' + task['id'], token=token)['task']
    return post(api, 'tasks/' + task['id'], {'version': current['version'], **fields}, token=token)


@pytest.mark.parametrize('fields,field', [({'status': 'doing'}, 'status'),
    ({'due': '2027-01-01T00:00:00Z'}, 'due'), ({'owner': 'cpo'}, 'owner'),
    ({'title': 'Check the revised packet'}, 'title'), ({'body': 'Review the revised packet.'}, 'body'),
    ({'rank': 12}, 'rank'), ({'step_rank': 11}, 'step_rank')])
def test_manager_updates_and_is_named_in_history(api, manager, fields, field):
    token, actor = manager
    task = make(api)
    changed = change(api, task, token, **fields)
    assert changed[field] != task.get(field)
    with api.app.state.store.read() as c:
        events = [e for e in H.task_history(c, task['id']) if e['field'] == field]
        if field not in ('rank', 'step_rank'):
            assert events[-1]['actor'] == actor
        assert c.execute("SELECT actor FROM events WHERE action='task.update' AND target=? ORDER BY rowid DESC LIMIT 1",
                         (task['id'],)).fetchone()[0] == actor


def test_manager_changes_step_and_preserves_review_checks(api, manager):
    token, actor = manager
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, 'Manager workflow', [
            {'name': 'Todo', 'status': 'open'}, {'name': 'Build', 'status': 'doing'}])
    task = make(api, type=typ['id'])
    assert change(api, task, token, step='Build')['status'] == 'doing'
    with api.app.state.store.read() as c:
        assert [e for e in H.task_history(c, task['id']) if e['field'] == 'step'][-1]['actor'] == actor


def test_manager_reads_both_private_parties_in_detail_list_sql_and_loses_access(api, manager):
    token, _ = manager
    owned = make(api)
    requested = make(api, requester='bot:ops', owner='human:priya')
    unrelated = make(api, requester='human:priya', owner='bot:cmo')
    for task in (owned, requested):
        assert get(api, 'tasks/' + task['id'], token=token)['task']['id'] == task['id']
    get(api, 'tasks/' + unrelated['id'], token=token, expected=404)
    ids = {t['id'] for t in get(api, 'tasks', token=token)['tasks']}
    assert {owned['id'], requested['id']} <= ids and unrelated['id'] not in ids
    response = post(api, 'sql', {'sql': 'SELECT id,title FROM tasks'}, token=token)
    assert owned['id'] in str(response) and unrelated['id'] not in str(response)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:priya' WHERE bot='ops'")
    get(api, 'tasks/' + owned['id'], token=token, expected=404)
    assert owned['id'] not in {t['id'] for t in get(api, 'tasks', token=token)['tasks']}


def test_manager_comment_and_question_wake_owner_without_mention(api, manager):
    token, actor = manager
    task = make(api)
    # The manager is not the direct boss: add a second bot layer for bot and human cases.
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {'middle': {'name': 'Middle', 'runtime': 'fake', 'status': 'active'}}, {})
        c.execute("INSERT INTO bot_config(bot,config,reports_to,operator) VALUES('middle','{}','cpo','ana')")
        c.execute("UPDATE bot_config SET reports_to='middle' WHERE bot='ops'")
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0])
        config['bot_contact'] = 'tasks'
        c.execute("UPDATE bot_config SET config=? WHERE bot='ops'", (json.dumps(config),))
    for fields in ({'text': 'Please continue the work.'}, {'text': 'Which approach works?', 'ask': {
            'questions': [{'id': 'approach', 'header': 'Approach', 'question': 'Which approach works?', 'other': True}]}}):
        result = post(api, 'tasks/' + task['id'] + '/comments', fields, token=token)
        assert result['woke'], result
        assert result['comment']['from_actor'] == actor
        with api.app.state.store.read() as c:
            message = H.message(c, result['comment']['id'])
            assert message['to_actor'] == 'bot:ops' and not message['refs'].get('quiet')
            assert c.execute('SELECT 1 FROM jobs WHERE message_id=?', (message['id'],)).fetchone()


def test_requester_manager_comment_wakes_owner_with_requester_permissions(api, manager):
    token, actor = manager
    task = make(api, owner='bot:cmo', requester='bot:ops')
    result = post(api, 'tasks/' + task['id'] + '/comments', {'text': 'Please continue the packet.'}, token=token)
    assert result['woke'] and result['comment']['from_actor'] == actor
    with api.app.state.store.read() as c:
        message = H.message(c, result['comment']['id'])
        assert message['to_actor'] == 'bot:cmo'
        assert c.execute('SELECT 1 FROM jobs WHERE message_id=?', (message['id'],)).fetchone()


def test_manager_blockers_stale_link_and_reparent(api, manager):
    token, actor = manager
    task = make(api)
    blocker = make(api, private=False)
    parent = make(api)
    path = 'tasks/' + task['id'] + '/relations'
    post(api, path, {'task': blocker['id'], 'kind': 'blocked_by'}, token=token)
    post(api, path, {'task': blocker['id'], 'kind': 'blocked_by', 'remove': True}, token=token)
    post(api, path, {'task': parent['id'], 'kind': 'parent'}, token=token)
    post(api, path, {'task': parent['id'], 'kind': 'parent', 'remove': True}, token=token)
    link = post(api, 'tasks/' + task['id'] + '/links', {'url': 'https://example.com/report'}, token=token)
    with api.app.state.store.read() as c:
        lid = H.task_links(c, task['id'])[0]['id']
    response = api.delete('/api/v2/tasks/' + task['id'] + '/links/' + lid, headers=headers(token))
    assert response.status_code == 200, response.text
    with api.app.state.store.read() as c:
        assert TR.blocker_ids(c, task['id']) == [] and TR.parent_of(c, task['id']) is None
        assert all(e['actor'] == actor for e in H.task_history(c, task['id']) if e['field'] in ('blocked_by', 'parent_id', 'link'))


@pytest.mark.parametrize('descendant_blocked', [False, True])
def test_manager_reparent_preserves_ancestor_blocker_guard(api, manager, descendant_blocked):
    token, _ = manager
    parent = make(api)
    child = make(api)
    blocked = make(api, parent_id=child['id']) if descendant_blocked else child
    path = 'tasks/' + blocked['id'] + '/relations'
    post(api, path, {'task': parent['id'], 'kind': 'blocked_by'}, token=token)
    response = post(api, 'tasks/' + child['id'] + '/relations',
                    {'task': parent['id'], 'kind': 'parent'}, token=token, expected=422)
    assert 'ancestor' in str(response)
    with api.app.state.store.read() as c:
        assert TR.parent_of(c, child['id']) is None
        assert TR.blocker_ids(c, blocked['id']) == [parent['id']]
    post(api, path, {'task': parent['id'], 'kind': 'blocked_by', 'remove': True}, token=token)
    post(api, 'tasks/' + child['id'] + '/relations', {'task': parent['id'], 'kind': 'parent'}, token=token)
    with api.app.state.store.read() as c:
        assert TR.parent_of(c, child['id']) == parent['id']


def test_requester_manager_closes_reopens_owner_manager_cannot_close(api, manager):
    token, actor = manager
    owned = make(api)
    # The owner bot cannot close what a person asked for, so neither can its manager, bot or non-mover person.
    post(api, 'tasks/' + owned['id'], {'version': owned['version'], 'close': True}, token=token, expected=403)
    closed = change(api, owned, 'ana-test', close=True)
    post(api, 'tasks/' + owned['id'], {'version': closed['version'], 'status': 'doing'}, token=token, expected=403)
    requested = make(api, requester='bot:ops', owner='bot:cmo')
    closed = change(api, requested, token, close=True)
    assert closed['status'] == 'closed' and closed['closed_by'] == actor
    assert change(api, requested, token, status='doing')['status'] == 'doing'
    # A person's close (Ben's, above the requester) cannot be undone by the requester's bot manager.
    if actor.startswith('bot:'):
        closed = change(api, requested, 'ben-test', close=True)
        post(api, 'tasks/' + requested['id'], {'version': closed['version'], 'status': 'doing'}, token=token, expected=403)


def test_manager_snoozes_defers_asks_and_resolves_its_reports_question(api, manager):
    token, actor = manager
    task = make(api)
    snoozed = post(api, 'tasks/' + task['id'] + '/snooze', {'until': '2027-01-01T00:00:00Z'}, token=token)
    assert snoozed['snoozed_by'] == actor
    assert post(api, 'tasks/' + task['id'] + '/unsnooze', {}, token=token)['snoozed_until'] is None
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET next_run=1 WHERE id=?', (task['id'],))
    post(api, 'tasks/' + task['id'] + '/defer', {'at': '2027-01-01T00:00:00Z'}, token=token)
    ask = post(api, 'tasks/' + task['id'] + '/ask', {'text': 'Which packet should I use?'}, token=token)
    assert ask['from_actor'] == actor
    with api.app.state.store.transaction() as c:
        H.task_ask_resolve(c, actor, task['id'], ask['id'])
        question = H.task_comment(c, 'bot:ops', task['id'], 'Which format?', ask={
            'who': 'human:ana', 'questions': [{'id': 'format', 'header': 'Format', 'question': 'Which format?', 'other': True}]})
    result = post(api, 'tasks/' + task['id'] + '/comments/' + question['id'] + '/resolve', {'quote': 'Use Markdown.'}, token=token)
    assert result['comment']['from_actor'] == actor


def test_manager_finishing_report_subtask_returns_result_to_parent_once(api, manager):
    token, actor = manager
    parent = make(api, owner='bot:cmo', requester='human:priya', private=False)
    child = make(api, requester='bot:cmo', private=False, parent_id=parent['id'])
    # Another open child keeps the generic all-children-finished wake from hiding a lost result.
    make(api, requester='bot:cmo', private=False, parent_id=parent['id'])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed'")

    change(api, child, token, status='done', note='The packet is ready.')
    with api.app.state.store.read() as c:
        notices = c.execute(
            "SELECT m.body,json_extract(m.refs,'$.task') AS parent_id FROM jobs j JOIN messages m ON m.id=j.message_id "
            "WHERE j.bot='cmo' AND j.state='queued' AND json_extract(m.refs,'$.subtask')=?",
            (child['id'],)).fetchall()
        assert len(notices) == 1 and notices[0]['parent_id'] == parent['id']
        assert 'The packet is ready.' in notices[0]['body']
        assert [e for e in H.task_history(c, child['id']) if e['field'] == 'status'][-1]['actor'] == actor
    change(api, child, token, status='done', note='The packet is ready.')
    with api.app.state.store.read() as c:
        assert c.execute(
            "SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id "
            "WHERE j.bot='cmo' AND j.state='queued' AND json_extract(m.refs,'$.subtask')=?",
            (child['id'],)).fetchone()[0] == 1


def test_role_holder_keeps_snooze_right_without_lending_it_to_manager(api, manager):
    token, _ = manager
    report = bot_token(api, 'ops')
    task = make(api, owner='bot:cmo', requester='human:priya', private=False)
    with api.app.state.store.transaction() as c:
        from backend.task_roles import set_roles
        set_roles(c, 'human:priya', task['id'], {'reviewer': ['bot:ops']})
    path = 'tasks/' + task['id']
    post(api, path + '/snooze', {'until': '2027-01-01T00:00:00Z'}, token=token, expected=403)
    snoozed = post(api, path + '/snooze', {'until': '2027-01-01T00:00:00Z'}, token=report)
    assert snoozed['snoozed_by'] == 'bot:ops'
    post(api, path + '/unsnooze', {}, token=token, expected=403)
    with api.app.state.store.read() as c:
        assert H.task(c, task['id'])['snoozed_until'] == snoozed['snoozed_until']
    assert post(api, path + '/unsnooze', {}, token=report)['snoozed_until'] is None


def test_manager_run_now_and_waiting_person(api, manager):
    token, actor = manager
    bot_token(api, 'ops')  # report has a current assigned computer
    task = make(api)
    post(api, 'tasks/' + task['id'] + '/run-now', {}, token=token)
    with api.app.state.store.transaction() as c:
        H.task_update(c, actor, task['id'], status='waiting', waiting_on='ana', note='Choose the packet format.')
        assert any(e['actor'] == actor and e['field'] == 'status' for e in H.task_history(c, task['id']))


def test_nonmanager_unchanged_and_refusal_names_managers(api, manager):
    _, _ = manager
    outsider = bot_token(api, 'cmo')
    private = make(api)
    get(api, 'tasks/' + private['id'], token=outsider, expected=404)
    task = make(api, private=False)
    refusal = post(api, 'tasks/' + task['id'], {'version': task['version'], 'status': 'doing'}, token=outsider, expected=403)
    assert 'managers' in str(refusal) and 'owner' in str(refusal)
    post(api, 'tasks/' + task['id'] + '/comments', {'text': 'Unrelated comment.'}, token=outsider, expected=403)


def test_manager_cannot_edit_or_delete_another_authors_comment(api, manager):
    token, _ = manager
    task = make(api)
    with api.app.state.store.transaction() as c:
        comment = H.task_comment(c, 'bot:ops', task['id'], 'Original report.')
    path = '/api/v2/tasks/' + task['id'] + '/comments/' + comment['id']
    assert api.post(path, json={'text': 'Changed report.'}, headers=headers(token)).status_code == 403
    assert api.post(path + '/delete', json={}, headers=headers(token)).status_code == 403


def test_cycles_and_current_org_changes_do_not_keep_inherited_authority(api, manager):
    token, actor = manager
    task = make(api)
    with api.app.state.store.transaction() as c:
        assert H.task_represents(c, actor, 'bot:ops')
        c.execute("UPDATE bot_config SET reports_to='ops' WHERE bot='cpo'")
        assert not H.task_represents(c, actor, 'bot:ops')
    get(api, 'tasks/' + task['id'], token=token, expected=404)


def test_manager_classifies_labels_but_keeps_mover_and_publish_gates(api, manager):
    token, _ = manager
    task = make(api)
    with api.app.state.store.transaction() as c:
        H.tag_create(c, H.KEEPER, 'manager-check', label='Manager check')
    labelled = change(api, task, token, labels=['manager-check'])
    assert labelled['labels'] == ['manager-check']
    for fields in ({'number': 999}, {'lane': 'company'}, {'private': False}):
        current = get(api, 'tasks/' + task['id'], token=token)['task']
        response = api.post('/api/v2/tasks/' + task['id'], json={'version': current['version'], **fields}, headers=headers(token))
        assert response.status_code in (403, 422), response.text


def test_task_management_never_grants_a_named_approval(api, manager):
    token, _ = manager
    task = make(api)
    with api.app.state.store.transaction() as c:
        approval = H.approval_request(c, 'bot:ops', 'merge', {'repo': 'acme/example', 'pr': 7}, task_id=task['id'])
    response = api.post('/api/v2/approvals/' + approval['id'], json={'decision': 'approved'}, headers=headers(token))
    assert response.status_code == 403, response.text
    with api.app.state.store.read() as c:
        assert H.approval(c, approval['id'])['decision'] is None


def test_person_report_task_changes_follow_chart_but_person_private_tasks_stay_private(api):
    # Person management already walks department groups; task rights use the identical chart.
    with api.app.state.store.transaction() as c:
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for person in roster['people']:
            if person['id'] == 'priya':
                person['reports_to'] = 'ben'
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))
        c.execute("UPDATE humans SET teams='[\"sales\"]' WHERE id='ben'")
        assert H.task_represents(c, 'human:ben', 'human:priya')
    private = make(api, owner='human:priya')
    get(api, 'tasks/' + private['id'], token='ben-test', expected=404)
    assert private['id'] not in {t['id'] for t in get(api, 'tasks', token='ben-test')['tasks']}
    public = make(api, owner='human:priya', private=False)
    assert change(api, public, 'ben-test', due='2027-01-01T00:00:00Z')['due']


def test_archived_parent_cycle_terminates_without_a_task_grant():
    from backend import people
    entries = {'worker': {'reports_to': 'old-a'}, 'old-a': {'reports_to': 'old-b'},
               'old-b': {'reports_to': 'old-a'}}
    assert people.org_parent('bot', 'worker', {'people': []}, entries, {'old-a', 'old-b'}) == ''
