# pyright: strict
"""The answers of the Team routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, obj

SCHEMAS.update(
    {
        "Config": obj(
            {
                "company_name": "s",
                "app_name": "s",
                "assistant_name": "s",
                "assistant_bot": "s",
                "public_url": "s",
                "owner_email": "s",
                "version": "s",
                "administration_url": {
                    "type": "string",
                    "description": "Signed-in humans: the Tico account portal "
                    "URL, or empty for an install without a Tico account or portal company. "
                    "Open externally; carries no sign-in credentials.",
                },
                "visual_origin": {
                    "type": "string",
                    "description": "Configured non-secret visual origin, or empty "
                    "when absent or invalid; grants no native IPC permission.",
                },
                "visuals": {
                    "type": "string",
                    "enum": ["full", "hosted", "media"],
                    "description": "How visuals show, from the setup (no switch): full with a separate "
                    "visual address; hosted without one while Tico file hosting takes new "
                    "files (pages and SVG inline from the company's files address); media "
                    "otherwise (pictures, video and audio inline, pages and SVG open in the "
                    "file viewer).",
                },
                "visual_files": {
                    "type": "string",
                    "description": "Signed-in callers: the Tico file hosting origin "
                    "pages and SVG show from when there is no visual address, else empty; grants no "
                    "native IPC permission.",
                },
                "s3_view_urls": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Signed-in callers: bucket -> https base an s3://bucket/key opens at "
                    "(base + '/' + key, each segment URL-encoded; TICO_S3_VIEW_URLS)",
                },
            },
            required=[
                "company_name",
                "app_name",
                "assistant_name",
                "assistant_bot",
                "public_url",
                "owner_email",
                "version",
            ],
        ),
        # A Tico Cloud company's access as every page reads it (backend/cloud_access.py view and shown). It is also the
        # `cloud` of a 402 `locked` refusal (the shared Problem).
        "CloudAccess": obj(
            {
                "access": {
                    "type": "string",
                    "enum": ["open", "frozen", "locked"],
                    "description": "open: working; frozen: out of credit, no new turn on a Tico model; locked: pay to "
                    "continue, people's calls answer 402 `locked` but for sign-in, this read, billing and the download",
                },
                "reason": {"type": ["string", "null"], "enum": [None, "out_of_credit", "trial_over", "unpaid"]},
                "plan": "s",
                "trial_days_left": {"type": ["integer", "null"]},
                "billing_url": {
                    "type": ["string", "null"],
                    "description": "Where to pay or add credit: an https page at Tico HQ, or null",
                },
                "days_left": {
                    "type": ["integer", "null"],
                    "description": "While open with under two days of spend left: about how many; else null",
                },
            }
        ),
        "CloudAccessView": obj(
            {
                "cloud": {"oneOf": [ref("CloudAccess"), {"type": "null"}]}
                | {"description": "Null for a company that is not on Tico Cloud, or before Tico HQ has said"}
            }
        ),
        "PreferenceValue": obj({"key": "s", "value": {"description": "Any JSON value, or null when unset"}}),
        # The audit trail (backend/audit_log.py).
        "AuditPage": {
            "type": "object",
            "required": ["rows", "next"],
            "properties": {
                "rows": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                "next": {"type": ["string", "null"]},
            },
        },
    }
)
