"""One API tool (a `tools` row, backend/api_tools.py) per key a person stored for a service, so every existing key shows
on the Tools page under a tool and its grants become that tool's "Who can use it".

A tool-type key is an `api_key` or `token` credential with a stored value. Not tools: model keys (their variable is a
providers.MODEL_KEY_NAMES name; they belong to the model setup), computer keys and their shared model logins (a
computer_keys row, a grant to the computers, or a `computers:` source), Finance's bank logins (`finance` source), a
bot's own `HUB_` keys back into Tico, passwords and files. Subscriptions and gateway keys live in their own tables and are untouched.

Every tool made here is `delivery = 'computer'`: the value keeps going to the run's environment exactly as before, and
the Tools page shows "Key on computers" until a person moves it to the proxy. Its base URL, auth scheme and check
path come from backend/tool_services.py when the variable or name matches a known service. Grants are not touched, so
each keeps the Read and write level its column defaults to. A file without the earlier migration's columns (a new file:
the credentials table does not exist yet) has nothing to move.
"""

import re

from backend import tool_services
from backend.providers import MODEL_KEY_NAMES


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:48].strip("-") or "tool"


def up(conn):
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"credentials", "tools"} <= have or "tool_id" not in _columns(conn, "credentials"):
        return
    cols = _columns(conn, "credentials")
    created = "created_at" if "created_at" in cols else "created"
    updated = "updated_at" if "updated_at" in cols else "updated"
    computer = set()
    if "computer_keys" in have:
        computer |= {r[0] for r in conn.execute("SELECT credential_id FROM computer_keys")}
    if "credential_grants" in have:
        computer |= {r[0] for r in conn.execute(
            "SELECT credential_id FROM credential_grants WHERE subject='computers' OR subject LIKE 'computer:%'")}
    used = {r[0] for r in conn.execute("SELECT id FROM tools")}
    rows = conn.execute(
        f"SELECT id, name, env, source, updated_by, created_by, {created}, {updated} FROM credentials"
        " WHERE tool_id IS NULL AND kind IN ('api_key','token') AND ciphertext IS NOT NULL ORDER BY id").fetchall()
    for cid, name, env, source, updated_by, created_by, created_at, updated_at in rows:
        # credentials.model_env: a key named like a model variable with none set is a model key too.
        # A HUB_ key is a bot's way back into Tico itself (credentials.migrate), not a service.
        if (cid in computer or (env or name.strip()) in MODEL_KEY_NAMES or (env or "").startswith("HUB_")
                or (source or "").startswith("computers:") or source == "finance"):
            continue
        service = tool_services.match(env=env, name=name)
        fields = tool_services.defaults(service)
        base, n, tid = _slug(service or name), 1, _slug(service or name)
        while tid in used:
            n += 1
            tid = f"{base}-{n}"
        used.add(tid)
        conn.execute(
            "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,'computer',?,?,?)",
            # A second key for the same service keeps its own name, so the two tools can be told apart.
            (tid, fields["name"] if service and tid == base else name, service or "", fields.get("base_url", ""),
             fields.get("auth_scheme", "bearer"), fields.get("check_path", ""), created_by or updated_by or "",
             created_at or updated_at, updated_at or created_at))
        conn.execute("UPDATE credentials SET tool_id=?, access_level='read_write' WHERE id=?", (tid, cid))

