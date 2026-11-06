"""`hub tools call <tool> <METHOD> <path>`: one call to a proxied tool from a bot's turn (backend/tool_proxy.py).

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

TOKEN_PREFIX = "tpx1."
MAX_ANSWER = 1_000_000
TIMEOUT = 130


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
        body = token[len(TOKEN_PREFIX):].split(".", 1)[0]
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))).get("t")
    except (ValueError, AttributeError):
        return None


def find(tool, environ=None):
    """(proxy address, token) for `tool` in this turn's environment."""
    environ = os.environ if environ is None else environ
    address = environ.get(url_env(tool), "")
    if not address:
        have = sorted(k for k in environ if k.startswith("TICO_TOOL_") and k.endswith("_URL"))
        raise CallError(f"{tool} is not a proxied tool in this run" +
                        (f" (this run has {', '.join(have)})" if have else " (this run has none)"))
    wanted = address.rstrip("/").rsplit("/", 1)[-1]
    for value in environ.values():
        if value.startswith(TOKEN_PREFIX) and _tool_of(value) == urllib.parse.unquote(wanted):
            return address.rstrip("/"), value
    raise CallError(f"No token for {tool} in this run's environment")


def call(tool, method, path, data=None, query=(), environ=None):
    """Make the call and return {status, content_type, body}: JSON parsed when the answer is JSON, else text."""
    address, token = find(tool, environ)
    url = address + "/" + path.lstrip("/")
    pairs = []
    for item in query or ():
        key, sep, value = item.partition("=")
        if not sep:
            raise CallError(f"--query takes key=value, not {item!r}")
        pairs.append((key, value))
    if pairs:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(pairs)
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json, */*"}
    body = None
    if data is not None:
        body = (data if isinstance(data, str) else json.dumps(data)).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=method.upper(), headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=TIMEOUT) as answer:
            status, kind, raw = answer.status, answer.headers.get("content-type", ""), answer.read(MAX_ANSWER + 1)
    except urllib.error.HTTPError as answer:
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
