"""A chrome bot's own browser on its computer (runner-rs/src/browser): fill a login into the page, read its status.

The runner gives each run of a bot with the `chrome` tool `TICO_BROWSER_URL`, a loopback address with a secret
for that run. These calls never go through a proxy and never carry a login's value back: `/fill` answers only
`{"ok": true}` or `{"ok": false, "error": ...}`.
"""

import json
import os
import urllib.error
import urllib.request

URL_ENV = "TICO_BROWSER_URL"
FIELDS = ("username", "password", "totp")


def _base():
    url = (os.environ.get(URL_ENV) or "").rstrip("/")
    if not (url.startswith("http://127.0.0.1:") or url.startswith("http://localhost:")):
        return None
    return url


def _call(method, path, body=None, timeout=90):
    base = _base()
    if not base:
        return {"ok": False, "error": "no_browser",
                "detail": f"{URL_ENV} is not set: this bot has no chrome tool here, or its computer is not Linux"}
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as reply:
            return json.loads(reply.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            answer = json.loads(e.read() or b"{}")
        except ValueError:
            answer = {}
        return {"ok": False, "error": answer.get("error") or f"http_{e.code}", **({"problem": answer["problem"]} if answer.get("problem") else {})}
    except (urllib.error.URLError, OSError, ValueError):
        return {"ok": False, "error": "browser_unreachable"}


def fill(credential, field, selector=None, target_id=None):
    """Type a granted login's username, password or current authenticator code into the focused field."""
    if field not in FIELDS:
        return {"ok": False, "error": "bad_request", "detail": f"field is one of {', '.join(FIELDS)}"}
    body = {"credential": credential, "field": field}
    if selector:
        body["selector"] = selector
    if target_id:
        body["target_id"] = target_id
    return _call("POST", "/fill", body)


def status():
    """{running, version, display, profile, downloads} of this bot's Chrome."""
    return _call("GET", "/status", timeout=30)
