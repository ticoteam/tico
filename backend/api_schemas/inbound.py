# pyright: strict
"""The answers of the Inbound routes (backend/openapi_v2.py gathers them)."""

from ._base import NUM_N, SCHEMAS, items, obj

SCHEMAS.update(
    {
        'InboundConnectionCompany': {
            **obj(
                {
                    'company_id': {'type': 'string', 'format': 'uuid'},
                    'name': {'type': 'string', 'minLength': 1, 'maxLength': 100},
                }
            ),
            'additionalProperties': False,
        },
        'InboundCompanyConnection': {
            'oneOf': [
                {
                    **obj(
                        {
                            'connected': {'enum': [False]},
                            'scope': {'enum': ['inbox']},
                            'company': {'$ref': '#/components/schemas/InboundConnectionCompany'},
                            'expires_at': {'type': 'number'},
                        }
                    ),
                    'additionalProperties': False,
                },
                {
                    **obj(
                        {
                            'connected': {'enum': [True]},
                            'scope': {'enum': ['inbox']},
                            'company': {'$ref': '#/components/schemas/InboundConnectionCompany'},
                        }
                    ),
                    'additionalProperties': False,
                },
            ]
        },
        'InboundCursor': obj({'cursor': 'i'}),
        'InboundInbox': obj(
            {
                'inbox_id': 's',
                'kind': 's',
                'name': 's',
                'address': 's',
                'state': 's',
                'audience': {'enum': ['company', 'team']},
                'group_id': {'type': ['string', 'null']},
            }
        ),
        'InboundInboxes': obj(
            {
                'inboxes': items({'type': 'object'}),
                'configured': 'b',
                'last_synced_at': NUM_N,
                'error': 's',
                'gaps': items({'type': 'object'}),
            }
        ),
        'InboundMessages': obj(
            {'messages': items({'type': 'object'}), 'next': {'type': ['integer', 'null']}, 'cursor': 'i'}
        ),
        'InboundPhoneRequests': obj(
            {'phone_requests': items({'type': 'object'}), 'next': {'type': ['string', 'null']}}
        ),
    }
)
