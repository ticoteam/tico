"""Native goals share chat privacy, the runner lease and conversation live updates."""

import pytest

from backend.store import H, encode
from backend.tests.helpers.api import assign, claim, get, post, ready, runner, restrict  # noqa: F401
from backend import bot_settings as BS
from backend.tests.helpers.chat_goals import action, goal_event, setup, task_goal_world


def test_a_met_goal_leaves_one_notice_that_wakes_nobody(api):
    machine, cid = setup(api)
    goal = action(api, cid, objective='Produce the Acme summary')['goal']
    assert goal['status'] == 'active' and goal['set_by'] == 'human:ana'
    shown = get(api, f'conversations/{cid}/goal')
    assert shown['supported'] is True
    assert [c['name'] for c in shown['commands'] if c['kind'] == 'harness'] == [
        'compact',
        'docs:publish',
    ]  # its repo's own
    assert next(b for b in get(api, 'bots') if b['slug'] == 'ops')['goal_active'] is True
    assert get(api, f'conversations/{cid}/snapshot')['goal'] == goal
    goal_event(api, machine, claim(api, machine), 'met', 'The condition holds.')
    met = get(api, f'conversations/{cid}/goal')['goal']
    assert met['status'] == 'met' and met['ended_at'] and met['note'] == 'The condition holds.'
    notices = [m for m in get(api, f'conversations/{cid}/messages') if m['refs'].get('goal_status') == 'met']
    assert len(notices) == 1 and notices[0]['body'].startswith('Goal met: ')
    assert next(b for b in get(api, 'bots') if b['slug'] == 'ops')['goal_active'] is False
    with api.app.state.store.read() as c:
        assert not c.execute('SELECT 1 FROM jobs WHERE message_id=?', (notices[0]['id'],)).fetchone()


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
        assert c.execute("SELECT count(*) FROM jobs WHERE state='canceled'").fetchone()[0] == 4


@pytest.mark.parametrize('objective', ['x' * 4001])
def test_objective_bounds(api, objective):
    _, cid = setup(api)
    action(api, cid, objective=objective, expected=422)


def test_read_access_is_not_chat_access_and_other_rooms_stay_private(api):
    _, cid = setup(api)
    # A reader can inspect the goal in a conversation they joined but cannot change it.
    with api.app.state.store.transaction() as c:
        conv = H.conversation(c, cid)
        c.execute(
            "UPDATE conversations SET scope='direct',owner_id=NULL,participants=? WHERE id=?",
            (encode(conv['participants'] + ['human:cara']), cid),
        )
        restrict(c, 'ops', see={'people': ['ana', 'cara']}, read={'people': ['ana', 'cara']}, write={'people': ['ana']})
    assert get(api, f'conversations/{cid}/goal', 'cara-test')['goal'] is None
    action(api, cid, objective='Unauthorized change', token='cara-test', expected=403)
    other = post(api, 'conversations', {'participants': ['bot:ops']})['id']
    get(api, f'conversations/{other}/goal', 'cara-test', expected=404)
    action(api, other, objective='Private room', token='cara-test', expected=404)


def test_old_runners_and_unsupported_harnesses(api):
    # The computer's probe is the only word on goals: a CLI it found none in says so, whatever its name.
    machine, cid = setup(api, False, 'gemini')
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
    post(
        api,
        f"attempts/{attempt['id']}/events",
        {'events': [{'seq': 1, 'kind': 'goal', 'payload': payload}]},
        machine['token'],
    )
    assert get(api, f'conversations/{cid}/goal')['goal'] == newer
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE attempts SET lease_until=? WHERE id=?', (H.shift(H.now(), seconds=-1), attempt['id']))
    payload['revision'] = newer['updated_at']
    post(
        api,
        f"attempts/{attempt['id']}/events",
        {'events': [{'seq': 2, 'kind': 'goal', 'payload': payload}]},
        machine['token'],
    )
    assert get(api, f'conversations/{cid}/goal')['goal'] == newer


@pytest.mark.parametrize('ruled', [False, True])
def test_a_task_goal_is_set_by_the_keeper_and_meeting_it_marks_the_task_done(api, ruled):
    """With a company rule in force the done note is the fixed "Goal met.": the harness's words were never checked."""
    if ruled:
        from backend.tests.helpers.org_rules import rule

        rule(api, {'kind': 'internal', 'text': 'Never name the board.', 'who': {'bots': ['ops']}})
    machine = runner(api)
    assign(api, machine, 'ops')
    with api.app.state.store.transaction() as c:
        BS.write(c, 'ops', {'runtime': 'codex', 'task_goal': 'on'})
    post(
        api,
        'runners/heartbeat',
        {
            'version': 'test',
            'platform': 'test',
            'capabilities': ['task_lanes_v2', 'session_roots_v1', 'task_goals_v1'],
            'readiness': {'schema_version': 1, 'bots': {'ops': {'ready': True, 'runtime': 'codex', 'goals': True}}},
        },
        machine['token'],
    )
    task = post(
        api,
        'tasks',
        {
            'owner': 'ops',
            'title': 'Publish the Acme summary',
            'body': 'For the board.',
            'acceptance_criteria': ['The summary is in Docs'],
        },
    )
    attempt = claim(api, machine)
    goal = attempt['chat_goal']
    assert (
        goal['status'] == 'active' and goal['set_by'] == 'keeper' and attempt['task']['goal']['goal_id'] == goal['id']
    )
    assert goal['conversation_id'] == task['conversation_id'] and 'The summary is in Docs' in goal['objective']

    goal_event(api, machine, attempt, 'met', 'The summary is in Docs.')
    assert get(api, f"conversations/{task['conversation_id']}/goal")['goal']['status'] == 'met'
    done = get(api, 'tasks/' + task['id'])['task']
    assert done['status'] == 'done'
    with api.app.state.store.read() as c:  # the goal's note is the owner's answer to "owner says done"
        notes = [
            r[0]
            for r in c.execute(
                "SELECT a.notes FROM answers a JOIN visits v ON v.id=a.visit_id WHERE v.task_id=?", (task['id'],)
            )
        ]
    assert notes == ['Goal met.' if ruled else 'The summary is in Docs.']


def test_turning_a_task_goal_off_clears_it_natively_and_a_persons_goal_never_completes_the_task(api):
    from backend import chat_goals

    machine, task = task_goal_world(api)
    first = claim(api, machine)
    assert first['chat_goal']['status'] == 'active'
    goal_event(api, machine, first, 'paused', 'Waiting on: Ana')  # Codex blocked on a person: the goal pauses
    post(api, f"attempts/{first['id']}/complete", {'outcome': 'completed', 'last_seq': 1}, machine['token'])
    with api.app.state.store.transaction() as c:
        BS.write(c, 'ops', {'runtime': 'codex'})
        H.task_comment(c, 'human:ana', task['id'], 'Use the new numbers.')
    second = claim(api, machine)
    assert second['chat_goal']['status'] == 'cleared' and 'goal' not in second['task']
    with api.app.state.store.read() as c:
        clear = c.execute(
            "SELECT m.body,m.refs FROM jobs j JOIN messages m ON m.id=j.message_id "
            "WHERE j.state='queued' AND m.conversation_id=?",
            (task['conversation_id'],),
        ).fetchall()
    assert [(b, '"goal_action": "clear"' in r) for b, r in clear] == [('/goal clear', True)]
    # A goal a person set in the task's room being met leaves the task as it is.
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE chat_goals SET set_by='human:ana',objective='Summarize current progress',status='active' "
            "WHERE conversation_id=?",
            (task['conversation_id'],),
        )
        goal = chat_goals.current(c, task['conversation_id'])
        chat_goals.task_goal_ended(c, goal, task['id'], 'met', 'Summarized.')
        assert H.task(c, task['id'])['status'] != 'done'


def test_another_bots_turn_in_the_task_room_leaves_the_owners_task_goal_alone(api):
    from backend.tests.helpers.member_bots import finish

    machine, task = task_goal_world(api)
    first = claim(api, machine)
    assert first['chat_goal']['status'] == 'active'
    finish(api, machine, first)
    assign(api, machine, 'product-design')
    post(
        api,
        'runners/heartbeat',
        {
            'version': 'test',
            'platform': 'test',
            'capabilities': ['task_lanes_v2', 'session_roots_v1', 'task_goals_v1'],
            'readiness': {
                'schema_version': 1,
                'bots': {
                    'ops': {'ready': True, 'runtime': 'codex', 'goals': True},
                    'product-design': {'ready': True, 'runtime': 'codex', 'goals': True},
                },
            },
        },
        machine['token'],
    )
    with api.app.state.store.transaction() as c:  # the owner asks another bot on the task: its turn, in the task's room
        H.task_ask(
            c,
            'bot:ops',
            task['id'],
            'bot:product-design',
            'Is the summary right?',
            seen_version=H.task(c, task['id'])['version'],
        )
    other = claim(api, machine)
    assert other['bot'] == 'product-design' and other['task']['id'] == task['id'] and other['chat_goal'] is None
    goal = get(api, f"conversations/{task['conversation_id']}/goal")['goal']
    assert goal['id'] == first['chat_goal']['id'] and goal['status'] == 'active'
    with api.app.state.store.read() as c:
        queued = c.execute(
            "SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued' "
            "AND json_extract(m.refs,'$.goal_action') IS NOT NULL"
        ).fetchone()[0]
    assert queued == 0


def test_handing_the_task_to_a_person_ends_the_old_owners_task_goal(api):
    from backend.tests.helpers.member_bots import finish

    machine, task = task_goal_world(api)
    first = claim(api, machine)
    finish(api, machine, first)
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'human:ana', task['id'], owner='human:ana')
    goal = get(api, f"conversations/{task['conversation_id']}/goal")['goal']
    assert goal['id'] == first['chat_goal']['id'] and goal['status'] == 'cleared'
    control = claim(api, machine)  # the old owner's session drops the native goal
    assert control['bot'] == 'ops' and control['message']['refs']['goal_action'] == 'clear'


def test_a_done_tasks_goal_is_met_even_when_the_bot_signs_off_waiting(api):
    machine, task = task_goal_world(api)
    attempt = claim(api, machine)
    # The run starts first: a turn whose task finished before it started is refused (execution.held_before_start).
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'fake-thread'}, machine['token'])
    with api.app.state.store.transaction() as c:  # the bot marks its task done, then says what it waits on elsewhere
        H.task_done(c, 'bot:ops', task['id'], 'Published.', H.task(c, task['id'])['version'])
    goal_event(api, machine, attempt, 'paused', 'Waiting on: Ana to "close" it.', started=False)
    goal = get(api, f"conversations/{task['conversation_id']}/goal")['goal']
    assert goal['status'] == 'met' and goal['note'] == 'Waiting on: Ana to "close" it.'
    assert get(api, 'tasks/' + task['id'])['task']['status'] == 'done'


def test_a_persons_clear_of_a_task_goal_stands_while_task_goals_stay_on(api):
    from backend.tests.helpers.member_bots import finish

    machine, task = task_goal_world(api)
    first = claim(api, machine)
    finish(api, machine, first)
    cleared = action(api, task['conversation_id'], 'clear')['goal']
    assert cleared['status'] == 'cleared' and cleared['set_by'] == 'human:ana'
    control = claim(api, machine)  # the "/goal clear" control, with task goals still on
    assert control['message']['refs']['goal_action'] == 'clear'
    goal = get(api, f"conversations/{task['conversation_id']}/goal")['goal']
    assert goal['id'] == cleared['id'] and goal['status'] == 'cleared' and control['chat_goal']['id'] == cleared['id']


def test_a_persons_edit_of_a_task_goal_survives_task_goals_turning_off(api):
    from backend.tests.helpers.member_bots import finish

    machine, task = task_goal_world(api)
    finish(api, machine, claim(api, machine))
    edited = action(api, task['conversation_id'], 'edit', 'Publish it and tell the board')['goal']
    assert edited['set_by'] == 'human:ana'
    finish(api, machine, claim(api, machine))  # the edit's control
    with api.app.state.store.transaction() as c:
        BS.write(c, 'ops', {'runtime': 'codex'})
        H.task_comment(c, 'human:ana', task['id'], 'Use the new numbers.')
    claim(api, machine)
    goal = get(api, f"conversations/{task['conversation_id']}/goal")['goal']
    assert goal['status'] == 'active' and goal['objective'] == 'Publish it and tell the board'


@pytest.mark.parametrize('where', ['dm', 'home'])
def test_a_bot_dm_or_home_thread_takes_a_goal(api, where):
    # The bot page's Home and DM are threads (docs/dev/threads-contract.md): the goal route takes their id as the
    # conversation, and a native DM names its bot only as a member, not on the participant list.
    machine, old = setup(api)
    with api.app.state.store.transaction() as c:  # not the legacy chat room: a DM made as threads make one
        c.execute('UPDATE conversations SET closed_at=? WHERE id=?', (H.now(), old))
    thread = post(api, 'threads/dm', {'members': ['bot:ops']}) if where == 'dm' else get(api, 'threads/home/bot:ops')
    tid = thread['id']
    assert tid != old
    assert get(api, f'conversations/{tid}/goal')['supported'] is True
    goal = action(api, tid, objective='Ship the Acme pricing page')['goal']
    assert goal['status'] == 'active' and goal['conversation_id'] == tid
    attempt = claim(api, machine)
    assert attempt['message']['body'] == '/goal Ship the Acme pricing page'
    assert attempt['message']['refs']['goal_action'] == 'set'


@pytest.mark.parametrize('where', ['dm', 'home'])
def test_a_bot_dm_or_home_thread_takes_a_harness_command_and_shows_its_run(api, where):
    # The bot page's composer (ui/app/thread-view.ts) sends a harness's own "/" command as the old chat did: a message
    # marked as a command, to the bot, in the thread (POST /v2/messages with the thread as its conversation). Its Stop
    # reads the thread's run from the thread's conversation snapshot.
    machine, old = setup(api)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE conversations SET closed_at=? WHERE id=?', (H.now(), old))
    thread = post(api, 'threads/dm', {'members': ['bot:ops']}) if where == 'dm' else get(api, 'threads/home/bot:ops')
    tid = thread['id']
    post(api, 'messages', {'to': 'bot:ops', 'text': '/compact', 'conversation_id': tid, 'command': True})
    assert get(api, f'conversations/{tid}/snapshot?limit=1')['execution']['state'] == 'queued'
    attempt = claim(api, machine)
    assert attempt['message']['body'] == '/compact' and attempt['message']['refs']['command'] is True
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'fake-thread'}, machine['token'])
    execution = get(api, f'conversations/{tid}/snapshot?limit=1')['execution']
    assert execution['state'] == 'running' and execution['run_id'] == attempt['id']
    assert post(api, 'bots/ops/stop', {'run_id': execution['run_id']})['stopping'] is True
