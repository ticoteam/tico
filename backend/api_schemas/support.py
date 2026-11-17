# pyright: strict
"""The answers of the Support routes (backend/openapi_v2.py gathers them)."""

from ._base import SCHEMAS, obj

SCHEMAS.update(
    {
        "SupportReportResult": obj(
            {
                "status": {
                    "enum": ["sent", "pending"],
                    "description": "sent: filed now; pending: a person OKs it first",
                },
                "task_id": {
                    "type": ["string", "null"],
                    "description": "The task the report is on, or that holds its OK",
                },
                "requirement_id": {"type": "string", "description": "pending: the OK a person gives on the task"},
                "ticket": {"type": "object", "description": "sent: the ticket as filed"},
                "filed_as": {"type": "string", "description": "sent: the person it was filed as"},
            },
            required=["status", "task_id"],
        ),
    }
)
