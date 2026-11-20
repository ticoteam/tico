# pyright: strict
"""The answers of the Calendar routes (backend/openapi_v2.py gathers them)."""

from typing import Any

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

_CAL_OBJ = obj({"ref": "s", "type": "s", "id": "s", "name": "s"})


_CAL_ERRORS = items(obj({"ref": "s", "reason": "s"}))


def _optional(schema: dict[str, Any], *names: str) -> dict[str, Any]:
    """The schema with these properties left out of `required`: only some answers carry them."""
    return {**schema, "required": [k for k in schema["required"] if k not in names]}


SCHEMAS.update(
    {
        "CalendarOccurrence": _optional(
            obj(
                {
                    "occurrence_id": "s",
                    "event_id": "n",
                    "calendar": _CAL_OBJ,
                    "home": {"type": "string", "enum": ["google", "tico", "derived"]},
                    "kind": {"type": "string", "enum": ["event", "run", "due", "away"]},
                    "title": "s",
                    "start": "s",
                    "end": "s",
                    "all_day": "b",
                    "time_zone": "n",
                    "attendees": "a",
                    "response": "n",
                    "busy_only": "b",
                    "recurring": "b",
                    "status": "s",
                    "meeting_url": "n",
                    "open_url": "n",
                    "links": obj({"thread": "n", "meeting": "n", "task": "n", "automation": "n", "attempt": "n"}),
                    "can": obj({"join": "b", "edit": "b"}),
                },
                bound={
                    "type": "string",
                    "enum": ["not_before", "due_by"],
                    "description": "A matter's key date (due:matter:<task id>:<date id>:<bound>): not_before is the earliest its step may "
                    "be done, never a deadline; due_by is the deadline",
                },
            ),
            "bound",
        ),
        "CalendarView": obj(
            {
                "ref": "s",
                "type": "s",
                "id": "s",
                "name": "s",
                "home": "n",
                "time_zone": "n",
                "work_starts": "n",
                "work_ends": "n",
                "work_days": "n",
                "can_write": "b",
            }
        ),
        "CalendarEvents": obj(
            {
                "from": "s",
                "to": "s",
                "calendars": items(ref("CalendarView")),
                "occurrences": items(ref("CalendarOccurrence")),
                "errors": _CAL_ERRORS,
            },
            month_counts=items(obj({"bot_id": "s", "date": "s", "count": "i"})),
        ),
        "CalendarList": obj({"calendars": items(ref("CalendarView")), "errors": _CAL_ERRORS}),
        "CalendarSearch": obj({"occurrences": items(ref("CalendarOccurrence")), "partial": "b", "errors": _CAL_ERRORS}),
        "CalendarFree": obj({"slots": items(obj({"start": "s", "end": "s"})), "errors": _CAL_ERRORS}),
        "CalendarSettingsView": obj(
            {
                "time_zone": "s",
                "time_zone_set": "b",
                "work_starts": "s",
                "work_ends": "s",
                "work_days": "s",
            }
        ),
    }
)
