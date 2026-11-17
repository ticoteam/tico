# pyright: strict
"""Multi-secret auth templates on a tool (docs/credential-vault.md#auth-templates): one tool row holds a vendor
account's several values, and its template says how they reach the vendor.

A template (`tools.auth_template`, JSON; '' for a tool whose one key goes on a call as its `auth_scheme` says) is a
known vendor's (`{"id": "twilio"}`, backend/tool_services.py TEMPLATES) or the tool's own (`{"fields": [...], "ways":
[...]}`). Its fields are the account's values, each a variable: `secret` (a vault key on the tool), `id` (an identifier
the bot sends as it is: a setting, or a key whose value runs get as it is, never a proxy token) or `setting` (a host,
region or profile: a setting). Its ways say how a proxied call carries them, the first whose fields all have a value:
`basic` (two fields as user and password: Twilio's Account SID and Auth Token), `headers` (a header pair, or one),
`query` (with headers, query + header). A template with no ways signs on the computer (AWS SigV4, Google Ads' OAuth
refresh): its keys go to computers.

What a run gets is what it got before: every field the bot holds is its own variable, a secret as a proxy token (proxy
delivery) or its value (computer), an id or setting as its value. The proxy (backend/tool_proxy.py) builds the vendor's
auth from every field the caller holds (`proxy_auth`), whatever the caller put where.

A tool with a template holds one key per variable per level (backend/api_tools.py): a level grants each variable's key
for that level, the default read reads each, and the check sends them together.

Grouping (`group_once`, once per company at start, `registry_metadata` GROUPED): the tools that are one vendor account's
values (each tool's every key and setting a field of one known template, under one label: POSTHOG_CRO_API_KEY and
POSTHOG_CRO_PROJECT_ID are PostHog's CRO account) become one tool named vendor and label ("Twilio", "PostHog · CRO").
The parts' keys and settings move onto the oldest proxied part (else the oldest), the others go, and a key's run address
variable keeps its old tool's name (`credentials.former_tool_id`). Whoever held a part explicitly gets the account's
other values at their level (the union of grants). Each grouping runs in a savepoint between two reads of every bot's
run environment (`run_env`): one that would change or drop a variable any bot reads is rolled back and left as it was;
one that would give a bot a variable it did not have is kept with the gains in its event, except for a sensitive vendor
(AWS) or an Only listed tool, which is rolled back and asked of the Tools Owner as a task. A vendor whose variable two
tools hold (two accounts, or copies) is left alone. Each grouping's event `tool.grouped` holds what `ungroup` needs to
split it back: `POST /api/v2/api-tools/{id}/ungroup` (a credential administrator). Calls and checks stay on the grouped
tool.

Login pairs (`offer_logins`, in the same start): a USERNAME and PASSWORD stored as two keys (VPM_TIDY_USERNAME and
VPM_TIDY_PASSWORD) is a website login. The Tools Owner gets one task per pair to offer it as a website account on its
vendor; the variables keep reaching runs until the Tools Owner confirms no bot reads them (skill
manage-tools-and-keys).

Paste to classify (`classify`): pasted text (lines `NAME=value`, `NAME: value`, or bare values) is split into values,
each matched to a template's field by name or shape; for an unknown vendor each is marked secret, id or setting by
tool_settings' rules. `POST /api/v2/tool-templates/classify` answers what it found (no secret's value);
`POST /api/v2/api-tools/{id}/paste` stores it in one click.
"""

import base64
import hashlib
import importlib
import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import ConfigDict, Field, SecretStr

from . import tool_services as tool_services_module
from . import tool_settings
from .models import Contract
from .store import H as hub_helpers
from .store import Problem

# Untyped neighbours, typed as Any at the door (as backend/tool_settings.py does).
TS: Any = tool_services_module
H: Any = hub_helpers

GROUPED = "tool_templates.grouped"  # registry_metadata: the one-time grouping of vendor accounts has run
ROLES = ("secret", "id", "setting")
NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,99}$")
PLACE = re.compile(r"\{([a-z0-9_]{1,40})\}")
HEADER = re.compile(r"^[A-Za-z0-9-]{1,60}$")
PARAM = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")
FIELDS_MAX = 12
PASTE_MAX = 20_000
SEP = " · "
# Short words of a label that are words, not acronyms ("Sentry bot", but "PostHog · CRO").
WORDS = frozenset("BOT APP NEW OLD DEV OPS WEB ADS KEY ALL ONE TWO OWN OUR MY".split())
# The last words of a variable that say only "a key": "SLACK_SENTRY_BOT_TOKEN" is Slack's "Sentry bot".
KEY_TAIL = frozenset("TOKEN KEY API SECRET PAT ACCESS PERSONAL AUTH".split())
GENERIC = KEY_TAIL | frozenset("BOT USER APP CLIENT OAUTH".split())
LOGIN_USER = ("USERNAME", "USER", "EMAIL", "LOGIN")
LOGIN_PASSWORD = ("PASSWORD", "PASS", "PWD", "PASSWD")


def _is(value: Any, kind: Any) -> bool:
    """isinstance, without narrowing an Any to a container of unknowns."""
    return isinstance(value, kind)


def _list(value: Any) -> list[Any]:
    return value or []


def _dict(value: Any) -> dict[str, Any]:
    return value or {}


# ---- a template


def known(template_id: Any) -> Any:
    return TS.TEMPLATES.get(str(template_id or ""))


def _field_view(f: Any) -> Any:
    return {
        "id": f["id"],
        "env": f.get("env", ""),
        "suffix": f.get("suffix", ""),
        "aliases": list(f.get("aliases") or ()),
        "role": f["role"],
        "shape": f.get("shape", ""),
        "optional": bool(f.get("optional")),
    }


def validate(spec: Any) -> Any:
    """A tool's own template, checked: 1 to FIELDS_MAX fields, each an `id`, a variable (`env`) and a role; ways that
    name only its fields, in headers and query parameters of plain names. Raises a 422 Problem."""
    if not _is(spec, dict):
        raise Problem("auth_template", "A template is an object", 422)
    if spec.get("id"):
        if not known(spec["id"]):
            raise Problem("auth_template", f"No template named {spec['id']}", 422)
        return {"id": spec["id"]}
    fields = spec.get("fields")
    if not _is(fields, list) or not 1 <= len(fields) <= FIELDS_MAX:
        raise Problem("auth_template", f"A template has 1 to {FIELDS_MAX} fields", 422)
    out: list[Any] = []
    ids: set[str] = set()
    envs: set[str] = set()
    for f in fields:
        if not _is(f, dict):
            raise Problem("auth_template", "A field is an object", 422)
        fid, env, role = str(f.get("id") or ""), str(f.get("env") or ""), str(f.get("role") or "secret")
        if not re.fullmatch(r"[a-z0-9_]{1,40}", fid) or fid in ids:
            raise Problem("auth_template", "A field's id is a short lowercase name, once", 422)
        if not NAME.match(env) or env in envs:
            raise Problem("auth_template", "A field's variable is capitals, digits and _, once", 422)
        if role not in ROLES:
            raise Problem("auth_template", "A field is a secret, an id or a setting", 422)
        ids.add(fid)
        envs.add(env)
        out.append({"id": fid, "env": env, "role": role})
    ways = _list(spec.get("ways"))
    if not _is(ways, list) or len(ways) > 4:
        raise Problem("auth_template", "A template has at most 4 ways", 422)
    clean: list[Any] = []
    for way in ways:
        if not _is(way, dict) or not set(way) <= {"basic", "headers", "query"} or not way:
            raise Problem("auth_template", "A way is basic, headers and/or query", 422)
        one: dict[str, Any] = {}
        if "basic" in way:
            pair = way["basic"]
            if not _is(pair, list) or len(pair) != 2 or not all(p == "" or p in ids for p in pair):
                raise Problem("auth_template", "basic is [user field, password field]", 422)
            one["basic"] = list(pair)
        for part, name_re in (("headers", HEADER), ("query", PARAM)):
            if part in way:
                given = way[part]
                if not _is(given, dict) or not given:
                    raise Problem("auth_template", f"{part} is names to text with {{field}}", 422)
                for name, text in given.items():
                    used = PLACE.findall(str(text))
                    if not name_re.match(str(name)) or not used or not set(used) <= ids or len(str(text)) > 200:
                        raise Problem("auth_template", f"{part}: {name} must use the template's fields", 422)
                one[part] = {str(k): str(v) for k, v in given.items()}
        clean.append(one)
    return {"fields": out, "ways": clean}


def spec_of(tool: Any) -> Any:
    """The tool's template ({"id"?, "fields", "ways", "delivery"?, "sensitive"?}), or None."""
    try:
        raw = tool["auth_template"]
    except (IndexError, KeyError):
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if _is(data, dict) and data.get("id"):
        base = known(data["id"])
        return {**base, "id": data["id"]} if base else None
    return data if _is(data, dict) and data.get("fields") else None


def stored(spec: Any) -> Any:
    """What `tools.auth_template` keeps for a spec: a known one by id, a tool's own whole."""
    if spec is None:
        return ""
    if spec.get("id"):
        return json.dumps({"id": spec["id"]}, separators=(",", ":"))
    return json.dumps({"fields": spec["fields"], "ways": _list(spec.get("ways"))}, separators=(",", ":"))


def _suffixes(f: Any) -> Any:
    return (f["suffix"], *f.get("aliases", ())) if f.get("suffix") else ()


def match_env(env: Any, only: Any = None) -> Any:
    """(template id, field, label) for a variable that is a known template's field: `<PREFIX>_<SUFFIX>`, with an
    account's label after the prefix (POSTHOG_CRO_API_KEY) or before it (TIDY_AWS_ACCESS_KEY_ID); the longest suffix
    wins (POSTHOG_PROJECT_API_KEY is the project key, not the API key of a "PROJECT" account). None otherwise."""
    env = str(env or "")
    best = None
    for tid, spec in TS.TEMPLATES.items():
        if only and tid != only:
            continue
        prefix = spec["prefix"]
        for f in spec["fields"]:
            for suffix in _suffixes(f):
                m = re.fullmatch(rf"(?:([A-Z0-9_]+?)_)?{prefix}(?:_([A-Z0-9_]+?))?_{suffix}", env)
                if m and (best is None or len(suffix) > best[0]):
                    label = "_".join(x for x in m.groups() if x)
                    best = (len(suffix), tid, f, label)
    return None if best is None else best[1:]


def field_for(spec: Any, env: Any) -> Any:
    """The field of `spec` a variable is: its own `env`, else a known template's suffix."""
    if spec is None:
        return None
    for f in spec["fields"]:
        if f.get("env") and f["env"] == env:
            return f
    if spec.get("id"):
        found = match_env(env, only=spec["id"])
        return found[1] if found else None
    return None


def choose(spec: Any, values: Any) -> Any:
    """The first way whose fields all have a value, or None."""
    for way in _list(spec.get("ways")):
        used: set[str] = set(x for x in _list(way.get("basic")) if x)
        for part in ("headers", "query"):
            for text in _dict(way.get(part)).values():
                used |= set(PLACE.findall(text))
        if used and all(values.get(x) for x in used):
            return way
    return None


def render(way: Any, values: Any) -> Any:
    """(headers, query params, the secrets sent) for one way."""

    def fill(text: str) -> str:
        return PLACE.sub(lambda m: str(values.get(m.group(1), "")), text)

    headers: dict[str, str] = {}
    query: dict[str, str] = {}
    sent: list[str] = []
    if way.get("basic"):
        user, password = (values.get(x, "") if x else "" for x in way["basic"])
        pair = f"{user}:{password}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
        sent += [password, pair, headers["Authorization"][6:]]
    for name, text in _dict(way.get("headers")).items():
        headers[name] = fill(text)
        sent += [values[x] for x in PLACE.findall(text) if values.get(x)]
    for name, text in _dict(way.get("query")).items():
        query[name] = fill(text)
        sent += [values[x] for x in PLACE.findall(text) if values.get(x)]
    return headers, query, sent


def auth_names(spec: Any) -> Any:
    """(header names, query params) any of the template's ways sends: where a caller may have put its proxy token."""
    names: set[str] = {"authorization"}
    params: set[str] = set()
    for way in _list(_dict(spec).get("ways")):
        names |= {str(k).lower() for k in _dict(way.get("headers"))}
        params |= set(_dict(way.get("query")))
    return names, params


def home_for(c: Any, env: Any, computer_only: Any = False) -> Any:
    """The tool a newly stored key under `env` belongs on: a tool with `env`'s known template, seen by everyone, whose
    keys and settings are all that account's (one label) and none of them `env`. With `computer_only` (a key a bot
    already used from its computer's files), only such a tool whose keys go to computers. None otherwise."""
    found = match_env(env)
    if not found:
        return None
    template_id, _field, label = found
    for tool in c.execute(
        "SELECT * FROM tools WHERE auth_template<>'' AND audience IS NULL AND COALESCE(kind,'api')='api' "
        "ORDER BY created_at, id"
    ).fetchall():
        if _dict(spec_of(tool)).get("id") != template_id or (computer_only and tool["delivery"] != "computer"):
            continue
        keys, settings = _tool_envs(c, tool["id"])
        envs = {k["env"] for k in keys} | {s["name"] for s in settings}
        labels = {m[2] for m in (match_env(e, only=template_id) for e in envs) if m}
        if env not in envs and labels <= {label}:
            return tool["id"]
    return None


# ---- the proxy and the check


def _values(c: Any, vault: Any, spec: Any, tool_id: Any, keys: Any) -> Any:
    """{field id: value} of the given keys and the tool's settings (a key wins over a setting of the same field)."""
    values: dict[str, Any] = {}
    for key in keys:
        f = field_for(spec, key["env"])
        if f and key["ciphertext"] is not None:
            values[f["id"]] = vault.cipher.decrypt(c, key)
    for row in c.execute("SELECT name, value FROM tool_settings WHERE tool_id=?", (tool_id,)):
        f = field_for(spec, row["name"])
        if f and f["id"] not in values:
            values[f["id"]] = row["value"]
    return values


def proxy_auth(c: Any, vault: Any, tool: Any, spec: Any, subject: Any) -> Any:
    """What a call through the proxy by `subject` sends: {credential (the first secret key used, for the call's log),
    level, reads_only, headers, query, secrets}. Raises a 403 Problem naming what is missing."""
    api_tools: Any = importlib.import_module(".api_tools", __package__)

    keys, level = api_tools.keys_for(c, tool["id"], subject)
    values = _values(c, vault, spec, tool["id"], keys)
    way = choose(spec, values)
    name = tool["name"] or tool["id"]
    if way is None:
        missing = [f["env"] or f["suffix"] for f in spec["fields"] if f["role"] == "secret" and f["id"] not in values]
        raise Problem(
            "tool_access",
            f"{name} needs {' and '.join(missing[:3]) or 'its keys'} to call it. Add them in Tools > {name} > Keys",
            403,
        )
    headers, query, sent = render(way, values)
    used: set[str] = set(PLACE.findall(json.dumps(way))) | set(x for x in _list(way.get("basic")) if x)
    by_field: dict[str, Any] = {}
    for key in keys:
        f = field_for(spec, key["env"])
        if f:
            by_field[f["id"]] = key
    chosen = [by_field[x] for x in used if x in by_field]
    primary = next((k for k in chosen if _dict(field_for(spec, k["env"])).get("role") == "secret"), None)
    primary = primary or (chosen[0] if chosen else keys[0])
    return {
        "credential": primary,
        "level": level,
        "reads_only": level == "read" and any(k["access_level"] != "read" for k in chosen),
        "headers": headers,
        "query": query,
        "secrets": sorted({s for s in sent if s}, key=len, reverse=True),
    }


def check_auth(c: Any, vault: Any, tool: Any, spec: Any) -> Any:
    """(credential, headers, query) a check sends: the tool's Read and write keys and settings, by the first way they
    fill; None when they fill none."""
    keys = [
        k
        for k in c.execute(
            "SELECT * FROM credentials WHERE tool_id=? AND access_level='read_write' AND ciphertext IS NOT NULL "
            "ORDER BY created_at, id",
            (tool["id"],),
        )
    ]
    values = _values(c, vault, spec, tool["id"], keys)
    way = choose(spec, values)
    if way is None or not keys:
        return None
    headers, query, _ = render(way, values)
    secret = next((k for k in keys if _dict(field_for(spec, k["env"])).get("role") == "secret"), keys[0])
    return secret, headers, query


# ---- what a run gets


def _digest(value: Any) -> Any:
    return "value:" + hashlib.sha256(str(value).encode()).hexdigest()[:16]


def run_env(c: Any, vault: Any, settings: Any, subject: Any, only: Any = None) -> Any:
    """[(variable, shape)] a run of `subject` gets from credential-runtime, sorted: a key as its value's digest or
    `proxy-token`, the proxy's address variable with its address, a setting as its value's digest. The same choices
    as credential-runtime (backend/credentials.py), with nothing minted, revealed or recorded. `only`: just these
    variables (and their keys' proxy addresses), the ones a change can touch."""
    tool_proxy: Any = importlib.import_module(".tool_proxy", __package__)
    credentials: Any = importlib.import_module(".credentials", __package__)
    granted: Any = credentials.granted
    held_at_read: Any = credentials.held_at_read

    held = granted(c, subject)
    proxied = tool_proxy.proxied_tools(c)
    out: list[Any] = []
    envs: set[str] = set()
    for row in c.execute(credentials.RUN_ROWS):
        if row["ciphertext"] is None or row["id"] not in held or (only is not None and row["env"] not in only):
            continue
        tool, way = held_at_read(c, proxied, row, subject, vault.cipher)
        if way == "skip":
            continue
        envs.add(row["env"])
        if tool is not None:
            home = tool_proxy.home_of(row, tool)
            out += [(row["env"], "proxy-token"), (tool_proxy.url_env(home), tool_proxy.proxy_url(settings, home))]
        else:
            out.append((row["env"], _digest(vault.cipher.decrypt(c, row))))
    items = tool_settings.runtime_items(c, held, envs)
    out += [(i["env"], _digest(i["value"])) for i in items if only is None or i["env"] in only]
    return sorted(out)


def _bots(c: Any) -> Any:
    return ["bot:" + r[0] for r in c.execute("SELECT id FROM bots WHERE state<>'archived' ORDER BY id")]


def _envs(c: Any, vault: Any, settings: Any, only: Any = None) -> Any:
    return {s: run_env(c, vault, settings, s, only) for s in _bots(c)}


def compare(before: Any, after: Any) -> Any:
    """({subject: variables changed or gone}, {subject: variables gained})."""
    changed: dict[str, list[str]] = {}
    gained: dict[str, list[str]] = {}
    for subject, was in before.items():
        now = after.get(subject, [])
        lost = sorted(set(was) - set(now))
        new = sorted(set(now) - set(was))
        gone = sorted({e for e, _ in lost})
        if gone:
            changed[subject] = gone
        added = sorted({e for e, _ in new} - {e for e, _ in was})
        if added:
            gained[subject] = added
    return changed, gained


# ---- names


def label_text(label: Any) -> Any:
    """An account label as words: CRO stays CRO, SENTRY_BOT is "Sentry bot"."""
    words = [w for w in str(label or "").split("_") if w]
    out = [w.upper() if len(w) <= 3 and w.isalpha() and w not in WORDS else w.lower() for w in words]
    text = " ".join(out)
    return text[:1].upper() + text[1:]


def display_name(vendor_name: Any, label: Any) -> Any:
    text = label_text(label)
    return vendor_name + (SEP + text if text else "")


def name_from_variable(env: Any, vendor: Any) -> Any:
    """A tool named after its variable, as vendor and label: SLACK_SENTRY_BOT_TOKEN is "Slack · Sentry bot",
    SLACK_BOT_TOKEN "Slack". None when the variable is not the vendor's."""
    words = [w for w in str(env or "").split("_") if w]
    vendor_words = [w.upper() for w in re.findall(r"[a-z0-9]+", str(vendor or "").lower())]
    if not vendor or not words or words[: len(vendor_words)] != vendor_words:
        return None
    rest = words[len(vendor_words) :]
    while rest and rest[-1] in KEY_TAIL:
        rest.pop()
    if all(w in GENERIC for w in rest):
        rest: list[str] = []
    return display_name(TS.vendor_name(vendor), "_".join(rest))


# ---- grouping a vendor's account into one tool


def _rows(c: Any, sql: Any, args: Any = ()) -> Any:
    return c.execute(sql, args).fetchall()


def _tool_envs(c: Any, tool_id: Any) -> Any:
    keys = _rows(c, "SELECT * FROM credentials WHERE tool_id=? ORDER BY created_at, id", (tool_id,))
    settings = _rows(c, "SELECT * FROM tool_settings WHERE tool_id=? ORDER BY name, id", (tool_id,))
    return keys, settings


def accounts(c: Any) -> Any:
    """{(template id, label): [tool rows]}: API tools (no MCP address, not a website) whose every key and setting is a
    field of one known template under one label, oldest first."""
    out: dict[Any, list[Any]] = {}
    for tool in _rows(
        c,
        "SELECT * FROM tools WHERE COALESCE(kind,'api')='api' AND COALESCE(mcp_url,'')='' ORDER BY created_at, id",
    ):
        keys, settings = _tool_envs(c, tool["id"])
        envs = [k["env"] for k in keys] + [s["name"] for s in settings]
        if not envs or any(k["kind"] not in ("api_key", "token") for k in keys):
            continue
        found = [match_env(e) for e in envs]
        if any(f is None for f in found):
            continue
        account = {(f[0], f[2]) for f in found if f}
        if len(account) == 1:
            out.setdefault(account.pop(), []).append(tool)
    return out


def _all_values(c: Any, vault: Any, keys: Any) -> Any:
    """Whether every key's value reaches a run as itself whatever the tool's delivery: an id (tool_settings.is_setting)."""
    for key in keys:
        if key["ciphertext"] is None or not tool_settings.known_name(key["env"]):
            return False
        try:
            if not tool_settings.is_setting(key["env"], vault.cipher.decrypt(c, key)):
                return False
        except Problem:
            return False
    return True


def why_not(c: Any, vault: Any, parts: Any) -> Any:
    """Why these tools cannot be one tool, or "": two hold one variable (two accounts, or copies), their keys reach runs
    differently, they are at different addresses, or they differ in who can see them."""
    seen: dict[str, str] = {}
    for tool in parts:
        keys, settings = _tool_envs(c, tool["id"])
        for env in {k["env"] for k in keys} | {s["name"] for s in settings}:
            if env in seen and seen[env] != tool["id"]:
                return f"{env} is on two tools"
            seen[env] = tool["id"]
    ways: set[str] = set()
    for tool in parts:
        keys, _settings = _tool_envs(c, tool["id"])
        if keys and not _all_values(c, vault, keys):
            ways.add(tool["delivery"])
    if len(ways) > 1:
        return "their keys reach runs differently"
    if len({t["base_url"] for t in parts if t["base_url"] and t["delivery"] == "proxy"}) > 1:
        return "they are at different addresses"
    if len({t["audience"] for t in parts}) > 1:
        return "they differ in who can see them"
    return ""


def _survivor(c: Any, vault: Any, parts: Any) -> Any:
    """The part the account keeps: the oldest proxied one with an address, else the oldest with a stored key that is
    not only an id, else the oldest."""
    for tool in parts:
        if tool["delivery"] == "proxy" and tool["base_url"]:
            return tool
    for tool in parts:
        keys, _ = _tool_envs(c, tool["id"])
        if keys and not _all_values(c, vault, keys):
            return tool
    return parts[0]


def _level(key: Any, grant: Any) -> Any:
    return "read" if key["access_level"] == "read" or grant["access_level"] == "read" else "read_write"


def _union(c: Any, keys: Any) -> Any:
    """Grants so that whoever held one of the account's keys explicitly holds every variable of it at their level:
    [{id, subject, env, level}] made. A bot already given a variable by a key outside the account keeps that one (one value per
    variable)."""
    effective_grant: Any = importlib.import_module(".credentials", __package__).effective_grant

    holders: dict[str, Any] = {}
    for key in keys:
        for g in _rows(c, "SELECT * FROM credential_grants WHERE credential_id=? AND revoked_at IS NULL", (key["id"],)):
            if not g["subject"].startswith(("bot:", "human:")) or effective_grant(c, key["id"], g["subject"]) is None:
                continue
            level = _level(key, g)
            had = holders.setdefault(g["subject"], {"level": level, "envs": set()})
            had["envs"].add(key["env"])
            if level == "read_write":
                had["level"] = level
    by_env: dict[str, list[Any]] = {}
    for key in keys:
        if key["ciphertext"] is not None:
            by_env.setdefault(key["env"], []).append(key)
    made: list[dict[str, Any]] = []
    ids = {k["id"] for k in keys}
    for subject, had in sorted(holders.items()):
        for env, options in sorted(by_env.items()):
            if env in had["envs"]:
                continue
            options = sorted(options, key=lambda k: (k["access_level"] != had["level"], str(k["created_at"])))
            key = options[0]
            if had["level"] == "read_write" and key["access_level"] != "read_write":
                continue
            if subject.startswith("bot:") and any(
                effective_grant(c, o["id"], subject) is not None
                for o in _rows(c, "SELECT id FROM credentials WHERE env=? AND ciphertext IS NOT NULL", (env,))
                if o["id"] not in ids
            ):
                continue
            gid = H.new_id()
            c.execute(
                "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at,access_level)"
                " VALUES(?,?,?,?,?,?)",
                (gid, key["id"], subject, "system", H.now(), had["level"]),
            )
            H.event(c, "system", "credential.granted", key["id"], {"grant": gid, "subject": subject, "why": "grouped"})
            made.append({"id": gid, "subject": subject, "env": env, "level": had["level"]})
    return made


PART_COLUMNS = (
    "id",
    "name",
    "service",
    "base_url",
    "auth_scheme",
    "check_path",
    "delivery",
    "created_by",
    "created_at",
    "updated_at",
    "audience",
    "everyone_reads",
    "auth_template",
)


def _apply(c: Any, vault: Any, template_id: Any, label: Any, parts: Any, survivor: Any) -> Any:
    """Move the parts onto the survivor, name it and give it the template: the undo record."""
    spec = TS.TEMPLATES[template_id]
    parts_record: list[dict[str, Any]] = []
    record: dict[str, Any] = {
        "template": template_id,
        "label": label,
        "survivor": {k: survivor[k] for k in PART_COLUMNS},
        "parts": parts_record,
    }
    keys: list[Any] = []
    for tool in parts:
        tool_keys, settings = _tool_envs(c, tool["id"])
        keys += tool_keys
        if tool["id"] == survivor["id"]:
            continue
        parts_record.append(
            {
                **{k: tool[k] for k in PART_COLUMNS},
                "keys": [k["id"] for k in tool_keys],
                "settings": [s["id"] for s in settings],
            }
        )
        c.execute(
            "UPDATE credentials SET tool_id=?, former_tool_id=CASE WHEN ? THEN ? ELSE former_tool_id END WHERE tool_id=?",
            (survivor["id"], tool["delivery"] == "proxy", tool["id"], tool["id"]),
        )
        c.execute("UPDATE tool_settings SET tool_id=? WHERE tool_id=?", (survivor["id"], tool["id"]))
        c.execute("UPDATE tool_calls SET tool_id=? WHERE tool_id=?", (survivor["id"], tool["id"]))
        c.execute("UPDATE tool_checks SET tool_id=? WHERE tool_id=?", (survivor["id"], tool["id"]))
        c.execute("DELETE FROM tool_oauth_states WHERE tool_id=?", (tool["id"],))
        c.execute("DELETE FROM tools WHERE id=?", (tool["id"],))
    name = display_name(TS.vendor_name(spec["service"]), label)
    taken = c.execute("SELECT 1 FROM tools WHERE lower(name)=lower(?) AND id<>?", (name, survivor["id"])).fetchone()
    c.execute(
        "UPDATE tools SET name=?, service=?, auth_template=?, updated_at=? WHERE id=?",
        (
            survivor["name"] if taken else name,
            spec["service"],
            stored({"id": template_id}),
            H.now(),
            survivor["id"],
        ),
    )
    record["grants"] = _union(c, keys)
    return record


def group(c: Any, vault: Any, settings: Any, template_id: Any, label: Any, parts: Any) -> Any:
    """One account's tools as one tool, in a savepoint: ('grouped' | 'asked' | 'kept' | 'skipped', detail)."""
    reason = why_not(c, vault, parts)
    if reason:
        return "skipped", reason
    survivor = _survivor(c, vault, parts)
    spec = TS.TEMPLATES[template_id]
    # Only the account's variables can change: no other key moves, and a default read or a setting of another name is
    # decided by its own variable alone.
    only: set[str] = set()
    for tool in parts:
        keys, tool_settings_ = _tool_envs(c, tool["id"])
        only |= {k["env"] for k in keys} | {r["name"] for r in tool_settings_}
    before = _envs(c, vault, settings, only)
    c.execute("SAVEPOINT tool_group")
    try:
        record = _apply(c, vault, template_id, label, parts, survivor)
        changed, gained = compare(before, _envs(c, vault, settings, only))
        sensitive = bool(spec.get("sensitive")) or survivor["audience"] is not None
        if changed or (gained and sensitive):
            c.execute("ROLLBACK TO tool_group")
            c.execute("RELEASE tool_group")
            if changed:
                return "kept", "it would change what " + ", ".join(sorted(changed)[:5]) + " read"
            return "asked", gained
        c.execute("RELEASE tool_group")
    except BaseException:
        c.execute("ROLLBACK TO tool_group")
        c.execute("RELEASE tool_group")
        raise
    record["gained"] = {s: envs for s, envs in sorted(gained.items())}
    H.event(c, "system", "tool.grouped", survivor["id"], record)
    return "grouped", record


def _ask(c: Any, template_id: Any, label: Any, parts: Any, gained: Any) -> Any:
    """A Tools Owner task asking a person to group a sensitive account whose grouping gives someone a new value."""
    owner = _tools_owner(c)
    key = f"{template_id}:{label}"
    if not owner or _asked(c, "tool.group_asked", key):
        return None
    name = display_name(TS.vendor_name(TS.TEMPLATES[template_id]["service"]), label)
    who = ", ".join(sorted({s for s in gained})[:10])
    body = (
        f"These tools hold one {name} account's values: "
        + ", ".join(t["name"] for t in parts)
        + f". Making them one tool would give {who} values they do not hold today ("
        + ", ".join(sorted({e for envs in gained.values() for e in envs})[:10])
        + "). Ask a credential administrator whether each should have them; if yes, set their levels, and the next "
        "start groups the tools. If not, leave them apart."
    )
    task = H.task_create(c, H.KEEPER, f"Group {name}?", body, owner, deduplicate=False, lint=False)
    H.event(c, "system", "tool.group_asked", key, {"task": task["id"], "tools": [t["id"] for t in parts]})
    return task


def _tools_owner(c: Any) -> Any:
    page_owners: Any = importlib.import_module(".page_owners", __package__)

    return page_owners.owner(c, "tools", fallback=False)


def _asked(c: Any, action: Any, key: Any) -> Any:
    return c.execute("SELECT 1 FROM events WHERE action=? AND target=? LIMIT 1", (action, key)).fetchone() is not None


def _rename(c: Any, vault: Any) -> Any:
    """Tools named after their variable get vendor and label ("Slack · Sentry bot"), once, with an event to undo by."""
    renamed = 0
    for tool in _rows(c, "SELECT * FROM tools WHERE COALESCE(kind,'api')='api' ORDER BY created_at, id"):
        if not NAME.match(tool["name"] or "") or tool["name"].endswith(LOGIN_USER + LOGIN_PASSWORD):
            continue  # a login pair's keys keep their names until they are a website account
        keys = _rows(c, "SELECT env FROM credentials WHERE tool_id=?", (tool["id"],))
        if tool["name"] not in {k["env"] for k in keys}:
            continue
        vendor = TS.vendor_of(tool["service"], tool["name"], [k["env"] for k in keys], tool["base_url"])
        name = name_from_variable(tool["name"], vendor)
        if not name or c.execute("SELECT 1 FROM tools WHERE lower(name)=lower(?)", (name,)).fetchone():
            continue
        c.execute("UPDATE tools SET name=?, updated_at=? WHERE id=?", (name, H.now(), tool["id"]))
        H.event(c, "system", "tool.updated", tool["id"], {"name": name, "was": tool["name"], "why": "named"})
        renamed += 1
    return renamed


def group_all(c: Any, vault: Any, settings: Any) -> Any:
    """Every account grouping, then names and login pairs: a summary {grouped, asked, kept, skipped, renamed,
    logins}."""
    out: dict[str, Any] = {"grouped": [], "asked": [], "kept": [], "skipped": [], "renamed": 0, "logins": 0}
    for (template_id, label), parts in sorted(accounts(c).items()):
        if len(parts) == 1 and spec_of(parts[0]) is not None:
            continue
        what, detail = group(c, vault, settings, template_id, label, parts)
        tag = f"{template_id}:{label}" if label else template_id
        if what == "asked":
            _ask(c, template_id, label, parts, detail)
        out[what].append(f"{tag}: {detail}" if what in ("kept", "skipped") else tag)
    out["renamed"] = _rename(c, vault)
    out["logins"] = len(offer_logins(c))
    return out


def group_once(store: Any, vault: Any) -> Any:
    """Once per company, at start: `group_all`, recorded under GROUPED. Needs the vault to read values."""
    with store.transaction() as c:
        if c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (GROUPED,)).fetchone():
            return None
        if not vault.cipher.configured:
            return None  # nothing can be read yet: a later start groups them
        summary = group_all(c, vault, store.settings)
        c.execute("INSERT INTO registry_metadata VALUES(?,?)", (GROUPED, json.dumps(summary, sort_keys=True)))
        return summary


def latest_group(c: Any, tool_id: Any) -> Any:
    """The tool's latest `tool.grouped` event while it stands (no `tool.ungrouped` after it), or None."""
    row = c.execute(
        "SELECT rowid, detail FROM events WHERE action='tool.grouped' AND target=? ORDER BY rowid DESC LIMIT 1",
        (tool_id,),
    ).fetchone()
    if (
        row is None
        or c.execute(
            "SELECT 1 FROM events WHERE action='tool.ungrouped' AND target=? AND rowid>? LIMIT 1",
            (tool_id, row["rowid"]),
        ).fetchone()
    ):
        return None
    return row


def ungroup(c: Any, who: Any, vault: Any, tool_id: Any) -> Any:
    """Split a grouped tool back as it was before its latest `tool.grouped`: its parts made again with their keys and
    settings, the grants the grouping made taken back, its name and template restored. Calls and checks stay."""
    api_tools: Any = importlib.import_module(".api_tools", __package__)
    TA: Any = importlib.import_module(".tool_access", __package__)

    TA.require(c, who, TA.tool(tool_id=tool_id), action=TA.EDIT, admins=vault.admins, auth=vault.auth)
    api_tools._readable(c, who, tool_id, vault.admins)
    row = latest_group(c, tool_id)
    if row is None:
        raise Problem("not_grouped", "This tool was not grouped from others", 409)
    record = json.loads(row["detail"])
    for part in record["parts"]:
        if c.execute("SELECT 1 FROM tools WHERE id=?", (part["id"],)).fetchone():
            raise Problem("ungroup", f"A tool named {part['id']} exists again: rename it first", 409)
    for part in record["parts"]:
        columns = list(PART_COLUMNS)
        c.execute(
            f"INSERT INTO tools({','.join(columns)}) VALUES({','.join('?' * len(columns))})",
            [part[k] for k in columns],
        )
        for cid in part["keys"]:
            c.execute(
                "UPDATE credentials SET tool_id=?, former_tool_id=NULL WHERE id=? AND tool_id=?",
                (part["id"], cid, tool_id),
            )
        for sid in part["settings"]:
            c.execute("UPDATE tool_settings SET tool_id=? WHERE id=? AND tool_id=?", (part["id"], sid, tool_id))
    survivor = record["survivor"]
    c.execute(
        "UPDATE tools SET name=?, service=?, auth_template=?, updated_at=? WHERE id=?",
        (survivor["name"], survivor["service"], survivor["auth_template"] or "", H.now(), tool_id),
    )
    for grant in _list(record.get("grants")):
        c.execute(
            "UPDATE credential_grants SET revoked_at=?, revoked_by=? WHERE id=? AND revoked_at IS NULL",
            (H.now(), who.actor, grant["id"]),
        )
    H.event(c, who.actor, "tool.ungrouped", tool_id, {"parts": [p["id"] for p in record["parts"]]})
    return api_tools.detail(c, who, tool_id, vault.admins)


# ---- login pairs


def login_pairs(c: Any) -> Any:
    """[{prefix, username, password}]: two stored keys under one prefix, one its USERNAME (or USER, EMAIL, LOGIN) and
    the other its PASSWORD, each a credential row: a website login stored as keys."""
    rows = _rows(
        c, "SELECT * FROM credentials WHERE kind IN ('api_key','token') AND env<>'' AND ciphertext IS NOT NULL"
    )
    by_env = {r["env"]: r for r in rows}
    out: list[dict[str, Any]] = []
    for env, row in sorted(by_env.items()):
        for tail in LOGIN_PASSWORD:
            if not env.endswith("_" + tail):
                continue
            prefix = env[: -len(tail) - 1]
            user = next((by_env[prefix + "_" + u] for u in LOGIN_USER if prefix + "_" + u in by_env), None)
            if prefix and user is not None:
                out.append({"prefix": prefix, "username": user, "password": row})
    return out


def offer_logins(c: Any) -> Any:
    """One Tools Owner task per login pair not offered before: offer the person a website account on the vendor; the
    variables keep reaching runs until the Tools Owner confirms no bot reads them. The tasks made."""
    owner = _tools_owner(c)
    if not owner:
        return []
    made: list[Any] = []
    for pair in login_pairs(c):
        key = "login:" + pair["prefix"]
        if _asked(c, "tool.login_offered", key):
            continue
        user, password = pair["username"], pair["password"]
        holders = sorted(
            {
                r["subject"]
                for r in _rows(
                    c,
                    "SELECT subject FROM credential_grants WHERE credential_id IN (?,?) AND revoked_at IS NULL",
                    (user["id"], password["id"]),
                )
            }
        )
        vendor = TS.vendor_of(envs=[user["env"]]) or pair["prefix"].lower()
        body = (
            f"{user['env']} and {password['env']} are a sign-in stored as two keys"
            + (f", held by {', '.join(holders[:10])}" if holders else "")
            + f". Offer the person who manages Tools a website account on {TS.vendor_name(vendor)} instead (skill "
            "manage-tools-and-keys, \"Login pairs\"): Tico types it, with 2FA, and no bot sees the password. Keep both "
            "variables until you have confirmed no bot reads them, then ask for the two keys to be deleted."
        )
        task = H.task_create(
            c, H.KEEPER, f"Make {pair['prefix']} a website account?", body, owner, deduplicate=False, lint=False
        )
        H.event(c, "system", "tool.login_offered", key, {"task": task["id"], "keys": [user["id"], password["id"]]})
        made.append(task)
    return made


# ---- paste to classify


def _lines(text: Any) -> Any:
    """[(name or "", value)]: `NAME=value`, `NAME: value`, `export NAME="value"`, else each bare value."""
    out: list[tuple[str, str]] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]{0,99})\s*[=:]\s*(.+)", line)
        if m and not m.group(2).startswith("//"):
            value = m.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            out.append((m.group(1).upper(), value))
        else:
            out += [("", part) for part in line.split()]
    return out


def _shape(f: Any, value: Any) -> Any:
    return bool(f.get("shape")) and re.fullmatch(f["shape"], value) is not None


def _role_of(name: Any, value: Any) -> Any:
    """secret, id or setting for a value of an unknown vendor, by tool_settings' rules (name and value both)."""
    if name and tool_settings.is_setting(name, value):
        return "id" if name.endswith(tool_settings.ID_SUFFIXES) else "setting"
    if not name and not tool_settings.secret_reason(value) and "://" in value:
        return "setting"
    return "secret"


def classify(text: Any, template_id: Any = "") -> Any:
    """{template, items: [{name, field, role, value}]}: each pasted value with the variable it goes under, the template
    field it is (by its name, else its shape), and secret, id or setting. A known template is the one given, else the
    one most values match. A value no template field takes keeps its name, and its role by tool_settings' rules; a
    bare value with no name and no shape is a secret named by its order."""
    pairs = _lines(text)[: FIELDS_MAX * 2]
    scores: dict[str, int] = {}
    for name, value in pairs:
        for tid, spec in TS.TEMPLATES.items():
            if template_id and tid != template_id:
                continue
            if name and match_env(name, only=tid):
                scores[tid] = scores.get(tid, 0) + 2
            elif not name and any(_shape(f, value) for f in spec["fields"] if f["role"] != "setting"):
                scores[tid] = scores.get(tid, 0) + 1
    chosen = template_id if template_id in TS.TEMPLATES else max(scores, key=lambda t: scores[t]) if scores else ""
    spec = known(chosen)
    matched = [m for m in (match_env(n, only=chosen) for n, _v in pairs if n and spec) if m]
    label = matched[0][2] if matched else ""
    items: list[dict[str, Any]] = []
    used: set[str] = {m[1]["id"] for m in matched}
    for index, (name, value) in enumerate(pairs):
        f = None
        if spec and name:
            found = match_env(name, only=chosen)
            f = found[1] if found else None
        elif spec:
            # The first field whose shape fits and is still free; a 32-hex Twilio secret is the API key's when an
            # SK key is in the paste, else the Auth Token.
            fits = [x for x in spec["fields"] if x["id"] not in used and _shape(x, value)]
            if len(fits) > 1 and chosen == "twilio":
                has_sk = any(_shape(x, v) for _n, v in pairs for x in spec["fields"] if x["id"] == "api_key")
                fits.sort(key=lambda x: x["id"] != ("api_secret" if has_sk else "auth_token"))
            f = fits[0] if fits else None
        if f is not None:
            used.add(f["id"])
            prefix = spec["prefix"] if spec else ""
            env = name or (prefix + ("_" + label if label else "") + "_" + f["suffix"])
            role = f["role"]
            if role == "id" and not tool_settings.is_setting(env, value):
                role = "secret"  # an id-named value that looks like a key is kept as a key
            items.append({"name": env, "field": f["id"], "role": role, "value": value})
        else:
            items.append(
                {"name": name or f"VALUE_{index + 1}", "field": "", "role": _role_of(name, value), "value": value}
            )
    return {"template": chosen, "items": items}


def _preview(item: Any) -> Any:
    value = item["value"]
    if item["role"] != "secret":
        return value
    return (value[:4] + "…" + value[-4:]) if len(value) > 12 else "••••"


class Paste(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    text: SecretStr = Field(min_length=1, max_length=PASTE_MAX)
    template: str = Field(default="", max_length=40)


class TemplateSet(Contract):
    model_config = ConfigDict(extra="forbid")
    template: dict[str, Any] | None = None


def classified(text: Any, template: Any = "") -> Any:
    """`classify` as answered to a page: no secret's value, only its first and last characters when long."""
    found = classify(text, template)
    return {
        "template": found["template"],
        "items": [{k: v for k, v in i.items() if k != "value"} | {"preview": _preview(i)} for i in found["items"]],
    }


def paste(c: Any, who: Any, vault: Any, tool_id: Any, body: Any) -> Any:
    """Store a classified paste on a tool in one step: each secret as a Read and write key under its variable, each id
    or setting as a setting (a value tool_settings refuses as one is a key), and the template when one matched."""
    api_tools: Any = importlib.import_module(".api_tools", __package__)
    TA: Any = importlib.import_module(".tool_access", __package__)

    TA.require(c, who, TA.tool(tool_id=tool_id), action=TA.EDIT, admins=vault.admins, auth=vault.auth)
    tool = api_tools._readable(c, who, tool_id, vault.admins)
    found = classify(body.text.get_secret_value(), body.template)
    if found["template"] and spec_of(tool) is None:
        c.execute(
            "UPDATE tools SET auth_template=?, updated_at=? WHERE id=?",
            (stored({"id": found["template"]}), H.now(), tool_id),
        )
        H.event(c, who.actor, "tool.template_set", tool_id, {"template": found["template"]})
    keys = settings = 0
    for item in found["items"]:
        name, value = item["name"], item["value"]
        if item["role"] != "secret" and tool_settings.is_setting(name, value) or item["role"] == "setting":
            if not tool_settings.secret_reason(value):
                have = c.execute("SELECT id FROM tool_settings WHERE tool_id=? AND name=?", (tool_id, name)).fetchone()
                if have is None:
                    tool_settings.add(c, who, tool_id, tool_settings.SettingWrite(name=name, value=value), vault.admins)
                else:
                    tool_settings.update(
                        c, who, tool_id, have["id"], tool_settings.SettingUpdate(value=value), vault.admins
                    )
                settings += 1
                continue
        api_tools.add_key(c, who, vault, tool_id, api_tools.KeyCreate(name=name, value=SecretStr(value), env=name))
        keys += 1
    return {"keys": keys, "settings": settings} | {"tool": api_tools.detail(c, who, tool_id, vault.admins)}


def set_template(c: Any, who: Any, vault: Any, tool_id: Any, value: Any) -> Any:
    """A tool's template: a known one by id, the tool's own, or none (null)."""
    api_tools: Any = importlib.import_module(".api_tools", __package__)
    TA: Any = importlib.import_module(".tool_access", __package__)

    TA.require(c, who, TA.tool(tool_id=tool_id), action=TA.EDIT, admins=vault.admins, auth=vault.auth)
    api_tools._readable(c, who, tool_id, vault.admins)
    spec = None if value is None else validate(value)
    c.execute("UPDATE tools SET auth_template=?, updated_at=? WHERE id=?", (stored(spec), H.now(), tool_id))
    H.event(c, who.actor, "tool.template_set", tool_id, {"template": _dict(spec).get("id") or ("own" if spec else "")})
    return api_tools.detail(c, who, tool_id, vault.admins)


def listing() -> Any:
    """The known templates, for the Add flow and the Tools chat's key drop."""
    return {
        "templates": [
            {
                "id": tid,
                "name": TS.vendor_name(spec["service"]),
                "prefix": spec["prefix"],
                "delivery": spec["delivery"],
                "fields": [_field_view(f) for f in spec["fields"]],
                "ways": spec["ways"],
            }
            for tid, spec in TS.TEMPLATES.items()
        ]
    }


def view(tool: Any) -> Any:
    """A tool's template on its page: id ('' for its own), fields and ways; None for none."""
    spec = spec_of(tool)
    if spec is None:
        return None
    return {
        "id": spec.get("id", ""),
        "fields": [_field_view(f) for f in spec["fields"]],
        "ways": _list(spec.get("ways")),
        "delivery": spec.get("delivery", ""),
    }


def install(app: Any, store: Any) -> Any:
    """`GET /api/v2/tool-templates`, `POST /api/v2/tool-templates/classify`, and on an API tool `PUT …/template`,
    `POST …/paste` and `POST …/ungroup` (whoever manages the tool)."""
    from fastapi import Request

    api_tools: Any = importlib.import_module(".api_tools", __package__)
    TA: Any = importlib.import_module(".tool_access", __package__)
    validate_identity: Any = importlib.import_module(".auth", __package__).validate_identity

    def vault() -> Any:
        return app.state.vault

    def manager(c: Any, w: Any) -> Any:
        TA.require(c, w, TA.tool(), action=TA.EDIT, admins=vault().admins, auth=vault().auth)

    def change(request: Any, body: Any, fn: Callable[[Any], Any]) -> Any:
        return vault().change(
            request.state.identity, request.url.path, request.headers.get("idempotency-key"), body, manager, fn
        )

    @app.get("/api/v2/tool-templates")
    def tool_templates(request: Request) -> Any:
        with store.read() as c:
            validate_identity(c, request.state.identity)
        return listing()

    @app.post("/api/v2/tool-templates/classify")
    def classify_paste(request: Request, body: Paste) -> Any:
        """What a paste holds, value by value: no secret's value is answered, and nothing is stored or logged."""
        with store.read() as c:
            validate_identity(c, request.state.identity)
            manager(c, request.state.identity)
        return classified(body.text.get_secret_value(), body.template)

    @app.post("/api/v2/api-tools/{tool_id}/paste")
    def paste_to_tool(request: Request, tool_id: str, body: Paste) -> Any:
        return change(
            request, api_tools._Receipt(body), lambda c: paste(c, request.state.identity, vault(), tool_id, body)
        )

    @app.put("/api/v2/api-tools/{tool_id}/template")
    def set_tool_template(request: Request, tool_id: str, body: TemplateSet) -> Any:
        return change(request, body, lambda c: set_template(c, request.state.identity, vault(), tool_id, body.template))

    @app.post("/api/v2/api-tools/{tool_id}/ungroup")
    def ungroup_tool(request: Request, tool_id: str) -> Any:
        with store.transaction() as c:
            validate_identity(c, request.state.identity)
            return ungroup(c, request.state.identity, vault(), tool_id)
