"""Branch creation, permissions, routing and lifecycle across the v2 contract."""
import json
from backend import shared_bots
from backend.store import H
from backend.tests.test_api import api, get, post, runner, restrict, ready, claim  # noqa: F401


def revision(api, bot):
    return get(api, 'bots/' + bot)['revision']


def bot_id(api, slug):
    """The id a bot is stored under: actors, readiness reports, claims and most tables use it, not the slug."""
    with api.app.state.store.read() as c:
        return H.resolve_bot(c, slug)['id']


def share(api, enabled=True):
    post(api, 'bots/cpo/definition', {'shared': enabled, 'expected_revision': revision(api, 'cpo')})


def branch(api, computer=None, token='cara-test', bot='cpo', expected=200):
    return post(api, f'bots/{bot}/copies', {'runner_id': computer['runner_id']} if computer else {}, token, expected=expected)


def test_creation_is_personal_idempotent_and_requires_read_and_own_computer(api):
    computer = runner(api, 'cara')
    branch(api, computer, expected=409)
    share(api)
    made = branch(api, computer)
    assert (made['slug'], made['shared_from'], made['operator'], made['created']) == ('cpo-cara', 'cpo', 'cara', True)
    assert (made['repo'], made['reports_to'], made['thread_mode'], made['status']) == ('emp-cpo', 'human:cara', 'personal', 'active')
    assert made['assignment']['runner_id'] == computer['runner_id']
    assert branch(api, computer)['created'] is False
    branch(api, token='ben-test', expected=409)
    branch(api, runner(api, 'ana'), expected=403)
    branch(api, bot='cpo-cara', expected=422)
    listed = get(api, 'bots/cpo/branches', 'cara-test')
    assert listed['original'] == 'cpo' and listed['shared'] is True
    assert [b['slug'] for b in listed['branches']] == ['cpo-cara']
    with api.app.state.store.transaction() as c:
        restrict(c, 'cpo', see={'everyone': True}, read={'people': ['ana']}, write={'people': ['ana']})
    branch(api, computer, expected=403)


def test_a_saved_config_with_the_removed_rotation_settings_still_loads_and_is_not_followed(api):
    share(api)
    computer = runner(api, 'cara')
    branch(api, computer)
    with api.app.state.store.transaction() as c:
        declared = shared_bots.declared(c, 'cpo')
        c.execute('UPDATE bot_config SET config=? WHERE bot=?', (json.dumps(
            {**declared, 'model': 'team-model', 'rotate_at': 150000, 'rotate_after_turns': 3}), 'cpo'))
    assert get(api, 'bots/cpo')
    config = get(api, 'runners/assignments', computer['token'])[0]['config']
    assert config['model'] == 'team-model' and 'rotate_at' not in config and 'rotate_after_turns' not in config


def test_follows_model_session_fallback_and_refuses_definition_edits(api):
    share(api)
    computer = runner(api, 'cara')
    branch(api, computer)
    with api.app.state.store.transaction() as c:
        settings = {'runtime': 'fake', 'model': 'team-model', 'harness': 'fake', 'reasoning_effort': 'high',
                    'session': 'task', 'fallback': {'harness': 'fake', 'model': 'fallback-model'}}
        declared = shared_bots.declared(c, 'cpo')
        c.execute('UPDATE bot_config SET config=? WHERE bot=?', (json.dumps({**declared, **settings}), 'cpo'))
    assignment = get(api, 'runners/assignments', computer['token'])[0]
    assert all(assignment['config'][key] == value for key, value in settings.items())
    branch_id = bot_id(api, 'cpo-cara')
    ready(api, computer, [branch_id])
    post(api, 'chat/cpo', {'text': 'Review this'}, 'cara-test')
    attempt = claim(api, computer, branch_id)
    assert all(attempt['config'][key] == value for key, value in settings.items())
    post(api, f'attempts/{attempt["id"]}/started', {'thread_id': 'branch-thread'}, computer['token'])
    with api.app.state.store.read() as c:
        session = c.execute("SELECT runtime,model FROM bot_sessions WHERE bot=?", (branch_id,)).fetchone()
        assert tuple(session) == ('fake', 'team-model')
    post(api, 'bots/cpo-cara/definition', {'description': 'Mine', 'expected_revision': revision(api, 'cpo-cara')}, 'cara-test', expected=409)
    post(api, 'bots/cpo-cara/model', {'model': 'team-model', 'expected_revision': revision(api, 'cpo-cara')}, 'cara-test', expected=409)
    post(api, 'bots/cpo-cara/definition', {'status': 'paused', 'expected_revision': revision(api, 'cpo-cara')}, 'cara-test')
    post(api, 'bots/cpo-cara/definition', {'status': 'active', 'expected_revision': revision(api, 'cpo-cara')}, 'cara-test')


def test_tasks_and_chat_route_only_to_an_active_branch_while_enabled(api):
    share(api)
    branch(api, runner(api, 'cara'))
    branch_actor = H.bot_actor(bot_id(api, 'cpo-cara'))
    mine = post(api, 'tasks', {'owner_id': 'cpo', 'title': 'Review design', 'body': 'Please review.'}, 'cara-test')
    assert mine['owner_id'] == branch_actor
    other = post(api, 'tasks', {'owner_id': 'cpo', 'title': 'Review another design', 'body': 'Please review.'})
    assert other['owner_id'] == 'bot:cpo'
    task_chat = post(api, f'tasks/{other["id"]}/chat', {'text': 'Discuss the design'}, 'cara-test')
    assert task_chat['bot'] == bot_id(api, 'cpo-cara')
    assert task_chat['message']['recipient_id'] == branch_actor
    chat = post(api, 'chat/cpo', {'text': 'Hello'}, 'cara-test')
    assert chat['recipient_id'] == branch_actor
    conversation = get(api, 'conversations?chat_with=cpo-cara', 'cara-test')['conversations'][0]
    assert set(conversation['participants']) == {'human:cara', branch_actor}
    chats = get(api, 'conversations?chat_with=cpo', 'cara-test')
    assert chats['conversations'][0]['id'] == chat['conversation_id']
    new = post(api, 'conversations', {'participants': ['bot:cpo'], 'kind': 'chat'}, 'cara-test')
    assert new['id'] == chat['conversation_id']
    share(api, False)
    assert post(api, 'chat/cpo', {'text': 'Original now'}, 'cara-test')['recipient_id'] == 'bot:cpo'
    with api.app.state.store.transaction() as c:
        assert H.task_create(c, 'human:cara', 'Direct task', 'Please.', 'cpo')['owner_id'] == 'bot:cpo'


def test_refuses_a_computer_already_running_original_or_a_sibling_branch(api):
    share(api)
    computer = runner(api, 'cara')
    with api.app.state.store.transaction() as c:
        c.execute('INSERT INTO assignments VALUES(?,?,1,?,?)', ('cpo', computer['runner_id'], H.now(), 'human:ana'))
    assert branch(api, computer, expected=409)['error']['code'] == 'shared_runner'
    with api.app.state.store.transaction() as c:
        c.execute('DELETE FROM assignments WHERE bot=?', ('cpo',))
    branch(api, computer)
    branch(api, token='ana-test')
    post(api, 'bots/cpo-ana/assignment', {'runner_id': computer['runner_id'], 'expected_generation': 0}, expected=409)
    post(api, 'bots/cpo/assignment', {'runner_id': computer['runner_id'], 'expected_generation': 0}, expected=409)


def test_archive_restores_branches_without_losing_their_work_or_assignment(api):
    share(api)
    computer = runner(api, 'cara')
    branch(api, computer)
    task = post(api, 'tasks', {'owner_id': 'cpo', 'title': 'Keep work', 'body': 'Please.'}, 'cara-test')
    branch_id = bot_id(api, 'cpo-cara')
    post(api, 'bots/cpo/archive', {'expected_revision': revision(api, 'cpo')})
    with api.app.state.store.read() as c:
        assert H.bot(c, branch_id)['state'] == 'archived'
        assert H.task(c, task['id'])['owner_id'] == H.bot_actor(branch_id)
        assert c.execute('SELECT runner_id FROM assignments WHERE bot=?', (branch_id,)).fetchone()[0] == computer['runner_id']
    # Restore uses upstream's activation check; the original was already active before archive.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.materialize',true) WHERE bot='cpo'")
    post(api, 'bots/cpo/restore', {})
    assert get(api, 'bots/cpo-cara')['state'] == 'active'


def test_original_archive_preserves_paused_and_independently_archived_branches(api):
    share(api)
    branch(api, runner(api, 'cara'))
    branch(api, token='ana-test')
    post(api, 'bots/cpo-cara/definition', {'status': 'paused', 'expected_revision': revision(api, 'cpo-cara')}, 'cara-test')
    post(api, 'bots/cpo-ana/archive', {'expected_revision': revision(api, 'cpo-ana')})
    post(api, 'bots/cpo/archive', {'expected_revision': revision(api, 'cpo')})
    post(api, 'bots/cpo-cara/definition', {'status': 'active', 'expected_revision': revision(api, 'cpo-cara')}, 'cara-test', expected=409)
    post(api, 'bots/cpo-cara/status', {'state': 'active'}, 'cara-test', expected=409)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.materialize',true) WHERE bot='cpo'")
    post(api, 'bots/cpo/restore', {})
    cara, ana = bot_id(api, 'cpo-cara'), bot_id(api, 'cpo-ana')
    with api.app.state.store.read() as c:
        assert H.bot(c, cara)['state'] == 'paused'
        assert H.bot(c, ana)['state'] == 'archived'


def test_archiving_the_original_closes_a_parked_branch_setup_reminder(api):
    """A branch archived with its original no longer needs its setup finished, so its reminder leaves Needs you."""
    share(api)
    branch(api, runner(api, 'cara'))
    branch_id = bot_id(api, 'cpo-cara')
    with api.app.state.store.transaction() as c:
        reminder = H._setup_reminder(c, branch_id)
    post(api, 'bots/cpo/archive', {'expected_revision': revision(api, 'cpo')})
    with api.app.state.store.read() as c:
        assert H.task(c, reminder['id'])['status'] == 'done'
        assert 'was archived' in H.task(c, reminder['id'])['note']


def test_name_only_roster_marks_branches_without_exposing_the_original(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config, '$.shared_from', 'finance') WHERE bot='cpo'")
        restrict(c, 'cpo', see={'everyone': True}, read={'people': ['ben']}, write={'people': ['ben']})
        restrict(c, 'finance', people=['ana'])
    response = api.get('/api/employees', headers={'Authorization': 'Bearer cara-test'})
    assert response.status_code == 200
    rows = {row['name']: row for row in response.json()}
    assert 'finance' not in rows
    branch_row = rows['cpo']
    assert branch_row['my_access']['read'] is False
    assert branch_row['is_branch'] is True and branch_row['owner_id'] == 'ben'
    assert not {'shared_from', 'repo', 'schedules', 'model'} & branch_row.keys()
