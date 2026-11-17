# pyright: strict
"""The answers of the Meetings routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

GRANOLA_SKIP_REASONS = {
    "type": "object",
    "additionalProperties": {"type": "integer"},
    "description": "Skipped items in the last attempt, by fixed code and step",
}


GRANOLA_ERROR_DETAIL = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "description": "Fixed facts about the last failure or skip; never provider text",
    "properties": {
        "step": {"type": "string"},
        "http_status": {"type": ["integer", "null"]},
        "rpc_code": {"type": ["integer", "null"]},
        "tool_error": {"type": "boolean"},
        "retry_after": {"type": ["integer", "null"]},
        "signal": {"type": ["string", "null"], "enum": ["rate limit", "slow down", "too many requests", None]},
        "batch": {"type": ["integer", "null"]},
    },
}


SCHEMAS.update(
    {
        "GranolaStatus": obj(
            {
                "mode": {"type": "string", "enum": ["account", "api_key", "off"]},
                "connected": "b",
                "email": "n",
                "plan_hint": {"type": ["string", "null"], "enum": ["free", "paid", None]},
                "last_synced_at": "n",
                "last_error": "n",
                "imported_count": "i",
                "needs_signin": "b",
                "syncing": "b",
                "skipped": "i",
            },
            skip_reasons=GRANOLA_SKIP_REASONS,
            last_error_detail=GRANOLA_ERROR_DETAIL,
        ),
        "GranolaSignIn": obj(
            {
                "state": "s",
                "mode": "s",
                "connected": "b",
                "email": "n",
                "plan_hint": "n",
                "last_synced_at": "n",
                "last_error": "n",
                "imported_count": "i",
                "needs_signin": "b",
                "syncing": "b",
                "skipped": "i",
            },
            skip_reasons=GRANOLA_SKIP_REASONS,
            last_error_detail=GRANOLA_ERROR_DETAIL,
        ),
        "GranolaDevice": obj(
            {
                "user_code": "s",
                "verification_uri": "s",
                "verification_uri_complete": "s",
                "expires_in": "i",
                "interval": "i",
            },
            required=["user_code", "verification_uri", "expires_in", "interval"],
        ),
        "GranolaSync": obj(
            {"state": {"type": "string", "enum": ["syncing", "recent", "off", "needs_signin"]}, "last_synced_at": "n"}
        ),
        "GranolaDisconnect": obj({"ok": "b"}),
        "MeetingSearch": obj({"results": "a", "next_offset": {"type": ["integer", "null"]}, "mode": "s"}),
        "ReviewedMeeting": obj(
            {"id": "s", "title": "s", "review_state": {"type": "string", "enum": ["pending", "live", "dismissed"]}}
        ),
        "MeetingReviewList": obj({"meetings": items(ref("ReviewedMeeting")), "count": "i", "pending_count": "i"}),
        "MyRecorder": obj(
            {
                "version": {
                    "type": "string",
                    "description": "Your most recently used copy's version; empty when none was seen",
                },
                "last_seen_at": "n",
                "recent": {"type": "boolean", "description": "A copy was seen in the last 30 days"},
                "latest": {"type": "string", "description": "The newest release's version; empty when unknown"},
                "update_available": "b",
                "download_url": "s",
            }
        ),
        "MeetingReviewPreferences": obj(
            {
                "auto_share": {"type": ["boolean", "null"]},
                "review_default": {"type": "string", "enum": ["review", "auto"]},
                "effective_auto_share": "b",
            }
        ),
        'MeetingTaskProposalAccepted': obj(
            {'item': obj({'id': 's', 'proposed_task_id': 'n', 'result_ref': 'n', 'status': 's'}), 'pushed': 'b'}
        ),
        'MeetingTaskLink': obj(
            {
                'task_id': 's',
                'number': {'type': ['integer', 'null']},
                'title': 's',
                'status': 's',
                'kind': {'enum': ['linked', 'sent', 'proposal']},
                'created_by': 's',
            }
        ),
        'MeetingTaskLinks': obj({'tasks': items(ref('MeetingTaskLink')), 'next_cursor': 'n'}),
        'MeetingTaskLinkBatch': obj(
            {
                'tasks': items(ref('MeetingTaskLink')),
                'next_cursor': 'n',
                'results': items(obj({'status': {'enum': [200, 404]}})),
            }
        ),
        'MeetingTaskRemoved': obj({'ok': 'b'}),
    }
)
