# pyright: strict
"""The answers of the Tools routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj, scalar

TOOL_STATUS = obj({'tone': {'enum': ['ok', 'wait', 'fail', 'run', 'idle']}, 'text': 's'})


# Website tools (backend/website_tools.py): a site signed in to through a browser, and its accounts. No answer carries a
# password or a 2FA key; the 2FA code goes only to a signed-in person who uses the account.
WEBSITE_WHO = obj({'subject': 's', 'kind': {'enum': ['person', 'bot']}, 'id': 's', 'name': 's', 'initials': 's'})


# One row of an account's Who can use it: a person, a bot or (to an administrator) a granted group with its member count.
# `via` names the granted groups a person or bot granted directly is also in.
WEBSITE_ACCESS = obj(
    {
        'subject': 's',
        'kind': {'enum': ['person', 'bot', 'group']},
        'id': 's',
        'name': 's',
        'initials': 's',
        'members': 'i',
        'via': items(scalar('s')),
    },
    required=['subject', 'kind', 'id', 'name', 'initials'],
)


WEBSITE_ACTIVITY_ROW = obj(
    {
        'id': 's',
        'at': 's',
        'who': WEBSITE_WHO,
        'verb': {'enum': ['open', 'fill', 'copy', 'code', 'needs_you', 'control', 'sign_in', 'download', 'upload']},
        'page': 's',
        'account': {'enum': ['shared', 'own']},
        'where': {'enum': ['central', 'own']},
        'result': {'enum': ['sent', 'refused', 'failed', 'use']},
    }
)


SCHEMAS['ToolIndexRow'] = obj(
    {
        'id': 's',
        'kind': {
            'enum': [
                'api',
                'mailbox',
                'number',
                'address',
                'slack',
                'github',
                'meetings',
                'listening',
                'webhook',
                'declared',
                'website',
            ]
        },
        'name': 's',
        'detail': 's',
        'way': 's',
        'status': TOOL_STATUS,
        'who': items(
            obj(
                {'kind': {'enum': ['bot', 'person', 'group']}, 'id': 's', 'name': 's', 'initials': 's', 'members': 'i'},
                required=['kind', 'id', 'name', 'initials'],
            )
        ),
        'who_total': 'i',
        'needs_you': 'b',
        'href': 's',
        'private': {'type': 'boolean', 'description': 'An API tool whose Who can see it is Only listed'},
        'service': {
            'type': 'string',
            'description': "An API or website tool's service, as stored (a person may set it)",
        },
        'vendor': {
            'type': 'string',
            'description': "Who makes it (google, aws, raygun); Tools lists one row a vendor. Empty for a row that is "
            'no vendor\'s (a mailbox, a number, Tico\'s own)',
        },
        'vendor_name': {'type': 'string', 'description': 'The vendor, by name'},
        'delivery': {
            'enum': ['proxy', 'computer'],
            'description': 'An API tool: proxy when Tico holds its key and adds it to each call',
        },
    },
    required=['id', 'kind', 'name', 'detail', 'way', 'status', 'who', 'who_total', 'needs_you', 'href'],
)

SCHEMAS['ToolIndex'] = obj(
    {
        'rows': items(ref('ToolIndexRow')),
        'counts': obj({'all': 'i', 'needs_you': 'i', 'api': 'i', 'mail': 'i', 'websites': 'i'}),
        'next': 'n',
    }
)

SCHEMAS['ToolVendor'] = obj({'vendor': 's', 'name': 's', 'rows': items(ref('ToolIndexRow'))})

SCHEMAS['ApiToolSettings'] = obj(
    {
        'id': 's',
        'name': 's',
        'service': 's',
        'base_url': 's',
        'auth_scheme': 's',
        'check_path': 's',
        'delivery': {'enum': ['proxy', 'computer']},
        'private': 'b',
        'everyone_reads': {
            'type': 'boolean',
            'description': 'Everyone can read: every bot reads it with no grant, where Read can be held to reads',
        },
        'template': {
            'type': ['object', 'null'],
            'description': 'How its several keys and settings reach the vendor (a known vendor\'s id, or its own): '
            'fields (variable, role: secret, id or setting) and ways (basic, headers, query); null for one key',
        },
    }
)

SCHEMAS['ApiTool'] = obj(
    {
        'tool': ref('ApiToolSettings'),
        'keys': items(
            obj(
                {
                    'id': 's',
                    'name': 's',
                    'preview': 's',
                    'access_level': {'enum': ['read', 'read_write']},
                    'expires_on': 'n',
                    'last_used_at': 'n',
                    'can_remove': 'b',
                },
                required=['id', 'name', 'preview', 'access_level'],
            )
        ),
        'settings': items(
            obj({'id': 's', 'name': 's', 'value': 's', 'updated_at': 's'}),
        )
        | {'description': 'Non-secret values (a host, a region), each a variable every run that holds a level gets'},
        'access': items(
            obj(
                {
                    'subject': 's',
                    'kind': {'enum': ['bot', 'person', 'finance']},
                    'name': 's',
                    'initials': 's',
                    'level': {'enum': ['read', 'read_write']},
                    'last_used_at': 'n',
                    'fixed': {
                        'type': 'boolean',
                        'description': 'Finance reads this key; it goes when the source is disconnected, not here',
                    },
                },
                required=['subject', 'kind', 'name', 'initials', 'level', 'last_used_at'],
            )
        ),
        'checks': items(
            obj({'ok': 'b', 'status_code': {'type': ['integer', 'null']}, 'detail': 's', 'checked_at': 's'})
        ),
        'recent': items({'type': 'object'}),
        'status': TOOL_STATUS,
        'can_manage': 'b',
        'private': 'b',
        'can_set_audience': 'b',
        'audience': {
            'anyOf': [ref('AccessAudience'), {'type': 'null'}],
            'description': 'Only to whoever may change it; null is Everyone',
        },
        'seen_by': items(
            obj(
                {
                    'subject': 's',
                    'kind': {'enum': ['person', 'group', 'bot', 'finance']},
                    'id': 's',
                    'name': 's',
                    'why': {'enum': ['Owner', 'Listed', 'Can use it']},
                }
            )
        )
        | {'description': 'Only to whoever may change it: who can see an Only listed tool, and why'},
        'mcp': {
            'anyOf': [
                obj(
                    {
                        'url': 's',
                        'transport': {'enum': ['http', 'sse']},
                        'auth': {'enum': ['token', 'oauth']},
                        'header': 's',
                        'tools': {'type': ['integer', 'null']},
                        'read_only': 'i',
                        'names': items('s'),
                        'listed_at': 'n',
                        'signed_in_by': 'n',
                        'signed_in_at': 'n',
                        'client': {'enum': ['registered', 'given', None]},
                    }
                ),
                {'type': 'null'},
            ],
            'description': 'A remote MCP server (Tools > Add > MCP server): its address, how it signs in, who signed '
            'in, and the tools its last check listed; null for an API tool',
        },
        'everyone_reads': obj({'on': 'b', 'enforceable': 'b'})
        | {
            'description': 'Every bot reads the tool with no grant while `on` (backend/default_read.py); never where '
            'Read cannot be held to reads (`enforceable` false), whatever the switch says'
        },
    },
    required=['tool', 'keys', 'access', 'checks', 'recent', 'status', 'can_manage', 'private', 'can_set_audience'],
)

SCHEMAS['ApiToolActivity'] = obj(
    {
        'rows': items(
            obj(
                {
                    'id': 's',
                    'kind': {'enum': ['call', 'use']},
                    'actor': 's',
                    'result': {'enum': ['sent', 'refused', 'failed', 'use']},
                    'at': 's',
                }
            )
        ),
        'next': 'n',
    }
)

SCHEMAS['WebsiteToolCreated'] = obj({'id': 's'})

SCHEMAS['WebsiteDeleted'] = obj({'deleted': 'b', 'id': 's'})

SCHEMAS['WebsiteAccountSaved'] = obj({'id': 's'})

SCHEMAS['WebsiteAccountAccessSet'] = obj({'subject': 's', 'on': 'b', 'access': items(WEBSITE_ACCESS)})

SCHEMAS['WebsiteToolGroups'] = items(obj({'id': 's', 'name': 's', 'members': 'i', 'actors': items(scalar('s'))}))

SCHEMAS['WebsiteCode'] = obj({'code': 's', 'expires_in': 'i'})

SCHEMAS['WebsiteToolSettings'] = obj(
    {'id': 's', 'name': 's', 'kind': {'enum': ['website']}, 'sites': items(scalar('s')), 'central_only': 'b'}
)

SCHEMAS['WebsiteToolActivity'] = obj({'rows': items(WEBSITE_ACTIVITY_ROW), 'next': 'n'})

SCHEMAS['WebsiteTool'] = obj(
    {
        'tool': ref('WebsiteToolSettings'),
        'status': obj({'label': 's', 'tone': {'enum': ['ok', 'waiting', 'fail', 'neutral']}}),
        'accounts': items(
            obj(
                {
                    'id': 's',
                    'kind': {'enum': ['shared', 'own']},
                    'username': 's',
                    'has_password': 'b',
                    'has_totp': 'b',
                    'all_bots': 'b',
                    'holder': {'anyOf': [WEBSITE_WHO, {'type': 'null'}]},
                    'access': items(WEBSITE_ACCESS),
                    'session': obj(
                        {
                            'state': {'enum': ['needs_you', 'in_use', 'unknown']},
                            'where': {'type': ['string', 'null'], 'enum': ['central', 'own', None]},
                            'computer': 'n',
                            'by': {'anyOf': [WEBSITE_WHO, {'type': 'null'}]},
                        },
                        required=['state'],
                    ),
                    'can_use': 'b',
                    'can_manage': 'b',
                    'can_open': {
                        'type': 'boolean',
                        'description': "The caller may open the account's live browser (the browser routes' own rule)",
                    },
                    'can_remove': 'b',
                },
                # `username` is left out for a bot, a run or a computer.
                required=[
                    'id',
                    'kind',
                    'has_password',
                    'has_totp',
                    'all_bots',
                    'holder',
                    'access',
                    'session',
                    'can_use',
                    'can_manage',
                    'can_open',
                ],
            )
        ),
        'host': {
            'anyOf': [obj({'runner_id': 's', 'label': 's', 'online': 'b'}), {'type': 'null'}],
            'description': 'The company browser host, null when none is set',
        },
        'recent': items(WEBSITE_ACTIVITY_ROW),
        'can_manage': 'b',
        'private': 'b',
        'can_set_audience': 'b',
        'audience': {
            'anyOf': [ref('AccessAudience'), {'type': 'null'}],
            'description': 'Only to whoever may change it; null is Everyone',
        },
        'seen_by': items(
            obj(
                {
                    'subject': 's',
                    'kind': {'enum': ['person', 'group', 'bot', 'finance']},
                    'id': 's',
                    'name': 's',
                    'why': {'enum': ['Owner', 'Listed', 'Can use it']},
                }
            )
        )
        | {'description': 'Only to whoever may change it: who can see an Only listed tool, and why'},
    },
    required=['tool', 'status', 'accounts', 'host', 'recent', 'can_manage', 'private', 'can_set_audience'],
)

SCHEMAS['BrowserOpened'] = obj({'ok': 'b', 'where': {'enum': ['central', 'own']}})

SCHEMAS['BrowserControlState'] = obj({'ok': 'b', 'control': 'b'})

SCHEMAS['BrowserInputDone'] = obj({'ok': 'b'})
