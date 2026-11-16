"""What a bot does with the credentials its turn was given, for the fakes (fake_claude.py, fake_codex.py) in the
credential delivery suite (runner_support/tests/test_credential_delivery.py). A prompt line starting with a marker
makes the turn use a credential the way a real bot would, from the environment and MCP configuration the runner
launched the harness with:

- `CALL <auth> <ENV> <url>`: one GET to `url` with the value of $ENV as `auth` says: `bearer`, `header:<Name>`,
  `query:<param>` or `basic:<username>`. A `url` starting with `$NAME` starts from that variable (a proxied tool's
  `$TICO_TOOL_<ID>_URL`), so the same line calls the vendor directly or through the Tools proxy.
- `FILE <ENV> <url>`: reads the file $ENV names (a `file` credential) and POSTs its bytes to `url`.
- `MCPCALL <server>`: connects to the named server of the harness's MCP configuration with the headers it gives
  (`servers`, resolved by the fake as its harness does) and sends initialize, tools/list and one tools/call.
- `LOGIN <site>`: signs in to a website tool with the bot-side commands: `tico browser open`, `tico login fill` for
  the username, password and code, then `tico browser click` to submit.
- `ECHO <ENV>`: says the value of $ENV in the reply, as a careless bot would, so the transcript's redaction is tested.

Each use appends one record to $FAKE_LOG (`{"use": ..., ...}`: status codes, the answer's body, the turn's whole
environment) and adds one line to the reply. A prompt with no marker changes nothing."""

import base64
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request

MARKER = re.compile(r"^(CALL|FILE|MCPCALL|LOGIN|ECHO) +(.+)$")
AGENT = "acme-bot-fake/1"


def _send(url, data=None, headers=None):
    """(status, body text) of one request; an HTTP error is an answer too."""
    request = urllib.request.Request(url, data=data, headers={"User-Agent": AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            return answer.status, answer.read().decode("utf-8", "replace"), dict(answer.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace"), dict(error.headers)
    except OSError as error:
        return 0, f"{type(error).__name__}: {error}", {}


def _url(text):
    found = re.match(r"^\$([A-Z_][A-Z0-9_]*)(.*)$", text)
    return (os.environ.get(found.group(1), "") + found.group(2)) if found else text


def call(auth, env, url):
    value = os.environ.get(env)
    if not value:
        return {"use": "call", "env": env, "present": False}
    url, headers = _url(url), {}
    if auth == "bearer":
        headers["Authorization"] = "Bearer " + value
    elif auth.startswith("header:"):
        headers[auth[7:]] = value
    elif auth.startswith("query:"):
        url += ("&" if "?" in url else "?") + auth[6:] + "=" + urllib.request.quote(value, safe="")
    elif auth.startswith("basic:"):
        headers["Authorization"] = "Basic " + base64.b64encode(f"{auth[6:]}:{value}".encode()).decode()
    status, body, _ = _send(url, headers=headers)
    return {"use": "call", "env": env, "present": True, "status": status, "body": body}


def read_file(env, url):
    path = os.environ.get(env)
    if not path or not os.path.isfile(path):
        return {"use": "file", "env": env, "present": bool(path), "path": path}
    with open(path, "rb") as f:
        data = f.read()
    status, body, _ = _send(_url(url), data=data, headers={"Content-Type": "application/octet-stream"})
    mode = oct(os.stat(path).st_mode & 0o777)
    return {
        "use": "file",
        "env": env,
        "present": True,
        "path": path,
        "mode": mode,
        "sha256": hashlib.sha256(data).hexdigest(),
        "status": status,
        "body": body,
    }


def mcp(name, servers):
    server = (servers or {}).get(name)
    if not server:
        return {"use": "mcp", "server": name, "present": False, "servers": sorted(servers or {})}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    headers.update(server.get("headers") or {})
    statuses, tools, text, errors = [], [], "", []

    def rpc(method, params=None, rid=None):
        message = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if rid is not None:
            message["id"] = rid
        status, body, answer_headers = _send(server["url"], data=json.dumps(message).encode(), headers=headers)
        statuses.append(status)
        if not 200 <= status < 300:
            errors.append(body[:300])
        session = answer_headers.get("Mcp-Session-Id") or answer_headers.get("mcp-session-id")
        if session:
            headers["Mcp-Session-Id"] = session
        try:
            return json.loads(body) if body else {}
        except ValueError:
            return {}

    hello = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": AGENT}}, 1)
    if "result" in hello:
        rpc("notifications/initialized")
        listed = rpc("tools/list", {}, 2)
        tools = [t["name"] for t in (listed.get("result") or {}).get("tools", [])]
        if tools:
            done = rpc("tools/call", {"name": tools[0], "arguments": {"order": "42"}}, 3)
            text = " ".join(c.get("text", "") for c in (done.get("result") or {}).get("content", []))
    return {
        "use": "mcp",
        "server": name,
        "present": True,
        "url": server["url"],
        "statuses": statuses,
        "errors": errors,
        "tools": tools,
        "text": text,
    }


def login(site):
    steps = [
        ["browser", "open", site],
        ["login", "fill", site, "username", "--ref", "e1"],
        ["login", "fill", site, "password", "--ref", "e2"],
        ["login", "fill", site, "code", "--ref", "e3"],
        ["browser", "click", site, "e4"],
    ]
    out = []
    for step in steps:
        try:
            done = subprocess.run(["tico", *step], capture_output=True, text=True, timeout=60)
            out.append({"argv": step, "code": done.returncode, "stdout": done.stdout, "stderr": done.stderr})
        except (OSError, subprocess.TimeoutExpired) as error:
            out.append({"argv": step, "code": -1, "stderr": f"{type(error).__name__}: {error}"})
        if out[-1]["code"] != 0:
            break
    return {"use": "login", "site": site, "steps": out}


def act(text, servers, record):
    """Every marker in the prompt, once each, in order: the reply's extra lines."""
    lines, done = [], set()
    for raw in text.splitlines():
        found = MARKER.match(raw.strip())
        if not found or raw.strip() in done:
            continue
        done.add(raw.strip())
        verb, args = found.group(1), found.group(2).split()
        if verb == "CALL" and len(args) == 3:
            entry = call(*args)
        elif verb == "FILE" and len(args) == 2:
            entry = read_file(*args)
        elif verb == "MCPCALL" and len(args) == 1:
            entry = mcp(args[0], servers)
        elif verb == "LOGIN" and len(args) == 1:
            entry = login(args[0])
        elif verb == "ECHO" and len(args) == 1:
            entry = {"use": "echo", "env": args[0], "present": bool(os.environ.get(args[0]))}
            lines.append(f"The value of {args[0]} is {os.environ.get(args[0], '')}.")
        else:
            continue
        entry["environ"] = dict(os.environ)
        record(entry)
        if verb != "ECHO":
            status = entry.get("status", entry.get("statuses", [s.get("code") for s in entry.get("steps", [])]))
            lines.append(f"{verb} {' '.join(args)}: {status if entry.get('present', True) else 'not given'}.")
    return (" " + " ".join(lines)) if lines else ""


def claude_servers(argv):
    """Claude Code's remote servers from `--mcp-config`, with `${VAR}` in headers read from the environment as
    Claude Code reads them."""
    if "--mcp-config" not in argv:
        return {}
    config = json.loads(argv[argv.index("--mcp-config") + 1]).get("mcpServers") or {}
    expand = lambda v: re.sub(r"\$\{([A-Z_][A-Z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), v)
    return {
        name: {"url": s["url"], "headers": {k: expand(v) for k, v in (s.get("headers") or {}).items()}}
        for name, s in config.items()
        if s.get("url")
    }


def codex_servers(config):
    """Codex's remote servers from a thread's `config.mcp_servers`, with headers as Codex builds them:
    `bearer_token_env_var` and `env_http_headers` from its own environment, `http_headers` as written."""
    out = {}
    for name, table in ((config or {}).get("mcp_servers") or {}).items():
        if not isinstance(table, dict) or not table.get("url"):
            continue
        headers = dict(table.get("http_headers") or {})
        for header, var in (table.get("env_http_headers") or {}).items():
            headers[header] = os.environ.get(var, "")
        if table.get("bearer_token_env_var"):
            headers["Authorization"] = "Bearer " + os.environ.get(table["bearer_token_env_var"], "")
        out[name] = {"url": table["url"], "headers": headers}
    return out
