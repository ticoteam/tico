# pyright: strict
"""The answers of the Rules routes (backend/openapi_v2.py gathers them)."""

from ._base import SCHEMAS, items, obj

_RULE_IDS = items({'type': 'string'})


SCHEMAS['RulesByTeammate'] = obj(
    {
        'rules': items(
            obj(
                {
                    'id': 's',
                    'kind': {'enum': ['internal', 'external']},
                    'text': 's',
                    'if_broken': {'enum': ['block', 'escalate', 'warn', None]},
                    'can_edit': 'b',
                }
            )
        ),
        'bots': items(obj({'slug': 's', 'who': _RULE_IDS, 'with': _RULE_IDS})),
        'people': items(obj({'id': 's', 'who': _RULE_IDS, 'with': _RULE_IDS})),
    }
)
