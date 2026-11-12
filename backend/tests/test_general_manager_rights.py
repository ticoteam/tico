"""A manager inherits a party's task rights, with current chart and original attribution. The route checks run on
the app; what the task calls allow (close, reopen, snooze, done, an ask) runs on the company alone."""

import json

import pytest

from backend import hubdb as H
from backend.tests.helpers import engine as X
from backend.tests.helpers.tasks_board import api, bot_token, get, headers, post  # noqa: F401
from backend import bot_settings as BS


def _manager(api, kind):
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
    token = bot_token(api, 'cpo') if kind == 'bot' else 'ben-test'
    actor = 'bot:cpo' if kind == 'bot' else 'human:ben'
    return token, actor


@pytest.fixture(params=['bot', 'person'])
def manager(api, request):
    """Both kinds of manager, for the paths where a person's manager differs from a bot's."""
    return _manager(api, request.param)


@pytest.fixture
def bot_manager(api):
    """The bot manager alone: the paths a person manager takes are the same code."""
    return _manager(api, 'bot')


def make(api, *, owner='bot:ops', requester='human:ana', private=True, **kwargs):
    with api.app.state.store.transaction() as c:
        number = c.execute('SELECT count(*) FROM tasks').fetchone()[0]
        return H.task_create(
            c,
            requester,
            f'Check the permission packet {number}',
            'Review the packet.',
            owner,
            private=private,
            lint=False,
            **kwargs,
        )


def change(api, task, token, **fields):
    current = get(api, 'tasks/' + task['id'], token=token)['task']
    return post(api, 'tasks/' + task['id'], {'version': current['version'], **fields}, token=token)


def test_a_manager_of_either_kind_changes_and_labels_but_gains_no_more(api, manager, subtests):
    """One install per manager kind: every field, the outsider's refusal, and labels with the mover and publish gates.
    Each check makes its own tasks."""
    for check in (
        _manager_updates_every_field_and_is_named_in_history,
        _nonmanager_unchanged_and_refusal_names_managers,
        _manager_classifies_labels_but_keeps_mover_and_publish_gates,
    ):
        with subtests.test(check.__name__):
            check(api, manager)


def _manager_updates_every_field_and_is_named_in_history(api, manager):
    token, actor = manager
    for fields, field in [
        ({'due': '2027-01-01T00:00:00Z'}, 'due_at'),
        ({'title': 'Check the revised packet'}, 'title'),
        ({'body': 'Review the revised packet.'}, 'body'),
        ({'rank': 12}, 'rank'),
    ]:
        task = make(api)
        changed = change(api, task, token, **fields)
        assert changed[field] != task.get(field), field
        with api.app.state.store.read() as c:
            events = [e for e in H.task_history(c, task['id']) if e['field'] == {'due_at': 'due'}.get(field, field)]
            if field != 'rank':
                assert events[-1]['actor'] == actor, field
            assert (
                c.execute(
                    "SELECT actor FROM events WHERE action='task.update' AND target=? ORDER BY rowid DESC LIMIT 1",
                    (task['id'],),
                ).fetchone()[0]
                == actor
            ), field


def test_a_bot_manager_acts_for_its_reports_on_their_tasks(api, bot_manager, subtests):
    """One install: the bot manager's comment, link and run-now rights on its reports' tasks, and the comment rights
    it does not get. Each check makes its own tasks, in file order."""
    bot_token(api, 'ops')  # the report's own lease, for the run-now check
    checks = [
        (_manager_comment_wakes_owner_without_mention, ()),
        (_requester_manager_comment_wakes_owner_with_requester_permissions, ()),
        (_manager_links_and_unlinks, ()),
        (_manager_cannot_edit_or_delete_another_authors_comment, ()),
    ]
    for check, extra in checks:
        saved = _save_chart(api)
        try:
            with subtests.test(check.__name__, args=extra):
                check(api, bot_manager, *extra)
        finally:
            _restore_chart(api, saved)  # each check meets the fixture's chart: ops -> cpo -> Ben, no middle layer


def _save_chart(api):
    """Every bot's reporting line and ops' declaration, as the check found them."""
    with api.app.state.store.read() as c:
        return dict(c.execute('SELECT bot, reports_to FROM bot_config').fetchall()), BS.read(c, 'ops')


def _restore_chart(api, saved):
    """Put back what `_save_chart` read. A bot a check added (the comment check's `middle`) reports to nobody after
    it, so no fixture bot's chain runs through it."""
    lines, ops = saved
    with api.app.state.store.transaction() as c:
        for bot, reports_to in c.execute('SELECT bot, reports_to FROM bot_config').fetchall():
            if lines.get(bot) != reports_to:
                c.execute('UPDATE bot_config SET reports_to=? WHERE bot=?', (lines.get(bot), bot))
        BS.write(c, 'ops', ops)


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


def _manager_comment_wakes_owner_without_mention(api, bot_manager):
    token, actor = bot_manager
    task = make(api)
    # The manager is not the direct boss: add a second bot layer for bot and human cases.
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {'middle': {'name': 'Middle', 'runtime': 'fake', 'status': 'active'}}, {})
        c.execute("INSERT INTO bot_config(bot,reports_to,owner_id) VALUES('middle','cpo','ana')")
        BS.write(c, 'middle', {})
        c.execute("UPDATE bot_config SET reports_to='middle' WHERE bot='ops'")
        config = BS.read(c, 'ops')
        config['bot_contact'] = 'tasks'
        BS.write(c, 'ops', config)
    result = post(api, 'tasks/' + task['id'] + '/comments', {'text': 'Please continue the work.'}, token=token)
    assert result['woke'], result
    assert result['comment']['sender_id'] == actor
    with api.app.state.store.read() as c:
        message = H.message(c, result['comment']['id'])
        assert message['recipient_id'] == 'bot:ops' and not message['refs'].get('quiet')
        assert c.execute('SELECT 1 FROM jobs WHERE message_id=?', (message['id'],)).fetchone()


def _requester_manager_comment_wakes_owner_with_requester_permissions(api, bot_manager):
    token, actor = bot_manager
    task = make(api, owner='bot:cmo', requester='bot:ops')
    result = post(api, 'tasks/' + task['id'] + '/comments', {'text': 'Please continue the packet.'}, token=token)
    assert result['woke'] and result['comment']['sender_id'] == actor
    with api.app.state.store.read() as c:
        message = H.message(c, result['comment']['id'])
        assert message['recipient_id'] == 'bot:cmo'
        assert c.execute('SELECT 1 FROM jobs WHERE message_id=?', (message['id'],)).fetchone()


def _manager_links_and_unlinks(api, bot_manager):
    token, actor = bot_manager
    task = make(api)
    post(api, 'tasks/' + task['id'] + '/links', {'url': 'https://example.com/report'}, token=token)
    with api.app.state.store.read() as c:
        lid = H.task_links(c, task['id'])[0]['id']
    response = api.delete('/api/v2/tasks/' + task['id'] + '/links/' + lid, headers=headers(token))
    assert response.status_code == 200, response.text
    with api.app.state.store.read() as c:
        assert H.task_links(c, task['id']) == []
        assert all(e['actor'] == actor for e in H.task_history(c, task['id']) if e['field'] == 'link')


def _nonmanager_unchanged_and_refusal_names_managers(api, manager):
    _, _ = manager
    outsider = bot_token(api, 'cmo')
    private = make(api)
    get(api, 'tasks/' + private['id'], token=outsider, expected=404)
    task = make(api, private=False)
    refusal = post(
        api, 'tasks/' + task['id'], {'version': task['version'], 'title': 'Renamed'}, token=outsider, expected=403
    )
    assert 'managers' in str(refusal) and 'owner' in str(refusal)
    post(api, 'tasks/' + task['id'] + '/comments', {'text': 'Unrelated comment.'}, token=outsider, expected=403)


def _manager_cannot_edit_or_delete_another_authors_comment(api, bot_manager):
    token, _ = bot_manager
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


def _manager_classifies_labels_but_keeps_mover_and_publish_gates(api, manager):
    token, _ = manager
    task = make(api)
    with api.app.state.store.transaction() as c:
        H.tag_create(c, H.KEEPER, 'manager-check', label='Manager check')
    labelled = change(api, task, token, labels=['manager-check'])
    assert labelled['labels'] == ['manager-check']
    for fields in ({'number': 999}, {'lane': 'company'}, {'private': False}):
        current = get(api, 'tasks/' + task['id'], token=token)['task']
        response = api.post(
            '/api/v2/tasks/' + task['id'], json={'version': current['version'], **fields}, headers=headers(token)
        )
        assert response.status_code in (403, 422), response.text


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
    assert change(api, public, 'ben-test', due='2027-01-01T00:00:00Z')['due_at']


def test_archived_parent_cycle_terminates_without_a_task_grant():
    from backend import people

    entries = {'worker': {'reports_to': 'old-a'}, 'old-a': {'reports_to': 'old-b'}, 'old-b': {'reports_to': 'old-a'}}
    assert people.org_parent('bot', 'worker', {'people': []}, entries, {'old-a', 'old-b'}) == ''


# ----------------------------------------------------------------------------- the task calls, on the company alone
ANA = "human:ana"


@pytest.fixture(params=["bot", "person"])
def chart(company, request):
    """ops reports to cpo, and cpo to Cara, who moves no task of her own: a manager of either kind above ops."""
    with company.transaction() as c:
        X.bot(c, "cmo")
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='ops'")
        c.execute("UPDATE bot_config SET reports_to='human:cara' WHERE bot='cpo'")
        yield c, "bot:cpo" if request.param == "bot" else "human:cara"


def _task(c, requester, owner, title):
    return H.task_create(c, requester, title, "Review the packet.", owner, private=False, lint=False)["id"]


def _version(c, task_id):
    return H.task(c, task_id)["version"]


def test_a_manager_closes_and_reopens_its_reports_request_and_only_a_person_closes_a_persons(chart):
    c, manager = chart
    requested = _task(c, "bot:ops", "bot:cmo", "Check the permission packet")
    closed = H.task_close(c, manager, requested, "Not needed now.", _version(c, requested))
    assert closed["status"] == "closed" and closed["closed_by_id"] == manager
    reopened = H.task_move(c, manager, requested, H.TASK_REOPEN, "Needed after all.", _version(c, requested))
    assert reopened["status"] == "open"
    owned = _task(c, ANA, "bot:ops", "Check the revised packet")
    if manager.startswith("bot:"):
        # The owner bot cannot close what a person asked for, so neither can its bot manager: it asks that person.
        with pytest.raises(H.Refused) as refused:
            H.task_close(c, manager, owned, "Not needed now.", _version(c, owned))
        assert refused.value.rule == "task_needs_person"
        # A person's close cannot be undone by a bot.
        H.task_close(c, ANA, requested, "Ana closed it.")
        with pytest.raises(H.Refused) as refused:
            H.task_move(c, manager, requested, H.TASK_REOPEN, "Again.", _version(c, requested))
        assert refused.value.rule == "task_needs_person"
    else:
        assert H.task_close(c, manager, owned, "Not needed now.")["status"] == "closed"


def test_a_manager_snoozes_and_unsnoozes_its_reports_task(chart):
    c, manager = chart
    task = _task(c, ANA, "bot:ops", "Check the permission packet")
    snoozed = H.task_snooze(c, manager, task, "After the audit.", "2027-01-01T00:00:00Z", _version(c, task))
    assert snoozed["snooze"]["by_id"] == manager
    assert H.task_unsnooze(c, manager, task, "The audit is done.")["snooze"] is None


def test_an_outsider_bot_works_none_of_it(chart):
    c, _ = chart
    task = _task(c, ANA, "bot:ops", "Check the permission packet")
    with pytest.raises(H.Refused, match="worked by its owner"):
        H.task_close(c, "bot:cmo", task, "Not mine.", _version(c, task))


def test_a_person_manager_says_its_reports_task_is_done(company):
    with company.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:cara' WHERE bot='ops'")
        task = _task(c, ANA, "bot:ops", "Check the permission packet")
        done = H.task_done(c, "human:cara", task, "The packet is ready.")
        assert done["status"] == "done"
        assert (
            c.execute(
                "SELECT a.by_id FROM answers a JOIN visits v ON v.id = a.visit_id WHERE v.task_id=? AND a.answer='done'",
                (task,),
            ).fetchone()[0]
            == "human:cara"
        )


def test_managing_a_task_never_answers_an_ok_that_names_someone_else(chart):
    c, manager = chart
    task = _task(c, ANA, "bot:ops", "Check the permission packet")
    asked = H.task_ask(c, "bot:ops", task, ANA, "OK to send the packet?", kind="ok", seen_version=_version(c, task))
    with pytest.raises(H.Refused) as refused:
        H.task_answer(c, manager, task, asked["requirement_id"], "ok", None, _version(c, task))
    assert refused.value.rule == "task_requirement"
