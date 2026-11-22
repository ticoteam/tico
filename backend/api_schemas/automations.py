# pyright: strict
"""The answers of the Automations routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, merge, obj

_RUN = obj(
    {
        "id": "s",
        "automation_id": "s",
        "version": "i",
        "trigger_ref": "s",
        "started_at": "s",
        "finished_at": {"type": ["string", "null"]},
        "status": {"enum": ["queued", "running", "waiting", "ok", "partial", "failed", "empty", "skipped", "held"]},
        "cost": "f",
        "summary": "n",
        "steps": {
            "type": ["array", "null"],
            "items": {"type": "object"},
            "description": "Each step's status, outputs and cost; null after 30 days",
        },
    },
    required=["id", "automation_id", "version", "status", "steps"],
)


SCHEMAS.update(
    {
        "Automation": obj(
            {
                "id": "s",
                "name": "s",
                "owner_id": "s",
                "created_by_id": {"type": "string", "description": "Who created it: the person or bot it was made for"},
                "created_via": {
                    "type": "string",
                    "enum": ["botops", "assistant", "token"],
                    "description": "How it was made when not by a direct call: Agent Ops (botops), the Assistant, or a "
                    "personal token. Absent for a direct call",
                },
                "enabled": "b",
                "action": {"type": "object", "description": "Assistant Message create: the pending Confirm card"},
                "message_id": "s",
                "needs_confirm": "b",
                "trigger": {"type": "object"},
                "steps": items({"type": "object"}),
                "version": "i",
                "can_edit": "b",
                "can_pause": "b",
                "destinations": {
                    "type": ["array", "null"],
                    "items": {"type": "object"},
                    "description": "Named chains a filter rule's `to` runs (see the automation guide)",
                },
                "spend_cap": {"type": ["object", "null"]},
                "batch": {"type": ["object", "null"]},
                "last_run": {"type": ["object", "null"]},
                "counts": obj({"today": obj({"runs": "i", "dropped": "i"})}),
                "next_run": {"type": ["string", "null"]},
                "next_due_at": {
                    "type": ["string", "null"],
                    "description": "A due-date trigger's next due day (YYYY-MM-DD); next_run is when its run starts",
                },
                "pending": "i",
                "warning": "s",
                "kind": {
                    "type": "string",
                    "description": "The trigger as the overview groups it; system for Tico's own jobs",
                },
                "problems": {
                    "type": "array",
                    "description": "Wake rules that name a step their task type "
                    "does not have (they never fire until changed): rule id, the step named and why",
                    "items": obj({"rule": "s", "step": "s", "why": "s"}),
                },
                "paused": {"type": "boolean", "description": "Off because an earlier run guard paused it"},
                "hold": {
                    **obj(
                        {
                            "rule": {"enum": ["flood", "sustained"]},
                            "since": "s",
                            "held": "i",
                            "dropped": "i",
                            "task": {"type": ["string", "null"]},
                        }
                    ),
                    "description": "Present while the run guard keeps its new runs held, not run, until "
                    "someone releases or drops them; `task` is the Needs-you task",
                },
                "schedule": {"type": "string", "description": "A system job's schedule in words"},
            },
            required=["id", "can_edit"],
        ),
        "AutomationList": obj({"automations": items(ref("Automation")), "next": {"type": ["integer", "null"]}}),
        "AutomationDeleted": obj({"id": "s", "deleted": "b"}),
        "AutomationRun": _RUN,
        "AutomationRunNow": obj({"run": ref("AutomationRun")}),
        "AutomationRuns": obj(
            {
                "runs": items(ref("AutomationRun")),
                "next": {"type": ["string", "null"]},
                "daily": items(obj({"day": "s", "runs": "i", "dropped": "i"})),
            }
        ),
        "AutomationOverviewRuns": obj(
            {
                "runs": items(
                    obj(
                        {
                            "id": "s",
                            "automation_id": "s",
                            "automation_name": "s",
                            "trigger": {
                                "enum": [
                                    "message",
                                    "task_received",
                                    "schedule",
                                    "webhook",
                                    "listening",
                                    "event",
                                    "due",
                                    "system",
                                ]
                            },
                            "owner_id": "s",
                            "started_at": "s",
                            "finished_at": {"type": ["string", "null"]},
                            "status": "s",
                            "cost": "f",
                            "started_by": "s",
                            "outcome": "s",
                            "step_count": {"type": ["integer", "null"]},
                            "count": {"type": "integer", "description": "Quiet system-job runs folded into this row"},
                        },
                        required=["id", "automation_id", "automation_name", "trigger", "started_at", "status"],
                    )
                ),
                "next": {"type": ["string", "null"]},
                "since": "s",
            }
        ),
        "AutomationStats": obj(
            {
                "range": "s",
                "since": "s",
                "until": "s",
                "bucket_seconds": "i",
                "automations": items(
                    obj(
                        {
                            "id": "s",
                            "runs": "i",
                            "failed": "i",
                            "cost": "f",
                            "buckets": items(obj({"ok": "i", "failed": "i"})),
                        }
                    )
                ),
                "totals": obj(
                    {"runs": "i", "failed": "i", "cost": "f", "buckets": items(obj({"ok": "i", "failed": "i"}))}
                ),
            }
        ),
        "AutomationVersions": obj(
            {
                "versions": items(
                    obj(
                        {
                            "n": "i",
                            "actor": "s",
                            "created_at": "s",
                            "status": {"enum": ["active", "pending"]},
                            "summary": "s",
                            "flow": {"type": "object"},
                        },
                        required=["n", "actor", "created_at", "status", "flow"],
                    )
                ),
                "next": {"type": ["integer", "null"]},
            }
        ),
        "AutomationGuide": obj({"text": "s"}),
        "QuestionSets": obj(
            {
                "sets": items(
                    obj(
                        {
                            "id": "s",
                            "summary": "s",
                            "questions": {
                                "type": "object",
                                "description": "Keyed by question id, in judge.py's format",
                            },
                        }
                    )
                )
            }
        ),
        "AutomationTemplates": obj(
            {"templates": items(obj({"id": "s", "name": "s", "destinations": items({"type": "string"})}))}
        ),
        "AutomationTemplate": obj(
            {
                "name": "s",
                "owner": "s",
                "enabled": "b",
                "trigger": "o",
                "steps": items({"type": "object"}),
                "destinations": items({"type": "object"}),
            }
        ),
        "InboxSwitch": obj({"inbox": "s", "send": "b"}),
        "PersonAssistant": obj({"bot": {"type": ["string", "null"]}}),
    }
)

SCHEMAS["AutomationHeld"] = merge(
    SCHEMAS["Automation"], moved={"type": "integer", "description": "Runs released or dropped"}
)
