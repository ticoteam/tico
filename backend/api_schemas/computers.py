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
        # A computer's Screen setting (backend/screen.py): the mode now stored, and whether the computer offers a screen.
        "ComputerScreen": obj(
            {
                "screen": {
                    "type": "string",
                    "enum": ["off", "view", "control"],
                    "description": "Who may open the screen from the app: nobody (off), to watch (view), or to watch "
                    "and drive it (control); only the computer's operator and admins open it at all",
                },
                "screen_available": {
                    "type": "boolean",
                    "description": "Whether the computer's last heartbeat offered a screen; a mode other than off needs it",
                },
            }
        ),
        # One screen opening (backend/screen.py open_screen). The ticket is the relay's; nothing on the hub keeps it.
        "ScreenSession": obj(
            {
                "session_id": "s",
                "control": {
                    "type": "boolean",
                    "description": "Whether this opening drives the computer or only watches",
                },
                "ws_path": {
                    "type": "string",
                    "description": "The same-origin path of the screen's WebSocket, `/api/v2/runners/{id}/screen/ws?"
                    "ticket=...`. Its ticket is single-use and good for a minute: connect at once, never log or keep "
                    "the path, and open a new session to reconnect",
                },
                "width": {"type": "integer", "description": "The computer's display, in pixels"},
                "height": "i",
            },
            required=["session_id", "control", "ws_path", "width", "height"],
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
