# pyright: strict
"""The answers of the Model subscriptions routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import NUM_N, SCHEMAS, items, obj

_SUB_KIND = {
    "enum": ["claude_token", "claude_login", "codex_login", "anthropic_key", "openai_key", "cursor_key", "meta_key"]
}


_SET_WHY = obj({'kind': {'enum': ['soonest', 'held', 'reconnect', 'all_held', 'none']}, 'plan': 'n', 'until': 'n'})


_PERSON_SET = obj({'person': 's', 'person_name': 's', 'plans': 'i', 'on': 'n', 'on_name': 'n', 'why': _SET_WHY})


SCHEMAS['ModelSubscription'] = obj(
    {
        'id': 's',
        'name': 's',
        'kind': _SUB_KIND,
        'runtime': {'enum': ['claude', 'codex', 'cursor', 'musecode']},
        'label': 's',
        'status': {'enum': ['new', 'working', 'needs_reconnect']},
        'status_detail': 's',
        'status_at': 'n',
        'problem': {
            'type': 'string',
            'description': 'What a person must fix on a plan that still counts, in a few words ("Payment past due"), '
            'else empty',
        },
        'preview': {'type': 'string', 'description': 'Visible to admins and, for a plan, its owner'},
        'revision': 'i',
        'computers': items({'type': 'string'}),
        'usage': {
            'anyOf': [
                obj(
                    {
                        'window': {'type': ['string', 'null'], 'enum': ['5h', 'week', None]},
                        'used_pct': NUM_N,
                        'cost_usd_month': NUM_N,
                        'resets_at': 'n',
                        'quota_hold_until': {'type': 'string', 'description': 'Reset of an active quota hold'},
                        'capacity_until': {
                            'type': 'string',
                            'description': 'End of a temporary provider capacity hold',
                        },
                        'reason': {'type': ['string', 'null']},
                        'weekly_resets_at': 'n',
                        'weekly_used_pct': NUM_N,
                        'near': 'b',
                        'models': items(obj({'model': 's', 'used_percent': 'f', 'resets_at': 's'})),
                    },
                    required=['window', 'used_pct', 'cost_usd_month'],
                ),
                {'type': 'null'},
            ]
        },
        'added_by': 's',
        'added_by_name': 's',
        'can_remove': 'b',
        'owner_id': 's',
        'owner_name': 's',
        'created_at': 's',
        'updated_at': 's',
        'order': {'type': ['integer', 'null']},
        'in_use_on': items({'type': 'string'}),
        'available': {'type': 'boolean', 'description': 'Whether the credential can be selected now'},
        'next_in_rotation': {
            'type': 'boolean',
            'description': "An agent using another plan from this pool can use this plan next",
        },
        'agents': {
            **items(obj({'id': 's', 'name': 's', 'fallback': 'b'}, required=['id', 'name', 'fallback'])),
            'description': "Agents the viewer can read whose Paid by selects this provider or its pool for the tool",
        },
        'expired': 'b',
        'reconnect_by': 'n',
    },
    required=[
        'id',
        'name',
        'kind',
        'runtime',
        'label',
        'status',
        'status_detail',
        'status_at',
        'expired',
        'preview',
        'revision',
        'computers',
        'usage',
        'added_by',
        'added_by_name',
        'owner_id',
        'owner_name',
        'created_at',
        'updated_at',
        'order',
        'in_use_on',
    ],
)

SCHEMAS['ModelSubscriptionList'] = obj(
    {
        'subscriptions': items(ref('ModelSubscription')),
        'kinds': items(
            obj(
                {
                    'kind': _SUB_KIND,
                    'label': 's',
                    'runtime': {'enum': ['claude', 'codex', 'cursor', 'musecode']},
                    'secret_label': 's',
                    'help': 's',
                    'connect': 'b',
                    'owned': 'b',
                }
            )
        ),
    }
)

SCHEMAS['ModelSubscriptionDeleted'] = obj(
    {'deleted': 'b', 'id': 's', 'computers': 'i', 'stopped': 'i', 'sessions_reset': 'i'}
)

SCHEMAS['ComputerSubscriptionList'] = obj(
    {
        'assignments': items(
            obj(
                {
                    'runner_id': 's',
                    'runtime': {'enum': ['claude', 'codex', 'cursor', 'musecode']},
                    'subscription_id': 's',
                    'person': 's',
                    'set': {'anyOf': [_PERSON_SET, {'type': 'null'}]},
                    'mixed': 'b',
                }
            )
        ),
        'capable': items({'type': 'string'}),
        'people': items(obj({'id': 's', 'name': 's', 'claude': 'i', 'codex': 'i'}, required=['id', 'name'])),
    }
)

SCHEMAS['AgentSubscriptionList'] = obj(
    {
        'subscriptions': {
            'type': 'object',
            'additionalProperties': obj(
                {
                    'bot': 's',
                    'runtime': {'enum': ['claude', 'codex', 'cursor', 'musecode']},
                    'kind': {'enum': ['pool', 'plan', 'key']},
                    'person': 's',
                    'subscription_id': 's',
                    'set_by': 's',
                    'updated_at': 's',
                }
            ),
        },
    }
)
