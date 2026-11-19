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
        # One place meetings come from (media.py sources): Import and Close always, then each importer that is on.
        "MeetingSource": obj(
            {
                "id": {"type": "string", "description": "import, close, or an importer's source (zoom, granola...)"},
                "name": "s",
                "status": {"type": "string", "enum": ["available", "needs_setup", "syncing", "delayed", "error"]},
                "last_succeeded_at": "n",
                "last_attempt": "n",
                "last_import": "n",
                "pending": "i",
                "imported": "i",
                "error_code": "s",
                "error": {"type": "string", "description": "Why an importer fails, in words; empty when it does not"},
            },
            required=["id", "name", "status", "last_succeeded_at"],
        ),
        "MeetingSourceList": obj({"sources": items(ref("MeetingSource"))}),
        # One meeting importer and how it is doing (meeting_importers.py); the owner's Settings read.
        "MeetingImporter": obj(
            {
                "source": "s",
                "name": "s",
                "enabled": "b",
                "runner_id": {"type": "string", "description": "The computer that runs it; empty when none is chosen"},
                "runner_label": "s",
                "status": {"type": "string", "enum": ["off", "waiting", "syncing", "delayed", "error"]},
                "last_succeeded_at": "n",
                "last_attempt": "n",
                "last_import": "n",
                "imported_total": "i",
                "error_code": "s",
                "error": "s",
                "setup": obj({"file": "s", "keys": items("s"), "doc": "s"})
                | {"description": "Where its credentials go on that computer: the file, its keys and the setup doc"},
            }
        ),
        "MeetingImporterComputer": obj({"id": "s", "label": "s", "platform": "s", "online": "b", "last_seen_at": "n"}),
        "MeetingImporterList": obj(
            {"importers": items(ref("MeetingImporter")), "computers": items(ref("MeetingImporterComputer"))}
        ),
        "MeetingImporterSaved": obj({"ok": "b"}),
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
