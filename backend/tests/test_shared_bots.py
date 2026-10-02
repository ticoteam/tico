"""Branch creation, permissions, routing and lifecycle across the v2 contract."""
import json
from datetime import datetime, timezone
from backend import shared_bots
from backend.store import H
from backend.tests.test_api import api, get, post, runner, restrict, ready, claim  # noqa: F401
from backend.tests.test_member_bots import botops  # noqa: F401
from clients import hubcli, hubtools, remotecli


def revision(api, bot):
    return get(api, 'bots/' + bot)['revision']


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


def test_follows_model_session_fallback_and_refuses_definition_edits(api):
    share(api)
    computer = runner(api, 'cara')
    branch(api, computer)
    with api.app.state.store.transaction() as c:
        settings = {'runtime': 'fake', 'model': 'team-model', 'harness': 'fake', 'reasoning_effort': 'high',
                    'session': 'task', 'fallback': {'harness': 'fake', 'model': 'fallback-model'}}
        declared = shared_bots.declared(c, 'cpo')
        c.execute('UPDATE bot_config SET config_json=? WHERE bot=?', (json.dumps({**declared, **settings}), 'cpo'))
    assignment = get(api, 'runners/assignments', computer['token'])[0]
    assert all(assignment['config'][key] == value for key, value in settings.items())
    ready(api, computer, ['cpo-cara'])
    post(api, 'chat/cpo', {'text': 'Review this'}, 'cara-test')
    attempt = claim(api, computer, 'cpo-cara')
    assert all(attempt['config'][key] == value for key, value in settings.items())
    post(api, f'attempts/{attempt["id"]}/started', {'thread_id': 'branch-thread'}, computer['token'])
    with api.app.state.store.read() as c:
        session = c.execute("SELECT runtime,model FROM bot_sessions WHERE bot='cpo-cara'").fetchone()
        assert tuple(session) == ('fake', 'team-model')
    post(api, 'bots/cpo-cara/definition', {'description': 'Mine', 'expected_revision': revision(api, 'cpo-cara')}, 'cara-test', expected=409)
    post(api, 'bots/cpo-cara/model', {'model': 'team-model', 'expected_revision': revision(api, 'cpo-cara')}, 'cara-test', expected=409)
    post(api, 'bots/cpo-cara/definition', {'status': 'paused', 'expected_revision': revision(api, 'cpo-cara')}, 'cara-test')
    post(api, 'bots/cpo-cara/definition', {'status': 'active', 'expected_revision': revision(api, 'cpo-cara')}, 'cara-test')


def test_tasks_and_chat_route_only_to_an_active_branch_while_enabled(api):
    share(api)
    branch(api, runner(api, 'cara'))
    mine = post(api, 'tasks', {'owner': 'cpo', 'title': 'Review design', 'body': 'Please review.'}, 'cara-test')
    assert mine['owner'] == 'bot:cpo-cara'
    other = post(api, 'tasks', {'owner': 'cpo', 'title': 'Review another design', 'body': 'Please review.'})
    assert other['owner'] == 'bot:cpo'
    task_chat = post(api, f'tasks/{other["id"]}/chat', {'text': 'Discuss the design'}, 'cara-test')
    assert task_chat['bot'] == 'cpo-cara'
    assert task_chat['message']['to_actor'] == 'bot:cpo-cara'
    chat = post(api, 'chat/cpo', {'text': 'Hello'}, 'cara-test')
    assert chat['to_actor'] == 'bot:cpo-cara'
    conversation = get(api, 'conversations?chat_with=cpo-cara', 'cara-test')['conversations'][0]
    assert set(conversation['participants']) == {'human:cara', 'bot:cpo-cara'}
    chats = get(api, 'conversations?chat_with=cpo', 'cara-test')
    assert chats['conversations'][0]['id'] == chat['conversation_id']
    new = post(api, 'conversations', {'participants': ['bot:cpo'], 'kind': 'chat'}, 'cara-test')
    assert new['id'] == chat['conversation_id']
    share(api, False)
    assert post(api, 'chat/cpo', {'text': 'Original now'}, 'cara-test')['to_actor'] == 'bot:cpo'
    with api.app.state.store.transaction() as c:
        assert H.task_create(c, 'human:cara', 'Direct task', 'Please.', 'cpo')['owner'] == 'bot:cpo'


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


def test_a_planned_branch_starts_when_its_person_adds_a_computer(api):
    share(api)
    made = branch(api)
    assert made['status'] == 'planned' and made['assignment'] is None
    runner(api, 'ana')
    assert get(api, 'bots/cpo-cara', 'cara-test')['state'] == 'planned'
    computer = runner(api, 'cara')
    bot = get(api, 'bots/cpo-cara', 'cara-test')
    assert bot['state'] == 'active' and bot['assignment']['runner_id'] == computer['runner_id']


def test_pending_branch_does_not_block_enrollment_if_branches_are_disabled(api):
    share(api)
    branch(api)
    share(api, False)
    runner(api, 'cara')
    bot = get(api, 'bots/cpo-cara', 'cara-test')
    assert bot['state'] == 'planned' and bot['assignment'] is None


def test_an_owners_new_computer_keeps_the_planned_branch_instead_of_placing_its_original(api):
    share(api)
    branch(api, token='ana-test')
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.template','software-architect') WHERE bot='cpo'")
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('onboarding',?)", (json.dumps({'completed': H.now()}),))
    computer = runner(api, 'ana')
    bot = get(api, 'bots/cpo-ana')
    assert bot['state'] == 'active' and bot['assignment']['runner_id'] == computer['runner_id']
    assert get(api, 'bots/cpo')['assignment'] is None


def test_missing_branch_health_uses_the_original_repository_and_never_creates_an_empty_one(api):
    share(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET repo='Acme/bot-reviewer' WHERE bot='cpo'")
    computer = runner(api, 'cara', 'Branch Mac')
    branch(api, computer)
    post(api, 'runners/heartbeat', {'version': '0.5.4', 'platform': 'test', 'capacity': 4,
        'readiness': {'schema_version': 1, 'bots': {'cpo-cara': {
            'ready': False, 'repository_present': False, 'repository': '/projects/bot-reviewer',
            'problems': ['The original repository does not exist yet on GitHub.']}}}}, computer['token'])
    issue = next(i for i in get(api, 'fleet/check')['issues'] if i['kind'] == 'repository_missing')
    assert issue['bot'] == 'cpo-cara'
    assert issue['fix'] == 'Run `gh repo clone Acme/bot-reviewer /projects/bot-reviewer` on Branch Mac, or ask BotOps'
    summary = next(c['summary'] for c in get(api, 'health')['checks'] if c['id'] == 'repositories')
    assert issue['fix'] in summary


def test_archive_restores_branches_without_losing_their_work_or_assignment(api):
    share(api)
    computer = runner(api, 'cara')
    branch(api, computer)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Keep work', 'body': 'Please.'}, 'cara-test')
    post(api, 'bots/cpo/archive', {'expected_revision': revision(api, 'cpo')})
    with api.app.state.store.read() as c:
        assert H.bot(c, 'cpo-cara')['state'] == 'archived'
        assert H.task(c, task['id'])['owner'] == 'bot:cpo-cara'
        assert c.execute('SELECT runner_id FROM assignments WHERE bot=?', ('cpo-cara',)).fetchone()[0] == computer['runner_id']
    # Restore uses upstream's activation check; the original was already active before archive.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.materialize',true) WHERE bot='cpo'")
    post(api, 'bots/cpo/restore', {})
    assert get(api, 'bots/cpo-cara')['state'] == 'active'


def test_scheduler_skips_branch_routines(api):
    share(api)
    branch(api, runner(api, 'cara'))
    with api.app.state.store.transaction() as c:
        H.schedule_sync(c, H.KEEPER, [{'employee': 'cpo-cara', 'title': 'Never twice', 'cron': '* * * * *', 'playbook': 'Please.'}])
        c.execute("UPDATE schedules SET next_due='2026-01-01T00:00:00Z' WHERE bot='cpo-cara'")
    from backend.scheduler import Scheduler
    result = Scheduler(api.app.state.store, api.app.state.execution).tick(datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert result['fired'] == [] and result['failures'] == []


def test_tool_and_cli_keep_branch_and_copy_separate(api):
    class Client:
        def get(self, path, **query):
            return get(api, path, 'cara-test')
        def post(self, path, body, key=None):
            return post(api, path, body, 'cara-test')
    share(api)
    made = hubtools.bot_branch(Client(), {'bot': 'cpo'})
    assert made['slug'] == 'cpo-cara'
    assert hubtools.bot_branch(Client(), {'bot': 'cpo'})['created'] is False
    parsed = hubcli.parser().parse_args(['bot', 'branch', 'cpo', '--computer', 'Laptop'])
    assert (parsed.fn, parsed.bot, parsed.computer) == ('bot branch', 'cpo', 'Laptop')
    computer = runner(api, 'cara', 'Laptop')
    runner(api, 'ana', 'Laptop')
    assigned = remotecli.bots(Client(), parsed)
    assert assigned['assignment']['runner_id'] == computer['runner_id']
    assert hubtools.BY_NAME['hub_bot_update']['inputSchema']['properties']['shared']['type'] == 'boolean'


def test_botops_creates_a_branch_as_the_requester_and_can_enable_branches(api, botops):
    from backend.tests.test_mcp import call
    from backend.tests.test_member_bots import turn, finish
    request = turn(api, botops, person='ana-test', text='Allow branches on cpo')
    error, changed = call(api, 'hub_bot_update', {'slug': 'cpo', 'shared': True}, request['token'])
    assert not error and changed['shared'] is True, changed
    finish(api, botops, request)
    request = turn(api, botops, person='cara-test', text='Make my branch of cpo')
    error, made = call(api, 'hub_bot_branch', {'bot': 'cpo'}, request['token'])
    assert not error and made['slug'] == 'cpo-cara' and made['operator'] == 'cara', made


def test_repository_references_are_the_branchs_own_and_settings_follow_removal(api):
    share(api)
    branch(api)
    with api.app.state.store.transaction() as c:
        assert H.own_repos(c, 'bot:cpo-cara') == ('emp-cpo',)
        assert H.classify('See emp-cpo/memory/lessons.md', actor='bot:cpo-cara', conn=c) == 'normal'
        assert H.classify('See emp-ops/memory/lessons.md', actor='bot:cpo-cara', conn=c) == 'escape'
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.fallback',json('{\"model\":\"example\"}')) WHERE bot='cpo-cara'")
        assert 'fallback' not in shared_bots.follow(c, 'cpo-cara', shared_bots.declared(c, 'cpo-cara'))


def test_event_routines_stay_with_the_original_and_branches_use_only_their_computers(api):
    from backend import routines
    share(api)
    computer = runner(api, 'cara')
    branch(api, computer)
    other = runner(api, 'ana')
    post(api, 'bots/cpo-cara/assignment', {'runner_id': other['runner_id'], 'expected_generation': 1}, expected=403)
    with api.app.state.store.transaction() as c:
        for bot in ('cpo', 'cpo-cara'):
            c.execute("INSERT INTO schedules(id,bot,cron,title,playbook,event_name) VALUES(?,?,'','Review event','Please.','meeting.ready')", ('event-' + bot, bot))
        fired = routines.emit(c, 'meeting.ready', 'example-meeting', auth=api.app.state.auth)
        assert len(fired) == 1
        assert H.task(c, fired[0])['owner'] == 'bot:cpo'


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
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.materialize',true) WHERE bot='cpo'")
    post(api, 'bots/cpo/restore', {})
    with api.app.state.store.read() as c:
        assert H.bot(c, 'cpo-cara')['state'] == 'paused'
        assert H.bot(c, 'cpo-ana')['state'] == 'archived'


def test_architect_and_reviewer_templates_seed_branches_only_on_new_bots(api):
    for template in ('software-architect', 'pr-reviewer'):
        bot = post(api, 'bots/register', {'slug': 'new-' + template, 'template': template})
        assert bot['shared'] is True
        with api.app.state.store.read() as c:
            assert shared_bots.declared(c, bot['slug'])['session'] == 'task'
    assert get(api, 'bots/cpo')['shared'] is False


def test_a_branch_hangs_where_its_owner_puts_it_on_the_chart_but_still_follows_its_original(api):
    # Chris, 2026-10-02: Arthur's reviewer branches belong at the top of Engineering, not under Arthur.
    share(api)
    branch(api, runner(api, 'cara'))
    post(api, 'bots/cpo-cara/definition', {'reports_to': 'human:ana', 'expected_revision': revision(api, 'cpo-cara')}, 'cara-test')
    with api.app.state.store.read() as c:
        rows = {r['bot']: r['reports_to'] for r in c.execute("SELECT bot,reports_to FROM bot_config WHERE bot IN ('cpo','cpo-cara')")}
    assert rows['cpo-cara'] == 'human:ana' and rows['cpo'] != 'human:ana'
    post(api, 'bots/cpo-cara/definition', {'description': 'Mine', 'expected_revision': revision(api, 'cpo-cara')}, 'cara-test', expected=409)
