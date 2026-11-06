"""Tools: the registry, checks and calls.

Rows come from `sqlite3.Row` (SELECT *); see `backend/rows.py` for the rules."""

from typing import TypedDict


class ToolRow(TypedDict):
    """An API tool (backend/api_tools.py): a service bots call with a key, through the proxy or on computers."""

    id: str
    name: str
    service: str
    base_url: str
    auth_scheme: str
    check_path: str
    delivery: str
    created_by: str
    created_at: str
    updated_at: str
    audience: str | None


class ToolCheckRow(TypedDict):
    """One key's check of its tool: the status the Tools page shows until the next."""

    id: str
    tool_id: str
    credential_id: str | None
    ok: int
    status_code: int | None
    detail: str
    checked_at: str


class ToolCallRow(TypedDict):
    """One proxied call (track B's proxy writes it): the tool's activity, never the key or the body."""

    id: str
    tool_id: str
    credential_id: str | None
    actor: str
    bot: str
    access_level: str
    method: str
    path: str
    status_code: int | None
    result: str
    detail: str
    duration_ms: int | None
    created_at: str


TABLES = {
    ToolRow: "tools",
    ToolCheckRow: "tool_checks",
    ToolCallRow: "tool_calls",
}
