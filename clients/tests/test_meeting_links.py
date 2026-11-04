"""The CLI and MCP share link commands and keep requester delegation on the person-only routes."""
from clients import hubcli, hubtools, remotecli


class Api:
    def __init__(self, *args, **kwargs):
        self.calls = []
        self._tico_is_botops = False

    def get(self, path, **query):
        self.calls.append(('GET', path, query))
        return {'meetings': [], 'next_cursor': None} if path.endswith('/meetings') else {'tasks': []}

    def post(self, path, body, key=None):
        self.calls.append(('POST', path, body))
        return {'tasks': [], 'next_cursor': None}

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        self.calls.append((method, path, body))
        return {'ok': True}


def test_cli_links_lists_and_removes_through_the_mcp_handlers(monkeypatch):
    client = Api()
    monkeypatch.setattr(remotecli, 'Client', lambda *a, **kw: client)
    monkeypatch.setenv('HUB_API_URL', 'https://tico.acme.example/api/v2')
    parser = hubcli.parser()
    commands = [
        ['meeting', 'link', 'm1', '#12', 'task-12345678'],
        ['task', 'meetings', '#12', '--limit', '5', '--cursor', 'next'],
        ['meeting', 'unlink', 'm1', '#12'],
        ['task', 'list', '--q', 'pricing review'],
    ]
    for command in commands:
        remotecli.run(parser.parse_args(command))
    assert client.calls[:3] == [
        ('POST', 'meetings/m1/tasks', {'task_ids': ['#12', 'task-12345678'], 'kind': 'linked'}),
        ('GET', 'tasks/#12/meetings', {'limit': 5, 'cursor': 'next'}),
        ('DELETE', 'meetings/m1/tasks/#12', None),
    ]
    assert client.calls[-1][1] == 'tasks' and client.calls[-1][2]['q'] == 'pricing review'
    for name in ('hub_meeting_link', 'hub_meeting_unlink', 'hub_task_meetings'):
        audience = hubtools.offered_to(hubtools.BY_NAME[name])
        assert 'assistant' in audience and 'botops' in audience and 'bot' not in audience
