"""One `tools:` entry (older: `access:`) of a bot.yaml, as a schema (docs/creating-bots.md, "Access and credentials").

Two callers share it. The runner (`runner-rs/src/declared_access.rs`) reads a bot's entries and reports the
fields listed here. The server (`backend/bot_tools.py`) checks an entry a person registers for a bot
before it asks BotOps to write it into the bot's repository, so a request can only name what the
runner would report back. An entry holds names and verbs, never a credential: `env` is the variable's
name, and text that looks like a key, a token or a password is refused (`secret_in`).

A tool may also be a remote MCP server (`mcp:`: url, transport, headers). Its headers may hold
`${VAR}` placeholders for the entry's own `env` variable and nothing else, so the entry still holds
no value (`clean_mcp`; `clients/mcp_servers.py` is the runtime side).

Pure stdlib apart from PyYAML, like the rest of clients/.
"""

import ipaddress
import math
import re
from urllib.parse import urlsplit

import yaml

# The fields, beyond service, identity, can, env and note, that describe what a tool is scoped to.
SCOPE_KEYS = ("database", "channels", "channel", "project", "projects", "mailbox", "mailboxes", "sites", "site",
              "repo", "repos", "repositories", "org", "organization", "workspace", "account", "region", "domain",
              "domains", "calendars", "folders", "drive", "drives", "bucket", "buckets", "table", "tables",
              "dataset", "datasets", "collections", "read_only", "org_read", "max_rows", "timeout_seconds",
              "read_preference")
MAX_CAN = 20
MAX_SCOPE = 20
SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,59}$")
VERB_RE = re.compile(r"^[a-z][a-z_-]{0,39}$")
ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,99}$")
# The names a bot's turn owns; a credential may not arrive under one (runner-rs/src/env.rs).
# The HUB_ names the runner and the `hub` command set themselves. Other HUB_ names (a team's own HUB_BUCKET) are ordinary
# variables a bot may be granted.
RUNNER_HUB_ENV = {"HUB_TOKEN", "HUB_API_URL", "HUB_WORKSPACE", "HUB_EMPLOYEE", "HUB_OPERATION_ID", "HUB_BOT", "HUB_DIR",
                  "HUB_DB", "HUB_HUMAN_OVERRIDE", "HUB_INGEST_TOKEN", "HUB_RUNNER_CONFIG", "HUB_TASK_ID"}
RESERVED_ENV = {"HOME", "PATH", "SHELL", "PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS", "LD_PRELOAD",
                "DYLD_INSERT_LIBRARIES", "CODEX_HOME", "CLAUDE_CONFIG_DIR"} | RUNNER_HUB_ENV
RESERVED_PREFIXES = ("TICO_", "DYLD_", "LD_")

# Key shapes seen in the wild, and a URL with a password in it.
SECRET_SHAPES = re.compile(
    r"sk-[A-Za-z0-9_-]{8,}|[sr]k_(?:live|test)_\w+|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_\w+|glpat-[\w-]{10,}|"
    r"xox[abposr]-[\w-]+|xapp-[\w-]+|ph[xcs]_[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{12,}|AIza[\w-]{20,}|"
    r"ya29\.[\w.-]+|eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*|-----BEGIN [A-Z ]*KEY|npm_[A-Za-z0-9]{20,}|SG\.[\w-]{16,}|"
    r"\bBearer\s+\S{12,}|\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@|\bop:/{2}\S+")
MCP_TRANSPORTS = ("http", "sse")            # streamable HTTP, and the older server-sent-events transport
MCP_KEYS = ("url", "transport", "headers")
MAX_HEADERS = 10
PLACEHOLDER = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")
HEADER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
NOT_SETTABLE = {"host", "content-length", "transfer-encoding", "connection"}
TOKEN_PARTS = re.compile(r"[\s,;()\"'<>]+")
TOKEN_CHARS = re.compile(r"^[A-Za-z0-9_+/=-]+$")


class EntryError(ValueError):
    """The entry cannot be registered; the message says what to change."""

    def __init__(self, message, code="entry"):
        super().__init__(message)
        self.code = code


def entropy(token):
    counts = {ch: token.count(ch) for ch in set(token)}
    return -sum(n / len(token) * math.log2(n / len(token)) for n in counts.values())


def secret_in(text):
    """A reason when `text` looks like a credential value, else "". A guard, not a proof: the docs
    say where credentials go, and this stops the common mistake of pasting one into a field."""
    text = str(text or "")
    found = SECRET_SHAPES.search(text)
    if found:
        return "it contains what looks like a key, token or password"
    for token in TOKEN_PARTS.split(text):
        if (len(token) >= 24 and TOKEN_CHARS.match(token) and re.search(r"[a-z]", token) and re.search(r"[A-Z]", token)
                and re.search(r"\d", token) and entropy(token) >= 3.9):
            return "it contains a long random-looking string"
    return ""


def one_line(value, limit):
    return " ".join(str(value).split())[:limit]


def scalar(value, limit):
    if isinstance(value, bool):
        return "yes" if value else "no"
    return one_line(value, limit) if isinstance(value, (str, int, float)) else ""


def scope_of(entry):
    """The scope fields of an entry as {key: text | [text]}, capped; the runner's report and the form agree."""
    result = {}
    for key in SCOPE_KEYS:
        value = entry.get(key)
        if isinstance(value, (list, tuple)):
            items = [scalar(item, 100) for item in value[:20]]
            items = [item for item in items if item]
            if items:
                result[key] = items
        elif value not in (None, "") and scalar(value, 200):
            result[key] = scalar(value, 200)
        if len(result) >= MAX_SCOPE:
            break
    return result


def loopback(host):
    """Whether a URL's host is this computer: where plain http is allowed (a local MCP server)."""
    host = (host or "").lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def placeholders(text):
    """The variable names `${NAME}` placeholders in `text` refer to, in order."""
    return PLACEHOLDER.findall(str(text or ""))


def clean_mcp(mcp, env=""):
    """A `mcp:` block, validated and normalised: {url, transport, headers?}. Raises EntryError.

    https only (plain http for a loopback host), a known transport, and headers whose values are text
    with `${VAR}` placeholders for `env`, the entry's one credential variable. Anything else that looks
    like a credential is refused, so a value never reaches bot.yaml through this field."""
    if not isinstance(mcp, dict):
        raise EntryError("mcp is an object such as {url: \"https://mcp.example.com/mcp\", transport: http}")
    unknown = sorted(str(key) for key in mcp if key not in MCP_KEYS)
    if unknown:
        raise EntryError(f"mcp does not take {', '.join(unknown)}; it takes {', '.join(MCP_KEYS)}")
    url = str(mcp.get("url") or "").strip()
    if not url or len(url) > 500 or re.search(r"\s", url):
        raise EntryError("mcp.url is the server's address, such as https://mcp.example.com/mcp")
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        raise EntryError("mcp.url is not a valid address") from None
    if parts.scheme == "http":
        if not loopback(host):
            raise EntryError("mcp.url must be https; plain http is only for a server on this computer (localhost)")
    elif parts.scheme != "https":
        raise EntryError("mcp.url must be https")
    if not host or parts.username is not None or parts.password is not None or parts.fragment:
        raise EntryError("mcp.url is a plain address: a host, no password and no #fragment")
    if "${" in url or secret_in(url):
        raise EntryError("mcp.url holds no credential (put it in a header as ${VAR}, never in the address)", "secret")
    transport = str(mcp.get("transport") or "http").strip().lower()
    if transport not in MCP_TRANSPORTS:
        raise EntryError(f"mcp.transport is one of {', '.join(MCP_TRANSPORTS)}")
    headers = mcp.get("headers") or {}
    if not isinstance(headers, dict) or len(headers) > MAX_HEADERS:
        raise EntryError(f"mcp.headers is an object of at most {MAX_HEADERS} headers")
    kept = {}
    for name, value in headers.items():
        name = str(name).strip()
        if not HEADER_NAME.match(name) or name.lower() in NOT_SETTABLE:
            raise EntryError(f"{name!r} is not a header name Tico will send")
        if not isinstance(value, str) or not value.strip() or len(value) > 500 or "\n" in value or "\r" in value:
            raise EntryError(f"mcp.headers.{name} is one line of text, such as \"Bearer ${{JIRA_API_TOKEN}}\"")
        bare = PLACEHOLDER.sub("", value)
        if "${" in bare or "$" in bare:
            raise EntryError(f"mcp.headers.{name} may only use ${{VARIABLE}} placeholders (capitals, digits, underscores)")
        if secret_in(bare):
            raise EntryError(f"mcp.headers.{name} holds what looks like a credential. Keep the value out of the entry: "
                             "store it as the env variable and write ${VARIABLE} here", "secret")
        for used in placeholders(value):
            if used != env:
                raise EntryError(f"mcp.headers.{name} uses ${{{used}}}, but only the entry's own env variable "
                                 + (f"(${{{env}}}) " if env else "(set `env`) ") + "may be a placeholder")
        kept[name] = value.strip()
    result = {"url": url, "transport": transport}
    if kept:
        result["headers"] = kept
    return result


def clean(entry):
    """A registered entry, validated and normalised, in bot.yaml's key order. Raises EntryError."""
    if not isinstance(entry, dict):
        raise EntryError("An access entry is an object with a service, an identity and what it can do")
    service = str(entry.get("service") or "").strip().lower()
    if not SERVICE_RE.match(service):
        raise EntryError("service is a short name such as posthog or google-calendar (lowercase, digits, - and _)")
    can = entry.get("can")
    can = [can] if isinstance(can, str) else can
    if not isinstance(can, list) or not can:
        raise EntryError("can lists what the bot may do, at least one verb: read, draft, post, act, use, send, write")
    verbs = []
    for verb in can[:MAX_CAN + 1]:
        verb = str(verb).strip().lower()
        if not VERB_RE.match(verb):
            raise EntryError(f"{verb!r} is not a verb; use words such as read, draft, post, act, use, write")
        if verb not in verbs:
            verbs.append(verb)
    if len(verbs) > MAX_CAN:
        raise EntryError(f"can holds at most {MAX_CAN} verbs")
    scope = entry.get("scope") or {}
    if not isinstance(scope, dict):
        raise EntryError("scope is an object such as {\"database\": \"warehouse\"}")
    unknown = sorted(str(key) for key in scope if key not in SCOPE_KEYS)
    if unknown:
        raise EntryError(f"scope does not take {', '.join(unknown)}; it takes {', '.join(SCOPE_KEYS)}")
    kept = scope_of(scope)
    given = {key for key, value in scope.items() if value not in (None, "", [])}
    if given - set(kept):
        raise EntryError("scope values are text, numbers, yes/no, or lists of them")
    env = str(entry.get("env") or "").strip()
    if env and (not ENV_RE.match(env) or env in RESERVED_ENV or env.startswith(RESERVED_PREFIXES)):
        raise EntryError("env is the name of the environment variable that holds the credential, such as "
                         "POSTHOG_KEY (capitals, digits, underscores), never the credential itself", "secret"
                         if len(env) > 30 or secret_in(env) else "entry")
    identity, note = one_line(entry.get("identity") or "", 300), one_line(entry.get("note") or "", 500)
    for field, text in (("service", service), ("identity", identity), ("note", note), *(
            ("scope." + key, " ".join(value) if isinstance(value, list) else value) for key, value in kept.items())):
        why = secret_in(text)
        if why:
            raise EntryError(f"{field} is not accepted: {why}. Tico never takes a credential value; the operator "
                             "puts it on the bot's computer and this entry names only its variable", "secret")
    result = {"service": service}
    if identity:
        result["identity"] = identity
    if entry.get("mcp") not in (None, {}, ""):
        result["mcp"] = clean_mcp(entry["mcp"], env)
    result["can"] = verbs
    result.update(kept)
    if env:
        result["env"] = env
    if note:
        result["note"] = note
    return result


def to_yaml(entry):
    """The entry as a list item ready to paste under `tools:` in bot.yaml."""
    text = yaml.safe_dump([entry], sort_keys=False, default_flow_style=None, allow_unicode=True, width=200)
    return text.rstrip("\n")
