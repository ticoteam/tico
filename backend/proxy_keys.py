# pyright: strict
"""What is left to rotate now that write keys of ads, payments and CRM tools live in the proxy (#4892, plan P3/W4).

    python -m backend.proxy_keys <database file>

Read only (the file is opened `mode=ro`): it prints, as JSON, for the person who rotates the keys at each vendor:
- `direct`: each grant of a Read and write key on such a tool (tool_services.GATED) whose run still gets the key itself:
  granted before the move (`before`), or on a tool the proxy cannot call (`no_address`, Google Ads' several values);
- `computers`: each computer whose runner predates proxy tokens (PROXY_RELEASE) or reports no release, whose runs could
  not reach these tools through the proxy.
Nothing is revoked, rotated or changed: rotating a key is the person's, at the vendor.
"""

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from . import api_tools as _api_tools
from . import releases as _releases
from . import tool_proxy as _tool_proxy
from . import tool_services as _tool_services

# Untyped neighbours, typed as Any at the door.
api_tools: Any = _api_tools
releases: Any = _releases
tool_proxy: Any = _tool_proxy
TS: Any = _tool_services

PROXY_RELEASE = "0.5.192"  # the first runner that takes a proxy token in a key's place (runner-rs/src/env.rs)


def report(c: Any) -> dict[str, Any]:
    """{direct, computers}, as the module's text says."""
    since = c.execute("SELECT value FROM registry_metadata WHERE key=?", (api_tools.WRITE_PROXY_SINCE,)).fetchone()
    direct: list[dict[str, Any]] = []
    for tool in c.execute("SELECT * FROM tools WHERE delivery<>'proxy' ORDER BY lower(name), id").fetchall():
        category = TS.gated(tool["service"], tool["name"])
        if not category:
            continue
        no_address = bool(tool_proxy.address_problem(tool["base_url"] or "", tool["name"] or tool["id"]))
        for row in c.execute(
            "SELECT k.*, g.subject, g.created_at AS granted_at FROM credentials k JOIN credential_grants g "
            "ON g.credential_id=k.id WHERE k.tool_id=? AND k.access_level='read_write' AND k.ciphertext IS NOT NULL "
            "AND g.revoked_at IS NULL AND g.access_level='read_write' ORDER BY g.created_at, g.id",
            (tool["id"],),
        ).fetchall():
            if api_tools.write_by_proxy(c, row, row["subject"]):
                continue
            direct.append(
                {
                    "tool": tool["id"],
                    "name": tool["name"],
                    "category": category,
                    "key": row["name"],
                    "env": row["env"],
                    "subject": row["subject"],
                    "granted_at": row["granted_at"],
                    "why": "no_address" if no_address else "before",
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
    return {"since": since[0] if since else None, "direct": direct, "computers": computers}


def main(argv: list[str]) -> None:
    if len(argv) != 2 or not Path(argv[1]).is_file():
        raise SystemExit("usage: python -m backend.proxy_keys <database file>")
    with closing(sqlite3.connect(Path(argv[1]).resolve().as_uri() + "?mode=ro", uri=True)) as c:
        c.row_factory = sqlite3.Row
        print(json.dumps(report(c), indent=2))


if __name__ == "__main__":
    main(sys.argv)
