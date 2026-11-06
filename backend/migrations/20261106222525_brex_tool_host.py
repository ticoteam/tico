"""Brex tools move to the host Finance uses. A Brex tool made before then took the legacy host platform.brexapis.com
from backend/tool_services.py, so its check and proxy called a different API than Finance's sync (backend/finance/
brex.py, api.brex.com). Each Brex tool still on the legacy host takes the new one; a tool someone pointed elsewhere keeps
its address. Idempotent."""

LEGACY = "https://platform.brexapis.com"
HOST = "https://api.brex.com"


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tools'").fetchone():
        return
    conn.execute(
        "UPDATE tools SET base_url=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
        "WHERE service='brex' AND rtrim(base_url, '/')=?",
        (HOST, LEGACY),
    )
