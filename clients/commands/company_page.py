"""The company page.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

from urllib.parse import quote

from clients.commands._registry import tool
from clients.commands._schemas import _s


# ----------------------------------------------------------------------------- Company page (docs/company-page.md)
# Read-only views for owners and admins, over /api/v2 (backend/company_page). Nothing here changes the page or a reader.
@tool(
    'tico_company_page',
    'The Company page: its transparency (closed, private or open), public address and last update.',
    {},
)
def company_page_show(api, args):
    return api.get('company-page')


@tool(
    'tico_audiences_list',
    'The Company page\'s audiences: the built-in ones and any you added.',
    {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}, 'offset': {'type': 'integer', 'minimum': 0}},
)
def audiences_list(api, args):
    return api.get('audiences', **{k: args[k] for k in ('limit', 'offset') if args.get(k) is not None})


@tool(
    'tico_editions_list',
    'The Company page\'s editions, newest first: each audience\'s edition per period, with its status. '
    'before pages back from the previous page\'s next.',
    {
        'audience': _s('Only this audience id'),
        'status': _s('Only editions in this status'),
        'before': _s('The previous page\'s next'),
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
    },
)
def editions_list(api, args):
    return api.get(
        'editions', **{k: args[k] for k in ('audience', 'status', 'before', 'limit') if args.get(k) is not None}
    )


@tool(
    'tico_editions_show',
    'One Company page edition: its text, status, checks and approval.',
    {'id': _s('Edition id')},
    required=('id',),
)
def editions_show(api, args):
    return api.get('editions/' + quote(args['id'], safe=''))


@tool(
    'tico_readers_list',
    'The Company page\'s readers: outside people who sign in to its private audiences. Owners and admins only.',
    {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500}, 'cursor': {'type': 'integer', 'minimum': 0}},
)
def readers_list(api, args):
    return api.get('readers', **{k: args[k] for k in ('limit', 'cursor') if args.get(k) is not None})
