# pyright: strict
"""Default read: every bot reads every company tool whose Read can be held to reads, with no grant, unless the tool's
"Everyone can read" switch (`tools.everyone_reads`) is off. Explicit grants still decide Read and write.

A tool default-reads only where Read is enforceable, because "read" must never hand out a write key:
- it has a stored Read key (a run gets that key itself), or
- its calls go through the proxy, or its address lets the proxy call it (`api_tools.computer_way` would answer
  `proxy`): the run gets a proxy token, and the proxy lets only reads through (GET, HEAD, OPTIONS and MCP reads).
A tool with only a Read and write key that goes to computers with no address the proxy can call (a database URL, an
SSH key, a file) never default-reads; neither does an Only listed tool, a website tool (a login is never held to
reads), or a key someone holds as their own.

What a bot reads by default reaches its runs exactly as a grant at Read does: `credentials.granted` adds these keys
(credential-runtime, the watcher, MCP servers, the tool's settings), `api_tools._held` counts them at Read (the
proxy's `key_for`), and the bot's Tools tab marks them. An explicit grant always wins: a bot holding any grant on a
tool reads it through that grant, and a bot already given a variable by an explicit grant gets no default key under
the same name (one value per variable). A variable several default-read tools would deliver is given by none of them
by default: the bot's Tools tab lists it as several to choose from, and a person picks. The Tools page's owner holds no
key, so it reads nothing by default.
"""

import importlib
import sqlite3
from typing import Any

from . import read_cache as read_cache_module
from . import store as store_module

H: Any = store_module.H
read_cache: Any = read_cache_module
Row = Any

CANDIDATES_SQL = (
    "SELECT t.id AS tool_id, t.name AS tool_name, t.base_url, t.delivery, t.everyone_reads, t.auth_template, k.id, k.env, "
    "k.access_level FROM tools t JOIN credentials k ON k.tool_id=t.id "
    "WHERE t.audience IS NULL AND COALESCE(t.kind,'api')='api' "
    "AND k.ciphertext IS NOT NULL AND k.kind!='login' AND COALESCE(k.holder,'')='' "
    "ORDER BY t.id, k.access_level, k.created_at, k.id"
)


def _proxy_reaches(tool: Row) -> bool:
    tool_proxy: Any = importlib.import_module(".tool_proxy", __package__)
    return tool["delivery"] == "proxy" or not tool_proxy.address_problem(
        tool["base_url"] or "", tool["tool_name"] or tool["tool_id"]
    )


def _pick(keys: list[Row]) -> Row | None:
    """The key a bot reads with by default among one variable's keys (or one tool's): its stored Read key, else its
    Read and write key when the proxy can hold it to reads."""
    read = next((k for k in keys if k["access_level"] == "read"), None)
    full = next((k for k in keys if k["access_level"] == "read_write"), None)
    if read is not None:
        return read
    return full if full is not None and _proxy_reaches(full) else None


def enforceable_keys(c: Any) -> dict[str, list[Row]]:
    """{tool id: the keys a bot would read it with by default} for each tool where Read can be held to reads, the
    switch aside: its stored Read key, else its Read and write key when the proxy can hold it to reads; for a tool
    with a multi-secret template (backend/tool_templates.py), that for each of its variables. Read once per
    snapshot."""
    values: Any = read_cache.cache(c)
    if values is not None and ("default_read",) in values:
        return values[("default_read",)]
    rows: dict[str, list[Row]] = {}
    try:
        for row in c.execute(CANDIDATES_SQL):
            rows.setdefault(str(row["tool_id"]), []).append(row)
    except sqlite3.OperationalError as exc:  # a file from before the switch's migration reads nothing by default
        if "no such column" not in str(exc):
            raise
    found: dict[str, list[Row]] = {}
    for tool_id, keys in rows.items():
        if keys[0]["auth_template"]:
            by_env: dict[str, list[Row]] = {}
            for k in keys:
                by_env.setdefault(str(k["env"]), []).append(k)
            picked = [p for p in (_pick(group) for group in by_env.values()) if p is not None]
        else:
            one = _pick(keys)
            picked = [one] if one is not None else []
        if picked:
            found[tool_id] = picked
    if values is not None:
        values[("default_read",)] = found
    return found


def candidates(c: Any) -> dict[str, list[Row]]:
    """`enforceable_keys` of the tools whose "Everyone can read" switch is on."""
    return {t: k for t, k in enforceable_keys(c).items() if k[0]["everyone_reads"]}


def enforceable(c: Any, tool_id: str) -> bool:
    """Whether the tool can default-read at all (the switch aside)."""
    return tool_id in enforceable_keys(c)


def _shared(found: dict[str, list[Row]]) -> dict[str, list[str]]:
    """{variable: tool ids} for each variable more than one default-read tool would deliver."""
    by_env: dict[str, list[str]] = {}
    for tool_id, keys in found.items():
        for key in keys:
            if key["env"]:
                by_env.setdefault(str(key["env"]), []).append(tool_id)
    return {env: tools for env, tools in by_env.items() if len(tools) > 1}


def _explicit(c: Any, explicit: frozenset[str]) -> tuple[set[str], set[str]]:
    """(tool ids, variables) the explicitly granted credential ids bring."""
    held_tools: set[str] = set()
    held_envs: set[str] = set()
    for part in H.chunks(sorted(explicit)):
        marks = ",".join("?" * len(part))
        for row in c.execute(
            f"SELECT tool_id, env FROM credentials WHERE id IN ({marks}) AND ciphertext IS NOT NULL", part
        ):
            if row["tool_id"]:
                held_tools.add(str(row["tool_id"]))
            if row["env"]:
                held_envs.add(str(row["env"]))
    return held_tools, held_envs


def _reader(c: Any, subject: str) -> bool:
    """A live bot other than the Tools page's owner, which holds no key."""
    if not str(subject or "").startswith("bot:"):
        return False
    live = c.execute("SELECT 1 FROM bots WHERE id=? AND state<>'archived'", (H.actor_id(subject),)).fetchone()
    if live is None:
        return False
    page_owners: Any = importlib.import_module(".page_owners", __package__)
    return page_owners.owner(c, "tools", fallback=False) != subject


def reads(c: Any, subject: str, explicit: frozenset[str]) -> dict[str, str]:
    """{credential id: tool id} `subject` reads by default, given the credential ids it holds explicitly. A variable
    several default-read tools would deliver (two PostHog projects, say) is given by none of them: which account a bot
    needs is a question for a person (`choices`), never the first found."""
    if not _reader(c, subject):
        return {}
    found = candidates(c)
    if not found:
        return {}
    held_tools, held_envs = _explicit(c, explicit)
    shared = _shared(found)
    out: dict[str, str] = {}
    for tool_id, keys in found.items():
        if tool_id in held_tools:
            continue  # an explicit grant wins: the tool through it
        for key in keys:
            env = str(key["env"] or "")
            if env and env in held_envs:
                continue  # the variable from the explicit grant
            if env in shared:
                continue  # several to choose from: asked, not guessed
            out[str(key["id"])] = tool_id
    return out


def choices(c: Any, subject: str, explicit: frozenset[str]) -> dict[str, list[str]]:
    """{variable: tool ids} `subject` gets no default read for because several tools would deliver it, leaving out a
    variable it holds through an explicit grant: the bot's Tools tab says there are several to choose from."""
    if not _reader(c, subject):
        return {}
    held_tools, held_envs = _explicit(c, explicit)
    return {
        env: tools
        for env, tools in _shared(candidates(c)).items()
        if env not in held_envs and not held_tools & set(tools)
    }


def keys_of(c: Any, tool_id: str, subject: str) -> set[str]:
    """The keys `subject` reads `tool_id` with by default: one, or one per variable of a tool with a template."""
    credentials: Any = importlib.import_module(".credentials", __package__)
    return {cid for cid, tid in reads(c, subject, credentials.explicitly_granted(c, subject)).items() if tid == tool_id}


def summary(c: Any) -> dict[str, int]:
    """How many tools every bot reads by default, and why the rest do not: a count to check before and after the switch
    goes on (`python -c` against a copy made with `python -m backend.devcopy`)."""
    rows = c.execute(
        "SELECT id, audience, COALESCE(kind,'api') AS kind, everyone_reads FROM tools ORDER BY id"
    ).fetchall()
    keyed = enforceable_keys(c)
    out = {
        "tools": len(rows),
        "default_read": 0,
        "switched_off": 0,
        "not_enforceable": 0,
        "only_listed": 0,
        "website": 0,
    }
    for row in rows:
        if row["kind"] != "api":
            out["website"] += 1
        elif row["audience"] is not None:
            out["only_listed"] += 1
        elif row["id"] not in keyed:
            out["not_enforceable"] += 1
        elif not row["everyone_reads"]:
            out["switched_off"] += 1
        else:
            out["default_read"] += 1
    return out
