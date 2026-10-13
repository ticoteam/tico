"""Native wire protocols and transcript status records, without a paid model."""
import json
from unittest.mock import Mock

from runner import goals
from runner.hosts.claude import ClaudeHost
from runner.hosts.codex import CodexHost
from runner.service import Runner
from clients.tico import APIError


def test_codex_native_set_pause_resume_clear_and_completion():
    host = CodexHost()
    host.request = Mock(return_value={'turn': {'id': 'turn'}})
    assert host.start_goal('thread', 'set', 'Acme summary') == 'turn'
    assert host.request.call_args_list[0].args == ('thread/goal/set',
        {'threadId': 'thread', 'objective': 'Acme summary', 'status': 'active'})
    assert host.request.call_args_list[1].args == ('turn/start',
        {'threadId': 'thread', 'input': [{'type': 'text', 'text': 'Acme summary'}]})
    host.start_goal('thread', 'pause', 'Acme summary')
    assert host.request.call_args.args == ('thread/goal/set', {'threadId': 'thread', 'status': 'paused'})
    host.start_goal('thread', 'resume', 'Acme summary')
    assert host.request.call_args_list[-2].args[1]['status'] == 'active'
    host.start_goal('thread', 'clear', 'Acme summary')
    assert host.request.call_args.args == ('thread/goal/clear', {'threadId': 'thread'})
    host.drain()
    for status, expected in [('active', 'active'), ('complete', 'met'), ('blocked', 'paused'),
                             ('budgetLimited', 'stopped'), ('usageLimited', 'stopped')]:
        host._on_notification('thread/goal/updated', {'threadId': 'thread',
            'goal': {'objective': 'Acme summary', 'status': status}})
        event = host.drain()[0]
        assert event['kind'] == 'goal' and event['status'] == expected


def test_codex_headless_commands_use_app_server_equivalents():
    host = CodexHost()
    host.request = Mock(return_value={'turn': {'id': 'review-turn'}})
    host.start_command('thread', '/compact')
    assert host.request.call_args.args == ('thread/compact/start', {'threadId': 'thread'})
    assert host.start_command('thread', '/review Check Acme changes') == 'review-turn'
    assert host.request.call_args.args == ('review/start', {'threadId': 'thread',
        'target': {'type': 'custom', 'instructions': 'Check Acme changes'}})


def test_claude_reads_only_new_native_goal_records_for_its_session(tmp_path):
    config = tmp_path / 'config'
    directory = config / 'projects' / 'acme'
    directory.mkdir(parents=True)
    transcript = directory / 'session.jsonl'
    records = [
        {'type': 'attachment', 'attachment': {'type': 'goal_status', 'met': False, 'sentinel': True, 'condition': 'Acme summary'}},
        {'type': 'attachment', 'attachment': {'type': 'goal_status', 'met': True, 'condition': 'Acme summary', 'reason': 'The condition holds.'}},
        {'type': 'attachment', 'attachment': {'type': 'goal_status', 'met': False, 'failed': True, 'condition': 'Acme summary', 'reason': 'This condition is impossible.'}},
    ]
    transcript.write_text(''.join(json.dumps(row) + '\n' for row in records))
    (directory / 'other-session.jsonl').write_text(json.dumps({'attachment': {'type': 'goal_status', 'met': True}}) + '\n')
    host = ClaudeHost()
    host.resume_thread('ops', 'session', {'cwd': str(tmp_path), 'env': {'CLAUDE_CONFIG_DIR': str(config)}})
    host.poll_goal('session')
    events = host.drain()
    assert [e['status'] for e in events] == ['active', 'met', 'stopped']
    assert events[2]['note'] == 'This condition is impossible.'
    host.poll_goal('session')
    assert host.drain() == []
    with transcript.open('a') as stream:
        stream.write(json.dumps({'type': 'attachment', 'attachment': {'type': 'goal_status', 'met': True, 'sentinel': True}}))
    host.poll_goal('session')
    assert host.drain() == []
    with transcript.open('a') as stream:
        stream.write('\n')
    host.poll_goal('session')
    assert host.drain()[0]['status'] == 'cleared'


def test_claude_native_goal_prompts_and_new_session_pointer():
    host = ClaudeHost()
    host.start_turn = Mock(return_value='turn')
    host.start_goal('thread', 'set', 'Acme summary')
    assert host.start_turn.call_args.args == ('thread', '/goal Acme summary')
    host.start_goal('thread', 'pause', 'Acme summary')
    assert host.start_turn.call_args.args == ('thread', '/goal clear')
    host.start_goal('thread', 'resume', 'Acme summary')
    assert host.start_turn.call_args.args == ('thread', '/goal Acme summary')
    host.resume_thread('ops', 'thread', {'cwd': '/acme'})
    host._on_message({'type': 'system', 'subtype': 'init', 'session_id': 'fresh-session'}, 'thread', 'turn')
    assert host.session_id('thread') == 'fresh-session'
    assert host._argv('thread', host._threads['thread'], None)[-2:] == ['--resume', 'fresh-session']


def test_readiness_does_not_advertise_old_or_unsupported_harnesses():
    assert goals.capabilities('claude', None, '') == {'goals': False, 'commands': []}
    assert goals.capabilities('claude', 'claude', '2.1.100')['goals'] is False
    report = goals.capabilities('claude', 'claude', '2.1.287')
    assert report['goals'] is True and [c['name'] for c in report['commands']] == ['compact', 'clear', 'model']
    assert goals.capabilities('gemini', 'gemini', 'test') == {'goals': False, 'commands': []}


def test_readiness_rolls_back_to_an_old_server_without_losing_heartbeat():
    service = Runner.__new__(Runner)
    service.client = Mock()
    service.client.post.side_effect = [APIError('validation', 'readiness.StructuredReadiness.bots.ops.goals: Extra inputs are not permitted', 422), {}]
    report = {'readiness': {'bots': {'ops': {'ready': True, 'goals': True, 'commands': []}},
                            'runtimes': {'codex': {'installed': True, 'goals': True, 'commands': []}}}}
    service.report_heartbeat(report)
    assert service.client.post.call_count == 2
    assert report['readiness']['bots']['ops'] == {'ready': True}


def test_claude_bot_offers_its_repository_skills_and_commands(tmp_path):
    skill = tmp_path / '.claude' / 'skills' / 'release-notes'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: release-notes\ndescription: >\n  Draft release notes\n  for Acme\n'
                                    'argument-hint: [version]\n---\nBody\n')
    hidden = tmp_path / '.claude' / 'skills' / 'house-style'
    hidden.mkdir()
    (hidden / 'SKILL.md').write_text('---\nname: house-style\ndescription: Acme tone\nuser-invocable: false\n---\n')
    (tmp_path / '.claude' / 'commands' / 'docs').mkdir(parents=True)
    (tmp_path / '.claude' / 'commands' / 'docs' / 'publish.md').write_text('Publish the Acme docs\n')
    (tmp_path / '.claude' / 'commands' / 'compact.md').write_text('---\ndescription: not the built-in\n---\n')
    (tmp_path / '.claude' / 'commands' / 'Bad Name.md').write_text('x')
    service = Runner.__new__(Runner)
    service.tools = None
    builtins = goals.capabilities('claude', 'claude', '2.1.287')
    report = service.readiness([], [{'bot': 'ops', 'ready': True, 'runtime': 'claude', 'repository': str(tmp_path),
                                     'repository_present': True}], {'claude': {'installed': True, **builtins}})
    commands = report['bots']['ops']['commands']
    assert [c['name'] for c in commands] == ['compact', 'clear', 'model', 'release-notes', 'publish']
    assert commands[3] == {'name': 'release-notes', 'args': '[version]', 'help': 'Draft release notes for Acme',
                           'kind': 'harness'}
    # A server that refuses a repository command's name still receives the harness's own.
    service.client = Mock()
    service.client.post.side_effect = [APIError('validation', 'readiness.StructuredReadiness.bots.ops.commands.4.name: '
                                                'String should match pattern', 422), {}]
    service.report_heartbeat({'readiness': report})
    assert [c['name'] for c in report['bots']['ops']['commands']] == ['compact', 'clear', 'model']


def test_codex_resume_clear_snapshot_cannot_stop_new_active_goal():
    host = Mock()
    host.request.return_value = {"goal": {"objective": "Acme summary", "status": "active"}}
    snapshot = {"kind": "goal", "thread_id": "thread", "turn_id": None, "status": "cleared", "note": ""}
    event = goals.reconcile_codex_clear(host, "thread", snapshot, "Acme summary")
    assert event["status"] == "active" and event["objective"] == "Acme summary"
    assert snapshot["status"] == "cleared"
    host.request.assert_called_once_with("thread/goal/get", {"threadId": "thread"})


def test_codex_real_clear_and_stopped_states_are_not_hidden():
    host = Mock()
    snapshot = {"kind": "goal", "status": "cleared"}
    host.request.return_value = {"goal": None}
    assert goals.reconcile_codex_clear(host, "thread", snapshot, "Acme summary") == snapshot
    for native, status in [("paused", "paused"), ("complete", "met"), ("blocked", "paused"), ("usageLimited", "stopped")]:
        host.request.return_value = {"goal": {"objective": "Acme summary", "status": native}}
        assert goals.reconcile_codex_clear(host, "thread", snapshot, "Acme summary")["status"] == status
    host.request.return_value = {"goal": {"objective": "Different goal", "status": "active"}}
    event = goals.reconcile_codex_clear(host, "thread", snapshot, "Acme summary")
    assert event["status"] == "stopped" and event["note"] == "The native goal changed"


def test_codex_clear_verification_failure_does_not_invent_active_goal():
    import pytest
    from runner.hosts.base import HostError
    host = Mock()
    host.request.side_effect = HostError("unavailable")
    with pytest.raises(HostError):
        goals.reconcile_codex_clear(host, "thread", {"status": "cleared"}, "Acme summary")
    host.request.reset_mock()
    event = {"status": "active", "objective": "Acme summary"}
    assert goals.reconcile_codex_clear(host, "thread", event, "Acme summary") is event
    host.request.assert_not_called()
