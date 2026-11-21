# pyright: strict
"""What is left to rotate now that write keys of ads, payments and CRM tools live in the proxy (#4892, plan P3/W4).

    python -m backend.proxy_keys <database file>

Read only (backend.migrations.read_only: no byte of the file, its log or its index changes): it prints, as JSON, for
the person who rotates the keys at each vendor:
- `proxied`: each bot's effective grant on such a tool whose key reaches its runs only through the proxy (by the one
  delivery decision, credentials.held_at_read): a run before this may have had a write key itself, so it is one to
  rotate;
- `direct`: each one whose key still reaches runs as it is, with its level: a Read key, or any key of Google Ads or of
  a tool the proxy cannot call (Not covered on Rules › Links);
- `unknown`: each one only the key's value would decide (a Read key under an id-like variable: an id goes as it is);
- `computers`: each computer whose runner predates proxy tokens (PROXY_RELEASE) or reports no release.
Nothing is revoked, rotated or changed: rotating a key is the person's, at the vendor.
"""

import json
import sys
from pathlib import Path
from typing import Any

from . import api_tools as _api_tools
from . import credentials as _credentials
from . import migrations as _migrations
from . import releases as _releases
from . import tool_services as _tool_services

# Untyped neighbours, typed as Any at the door.
api_tools: Any = _api_tools
credentials: Any = _credentials
migrations: Any = _migrations
releases: Any = _releases
TS: Any = _tool_services

PROXY_RELEASE = "0.5.192"  # the first runner that takes a proxy token in a key's place (runner-rs/src/env.rs)


def report(c: Any) -> dict[str, Any]:
    """{proxied, direct, unknown, computers}, as the module's text says: each bot's effective grant on such a tool, by how its
    key actually reaches the bot's runs (credentials.held_at_read, the one delivery decision)."""
    out: dict[str, list[dict[str, Any]]] = {"proxied": [], "direct": [], "unknown": []}
    for tool in c.execute("SELECT * FROM tools WHERE kind='api' ORDER BY lower(name), id").fetchall():
        category = TS.gated(tool["service"], tool["base_url"] or api_tools._field(tool, "mcp_url") or "")
        if not category:
            continue
        proxied = {tool["id"]: tool} if tool["delivery"] == "proxy" else {}
        for row in c.execute(
            "SELECT k.*, g.subject, g.created_at AS granted_at, g.access_level AS granted FROM credentials k "
            "JOIN credential_grants g ON g.credential_id=k.id WHERE k.tool_id=? AND k.ciphertext IS NOT NULL "
            "AND g.revoked_at IS NULL AND g.subject LIKE 'bot:%' ORDER BY g.created_at, g.id",
            (tool["id"],),
        ).fetchall():
            if credentials.effective_grant(c, row["id"], row["subject"]) is None:
                continue  # its parent grant was taken back: no run gets it
            # The runtime's own decision, the values aside: where only a value would say, it is `unknown`.
            through, way = credentials.held_at_read(c, proxied, row, row["subject"], credentials.UNREAD)
            if way == "skip":
                continue  # a read key of the tool goes instead
            level = "read" if row["access_level"] == "read" or row["granted"] == "read" else "read_write"
            out["proxied" if through is not None else "unknown" if way == "unknown" else "direct"].append(
                {
                    "tool": tool["id"],
                    "name": tool["name"],
                    "category": category,
                    "key": row["name"],
                    "env": row["env"],
                    "level": level,
                    "subject": row["subject"],
                    "granted_at": row["granted_at"],
                }
            )
    want = releases.parse(PROXY_RELEASE)
    computers: list[dict[str, Any]] = []
    for row in c.execute(
        "SELECT r.id, r.label, v.release FROM runners r LEFT JOIN runner_versions v ON v.runner_id=r.id "
        "WHERE r.revoked_at IS NULL ORDER BY lower(r.label), r.id"
    ).fetchall():
        have = releases.parse(row["release"] or "")
        if not have or not want or have < want:
            computers.append({"id": row["id"], "label": row["label"], "release": row["release"] or ""})
    return {**out, "computers": computers}


def main(argv: list[str]) -> None:
    if len(argv) != 2 or not Path(argv[1]).is_file():
        raise SystemExit("usage: python -m backend.proxy_keys <database file>")
    import sqlite3

    with migrations.read_only(Path(argv[1])) as c:
        c.row_factory = sqlite3.Row
        print(json.dumps(report(c), indent=2))


if __name__ == "__main__":
    main(sys.argv)
