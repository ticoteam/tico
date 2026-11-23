"""`tico tools call <tool> <METHOD> <path>`: one call to a proxied tool from a bot's turn (backend/tool_proxy.py).
A tool whose key reaches the computer as it is (Jira) is called directly with that key: the error says so and names the
variables (`not_proxied`).

Runs beside the bot, with no hub API: a run granted a proxied tool has the proxy's address in `TICO_TOOL_<ID>_URL` and
a proxy token where the key would be (the credential's own variable). The token's claims name its tool, so the
variable holding it is found by reading them; they are not secret, and only the server can check the signature. The
token goes as Bearer, which the proxy accepts for every auth scheme and replaces with the real key.
"""

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

TOKEN_PREFIX = "tpx1."
READS = frozenset(("GET", "HEAD", "OPTIONS"))
MAX_ANSWER = 1_000_000
TIMEOUT = 130
# Cloudflare in front of Tico refuses Python's default User-Agent (error 1010, "browser signature banned") before the
# proxy sees the call, as clients/hermes_agent.py notes. Seen live on Tico Team: tico_tool_call to Sentry got that 403.
USER_AGENT = "tico-tool-call/1"


class CallError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A vendor's redirect comes back as the answer: following it would send the token to another address."""

    def redirect_request(self, *args, **kwargs):
        return None


def url_env(tool):
    """The same spelling as the server's (backend/tool_proxy.py `url_env`)."""
    return "TICO_TOOL_" + re.sub(r"[^A-Z0-9]", "_", str(tool).upper()) + "_URL"


def _tool_of(token):
    try:
        body = token[len(TOKEN_PREFIX) :].split(".", 1)[0]
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))).get("t")
    except (ValueError, AttributeError):
        return None


def own_variables(tool, environ):
    """The run's variables named after `tool`, when its key reached this computer as it is (no proxy): its id in capitals
    with every other character as `_`, else its first word (close-crm: CLOSE_CRM_*, then CLOSE_*). Never Tico's own."""
    spelled = re.sub(r"[^A-Z0-9]", "_", str(tool).upper()).strip("_")
    for stem in dict.fromkeys((spelled, spelled.split("_", 1)[0])):
        if not stem:
            continue
        found = sorted(k for k in environ if k.startswith(stem + "_") and not k.startswith("TICO_"))
        if found:
            # The key first, so the sentence leads with what authenticates.
            return sorted(found, key=lambda k: not re.search(r"TOKEN|KEY|SECRET|PASSWORD", k))
    return []


def names_command(stem):
    """The shell line that lists the variables named after a tool, names only. It reads the names themselves: text
    tools over `env` would print a line of a multi-line value that starts with the prefix. The first word's prefix covers
    the full id's too (own_variables' fallback), and Tico's own variables are left out as there."""
    first = stem.split("_", 1)[0] or stem
    return (
        "python3 -c 'import os; print(*sorted(k for k in os.environ if k.startswith(\""
        + first
        + "_\") and not k.startswith(\"TICO_\")))'"
    )


def not_proxied(tool, environ, shell=True):
    """Why `tool` cannot be called here, and what to do instead: a refusal read as "no access" stalls a turn whose key is
    in its own environment. Variable names only, never their values. `shell` is False for the MCP server: a harness
    starts it with Tico's own variables only (runner-rs/src/hosts/mod.rs HUB_MCP_ENV), so finding none there says
    nothing about the agent's shell, and the answer gives both cases."""
    proxied = sorted(k for k in environ if k.startswith("TICO_TOOL_") and k.endswith("_URL"))
    tail = f" Proxied tools in this run: {', '.join(proxied)}." if proxied else ""
    vendor = tool[:1].upper() + tool[1:]
    names = own_variables(tool, environ)
    if names:
        also = f" (this run also has {', '.join('$' + n for n in names[1:])})" if names[1:] else ""
        return (
            f"{tool}'s access is on this computer, not behind Tico's proxy, so tico tool call cannot use it. Call the "
            f"{vendor} API yourself with ${names[0]}{also}.{tail}"
        )
    stem = re.sub(r"[^A-Z0-9]", "_", str(tool).upper()).strip("_")
    if not shell:
        return (
            f"{tool} is not proxied for this MCP server, which cannot see your shell's variables. Run "
            f"`{names_command(stem)}` (names only): if it lists any, {tool}'s access is on your computer, so call the "
            f"{vendor} API yourself with them; if none, check that this bot is granted {tool} (tico tool show {tool})."
        )
    return (
        f"{tool} is not proxied in this run and no {stem}_ variable is set: check that this bot is granted {tool} "
        f"(tico tool show {tool}).{tail}"
    )


def find(tool, environ=None, shell=True):
    """(proxy address, token) for `tool` in this turn's environment."""
    environ = os.environ if environ is None else environ
    address = environ.get(url_env(tool), "")
    if not address:
        raise CallError(not_proxied(tool, environ, shell))
    wanted = address.rstrip("/").rsplit("/", 1)[-1]
    for value in environ.values():
        if value.startswith(TOKEN_PREFIX) and _tool_of(value) == urllib.parse.unquote(wanted):
            return address.rstrip("/"), value
    raise CallError(f"No token for {tool} in this run's environment")


def call(tool, method, path, data=None, query=(), environ=None, idempotency_key=None, shell=True):
    """Make the call and return {status, content_type, body}: JSON parsed when the answer is JSON, else text. A write
    carries an Idempotency-Key, one per call (`idempotency_key`, else a new one): the proxy checks a write against the
    org rules only with one."""
    address, token = find(tool, environ, shell)
    url = address + "/" + path.lstrip("/")
    pairs = []
    for item in query or ():
        key, sep, value = item.partition("=")
        if not sep:
            raise CallError(f"--query takes key=value, not {item!r}")
        pairs.append((key, value))
    if pairs:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(pairs)
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json, */*", "User-Agent": USER_AGENT}
    if method.upper() not in READS:
        headers["Idempotency-Key"] = idempotency_key or "tico-tool-call-" + uuid.uuid4().hex
    body = None
    if data is not None:
        body = (data if isinstance(data, str) else json.dumps(data)).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=method.upper(), headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=TIMEOUT) as answer:
            status, kind, raw = answer.status, answer.headers.get("content-type", ""), answer.read(MAX_ANSWER + 1)
    except urllib.error.HTTPError as answer:
        with answer:
            status, kind, raw = answer.code, answer.headers.get("content-type", ""), answer.read(MAX_ANSWER + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise CallError(f"The Tico tool proxy did not answer: {exc}") from None
    cut = len(raw) > MAX_ANSWER
    text = raw[:MAX_ANSWER].decode("utf-8", "replace")
    out = {"status": status, "content_type": kind}
    if "json" in kind and not cut:
        try:
            out["body"] = json.loads(text) if text else None
            return out
        except ValueError:
            pass
    out["body"] = text
    if cut:
        out["truncated"] = True
    return out
