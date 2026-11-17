# pyright: strict
"""The answers of the Sleep when idle routes (backend/openapi_v2.py gathers them)."""

from ._base import SCHEMAS, obj

SCHEMAS.update(
    {
        'SleepWhenIdle': obj(
            {
                'sleep': obj(
                    {
                        'hosted': 'b',
                        'layout': {'type': ['string', 'null']},
                        'hours': 'i',
                        'minutes': 'i',
                        'source': {'enum': ['cell', 'owner', 'default']},
                        'managed': 'b',
                        'runners': 'b',
                        'active': 'b',
                        'idle_since': {'type': ['string', 'null']},
                        'blocker': 's',
                        'wake_at': {'type': ['string', 'null']},
                        'last_request': {'type': ['object', 'null']},
                        'last_error': 's',
                    }
                )
            }
        ),
    }
)
