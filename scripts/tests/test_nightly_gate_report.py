"""Tasks-key delivery, replay boundaries and credential scope without any live HTTP."""
from email.message import Message
from io import BytesIO
import json
import socket
import sys
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

import pytest
from scripts import nightly_gate_report as reporter

ORIGIN = 'https://tico.acme.example'
TOKEN = 'synthetic-nightly-key'
ACK = {'task': {'id': 'synthetic-task'}, 'created': True, 'changed': True}
NOOP = {'task': None, 'created': False, 'changed': False}
FORBIDDEN = {'error': {'code': 'forbidden', 'detail': 'A service key only files tasks, with POST /api/v2/inbound/tasks'}}


def event(**changes):
    run = dict(id=42, event='schedule', head_branch='main', path='.github/workflows/ci.yml',
               status='completed', conclusion='failure', head_sha='a' * 40,
               head_repository={'full_name': 'acme/product'})
    run.update(changes)
    return {'repository': {'full_name': 'acme/product'}, 'workflow_run': run}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Reporter tests must not connect to any live service')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


def transport(replies):
    calls = []
    def open_request(req, *, timeout):
        calls.append(req)
        assert timeout == reporter.TIMEOUT
        status, body = replies[len(calls) - 1]
        raw = json.dumps(body).encode() if not isinstance(body, str) else body.encode()
        if not 200 <= status < 300:
            raise HTTPError(req.full_url, status, 'Synthetic response', {}, BytesIO(raw))
        return addinfourl(BytesIO(raw), {}, req.full_url, status)
    return calls, open_request


def test_red_payload_and_rerun_key_are_exact_and_no_precheck_is_made(capsys):
    calls, open_request = transport([(200, ACK)] * 3)
    assert reporter.report(event(), ORIGIN, TOKEN, open_request)
    assert reporter.report(event(), ORIGIN, TOKEN, open_request)
    assert reporter.report(event(id=43, conclusion='cancelled'), ORIGIN, TOKEN, open_request)
    expected_body = ('The scheduled full gate on main failed (failure). Fix the failing gate before the next '
                     'freeze; a newer full gate on main must be green.\n\nCandidate: ' + 'a' * 40 + '\n'
                     'Run: https://github.com/acme/product/actions/runs/42\n'
                     'Acceptance: repair the failure with focused regressions, then pass a new full Release validation on main.')
    assert json.loads(calls[0].data) == {
        'key': 'nightly-gate:42', 'owner': 'bot:pr-reviewer', 'type': 'Dev tickets', 'step': 'On Deck',
        'labels': ['nightly-red'], 'title': 'Repair failed nightly gate on main (run 42)',
        'body': expected_body, 'links': ['https://github.com/acme/product/actions/runs/42']}
    assert calls[0].data == calls[1].data
    assert json.loads(calls[2].data)['key'] == 'nightly-gate:43'
    assert all(req.full_url == ORIGIN + '/api/v2/inbound/tasks' and req.method == 'POST' for req in calls)
    assert all(req.get_header('Authorization') == 'Bearer ' + TOKEN for req in calls)
    assert TOKEN not in capsys.readouterr().out


def test_completed_timeout_reports_the_same_exact_idempotent_contract():
    calls, open_request = transport([(200, ACK)] * 3)
    assert reporter.report(event(), ORIGIN, TOKEN, open_request)
    expected = json.loads(calls[0].data)
    expected['body'] = expected['body'].replace('(failure)', '(timed_out)')
    assert reporter.report(event(conclusion='timed_out'), ORIGIN, TOKEN, open_request)
    assert reporter.report(event(conclusion='timed_out'), ORIGIN, TOKEN, open_request)
    assert len(calls) == 3
    assert json.loads(calls[1].data) == expected
    assert calls[1].data == calls[2].data
    assert all(req.method == 'POST' and req.full_url == ORIGIN + '/api/v2/inbound/tasks' for req in calls)


def test_ineligible_and_invalid_events_never_contact_tico():
    calls, open_request = transport([])
    for changes in ({'conclusion': 'success'}, {'event': 'workflow_dispatch'},
                    {'head_branch': 'feature'}, {'path': '.github/workflows/other.yml'}, {'status': 'in_progress'}):
        assert not reporter.report(event(**changes), None, None, open_request)
    for changes in ({'id': True}, {'head_sha': 'not-a-sha'}, {'head_repository': {'full_name': 'elsewhere/repo'}}):
        with pytest.raises(ValueError):
            reporter.report(event(**changes), ORIGIN, TOKEN, open_request)
    invalid_repo = event()
    invalid_repo['repository']['full_name'] = 'acme/product/extra'
    with pytest.raises(ValueError, match='repository'):
        reporter.report(invalid_repo, ORIGIN, TOKEN, open_request)
    assert calls == []


def test_non_2xx_and_application_refusals_are_visible_redacted_and_never_retried(capsys):
    for status, response in [(503, 'full refusal ' + TOKEN + ' x' * 5000),
                             (409, {'error': {'code': 'inactive_bot'}}),
                             (200, {'ok': False, 'error': 'refused'}), (200, NOOP)]:
        calls, open_request = transport([(status, response)])
        with pytest.raises(RuntimeError, match='no retry attempted'):
            reporter.report(event(), ORIGIN, TOKEN, open_request)
        assert len(calls) == 1
        output = capsys.readouterr().out
        assert f'HTTP {status}' in output and TOKEN not in output
        if status == 503:
            assert 'full refusal [redacted]' in output and ' x' * 5000 in output


def test_dry_run_only_closes_its_manual_run_key_then_proves_read_is_forbidden(capsys):
    calls, open_request = transport([(200, NOOP), (403, FORBIDDEN)])
    assert reporter.dry_run(123, ORIGIN, TOKEN, open_request)
    assert len(calls) == 2
    assert calls[0].method == 'POST' and json.loads(calls[0].data) == {'key': 'nightly-dry-run-123', 'close': True}
    assert calls[0].full_url == ORIGIN + '/api/v2/inbound/tasks'
    assert calls[1].method == 'GET' and calls[1].full_url == ORIGIN + '/api/v2/bots/pr-reviewer'
    assert calls[1].data is None
    assert all(req.get_header('Authorization') == 'Bearer ' + TOKEN for req in calls)
    output = capsys.readouterr().out
    assert 'HTTP 200' in output and 'HTTP 403' in output and TOKEN not in output


def test_dry_run_requires_an_exact_noop_before_any_read():
    for response in ({**NOOP, 'task': {'id': 'unexpected'}}, {**NOOP, 'changed': True}, {**NOOP, 'created': 0}):
        calls, open_request = transport([(200, response)])
        with pytest.raises(RuntimeError, match='expected no-op'):
            reporter.dry_run(123, ORIGIN, TOKEN, open_request)
        assert len(calls) == 1


@pytest.mark.parametrize('status,response', [(401, FORBIDDEN), (200, {'state': 'active'}),
                                           (403, {'error': {'code': 'forbidden', 'detail': 'A different refusal'}})])
def test_dry_run_refuses_invalid_auth_broad_keys_and_unrelated_forbidden(status, response):
    calls, open_request = transport([(200, NOOP), (status, response)])
    with pytest.raises(RuntimeError, match='tasks-only scope'):
        reporter.dry_run(123, ORIGIN, TOKEN, open_request)
    assert len(calls) == 2


def test_real_opener_does_not_follow_redirect_or_forward_credential(monkeypatch, capsys):
    calls = []
    class FakeHTTPS(HTTPSHandler):
        def https_open(self, req):
            calls.append(req)
            headers = Message()
            headers['Location'] = 'https://elsewhere.acme.example/stolen'
            response = addinfourl(BytesIO(('redirect response ' + TOKEN).encode()), headers, req.full_url, 302)
            response.msg = 'Found'
            return response
    monkeypatch.setattr(reporter, 'build_opener', lambda *handlers: build_opener(*handlers, FakeHTTPS()))
    with pytest.raises(RuntimeError, match='HTTP 302'):
        reporter.report(event(), ORIGIN, TOKEN)
    assert len(calls) == 1 and calls[0].full_url.startswith(ORIGIN + '/')
    output = capsys.readouterr().out
    assert TOKEN not in output and 'redirect response [redacted]' in output


def test_transport_error_and_invalid_origin_never_retry_or_expose_request(capsys):
    calls = []
    def interrupted(req, **kwargs):
        calls.append(req)
        raise TimeoutError('request with Bearer ' + TOKEN)
    with pytest.raises(RuntimeError, match='TimeoutError.*no retry') as error:
        reporter.report(event(), ORIGIN, TOKEN, interrupted)
    assert TOKEN not in str(error.value) and len(calls) == 1
    for origin in ('http://tico.acme.example', ORIGIN + '/path', 'https://user:password@tico.acme.example'):
        with pytest.raises(ValueError, match='HTTPS origin'):
            reporter.report(event(), origin, TOKEN, interrupted)
    assert len(calls) == 1 and TOKEN not in capsys.readouterr().out


def test_manual_entry_uses_actions_run_id_and_rejects_untrusted_ref(tmp_path, monkeypatch):
    path = tmp_path / 'event.json'
    path.write_text(json.dumps({'repository': {'full_name': 'acme/product'}}))
    for name, value in {'GITHUB_EVENT_PATH': str(path), 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                        'GITHUB_REF': 'refs/heads/main', 'GITHUB_RUN_ID': '321',
                        'HUB_API_URL': ORIGIN, 'HUB_TOKEN': TOKEN}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, 'argv', ['nightly_gate_report.py', '--dry-run'])
    calls, open_request = transport([(200, NOOP), (403, FORBIDDEN)])
    monkeypatch.setattr(reporter, 'build_opener', lambda *handlers: SimpleNamespace(open=open_request))
    assert reporter.main() == 0
    assert json.loads(calls[0].data)['key'] == 'nightly-dry-run-321'
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/untrusted')
    assert reporter.main() == 1 and len(calls) == 2
