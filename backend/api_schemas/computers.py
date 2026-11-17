# pyright: strict
"""The answers of the Computers routes (backend/openapi_v2.py gathers them)."""

from ._base import SCHEMAS, items, obj, scalar

SCHEMAS.update(
    {
        # A goal with what the page adds to it, a link with what says how it is doing: one object each, so a client sees every field.
        "BrowserExit": obj(
            {
                "sharing": "b",
                "bots": items(scalar("s")),
                "daily_cap_bytes": "i",
                "computer": obj({"id": "s", "label": "s"}),
                "used_today_bytes": {"type": ["integer", "null"]},
                "may_change": "b",
                "may_turn_off": "b",
            }
        ),
        "BrowserExitUsage": obj(
            {
                "rows": items(obj({"day": "s", "bot": "s", "host": "s", "bytes": "i", "tunnels": "i"})),
                "days": items(obj({"day": "s", "bytes": "i"})),
                "next": {"type": ["integer", "null"]},
            }
        ),
    }
)
