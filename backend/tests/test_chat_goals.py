"""Native goals share chat privacy, the runner lease and conversation live updates."""

import pytest

from backend.store import H, encode
from backend.tests.test_api import api, assign, claim, get, post, ready, runner, restrict  # noqa: F401
from backend.tests.test_runner import live  # noqa: F401
from runner import goals
from runner.hosts.fake import FakeHost
from runner.service import Runner


def setup(api, supported=True, runtime='codex'):
    machine = runner(api)
    assign(api, machine, 'ops')
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE bot_config SET config_json=? WHERE bot=?', (encode({'runtime': runtime}), 'ops'))
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {
        'schema_version': 1, 'bots': {'ops': {'ready': True, 'runtime': runtime, 'goals': supported,
            'commands': [{'name': 'compact', 'help': 'Compact this conversation', 'kind': 'harness'},
                         {'name': 'docs:publish', 'args': '[target]', 'help': 'Publish the Acme docs', 'kind': 'harness'}]}}}},
         machine['token'])
    conv = post(api, 'conversations', {'participants': ['bot:ops']})
    return machine, conv['id']


def action(api, cid, name='set', objective=None, **kwargs):
    return post(api, f'conversations/{cid}/goal', {'action': name,
        **({'objective': objective} if objective is not None else {})}, **kwargs)


def test_goal_roundtrip_from_real_http_runner_and_completion_notice(api, live, tmp_path):
    machine, cid = setup(api)
    goal = action(api, cid, objective='Produce the Acme summary')['goal']
    assert goal['status'] == 'active' and goal['set_by'] == 'human:ana'
    shown = get(api, f'conversations/{cid}/goal')
    assert shown['supported'] is True
    assert [c['name'] for c in shown['commands'] if c['kind'] == 'harness'] == ['compact', 'docs:publish']   # its repo's own
    assert next(b for b in get(api, 'bots') if b['slug'] == 'ops')['goal_active'] is True
    assert get(api, f'conversations/{cid}/snapshot')['goal'] == goal

    class GoalHost(FakeHost):
        def start_goal(self, thread, action, objective, effort=None):
            turn = super().start_goal(thread, action, objective, effort)
            self.goal_met(thread)
            return turn
    hosts = []
    def factory(*args):
        host = GoalHost(replies=['The summary is ready.'])
        hosts.append(host)
        return host
    service = Runner({'url': live, 'token': machine['token'], 'projects_dir': str(tmp_path)},
                     tmp_path / 'runner', host_factory=factory, push=lambda *a, **k: None)
    try:
        service.execute(claim(api, machine))
        met = get(api, f'conversations/{cid}/goal')['goal']
        assert met['status'] == 'met' and met['ended_at'] and met['note'] == 'The condition holds.'
        assert hosts[0].prompts[0][1] == '/goal ' + goals.native_objective('Produce the Acme summary')
        messages = get(api, f'conversations/{cid}/messages')
        notices = [m for m in messages if m['refs'].get('goal_status') == 'met']
        assert len(notices) == 1 and notices[0]['body'].startswith('Goal met: ')
        assert next(b for b in get(api, 'bots') if b['slug'] == 'ops')['goal_active'] is False
        with api.app.state.store.read() as c:
            assert not c.execute('SELECT 1 FROM jobs WHERE message_id=?', (notices[0]['id'],)).fetchone()
    finally:
        service.pool.shutdown()


def test_pause_resume_edit_clear_and_command_payload(api):
    machine, cid = setup(api)
    goal = action(api, cid, objective='Write a summary')['goal']
    paused = action(api, cid, 'pause')['goal']
    assert paused['status'] == 'paused' and paused['objective'] == goal['objective']
    resumed = action(api, cid, 'resume')['goal']
    assert resumed['status'] == 'active' and resumed['id'] == goal['id']
    edited = action(api, cid, 'edit', 'Write a shorter summary')['goal']
    assert edited['objective'] == 'Write a shorter summary'
    cleared = action(api, cid, 'clear')['goal']
    assert cleared['status'] == 'cleared' and cleared['ended_at']
    attempt = claim(api, machine)
    assert attempt['message']['body'] == '/goal clear'
    assert attempt['message']['refs']['command'] is True
    assert attempt['message']['refs']['goal_action'] == 'clear'
    assert attempt['chat_goal'] == cleared
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE state='cancelled'").fetchone()[0] == 4


@pytest.mark.parametrize('objective', ['x' * 4001])
def test_objective_bounds(api, objective):
    _, cid = setup(api)
    action(api, cid, objective=objective, expected=422)


def test_read_access_is_not_chat_access_and_other_rooms_stay_private(api):
    _, cid = setup(api)
    # A reader can inspect the goal in a conversation they joined but cannot change it.
    with api.app.state.store.transaction() as c:
        conv = H.conversation(c, cid)
        c.execute("UPDATE conversations SET scope='direct',owner_actor=NULL,participants_json=? WHERE id=?",
                  (encode(conv['participants'] + ['human:cara']), cid))
        restrict(c, 'ops', see={'people': ['ana', 'cara']}, read={'people': ['ana', 'cara']}, write={'people': ['ana']})
    assert get(api, f'conversations/{cid}/goal', 'cara-test')['goal'] is None
    action(api, cid, objective='Unauthorized change', token='cara-test', expected=403)
    other = post(api, 'conversations', {'participants': ['bot:ops']})['id']
    get(api, f'conversations/{other}/goal', 'cara-test', expected=403)
    action(api, other, objective='Private room', token='cara-test', expected=403)


def test_old_runners_and_unsupported_harnesses(api):
    machine, cid = setup(api, True, 'gemini')
    assert get(api, f'conversations/{cid}/goal')['supported'] is False
    action(api, cid, objective='A summary', expected=409)
    ready(api, machine, ['ops'])
    assert get(api, f'conversations/{cid}/goal')['supported'] is False


def test_expired_or_superseded_harness_events_cannot_finish_current_goal(api):
    machine, cid = setup(api)
    goal = action(api, cid, objective='First summary')['goal']
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'fake-thread'}, machine['token'])
    newer = action(api, cid, 'edit', 'Second summary')['goal']
    payload = {'goal_id': goal['id'], 'revision': goal['updated_at'], 'status': 'met'}
    post(api, f"attempts/{attempt['id']}/events", {'events': [{'seq': 1, 'kind': 'goal', 'payload': payload}]}, machine['token'])
    assert get(api, f'conversations/{cid}/goal')['goal'] == newer
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE attempts SET lease_until=? WHERE id=?', (H.shift(H.now(), seconds=-1), attempt['id']))
    payload['revision'] = newer['updated_at']
    post(api, f"attempts/{attempt['id']}/events", {'events': [{'seq': 2, 'kind': 'goal', 'payload': payload}]}, machine['token'])
    assert get(api, f'conversations/{cid}/goal')['goal'] == newer


def test_goal_migration_numbers_and_idempotency(api):
    from backend.chat_goals_schema import SCHEMA
    from backend import hubdb
    with api.app.state.store.transaction() as c:
        assert c.execute('SELECT 1 FROM cloud_migrations WHERE version=49').fetchone()
        H._apply(c, SCHEMA)
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='chat_goals_current'").fetchone()
    assert hubdb.MIGRATIONS[15] == SCHEMA


@pytest.mark.slow
def test_superseded_leased_control_never_sets_the_old_native_goal(api, live, tmp_path):
    machine, cid = setup(api)
    action(api, cid, objective='Old Acme summary')
    leased = claim(api, machine)
    newer = action(api, cid, 'edit', 'New Acme summary')['goal']
    host = FakeHost()
    service = Runner({'url': live, 'token': machine['token'], 'projects_dir': str(tmp_path)},
                     tmp_path / 'runner', host_factory=lambda *a: host, push=lambda *a, **k: None)
    try:
        service.execute(leased)
        assert host.prompts == []
        assert get(api, f'conversations/{cid}/goal')['goal'] == newer
        assert claim(api, machine)['message']['body'] == '/goal New Acme summary'
    finally:
        service.pool.shutdown()


def test_resume_no_goal_snapshot_does_not_cancel_new_goal(api, live, tmp_path):
    """Reproduce cloud receipts: cleared resume snapshot, active set, work, met."""
    machine, cid = setup(api)
    goal = action(api, cid, objective='Produce the Acme summary')['goal']
    class SnapshotHost(FakeHost):
        def request(self, method, params):
            assert method == 'thread/goal/get'
            assert params == {'threadId': self.prompts[0][0]}
            return {'goal': {'objective': goal['objective'], 'status': 'active'}}
        def start_goal(self, thread, action, objective, effort=None):
            self.emit('goal', thread, None, status='cleared', note='')
            turn = super().start_goal(thread, action, objective, effort)
            self.goal_met(thread)
            return turn
    service = Runner({'url': live, 'token': machine['token'], 'projects_dir': str(tmp_path)},
                     tmp_path / 'runner', host_factory=lambda *args: SnapshotHost(replies=['Done.']),
                     push=lambda *a, **k: None)
    try:
        service.execute(claim(api, machine))
        actual = get(api, f'conversations/{cid}/goal')['goal']
        assert actual['status'] == 'met'
        notices = get(api, f'conversations/{cid}/messages')
        assert not [m for m in notices if m['refs'].get('goal_status') == 'stopped']
        assert len([m for m in notices if m['refs'].get('goal_status') == 'met']) == 1
    finally:
        service.pool.shutdown()


def test_real_native_clear_still_stops_active_goal(api, live, tmp_path):
    machine, cid = setup(api)
    action(api, cid, objective='Acme summary')
    class ClearHost(FakeHost):
        def request(self, method, params):
            assert method == 'thread/goal/get'
            return {'goal': None}
        def start_goal(self, thread, action, objective, effort=None):
            self.emit('goal', thread, None, status='active', objective=objective)
            self.emit('goal', thread, None, status='cleared', note='')
            return self.start_turn(thread, objective, effort)
    service = Runner({'url': live, 'token': machine['token'], 'projects_dir': str(tmp_path)},
                     tmp_path / 'runner', host_factory=lambda *args: ClearHost(replies=['Stopped.']),
                     push=lambda *a, **k: None)
    try:
        service.execute(claim(api, machine))
        actual = get(api, f'conversations/{cid}/goal')['goal']
        assert actual['status'] == 'stopped' and actual['note'] == 'The harness cleared the goal'
    finally:
        service.pool.shutdown()


def test_a_clear_after_met_is_not_checked_again(api, live, tmp_path):
    """Once the goal is met the run stops asking the harness, so a later clear is not read back as a second "met"."""
    machine, cid = setup(api)
    action(api, cid, objective='Acme summary')
    asked = []
    class MetThenClearHost(FakeHost):
        def request(self, method, params):
            asked.append(method)
            return {'goal': {'objective': 'Acme summary', 'status': 'complete'}}
        def start_goal(self, thread, action, objective, effort=None):
            turn = super().start_goal(thread, action, objective, effort)
            self.goal_met(thread)
            self.emit('goal', thread, None, status='cleared', note='')
            return turn
    service = Runner({'url': live, 'token': machine['token'], 'projects_dir': str(tmp_path)},
                     tmp_path / 'runner', host_factory=lambda *args: MetThenClearHost(replies=['Done.']),
                     push=lambda *a, **k: None)
    try:
        service.execute(claim(api, machine))
        assert asked == []
        notices = get(api, f'conversations/{cid}/messages')
        assert len([m for m in notices if m['refs'].get('goal_status') == 'met']) == 1
    finally:
        service.pool.shutdown()
