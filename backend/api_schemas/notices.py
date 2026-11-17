# pyright: strict
"""The answers of the Notices routes (backend/openapi_v2.py gathers them)."""

from ._base import SCHEMAS, items, obj

SCHEMAS['NoticeList'] = obj(
    {
        'notices': items(
            obj(
                {
                    'id': 's',
                    'kind': 's',
                    'body': 's',
                    'url': 's',
                    'sent_at': 's',
                    'received_at': 's',
                    'source': {'enum': ['relay', 'latest']},
                }
            )
        )
    }
)
