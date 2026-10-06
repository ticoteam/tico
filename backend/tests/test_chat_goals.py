"""Native goals share chat privacy, the runner lease and conversation live updates."""

import pytest

from backend.store import H, encode
from backend.tests.test_api import api, assign, claim, get, post, ready, runner, restrict  # noqa: F401
from backend.tests.test_runner import live  # noqa: F401
from runner.hosts.fake import FakeHost
from runner.service import Runner


def setup(api, supported=True, runtime='codex'):
    machine = runner(api)
    assign(api, machine, 'ops')
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE bot_config SET config_json=? WHERE bot=?', (encode({'runtime': runtime}), 'ops'))
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {
        'schema_version': 1, 'bots': {'ops': {'ready': True, 'runtime': runtime, 'goals': supported,
            'commands': [{'name': 'compact', 'help': 'Compact this conversation', 'kind': 'harness'}]}}}}, machine['token'])
    conv = post(api, 'conversations', {'participants': ['bot:ops']})
    return machine, conv['id']


def action(api, cid, name='set', objective=None, **kwargs):
    return post(api, f'conversations/{cid}/goal', {'action': name,
        **({'objective': objective} if objective is not None else {})}, **kwargs)


def test_goal_roundtrip_from_real_http_runner_and_completion_notice(api, live, tmp_path):
    machine, cid = setup(api)
    goal = action(api, cid, objective='Produce the Acme summary')['goal']
    assert goal['status'] == 'active' and goal['set_by'] == 'human:ana'
    assert get(api, f'conversations/{cid}/goal')['supported'] is True
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
        assert hosts[0].prompts[0][1] == '/goal Produce the Acme summary'
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


@pytest.mark.parametrize('objective', [' ', 'x' * 4001])
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
def test_running_goal_pause_resume_and_clear_settle_and_keep_its_thread(api, live, tmp_path):
    import threading
    import time
    machine, cid = setup(api)
    hosts = []
    class HoldingGoalHost(FakeHost):
        def start_goal(self, thread, action, objective, effort=None):
            if action in ('set', 'resume'):
                self.hold_next_turn()
            return super().start_goal(thread, action, objective, effort)
    def factory(*args):
        host = HoldingGoalHost()
        hosts.append(host)
        return host
    service = Runner({'url': live, 'token': machine['token'], 'projects_dir': str(tmp_path)},
                     tmp_path / 'runner', host_factory=factory, push=lambda *a, **k: None)
    errors = []
    def execute(attempt):
        try:
            service.execute(attempt)
        except BaseException as exc:
            errors.append(exc)
    def wait_for_prompt(worker):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (not hosts or not hosts[-1].prompts):
            time.sleep(0.01)
        assert hosts[-1].prompts and worker.is_alive()
    try:
        action(api, cid, objective='Acme summary')
        worker = threading.Thread(target=execute, args=(claim(api, machine),))
        worker.start()
        wait_for_prompt(worker)
        thread = hosts[0].prompts[0][0]
        action(api, cid, 'pause')
        worker.join(timeout=5)
        assert not worker.is_alive() and not errors and hosts[0].interrupts
        service.execute(claim(api, machine))
        assert hosts[1].prompts[0] == (thread, '/goal clear')
        assert get(api, f'conversations/{cid}/goal')['goal']['status'] == 'paused'
        action(api, cid, 'resume')
        worker = threading.Thread(target=execute, args=(claim(api, machine),))
        worker.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (len(hosts) < 3 or not hosts[2].prompts):
            time.sleep(0.01)
        assert hosts[2].prompts[0] == (thread, '/goal Acme summary')
        action(api, cid, 'clear')
        worker.join(timeout=5)
        assert not worker.is_alive() and not errors
        service.execute(claim(api, machine))
        assert hosts[3].prompts[0] == (thread, '/goal clear')
        assert get(api, f'conversations/{cid}/goal')['goal']['status'] == 'cleared'
    finally:
        service.stop.set()
        if 'worker' in locals():
            worker.join(timeout=5)
        service.pool.shutdown()


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
