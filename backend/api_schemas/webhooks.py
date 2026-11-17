# pyright: strict
"""The answers of the Webhooks routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        'Webhook': obj(
            {
                'id': 's',
                'name': 's',
                'url': 's',
                'host': 's',
                'events': items({'type': 'string'}),
                'enabled': 'b',
                'paused_reason': 's',
                'rate_per_hour': 'i',
                'allow_http': 'b',
                'created_by': 's',
                'requested_by': 'n',
                'created_at': 's',
                'updated_at': 's',
                'revision': 'i',
                'old_secret_until': 'n',
                'can_change': 'b',
                'last_7_days': obj({'delivered': 'i', 'failed': 'i', 'pending': 'i'}),
                'filter': {'type': 'object', 'description': 'departments, bots, pipelines; an empty list means all'},
                'headers': items(
                    {'type': 'object', 'description': 'name, secret, and value unless secret (then set: true)'}
                ),
                'disabled_at': 'n',
                'disabled_reason': 'n',
            }
        ),
        'WebhookSendRow': obj(
            {
                'id': 's',
                'webhook_id': 's',
                'event_id': 's',
                'type': 's',
                'subject': 's',
                'state': {'enum': ['ready', 'sending', 'delivered', 'failed', 'canceled', 'withheld']},
                'attempts': 'i',
                'last_status': {'type': ['integer', 'null']},
                'last_error': 's',
                'next_attempt_at': 'n',
                'created_at': 's',
                'delivered_at': 'n',
            }
        ),
        'WebhookList': obj(
            {'items': items(ref('Webhook')), 'more': 'b', 'next_offset': 'i'}, required=['items', 'more']
        ),
        'WebhookCreated': obj(
            {
                'webhook': {
                    **ref('Webhook'),
                    'description': 'With `secret` once; `replayed: true` on a retry, without it',
                },
                'approval': {'type': 'object', 'description': 'A bot\'s request: {id, status: pending}'},
            },
            required=[],
        ),
        'WebhookShown': obj({'webhook': ref('Webhook'), 'recent_attempts': items({'type': 'object'})}),
        'WebhookChanged': obj({'webhook': ref('Webhook')}),
        'WebhookEnabled': obj(
            {
                'webhook': ref('Webhook'),
                'recover': {'type': ['object', 'null'], 'description': '{since, failed} after an automatic turn-off'},
            }
        ),
        'WebhookRecovered': obj({'webhook': 's', 'since': 's', 'recovered': 'i', 'more': 'b'}),
        'WebhookReplayed': obj({'webhook': 's', 'since': 's', 'checked': 'i', 'queued': 'i', 'more': 'b'}),
        'WebhookQueued': obj({'queued': 'i', 'type': 's'}),
        'WebhookSends': obj(
            {'items': items(ref('WebhookSendRow')), 'more': 'b', 'next_offset': 'i'}, required=['items', 'more']
        ),
    }
)
