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
        'people': ['ben'],
        'teams': ['legal'],
        'bots': ['analyst'],
    }


def test_page_card_sends_the_rows_to_the_card_route_and_only_bots_are_offered_it():
    # Spec chat-on-every-page, Release 2: `tico page card` and its MCP twin propose the Goals card.
    from clients.commands import _access
    from clients.commands.content import pages_card

    class Api:
        _tico_is_botops = False

        def __init__(self):
            self.sent = []

        def post(self, path, body, key=None):
            self.sent.append((path, body))
            return {'action': {'status': 'pending'}}

    api = Api()
    rows = '[{"title": "NPS above 40"}, {"title": "Churn under 3%", "owner": "ana"}]'
    parsed = hubcli.parser().parse_args(['page', 'card', '--page', 'goals', '--for', 'human:cara', '--rows', rows])
    pages_card(api, vars(parsed))
    assert api.sent == [
        (
            'page-chat/cards',
            {
                'page': 'goals',
                'for_person': 'human:cara',
                'rows': [{'title': 'NPS above 40'}, {'title': 'Churn under 3%', 'owner': 'ana'}],
            },
        )
    ]
    assert _access.AUDIENCE['tico_page_card'] == _access.BOTS
