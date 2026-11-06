"""CLI/MCP audience changes preserve omission and use the same audience shorthand as bot access."""
from clients import hubcli, hubtools


def test_page_access_reads_without_writing_and_clears_with_everyone():
    class Api:
        def __init__(self):
            self._tico_is_botops = False
            self.writes = []
        def get(self, path):
            assert path == 'pages/file-123'
            return {'page': {'audience': {'everyone': False, 'people': ['ben'], 'teams': [], 'bots': []}}}
        def patch(self, path, body, key=None):
            self.writes.append((path, body))
            return {'page': {'audience': None}}
    api = Api()
    parsed = hubcli.parser().parse_args(['page', 'access', 'file-123'])
    assert hubtools.pages_access(api, vars(parsed))['audience']['people'] == ['ben']
    assert api.writes == []
    parsed = hubcli.parser().parse_args(['page', 'access', 'file-123', '--audience', 'everyone'])
    hubtools.pages_access(api, vars(parsed))
    assert api.writes == [('pages/file-123', {'audience': {'everyone': True}})]
    assert 'audience' not in hubtools._page_fields({'name': 'weekly.html'})
    assert hubtools._page_fields({'audience': 'ben,group:legal,bot:analyst'})['audience'] == {
        'people': ['ben'], 'teams': ['legal'], 'bots': ['analyst']}
