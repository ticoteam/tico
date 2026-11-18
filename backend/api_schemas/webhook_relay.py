# pyright: strict
"""The answers of the Webhook relay routes (backend/openapi_v2.py gathers them)."""

from ._base import SCHEMAS, obj

SCHEMAS.update(
    {
        'WebhookRelayState': obj(
            {
                'relay': obj(
                    {
                        'key': 's',
                        'relays': 'i',
                        'cursor': 'i',
                        'last_pull': {'type': ['string', 'null']},
                        'last_error': 's',
                        'pending': {'type': ['integer', 'null']},
                        'oldest': {'type': ['string', 'null']},
                        'deleting': 'i',
                        'active': 'b',
                    }
                )
            }
        ),
        'WebhookRelayAddress': obj(
            {'relay': obj({'trigger': 's', 'relay_id': 's', 'address_prefix': 's', 'address': 's', 'replayed': 'b'})}
        ),
        'WebhookRelayWake': obj({'relay': obj({'trigger': 's', 'relay_id': 's', 'wake': 'b'})}),
        'WebhookRelayDeleted': obj({'relay': obj({'trigger': 's', 'deleted': 'b', 'gateway_told': 'b'})}),
        'WebhookWakeUrl': obj(
            {
                'url': {'type': ['string', 'null']},
                'set_at': {'type': ['string', 'null']},
                'set_by': {'type': ['string', 'null']},
            }
        ),
        'WebhookWakeUrlSet': obj({'url': {'type': ['string', 'null']}, 'secret': {'type': ['string', 'null']}}),
    }
)
