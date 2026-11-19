# pyright: strict
"""The answers of the Slack links routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS['SlackLink'] = obj(
    {
        'id': 's',
        'thread_id': 's',
        'channel': obj({'id': 's', 'name': 's', 'private': 'b'}),
        'audience': 's',
        'state': {'enum': ['requested', 'active', 'suspended', 'refused', 'unlinked']},
        'reason': 's',
        'history': obj({'state': {'enum': ['off', 'pending', 'running', 'done', 'failed']}, 'count': 'i'}),
        'linked_by': 's',
        'read_only': 'b',
        'created_at': 's',
        'updated_at': 's',
    }
)

SCHEMAS['SlackLinkList'] = obj({'links': items(ref('SlackLink')), 'next_cursor': 'n', 'available': 'b'})
