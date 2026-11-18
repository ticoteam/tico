# pyright: strict
"""The answers of the Cards and pins routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS['Card'] = obj(
    {
        'id': 'n',
        'template': {
            'enum': [
                'tasks_by_status',
                'done_per_week',
                'needs_you',
                'spend',
                'kpi',
                'automation_runs',
                'latest_pages',
                'needs_attention',
                'unread_threads',
                'my_tasks',
                'updates',
                'growth_number',
                'growth_funnel',
                'growth_channels',
                'growth_retention',
                'growth_board',
                'floor',
            ]
        },
        'title': 's',
        'heading': 's',
        'name': 's',
        'team_name': 's',
        'settings': 'o',
        'hidden': 'b',
        'data': {'type': ['object', 'null'], 'description': 'What the card shows the caller; null when hidden'},
    },
    required=['id', 'template', 'heading', 'settings', 'hidden', 'data'],
)

SCHEMAS['CardList'] = obj({'cards': items(ref('Card')), 'next': 'n'})

SCHEMAS['Place'] = obj(
    {
        'place': 's',
        'kind': {'enum': ['dashboard', 'growth', 'team', 'bot', 'task', 'person']},
        'saved': 'b',
        'can_edit': 'b',
        'can_link': {
            'type': 'boolean',
            'description': 'Task places: whether the reader may link a readable page; does not grant card or pin management',
        },
        'month': {'type': 'string', 'description': 'growth: the month its cards read (YYYY-MM)'},
        'months': {
            'type': 'array',
            'items': {'type': 'string'},
            'description': 'growth: the newest 24 months with cohort rows, oldest first',
        },
        'pins': items(
            obj(
                {
                    'id': 'n',
                    'item': 'n',
                    'position': 'i',
                    'size': {'enum': ['small', 'medium', 'large']},
                    'card': ref('Card'),
                    'can_unpin': {'type': 'boolean', 'description': 'Task pins: whether this reader may unlink it'},
                    'page': obj({'file_id': 's', 'title': 's', 'bot': 's', 'updated_at': 's', 'archived': 'b'}),
                },
                required=['id', 'item', 'position', 'size'],
            )
        ),
        'audience': {'type': 'object', 'description': 'A team page: who sees it (docs/permissions.md)'},
        'name': 's',
        'group': 's',
    },
    required=['place', 'kind', 'saved', 'can_edit', 'pins'],
)
