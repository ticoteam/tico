# pyright: strict
"""A tool's Settings: non-secret values a bot needs beside the tool's keys, such as a host, a region, a profile, a project
id or a bucket (POSTHOG_HOST, AWS_PROFILE). Each is an environment variable `name` with its plain `value`
(`tool_settings`), shown in the clear on the tool's page to whoever may read it, added, changed and removed by whoever
manages the tool (a credential administrator, as its keys are), and delivered as an environment variable to every run
that holds any level on the tool, whether its keys go to computers or through the proxy. A setting is never a key: it
is not encrypted, has no grant, is never revealed-with-log and never puts a tool under Needs you.

Delivery (`runtime_items`): credential-runtime and runner-watcher-credentials add one item per setting of each tool the
bot holds a key of, `{"id": "setting:<id>", "name", "env", "kind": "setting", "value", "tool"}`. A runner that knows
`kind: "setting"` (runner-rs/src/env.rs) puts it in the environment and keeps it out of what it masks in the turn's
output; an older runner reads it as an ordinary variable (any kind but `file`), so it still arrives, masked like a key. A
setting whose variable a key the run holds already sets is left out (the key wins), and so is a second setting of the
same name from another tool: a run never gets two values for one variable, which a runner refuses.

What is a setting, not a key (`is_setting`): a variable whose name ends in one of SUFFIXES (`_HOST`, `_REGION`,
`_PROFILE`, `_PROJECT_ID`, `_BUCKET`, `_URL`) and whose value does not look like a credential: one line, no key shape
(clients/access_entry.py `secret_in`), and for a URL no user, password or token in it. A computer's file migration
(`POST /api/v2/runner-credential-migration`) brings such a variable in as a setting on the tool of its vendor the bot
holds a key of (`import_for_bot`), and as a key, as before, when the bot holds no such tool or that tool already has a
different value under the name. `move_once` does the same, once, for the credentials stored before Settings existed.

An id is a setting too (ID_SUFFIXES: `_PROJECT_ID`, `_ACCOUNT_SID`, ...), only when its value is one short word and no
word of its name says key: AWS_ACCESS_KEY_ID, GOOGLE_ADS_CLIENT_ID, `*_KEY_ID` and `*_SECRET_ID` stay keys. An id never
goes behind the proxy, where a run would get a proxy token in its place: a newly stored one lands on a computer tool of
its own with no address (backend/api_tools.py `ensure_tool`), a proxied tool's id key reaches runs as its value
(backend/credentials.py `held_at_read`), and `repair_ids_once` moves, once, the ids stored as proxied tools' keys before
this rule into settings, else onto computer tools.
"""

import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pydantic import ConfigDict, Field

from clients import access_entry as access_entry_module

from . import auth as auth_mod
from . import tool_services as tool_services_module
from .models import Contract
from .store import H as hub_helpers
from .store import Problem

# Untyped neighbours, typed as Any at the door (as backend/website_tools.py does).
TS: Any = tool_services_module
H: Any = hub_helpers
access_entry: Any = access_entry_module
auth_module: Any = auth_mod


def _credentials() -> Any:
    """backend/credentials.py, imported at call time: it imports this module."""
    from . import credentials

    return credentials


def _api_tools() -> Any:
    """backend/api_tools.py, imported at call time: it imports this module."""
    from . import api_tools

    return api_tools


SUFFIXES = ("_HOST", "_REGION", "_PROFILE", "_PROJECT_ID", "_BUCKET", "_URL")
# Ids a bot sends as they are (in a path, a query, as Twilio's Basic username): an account's, project's or workspace's.
ID_SUFFIXES = (
    "_PROJECT_ID",
    "_ACCOUNT_ID",
    "_ACCOUNT_SID",
    "_ORG_ID",
    "_ORGANIZATION_ID",
    "_WORKSPACE_ID",
    "_TEAM_ID",
    "_CUSTOMER_ID",
)
NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,99}$")
# A name that says it is a key: the runner masks such a variable's value anyway (runner-rs/src/redact.rs for_turn).
KEY_WORDS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "PRIVATE_KEY")
# A word of a name that says key whatever the name ends in: AWS_ACCESS_KEY_ID, GOOGLE_ADS_CLIENT_ID, X_SECRET_ID.
NOT_SETTING_WORDS = frozenset(("KEY", "KEYS", "SECRET", "SECRETS", "TOKEN", "PASSWORD", "PASSWD", "PWD", "CLIENT"))
# An id's value: one short word of letters, digits and . _ : - (12345, AC + 32 hex, 123-456-7890, my-project).
ID_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
VALUE_MAX = 2000
SETTINGS_MAX = 50  # one tool's settings
URL_SECRETS = re.compile(r"(?i)(token|secret|password|passwd|pwd|api[_-]?key|key|sig|signature|auth|credential)")
MOVED = "tool_settings.moved"  # registry_metadata: the one-time move of stored keys that are settings has run
# registry_metadata: the one-time repair of ids stored as keys of proxied tools (`repair_ids_once`) has run.
IDS_REPAIRED = "tool_settings.ids_repaired"


class SettingWrite(Contract):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Z_][A-Z0-9_]*$")
    value: str = Field(min_length=1, max_length=VALUE_MAX)


class SettingUpdate(Contract):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Z_][A-Z0-9_]*$")
    value: str | None = Field(default=None, min_length=1, max_length=VALUE_MAX)


# ---- what a setting is


def secret_reason(value: Any) -> Any:
    """Why `value` cannot be a setting ("" when it can): it spans lines, looks like a key, or is a URL carrying one."""
    secret_in: Any = access_entry.secret_in

    value = str(value or "")
    if "\n" in value or "\r" in value:
        return "it spans more than one line"
    found = secret_in(value)
    if found:
        return found
    if "://" in value:
        text: str = value.strip()
        try:
            parts = urlsplit(text)
        except ValueError:
            return "it is not a URL"
        if parts.username or parts.password:
            return "its URL has a user or password in it"
        query: str = parts.query
        if any(URL_SECRETS.search(k) for k, _ in parse_qsl(query, keep_blank_values=True)):
            return "its URL has a key in its query"
    return ""


def known_name(env: Any) -> Any:
    """Whether `env`'s name says setting (SUFFIXES, ID_SUFFIXES) and no word of it says key (KEY_WORDS,
    NOT_SETTING_WORDS): POSTHOG_PROJECT_ID and TWILIO_ACCOUNT_SID are, AWS_ACCESS_KEY_ID is not."""
    env = str(env or "")
    return (
        bool(NAME.match(env))
        and env.endswith(SUFFIXES + ID_SUFFIXES)
        and not any(w in env for w in KEY_WORDS)
        and not NOT_SETTING_WORDS & set(env.split("_"))
    )


def is_setting(env: Any, value: Any) -> Any:
    """A setting, not a key, only when both say so: its name (`known_name`) and its value's shape. The value never
    looks like a credential (`secret_reason`: clients/access_entry.py `secret_in`), has no space in it, and an id's
    (ID_SUFFIXES) is one short word (ID_VALUE)."""
    text = str(value or "").strip()
    if not known_name(env) or not text or secret_reason(text) or any(ch.isspace() for ch in text):
        return False
    return not str(env).endswith(ID_SUFFIXES) or bool(ID_VALUE.match(text))


def _check(c: Any, tool_id: Any, name: Any, value: Any, setting_id: Any = "") -> Any:
    refuse_reserved: Any = _credentials().refuse_reserved

    if not NAME.match(name):
        raise Problem("name", "A setting's name is a variable: capitals, digits and _", 422)
    refuse_reserved(name)
    if c.execute("SELECT 1 FROM credentials WHERE tool_id=? AND env=?", (tool_id, name)).fetchone():
        raise Problem("name", f"{name} is this tool's key variable", 409)
    if any(w in name for w in KEY_WORDS):
        raise Problem("name", f"{name} names a key: add it under Keys", 422)
    reason = secret_reason(value)
    if reason:
        raise Problem("value", f"That looks like a key ({reason}): add it under Keys", 422)
    if c.execute(
        "SELECT 1 FROM tool_settings WHERE tool_id=? AND name=? AND id<>?", (tool_id, name, setting_id)
    ).fetchone():
        raise Problem("name", f"This tool already has {name}", 409)


# ---- reading and changing


def listing(c: Any, tool_id: Any) -> Any:
    """The tool's settings, by name: values in the clear (none is a secret)."""
    return [
        {"id": r["id"], "name": r["name"], "value": r["value"], "updated_at": r["updated_at"]}
        for r in c.execute("SELECT * FROM tool_settings WHERE tool_id=? ORDER BY name, id", (tool_id,))
    ]


def _insert(c: Any, actor: Any, tool_id: Any, name: Any, value: Any, source: Any = "") -> Any:
    sid, now = H.new_id(), H.now()
    c.execute(
        "INSERT INTO tool_settings(id,tool_id,name,value,created_by,created_at,updated_by,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (sid, tool_id, name, value, actor, now, actor, now),
    )
    H.event(
        c, actor, "tool.setting_added", tool_id, {"setting": sid, "name": name} | ({"source": source} if source else {})
    )
    return sid


def _managed(c: Any, who: Any, tool_id: Any, admins: Any) -> Any:
    _readable: Any = _api_tools()._readable
    from . import tool_access as TA

    TA.require(c, who, TA.tool(tool_id=str(tool_id or "")), action=TA.EDIT, admins=admins)
    return _readable(c, who, tool_id, admins)


def _row(c: Any, tool_id: Any, setting_id: Any) -> Any:
    row = c.execute("SELECT * FROM tool_settings WHERE id=? AND tool_id=?", (setting_id, tool_id)).fetchone()
    if row is None:
        raise Problem("not_found", "No such setting on this tool", 404)
    return row


def add(c: Any, who: Any, tool_id: Any, body: Any, admins: Any) -> Any:
    _managed(c, who, tool_id, admins)
    name, value = body.name, body.value.strip()
    if c.execute("SELECT count(*) FROM tool_settings WHERE tool_id=?", (tool_id,)).fetchone()[0] >= SETTINGS_MAX:
        raise Problem("too_many", f"A tool holds at most {SETTINGS_MAX} settings", 409)
    _check(c, tool_id, name, value)
    return _view(_row(c, tool_id, _insert(c, who.actor, tool_id, name, value)))


def update(c: Any, who: Any, tool_id: Any, setting_id: Any, body: Any, admins: Any) -> Any:
    _managed(c, who, tool_id, admins)
    row = _row(c, tool_id, setting_id)
    name = body.name if body.name is not None else row["name"]
    value = body.value.strip() if body.value is not None else row["value"]
    if (name, value) == (row["name"], row["value"]):
        return _view(row)
    _check(c, tool_id, name, value, setting_id)
    c.execute(
        "UPDATE tool_settings SET name=?, value=?, updated_by=?, updated_at=? WHERE id=?",
        (name, value, who.actor, H.now(), setting_id),
    )
    H.event(c, who.actor, "tool.setting_changed", tool_id, {"setting": setting_id, "name": name, "was": row["name"]})
    return _view(_row(c, tool_id, setting_id))


def delete(c: Any, who: Any, tool_id: Any, setting_id: Any, admins: Any) -> Any:
    _managed(c, who, tool_id, admins)
    row = _row(c, tool_id, setting_id)
    c.execute("DELETE FROM tool_settings WHERE id=?", (setting_id,))
    H.event(c, who.actor, "tool.setting_deleted", tool_id, {"setting": setting_id, "name": row["name"]})
    return {"deleted": True, "id": setting_id}


def drop_tool(c: Any, tool_id: Any) -> Any:
    """A deleted tool's settings go with it (backend/api_tools.py `delete_tool`)."""
    c.execute("DELETE FROM tool_settings WHERE tool_id=?", (tool_id,))


def _view(row: Any) -> Any:
    return {"id": row["id"], "name": row["name"], "value": row["value"], "updated_at": row["updated_at"]}


# ---- delivery


KEY_TOOLS_SQL = "SELECT id, tool_id FROM credentials WHERE tool_id IS NOT NULL AND ciphertext IS NOT NULL"


def held_tools(c: Any, held: Any) -> Any:
    """The tools whose keys `held` (credential ids a subject holds an effective grant to) include a stored one."""
    if not held:
        return []
    return sorted({r["tool_id"] for r in c.execute(KEY_TOOLS_SQL) if r["id"] in held})


def _settings_of(c: Any, tool_ids: Any) -> list[Any]:
    out: list[Any] = []
    for part in H.chunks(list(tool_ids)):
        out += c.execute(
            "SELECT s.*, t.name AS tool_name FROM tool_settings s JOIN tools t ON t.id=s.tool_id"
            f" WHERE s.tool_id IN ({','.join('?' * len(part))}) ORDER BY s.tool_id, s.name, s.id",
            part,
        ).fetchall()
    return out


def runtime_items(c: Any, held: Any, taken: Any = ()) -> list[dict[str, Any]]:
    """The settings a run of a subject holding `held` gets, as credential-runtime items; none whose variable is in
    `taken` (the keys it already gets) or came first from another tool."""
    seen: set[str] = set(taken)
    out: list[dict[str, Any]] = []
    for row in _settings_of(c, held_tools(c, held)):
        if row["name"] in seen:
            continue
        seen.add(row["name"])
        out.append(
            {
                "id": "setting:" + row["id"],
                "name": row["name"],
                "env": row["name"],
                "kind": "setting",
                "value": row["value"],
                "tool": row["tool_name"] or row["tool_id"],
            }
        )
    return out


def names(c: Any, held: Any) -> Any:
    """The variable names a subject holding `held` gets from settings (runner-credential-grants, bot_tools.granted)."""
    return sorted({row["name"] for row in _settings_of(c, held_tools(c, held))})


def names_each(c: Any, helds: Any) -> list[Any]:
    """`names` for each of `helds` (one set of credential ids per subject), in order, reading every key's tool and every
    held tool's settings once rather than once per subject (runner-credential-grants: a computer's ~20 bots)."""
    helds = list(helds)
    key_tool = {r["id"]: r["tool_id"] for r in c.execute(KEY_TOOLS_SQL)} if any(helds) else {}
    tools = [sorted({key_tool[k] for k in held if k in key_tool}) for held in helds]
    by_tool: dict[Any, set[Any]] = {}
    for row in _settings_of(c, sorted({t for ids in tools for t in ids})):
        by_tool.setdefault(row["tool_id"], set()).add(row["name"])
    return [sorted({n for t in ids for n in by_tool.get(t, ())}) for ids in tools]


# ---- a computer's files, and the keys stored before Settings


def _vendor_tools(c: Any, env: Any, tool_ids: Any) -> Any:
    """Of `tool_ids`, the API tools of `env`'s vendor (tool_services.vendor_of: POSTHOG_HOST is PostHog's, AWS_PROFILE
    AWS's), oldest first."""
    vendor = TS.vendor_of(envs=[env])
    if not tool_ids or not vendor:
        return []
    rows: list[Any] = []
    for part in H.chunks(list(tool_ids)):
        rows += c.execute(
            f"SELECT * FROM tools WHERE id IN ({','.join('?' * len(part))}) ORDER BY created_at, id", part
        ).fetchall()
    out: list[Any] = []
    for t in rows:
        if "kind" in t.keys() and t["kind"] not in (None, "", "api"):
            continue
        envs = [r[0] for r in c.execute("SELECT env FROM credentials WHERE tool_id=? AND env<>''", (t["id"],))]
        if TS.vendor_of(t["service"], t["name"], envs, t["base_url"]) == vendor:
            out.append(t)
    return out


def import_for_bot(c: Any, who: Any, bot: Any, env: Any, value: Any) -> Any:
    """A computer file's variable `env` the bot used, as a setting on the tool of its vendor the bot holds a key of.
    True when the bot now gets it that way (made, or already there with this value); False to store it as a key."""
    granted: Any = _credentials().granted

    if not is_setting(env, value):
        return False
    for tool in _vendor_tools(c, env, held_tools(c, granted(c, "bot:" + bot))):
        have = c.execute("SELECT value FROM tool_settings WHERE tool_id=? AND name=?", (tool["id"], env)).fetchone()
        if have is not None:
            return have["value"] == value
        if c.execute("SELECT 1 FROM credentials WHERE tool_id=? AND env=?", (tool["id"], env)).fetchone():
            return False
        _insert(c, who.actor, tool["id"], env, value, source="computer")
        return True
    return False


def _grantees(c: Any, cid: Any) -> Any:
    effective_grant: Any = _credentials().effective_grant

    subjects = {
        r[0]
        for r in c.execute("SELECT subject FROM credential_grants WHERE credential_id=? AND revoked_at IS NULL", (cid,))
    }
    return {s for s in subjects if effective_grant(c, cid, s) is not None}


def _protected(c: Any, row: Any) -> bool:
    """A write key that lives in the proxy (an ads, payments or CRM tool's: api_tools.proxy_policy): never turned
    into a setting nor moved to a tool with no address, whatever its variable or value says."""
    if not row["tool_id"] or row["access_level"] != "read_write":
        return False
    tool = c.execute("SELECT * FROM tools WHERE id=?", (row["tool_id"],)).fetchone()
    return tool is not None and _api_tools().proxy_policy(tool) == "proxy"


def _move_one(c: Any, vault: Any, row: Any) -> Any:
    """One stored key that is a setting, moved to a tool every one of its holders gets it from; False when none is."""
    api_tools: Any = _api_tools()
    if _protected(c, row):
        return False
    # Explicit grants only: a default read can be switched off, and then the variable would be gone.
    granted: Any = _credentials().explicitly_granted

    try:
        value = vault.cipher.decrypt(c, row)
    except Problem:
        return False
    env = row["env"]
    if not is_setting(env, value):
        return False
    subjects = _grantees(c, row["id"])
    if any(not s.startswith(("bot:", "human:")) for s in subjects):
        return False  # held by the computers (a model setup): not a tool's
    # Where every holder still holds a key after this one is gone: its own tool first, then its vendor's.
    others = {s: set(granted(c, s)) - {row["id"]} for s in subjects}
    own = [row["tool_id"]] if row["tool_id"] else []
    keyed = sorted(
        {r[0] for r in c.execute("SELECT tool_id FROM credentials WHERE tool_id IS NOT NULL AND id<>?", (row["id"],))}
    )
    candidates = [t for t in own if t in keyed] + [
        t["id"] for t in _vendor_tools(c, env, keyed) if t["id"] not in own and t["audience"] is None
    ]
    for tool_id in candidates:
        if not all(tool_id in held_tools(c, held) for held in others.values()):
            continue
        have = c.execute("SELECT value FROM tool_settings WHERE tool_id=? AND name=?", (tool_id, env)).fetchone()
        if have is not None and have["value"] != value:
            continue
        if c.execute(
            "SELECT 1 FROM credentials WHERE tool_id=? AND env=? AND id<>?", (tool_id, env, row["id"])
        ).fetchone():
            continue
        try:
            api_tools.refuse_finance_key(c, [row["id"]])
        except Problem:
            return False
        if have is None:
            _insert(c, "system", tool_id, env, value, source="key")
        api_tools._drop_key(c, SimpleNamespace(actor="system"), row["id"])
        # A tool made only for this value (a computer migration's) has nothing left: it goes rather than ask for a key.
        gone = row["tool_id"]
        if gone and gone != tool_id and not c.execute("SELECT 1 FROM credentials WHERE tool_id=?", (gone,)).fetchone():
            if not c.execute("SELECT 1 FROM tool_settings WHERE tool_id=?", (gone,)).fetchone():
                c.execute("DELETE FROM tool_calls WHERE tool_id=?", (gone,))
                c.execute("DELETE FROM tool_checks WHERE tool_id=?", (gone,))
                c.execute("DELETE FROM tools WHERE id=?", (gone,))
                from . import key_grants

                key_grants.tool_gone(c, gone)  # a key proposal naming it changes in its person's Needs you
                H.event(c, "system", "tool.deleted", gone, {"name": env, "keys": 0, "moved_to": tool_id})
        H.event(c, "system", "tool.setting_moved", tool_id, {"name": env, "credential": row["id"]})
        return True
    return False


def move_once(store: Any, vault: Any) -> Any:
    """Once per company: each stored key whose variable is a setting (`is_setting`, read with the vault) becomes a
    setting on a tool every one of its holders holds a key of, and the key goes only after the setting is there. A key
    no such tool exists for stays a key. The number moved."""
    with store.transaction() as c:
        if c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (MOVED,)).fetchone():
            return 0
        if not vault.cipher.configured:
            return 0  # nothing can be read yet: a later start moves them
        moved = 0
        for row in c.execute(
            "SELECT * FROM credentials WHERE env<>'' AND kind IN ('api_key','token') AND ciphertext IS NOT NULL ORDER BY id"
        ).fetchall():
            if known_name(row["env"]) and _move_one(c, vault, row):
                moved += 1
        c.execute("INSERT INTO registry_metadata VALUES(?,?)", (MOVED, str(moved)))
        return moved


def _off_the_proxy(c: Any, row: Any) -> None:
    """A proxied tool's id key that no tool can take as a setting: on a computer tool with no address, so runs get
    its value and no check sends it as a key. Its own tool when the id is all it holds, else a new tool for it."""
    tool_id = row["tool_id"]
    if c.execute("SELECT 1 FROM credentials WHERE tool_id=? AND id<>?", (tool_id, row["id"])).fetchone():
        tool = c.execute("SELECT service FROM tools WHERE id=?", (tool_id,)).fetchone()
        made = TS.insert_tool(
            c,
            tool["service"],
            row["env"],
            "computer",
            "system",
            H.now(),
            tid=_api_tools()._unique_id(c, TS.slug(row["env"])),
        )
        c.execute("UPDATE tools SET name=?, base_url='', check_path='' WHERE id=?", (row["env"], made))
        c.execute("UPDATE credentials SET tool_id=? WHERE id=?", (made, row["id"]))
        H.event(c, "system", "tool.key_moved", made, {"credential": row["id"], "from": tool_id, "why": "id"})
        return
    c.execute(
        "UPDATE tools SET delivery='computer', base_url='', check_path='', updated_at=? WHERE id=?", (H.now(), tool_id)
    )
    H.event(c, "system", "tool.updated", tool_id, {"delivery": "computer", "base_url": "", "why": "id"})


def repair_ids_once(store: Any, vault: Any) -> Any:
    """Once per company: each id stored as a key of a proxied tool (`is_setting`: its name and its value both say id;
    POSTHOG_PROJECT_ID, TWILIO_ACCOUNT_SID), which runs got as a proxy token instead of the id, becomes a setting on
    a tool every one of its holders holds a key of (`_move_one`), else stays a key on a computer tool with no address
    (`_off_the_proxy`). Either way every holder's runs get the id's value under the same variable. The number
    repaired."""
    with store.transaction() as c:
        if c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (IDS_REPAIRED,)).fetchone():
            return 0
        if not vault.cipher.configured:
            return 0  # nothing can be read yet: a later start repairs them
        repaired = 0
        for row in c.execute(
            "SELECT k.* FROM credentials k JOIN tools t ON t.id=k.tool_id WHERE t.delivery='proxy' AND t.mcp_url='' "
            "AND k.env<>'' AND k.kind IN ('api_key','token') AND k.ciphertext IS NOT NULL ORDER BY k.id"
        ).fetchall():
            if not known_name(row["env"]) or _protected(c, row):
                continue
            try:
                value = vault.cipher.decrypt(c, row)
            except Problem:
                continue
            if not is_setting(row["env"], value):
                continue
            if any(not s.startswith(("bot:", "human:")) for s in _grantees(c, row["id"])):
                continue  # held by the computers (a model setup): not a tool's
            if not _move_one(c, vault, row):
                _off_the_proxy(c, row)
            repaired += 1
        c.execute("INSERT INTO registry_metadata VALUES(?,?)", (IDS_REPAIRED, str(repaired)))
        return repaired


# ---- routes


def install(app: Any, store: Any) -> Any:
    """`POST /api/v2/api-tools/{id}/settings`, `PATCH|DELETE …/settings/{setting_id}`: whoever manages the tool."""
    _Receipt: Any = _api_tools()._Receipt

    vault = lambda: app.state.vault

    def manager(c: Any, w: Any) -> Any:
        from . import tool_access as TA

        TA.require(c, w, TA.tool(), action=TA.EDIT, admins=vault().admins, auth=vault().auth)

    def change(request: Any, body: Any, fn: Callable[[Any], Any]) -> Any:
        return vault().change(
            request.state.identity, request.url.path, request.headers.get("idempotency-key"), body, manager, fn
        )

    from fastapi import Request

    @app.post("/api/v2/api-tools/{tool_id}/settings")
    def add_api_tool_setting(request: Request, tool_id: str, body: SettingWrite) -> Any:
        return change(request, _Receipt(body), lambda c: add(c, request.state.identity, tool_id, body, vault().admins))

    @app.patch("/api/v2/api-tools/{tool_id}/settings/{setting_id}")
    def update_api_tool_setting(request: Request, tool_id: str, setting_id: str, body: SettingUpdate) -> Any:
        return change(
            request,
            _Receipt(body, setting=setting_id),
            lambda c: update(c, request.state.identity, tool_id, setting_id, body, vault().admins),
        )

    @app.delete("/api/v2/api-tools/{tool_id}/settings/{setting_id}")
    def delete_api_tool_setting(request: Request, tool_id: str, setting_id: str) -> Any:
        validate_identity: Any = auth_module.validate_identity

        with store.transaction() as c:
            validate_identity(c, request.state.identity)
            return delete(c, request.state.identity, tool_id, setting_id, vault().admins)
