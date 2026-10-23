"""A red nightly enters the Builder queue through the cloud CLI; unrelated runs never file work."""
import json
import subprocess
from scripts.nightly_gate_report import report


def event(**changes):
    run = dict(id=42, event='schedule', head_branch='main', path='.github/workflows/ci.yml',
               status='completed', conclusion='failure', head_sha='a' * 40,
               head_repository={'full_name': 'acme/product'})
    run.update(changes)
    return {'repository': {'full_name': 'acme/product'}, 'workflow_run': run}


def test_red_nightly_files_top_builder_dev_ticket_and_other_events_do_not():
    calls = []
    def call(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps({'state': 'active'} if 'GET' in args else {'id': 'fixture'}), '')
    assert report(event(), call)
    assert calls[0][-3:] == ['api', 'GET', 'bots/pr-reviewer']
    create = calls[1]
    for flag, value in [('--owner', 'bot:pr-reviewer'), ('--type', 'Dev tickets'), ('--step', 'On Deck')]:
        assert create[create.index(flag) + 1] == value
    assert '--top' in create
    assert create[create.index('--link') + 1] == 'https://github.com/acme/product/actions/runs/42'
    calls.clear()
    for changes in ({'conclusion': 'success'}, {'event': 'workflow_dispatch'}, {'head_branch': 'feature'}):
        assert not report(event(**changes), call)
    assert calls == []


def test_refused_write_and_inactive_builder_stop_without_retry(capsys):
    calls = []
    def call(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps({'state': 'active'} if 'GET' in args else {'error': 'cap'}), '')
    import pytest
    with pytest.raises(RuntimeError, match='refused'):
        report(event(), call)
    assert len(calls) == 2 and 'cap' in capsys.readouterr().out
    calls.clear()
    def inactive(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '{"state":"quarantined"}', '')
    with pytest.raises(RuntimeError, match='not active'):
        report(event(), inactive)
    assert len(calls) == 1
