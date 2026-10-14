"""A bot's remote MCP servers: the `mcp:` blocks of its `tools:` entries (docs/connect-tools.md), read once
here for the runner's hosts, its readiness report and the reachability check.

An entry looks like

    - service: linear
      mcp: {url: "https://mcp.linear.app/mcp", transport: http, headers: {Authorization: "Bearer ${LINEAR_API_KEY}"}}
      can: [read, write]
      env: LINEAR_API_KEY

The only placeholder a header may use is that entry's own `env` variable (`access_entry.clean_mcp`), and
it is filled only from the environment of this bot's run: what its own secrets, a credential profile and a
grant from the credential vault put there. Nothing else of the runner's environment is ever read into a
header. A server whose variable is not in the run is left out and named in `problems`, never sent half
configured. Where a harness reads `${VAR}` itself (Claude Code, Gemini CLI, Codex's env-based headers) the
placeholder goes through untouched, so no value is written to a file or a command line.

Pure stdlib, like the rest of clients/.
"""

import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .access_entry import EntryError, PLACEHOLDER, clean_mcp, placeholders

# What each harness can take. `transports` are the MCP transports it speaks; `why` is what a person reads
# when it cannot. A harness missing here is one Tico does not start (Hermes and OpenClaw run elsewhere).
SUPPORT = {
    "claude": {"name": "Claude Code", "transports": ("http", "sse")},
    "codex": {"name": "Codex", "transports": ("http",)},
    "gemini": {"name": "Gemini CLI", "transports": ("http", "sse")},
    "grok": {"name": "Grok Build", "transports": ("http", "sse")},
    "antigravity": {"name": "Antigravity", "why": "it reads MCP servers only from a config file in the operator's home, which Tico "
                                                   "does not touch (the operator's own servers would reach every bot)"},
    "cursor": {"name": "Cursor", "why": "cursor-agent reads MCP servers only from ~/.cursor/mcp.json or the bot's "
                                        ".cursor/mcp.json, with no per-run setting, so a bot would also get the operator's own servers"},
    "pi": {"name": "pi", "why": "pi has no MCP support"},
    "musecode": {"name": "Muse Code", "transports": ("http",)},
}
REACH_TIMEOUT_S = 4


def harness_key(runtime, harness=""):
    """The SUPPORT key for a bot's runtime (and, for Gemini's second harness, its harness)."""
    return "antigravity" if str(harness or "") == "antigravity" else str(runtime or "")


def server_name(service):
    """The name a harness shows the model: the service, made safe for a tool name (`google-drive` -> `google_drive`)."""
    return re.sub(r"[^a-z0-9_]", "_", str(service or "").lower()) or "mcp"


def declared(tools):
    """[(entry, mcp)] for each `tools:` entry that has a valid `mcp:` block; a bad one is skipped here and
    reported by `problem_of` (the runner's readiness row says why)."""
    found = []
    for entry in tools if isinstance(tools, list) else []:
        if isinstance(entry, dict) and entry.get("mcp") not in (None, {}, ""):
            try:
                found.append((entry, clean_mcp(entry["mcp"], str(entry.get("env") or "").strip())))
            except EntryError:
                pass
    return found


def problem_of(entry):
    """Why an entry's `mcp:` block cannot be used, or ""."""
    if not isinstance(entry, dict) or entry.get("mcp") in (None, {}, ""):
        return ""
    try:
        clean_mcp(entry["mcp"], str(entry.get("env") or "").strip())
    except EntryError as exc:
        return "The MCP block is not valid: " + str(exc)
    return ""


def servers_for_run(tools, env):
    """(servers, problems) for one run. A server is {name, service, url, transport, headers, env}: `headers`
    keep their `${VAR}` placeholders, `env` is the variable they need. One whose variable is absent from `env`
    is not returned; `problems` says so in words for the run's log."""
    servers, problems, used = [], [], set()
    for entry, mcp in declared(tools):
        service = str(entry.get("service") or "").strip().lower()
        var = str(entry.get("env") or "").strip()
        needs = {v for value in mcp.get("headers", {}).values() for v in placeholders(value)}
        if any(not str(env.get(v) or "").strip() for v in needs):
            problems.append(f"{service}: MCP server not passed, {', '.join(sorted(needs))} is not granted to this bot")
            continue
        name, n = server_name(service), 1
        while name in used:
            n += 1
            name = f"{server_name(service)}_{n}"
        used.add(name)
        servers.append({"name": name, "service": service, "url": mcp["url"], "transport": mcp["transport"],
                        "headers": dict(mcp.get("headers") or {}), "env": var})
    return servers, problems


def expand(text, env):
    """`text` with each `${VAR}` replaced by env[VAR], for a harness that cannot read the placeholder itself."""
    return PLACEHOLDER.sub(lambda m: str(env.get(m.group(1)) or ""), text)


def unsupported(servers, runtime, harness=""):
    """The servers this harness cannot take, as [(server, why)]; [] when it takes them all."""
    row = SUPPORT.get(harness_key(runtime, harness)) or {"name": str(runtime or "This harness"), "why": "it has no MCP support in Tico"}
    allowed = row.get("transports") or ()
    out = []
    for server in servers:
        if server["transport"] not in allowed:
            why = row.get("why") or f"it takes {' and '.join(allowed)} servers, not {server['transport']}"
            out.append((server, f"{row['name']} cannot use it: {why}"))
    return out


def supported(servers, runtime, harness=""):
    bad = {id(server) for server, _ in unsupported(servers, runtime, harness)}
    return [server for server in servers if id(server) not in bad]


def warnings_for(tools, runtime, harness=""):
    """Readiness warnings for one bot: each MCP server its harness cannot pass, by tool. Needs no credential."""
    servers = [{"service": str(entry.get("service") or "").strip().lower(), "transport": mcp["transport"], "url": mcp["url"]}
              for entry, mcp in declared(tools)]
    return [f"{server['service']}: its MCP server is not passed to the bot's runs. " + why
            for server, why in unsupported(servers, runtime, harness)]


# --------------------------------------------------------------------------- one config per harness
def claude_config(servers):
    """`--mcp-config` entries. Claude Code expands `${VAR}` in url and headers from the turn's own environment."""
    return {s["name"]: {"type": s["transport"], "url": s["url"],
                        **({"headers": dict(s["headers"])} if s["headers"] else {})} for s in servers}


def gemini_config(servers):
    """`mcpServers` for Gemini CLI's settings file: `httpUrl` is streamable HTTP, `url` is SSE. The CLI expands
    `${VAR}` in the file from its environment, so the file holds no value."""
    return {s["name"]: {("httpUrl" if s["transport"] == "http" else "url"): s["url"],
                        **({"headers": dict(s["headers"])} if s["headers"] else {})} for s in servers}


def grok_config(servers, env):
    """ACP `mcpServers` for `session/new`: headers as {name, value}, resolved, because ACP has no placeholders.
    They travel over the agent's stdin for this session and are written nowhere."""
    return [{"type": s["transport"], "name": s["name"], "url": s["url"],
             "headers": [{"name": k, "value": expand(v, env)} for k, v in s["headers"].items()]} for s in servers]


def codex_config(servers, env, process_env=None):
    """`mcp_servers` tables for Codex (streamable HTTP only). A header that is exactly `Bearer ${VAR}` on
    Authorization is `bearer_token_env_var`, one that is exactly `${VAR}` is `env_http_headers`: Codex reads
    those variables from its own process environment, which is this bot's (`process_env`). Any other shape
    (`Basic ${VAR}`), or a variable the app-server process does not have, is resolved into `http_headers`."""
    out = {}
    have = process_env if process_env is not None else {}
    for s in servers:
        table, static, by_env = {"url": s["url"]}, {}, {}
        for name, value in s["headers"].items():
            found = placeholders(value)
            var = found[0] if len(found) == 1 else ""
            if var and str(have.get(var) or "") == str(env.get(var) or "") and have.get(var):
                if name.lower() == "authorization" and value.strip() == "Bearer ${" + var + "}":
                    table["bearer_token_env_var"] = var
                    continue
                if value.strip() == "${" + var + "}":
                    by_env[name] = var
                    continue
            static[name] = expand(value, env)
        if by_env:
            table["env_http_headers"] = by_env
        if static:
            table["http_headers"] = static
        out[s["name"]] = table
    return out


def muse_config(servers, env):
    """(`mcp_servers.<name>` values, extra environment) for Muse Code's per-run `-c` settings. Muse takes a
    secret only as a header that is exactly one `${VAR}` it reads from its own environment, so a header
    of any other shape (`Bearer ${VAR}`) is resolved into a variable of its own, `TICO_MCP_<n>`, that only
    this run's environment carries. Nothing is written to a file or put on the command line."""
    out, extra = {}, {}
    for s in servers:
        headers = {}
        for name, value in s["headers"].items():
            found = placeholders(value)
            if len(found) == 1 and value.strip() == "${" + found[0] + "}":
                headers[name] = value.strip()
                continue
            var = f"TICO_MCP_{len(extra) + 1}"
            extra[var] = expand(value, env)
            headers[name] = "${" + var + "}"
        out[s["name"]] = {"transport": "streamable_http", "url": s["url"], **({"headers": headers} if headers else {})}
    return out, extra


# --------------------------------------------------------------------------- reachability
def reachability(url, transport, headers, timeout=REACH_TIMEOUT_S, opener=None):
    """reachable | auth_failed | unreachable, from one cheap request with a short timeout.

    Streamable HTTP gets an MCP `initialize` POST; SSE gets the GET that opens the stream (closed at once).
    401 and 403 are a refused credential; any other answer below 500 except 404 and 410 means a server is
    there. `headers` are the resolved ones. Redirects are not followed, so a credential never leaves for
    another host."""
    open_url = opener or _opener().open
    headers = {"User-Agent": "tico-runner", **{k: v for k, v in (headers or {}).items() if "\n" not in v and "\r" not in v}}
    if transport == "sse":
        request = urllib.request.Request(url, headers={**headers, "Accept": "text/event-stream"}, method="GET")
    else:
        body = (b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},'
                b'"clientInfo":{"name":"tico-runner","version":"1"}}}')
        request = urllib.request.Request(url, data=body, method="POST", headers={
            **headers, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    try:
        with open_url(request, timeout=timeout) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (urllib.error.URLError, OSError, ValueError):
        return "unreachable"
    if status in (401, 403):
        return "auth_failed"
    return "unreachable" if status >= 500 or status in (404, 410) else "reachable"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _opener():
    return urllib.request.build_opener(_NoRedirect)


def host_of(url):
    """The host of an MCP server's address, for a person to read."""
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""
