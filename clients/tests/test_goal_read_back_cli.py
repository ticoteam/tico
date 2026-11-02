"""CLI/MCP parity for server-written drafts and cited goal saves; no live team state."""
import json

import pytest

from clients import hubcli, hubtools, remotecli
from clients.tico import APIError


class Api:
    def __init__(self, *a, **kw):
        self.calls = []

    def post(self, path, body, key=None):
        self.calls.append(('POST', path, body, key, False))
        return {'message_id': 'draft', 'text': 'Server draft.\nReply yes to save exactly this; any other reply saves nothing.',
                'changes': []}

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        self.calls.append((method, path, body, key, delegate))
        return {'goal': {'id': 'new-goal'}, 'kpi': {}, 'proposal': {}}

    def get(self, path, **kw):
        assert path == 'me'
        return {'actor': 'human:owner', 'role': 'owner'}


@pytest.fixture
def api(monkeypatch):
    api = Api()
    monkeypatch.setattr(remotecli, 'Client', lambda *a, **kw: api)
    monkeypatch.setenv('HUB_API_URL', 'https://synthetic.invalid/api/v2')
    monkeypatch.setenv('HUB_OPERATION_ID', 'synthetic-operation')
    return api


def test_read_back_cli_mcp_same_arguments_response_and_key(api):
    changes = [{'kind': 'goal_create', 'title': 'A goal', 'owner': 'company'},
               {'kind': 'suggestion', 'proposal_id': 'p', 'decision': 'reject'}]
    result = remotecli.run(hubcli.parser().parse_args(['goal', 'read-back', json.dumps(changes)]))
    assert result == hubtools.goal_read_back(api, {'changes': changes, 'operation_id': 'synthetic-operation'})
    assert api.calls[0] == api.calls[1] == ('POST', 'goals/read-back', {'changes': changes}, 'synthetic-operation', False)
    assert result['message_id'] == 'draft'
    assert result['text'].endswith('Reply yes to save exactly this; any other reply saves nothing.')


def test_exact_save_cli_mcp_and_new_goal_substitution(api):
    body = '{"why":"The owner’s words"}'
    cli = ['goal', 'save', 'POST', '/api/v2/goals/{new}/why', body, '--created-goal', 'new-goal', '--on-behalf-of', 'turn']
    result = remotecli.run(hubcli.parser().parse_args(cli))
    assert result == hubtools.goal_save(api, {'method': 'POST', 'path': '/api/v2/goals/{new}/why', 'body': body,
                                            'created_goal': 'new-goal', 'on_behalf_of': 'turn',
                                            'operation_id': 'synthetic-operation'})
    assert api.calls[0] == api.calls[1] == ('POST', 'goals/new-goal/why', json.loads(body), 'synthetic-operation', 'turn')


@pytest.mark.parametrize('words,name', [
    (['goal', 'create', '--owner', 'company', '--title', 'Goal'], 'hub_goal_create'),
    (['goal', 'update', 'g', '--title', 'New title'], 'hub_goal_update'),
    (['kpi', 'create', 'Measure', '--goal', 'g'], 'hub_kpi_create'),
    (['kpi', 'link', 'g', 'k'], 'hub_kpi_link'),
    (['kpi', 'unlink', 'g', 'k'], 'hub_kpi_unlink'),
    (['kpi', 'target', 'g', 'k', '--target', '100', '--deadline', '2026-11-28'], 'hub_kpi_target'),
    (['proposal', 'decide', 'p', 'reject'], 'hub_proposal_decide'),
])
def test_named_writes_forward_cited_yes(api, words, name):
    # Stub the envelope for the uncited call as well.
    api.post = lambda path, body, key=None: api.call('POST', path, body, key)
    remotecli.run(hubcli.parser().parse_args([*words, '--on-behalf-of', 'owner-yes']))
    assert api.calls[-1][-1] == 'owner-yes'
    remotecli.run(hubcli.parser().parse_args(words))
    assert api.calls[-1][-1] is False
    assert 'on_behalf_of' in hubtools.BY_NAME[name]['inputSchema']['properties']
    assert 'assistant' in hubtools.offered_to(hubtools.BY_NAME[name])


def test_refusal_prints_reason_exits_nonzero_and_does_not_retry(api, capsys):
    def refuse(*a, **kw):
        api.calls.append('refused')
        raise APIError('not_agreed', 'The owner did not say yes; nothing was saved. Read it back again.', 403, False)
    api.post = refuse
    assert hubcli.main(['goal', 'create', '--owner', 'company', '--title', 'Goal']) == 2
    out = json.loads(capsys.readouterr().out)
    assert out['error'] == 'not_agreed'
    assert 'nothing was saved' in out['detail']
    assert api.calls == ['refused']


@pytest.mark.parametrize('changes', ['{}', '[]', '[1]'])
def test_bad_changes_do_not_reach_server(api, changes):
    with pytest.raises(ValueError):
        hubtools.goal_read_back(api, {'changes': changes})
    assert not api.calls


def test_goal_help_explains_plain_yes(capsys):
    assert hubcli.main(['goal', '--help']) == 0
    text = ' '.join(capsys.readouterr().out.split())
    assert 'read-back' in text and 'Saves nothing without a plain yes' in text
    assert hubcli.main(['goal', 'read-back', '--help']) == 0
    text = ' '.join(capsys.readouterr().out.split())
    assert 'body' in text and 'not_agreed' in text


def test_mcp_discovery_call_refusal_and_missing_command(api):
    protocol = hubtools.Protocol(api, api_error=APIError, kind='assistant')
    def request(method, **params):
        return protocol.handle({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
    names = {t['name'] for t in request('tools/list')['result']['tools']}
    assert {'hub_goal_read_back', 'hub_goal_save', 'hub_kpi_target'} <= names
    changes = [{'kind': 'goal_create', 'owner': 'company', 'title': 'Goal'}]
    result = request('tools/call', name='hub_goal_read_back', arguments={'changes': changes})['result']
    assert not result['isError'] and result['structuredContent']['message_id'] == 'draft'
    assert request('tools/call', name='hub_goal_nonexistent', arguments={})['error']['message'].startswith('Unknown tool:')
    def refuse(*a, **kw):
        raise APIError('not_agreed', 'The owner did not say yes; nothing was saved.', 403, False)
    api.call = refuse
    result = request('tools/call', name='hub_goal_save', arguments={
        'method': 'POST', 'path': '/api/v2/goals', 'body': '{}', 'on_behalf_of': 'turn'})['result']
    assert result['isError'] and result['structuredContent']['error'] == 'not_agreed'
    assert result['structuredContent']['detail'] == 'The owner did not say yes; nothing was saved.'


@pytest.mark.parametrize('extra', [{}, {'created_goal': '../wrong'}])
def test_save_requires_created_goal_before_substitution(api, extra):
    with pytest.raises(ValueError, match='created_goal'):
        hubtools.goal_save(api, {'method': 'POST', 'path': '/api/v2/goals/{new}/why',
                                'body': '{}', 'on_behalf_of': 'turn', **extra})
    assert not api.calls
