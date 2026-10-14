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


def test_a_task_goal_runs_natively_beside_the_task_prompt_and_meeting_it_marks_the_task_done(api, live, tmp_path):
    machine = runner(api)
    assign(api, machine, 'ops')
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE bot_config SET config_json=? WHERE bot=?', (encode({'runtime': 'codex', 'task_goal': 'on'}), 'ops'))
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test',
         'capabilities': ['task_lanes_v2', 'session_roots_v1', 'task_goals_v1'], 'readiness': {
         'schema_version': 1, 'bots': {'ops': {'ready': True, 'runtime': 'codex', 'goals': True}}}}, machine['token'])
    task = post(api, 'tasks', {'owner': 'ops', 'title': 'Publish the Acme summary', 'body': 'For the board.',
                               'acceptance_criteria': ['The summary is in Docs']})
    attempt = claim(api, machine)
    goal = attempt['chat_goal']
    assert goal['status'] == 'active' and goal['set_by'] == 'keeper' and attempt['task']['goal']['goal_id'] == goal['id']
    assert goal['conversation_id'] == task['conversation_id'] and 'The summary is in Docs' in goal['objective']

    class GoalHost(FakeHost):
        def start_goal_turn(self, thread, objective, text, effort=None):
            turn = super().start_goal_turn(thread, objective, text, effort)
            self.goal_met(thread, 'The summary is in Docs.')
            return turn
    hosts = []
    def factory(*args):
        hosts.append(GoalHost(replies=['Published.']))
        return hosts[-1]
    service = Runner({'url': live, 'token': machine['token'], 'projects_dir': str(tmp_path)},
                     tmp_path / 'runner', host_factory=factory, push=lambda *a, **k: None)
    try:
        service.execute(attempt)
    finally:
        service.pool.shutdown()
    (thread, objective, text), = hosts[0].goal_turns
    assert objective == goals.native_objective(goal['objective']) and 'Publish the Acme summary' in text
    assert get(api, f"conversations/{task['conversation_id']}/goal")['goal']['status'] == 'met'
    done = get(api, 'tasks/' + task['id'])['task']
    assert done['status'] == 'done' and done['note'] == 'The summary is in Docs.'


def task_goal_world(api):
    machine = runner(api)
    assign(api, machine, 'ops')
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE bot_config SET config_json=? WHERE bot=?', (encode({'runtime': 'codex', 'task_goal': 'on'}), 'ops'))
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test',
         'capabilities': ['task_lanes_v2', 'session_roots_v1', 'task_goals_v1'], 'readiness': {
         'schema_version': 1, 'bots': {'ops': {'ready': True, 'runtime': 'codex', 'goals': True}}}}, machine['token'])
    return machine, post(api, 'tasks', {'owner': 'ops', 'title': 'Publish the Acme summary', 'body': 'For the board.'})


def test_turning_a_task_goal_off_clears_it_natively_and_a_persons_goal_never_completes_the_task(api, live, tmp_path):
    from backend import chat_goals
    machine, task = task_goal_world(api)
    first = claim(api, machine)
    assert first['chat_goal']['status'] == 'active'
    class PausingHost(FakeHost):         # Codex blocked on a person: the goal pauses and the turn ends
        def start_goal_turn(self, thread, objective, text, effort=None):
            turn = super().start_goal_turn(thread, objective, text, effort)
            self.emit('goal', thread, None, status='paused', note='Waiting on: Ana')
            return turn
    service = Runner({'url': live, 'token': machine['token'], 'projects_dir': str(tmp_path)},
                     tmp_path / 'runner', host_factory=lambda *a: PausingHost(replies=['Working on it.']), push=lambda *a, **k: None)
    try:
        service.execute(first)
    finally:
        service.pool.shutdown()
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE bot_config SET config_json=? WHERE bot=?', (encode({'runtime': 'codex'}), 'ops'))
        H.task_comment(c, 'human:ana', task['id'], 'Use the new numbers.')
    second = claim(api, machine)
    assert second['chat_goal']['status'] == 'cleared' and 'goal' not in second['task']
    with api.app.state.store.read() as c:
        clear = c.execute("SELECT m.body,m.refs_json FROM jobs j JOIN messages m ON m.id=j.message_id "
                          "WHERE j.state='queued' AND m.conversation_id=?", (task['conversation_id'],)).fetchall()
    assert [(b, '"goal_action": "clear"' in r) for b, r in clear] == [('/goal clear', True)]
    # A goal a person set in the task's room being met leaves the task as it is.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE chat_goals SET set_by='human:ana',objective='Summarize current progress',status='active' "
                  "WHERE conversation_id=?", (task['conversation_id'],))
        goal = chat_goals.current(c, task['conversation_id'])
        chat_goals.task_goal_ended(c, goal, task['id'], 'met', 'Summarized.')
        assert H.task(c, task['id'])['status'] != 'done'


def run_task_goal(api, live, tmp_path, machine, judged, reply, during=None):
    class JudgedHost(FakeHost):
        def start_goal_turn(self, thread, objective, text, effort=None):
            turn = super().start_goal_turn(thread, objective, text, effort)
            if during:
                during()
            self.goal_met(thread, judged)
            return turn
    service = Runner({'url': live, 'token': machine['token'], 'projects_dir': str(tmp_path)}, tmp_path / 'runner',
                     host_factory=lambda *a: JudgedHost(replies=[reply]), push=lambda *a, **k: None)
    try:
        service.execute(claim(api, machine))
    finally:
        service.pool.shutdown()


def test_a_done_tasks_goal_is_met_even_when_the_bot_signs_off_waiting(api, live, tmp_path):
    machine, task = task_goal_world(api)
    def mark_done():                      # the bot marks its task done, then says what it waits on elsewhere
        with api.app.state.store.transaction() as c:
            H.task_update(c, 'bot:ops', task['id'], status='done', note='Published.')
    run_task_goal(api, live, tmp_path, machine, 'The task is done.',
                  'Published.\n\nWaiting on: Ana to "close" it.', during=mark_done)
    goal = get(api, f"conversations/{task['conversation_id']}/goal")['goal']
    assert goal['status'] == 'met' and goal['note'] == 'Waiting on: Ana to "close" it.'
    assert get(api, 'tasks/' + task['id'])['task']['status'] == 'done'


def test_an_open_tasks_goal_pauses_on_the_bots_whole_waiting_line(api, live, tmp_path):
    machine, task = task_goal_world(api)
    run_task_goal(api, live, tmp_path, machine, 'It says \\"Waiting on: Ana\'s \\"yes',
                  'Drafted.\n\nWaiting on: Ana\'s "yes" on the draft.')
    goal = get(api, f"conversations/{task['conversation_id']}/goal")['goal']
    assert goal['status'] == 'paused' and goal['note'] == 'Waiting on: Ana\'s "yes" on the draft.'
    assert get(api, 'tasks/' + task['id'])['task']['status'] not in ('done', 'closed')


def test_a_persons_clear_of_a_task_goal_stands_while_task_goals_stay_on(api):
    from backend.tests.test_member_bots import finish
    machine, task = task_goal_world(api)
    first = claim(api, machine)
    finish(api, machine, first)
    cleared = action(api, task['conversation_id'], 'clear')['goal']
    assert cleared['status'] == 'cleared' and cleared['set_by'] == 'human:ana'
    control = claim(api, machine)                        # the "/goal clear" control, with task goals still on
    assert control['message']['refs']['goal_action'] == 'clear'
    goal = get(api, f"conversations/{task['conversation_id']}/goal")['goal']
    assert goal['id'] == cleared['id'] and goal['status'] == 'cleared' and control['chat_goal']['id'] == cleared['id']


def test_a_persons_edit_of_a_task_goal_survives_task_goals_turning_off(api):
    from backend.tests.test_member_bots import finish
    machine, task = task_goal_world(api)
    finish(api, machine, claim(api, machine))
    edited = action(api, task['conversation_id'], 'edit', 'Publish it and tell the board')['goal']
    assert edited['set_by'] == 'human:ana'
    finish(api, machine, claim(api, machine))           # the edit's control
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE bot_config SET config_json=? WHERE bot=?', (encode({'runtime': 'codex'}), 'ops'))
        H.task_comment(c, 'human:ana', task['id'], 'Use the new numbers.')
    claim(api, machine)
    goal = get(api, f"conversations/{task['conversation_id']}/goal")['goal']
    assert goal['status'] == 'active' and goal['objective'] == 'Publish it and tell the board'
