# pyright: strict
"""What is left to rotate now that write keys of ads, payments and CRM tools live in the proxy (#4892, plan P3/W4).

    python -m backend.proxy_keys <database file>

Read only (backend.migrations.read_only: no byte of the file, its log or its index changes): it prints, as JSON, for
the person who rotates the keys at each vendor:
- `proxied`: each bot's effective grant of a Read and write key on such a tool, which now reaches its runs only through
  the proxy (api_tools.proxy_policy): a run before this may have had the key itself, so it is one to rotate;
- `direct`: each bot's effective grant of such a key that still reaches runs as it is (Google Ads, or a tool the proxy
  cannot call): Not covered on Rules › Links;
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
    """{proxied, direct, computers}, as the module's text says."""
    out: dict[str, list[dict[str, Any]]] = {"proxied": [], "direct": []}
    for tool in c.execute("SELECT * FROM tools WHERE kind='api' ORDER BY lower(name), id").fetchall():
        category = TS.gated(tool["service"], tool["base_url"] or api_tools._field(tool, "mcp_url") or "")
        if not category:
            continue
        way = "proxied" if api_tools.proxy_policy(tool) == "proxy" else "direct"  # the one decision's policy
        for row in c.execute(
            "SELECT k.*, g.subject, g.created_at AS granted_at FROM credentials k JOIN credential_grants g "
            "ON g.credential_id=k.id WHERE k.tool_id=? AND k.access_level='read_write' AND k.ciphertext IS NOT NULL "
            "AND g.revoked_at IS NULL AND g.access_level='read_write' AND g.subject LIKE 'bot:%' "
            "ORDER BY g.created_at, g.id",
            (tool["id"],),
        ).fetchall():
            if credentials.effective_grant(c, row["id"], row["subject"]) is None:
                continue  # its parent grant was taken back: no run gets it
            out[way].append(
                {
                    "tool": tool["id"],
                    "name": tool["name"],
                    "category": category,
                    "key": row["name"],
                    "env": row["env"],
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
