"""The Tico AI Gateway, as the app sees it: one base URL and one gateway key, for Decide and for the Tico models.

    <base>/decisions          Tico Decide 1.0 (clients/judge.py, the server's decisions)
    <base>/chat/completions   Tico Cheap / Mid / Power 1.0, OpenAI chat shape with streaming (bots, through pi)
    <base>/key                the key's own state this month: active or revoked, and how much of its cap is used

`check(key, base)` reads `<base>/key` and says, in words a person reads, whether the connection works. It never
returns or logs the key, and it keeps only the Gateway's code, never its text.
"""

import json
import urllib.error
import urllib.request

DEFAULT_BASE = "https://updates.tico.team/v1/gateway"
DECISIONS = "/decisions"
LABEL = "Tico AI Gateway"
TIMEOUT = 8
# The share of a key's monthly cap from which its holders are warned (Dev 183).
WARN_AT = 0.8
CAP_REACHED = "cap_reached"
CAP_MESSAGE = "Gateway cap reached"
# What each state says to the people who own the connection.
MESSAGES = {
    "connected": "Connected.",
    "warning": "This key has used {percent}% of its monthly Gateway cap. Bots stop at 100% until next month.",
    "cap_reached": "Gateway cap reached: this key has used its monthly cap. Bots on Tico models stop until next "
                   "month, or until Tico raises the cap.",
    "revoked": "Not working: Tico has revoked this gateway key. Ask Tico for a new key and save it here.",
    "invalid": "Not working: the Gateway does not know this key. Check you pasted the whole key.",
    "unreachable": "Not checked: the Gateway did not answer. Bots keep trying; check again later.",
}


def base_url(url=""):
    """The Gateway's base URL from a setting: Settings' URL, `TICO_GATEWAY_URL` or Tico HQ's. A decisions URL (how
    TICO_GATEWAY_URL names it for Decide) and a trailing slash are taken back to the base, so one setting serves
    both endpoints."""
    url = str(url or "").strip().rstrip("/")
    if url.endswith(DECISIONS):
        url = url[: -len(DECISIONS)].rstrip("/")
    return url or DEFAULT_BASE


def decisions_url(url=""):
    return base_url(url) + DECISIONS


def chat_base(url=""):
    """What an OpenAI-shaped client is given as its base URL; it adds `/chat/completions` itself."""
    return base_url(url)


def _code(body):
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("code") or error.get("type") or "")
    return str(error or "") if isinstance(error, str) else ""


def state_of(payload):
    """(state, used share or None) from a 200 answer of `<base>/key`."""
    payload = payload if isinstance(payload, dict) else {}
    if str(payload.get("status") or "active") == "revoked":
        return "revoked", None
    try:
        used = float(payload.get("used")) if payload.get("used") is not None else None
    except (TypeError, ValueError):
        used = None
    if used is None:
        try:
            cap, spent = float(payload.get("cap_usd") or 0), float(payload.get("spent_usd") or 0)
            used = spent / cap if cap > 0 else None
        except (TypeError, ValueError):
            used = None
    if payload.get("cap_reached") is True or (used is not None and used >= 1):
        return "cap_reached", used
    if payload.get("warning") is True or (used is not None and used >= WARN_AT):
        return "warning", used
    return "connected", used


def describe(state, used=None):
    """What the connection says for `state`, with the used share as a whole percent."""
    percent = int(min(max(used or WARN_AT, 0), 1) * 100)
    return MESSAGES.get(state, MESSAGES["unreachable"]).format(percent=percent)


def check(key, url="", timeout=TIMEOUT, opener=None):
    """{state, working, message, used} for a gateway key. `working` is False only for a key the Gateway refuses
    (revoked or unknown); an unreachable Gateway is not called broken. `opener(request, timeout)` is the tests' seam."""
    if not key:
        return {"state": "none", "working": False, "message": "No gateway key saved.", "used": None}
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(base_url(url) + "/key", method="GET", headers={
        "Authorization": "Bearer " + key, "Accept": "application/json"})
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.load(response)
        state, used = state_of(payload)
    except urllib.error.HTTPError as exc:
        try:
            code = _code(exc.read())
        except Exception:
            code = ""
        state, used = ("revoked" if code == "key_revoked" or exc.code == 403 else "invalid"
                       if exc.code == 401 else "unreachable"), None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        state, used = "unreachable", None
    return {"state": state, "working": state not in ("revoked", "invalid"), "message": describe(state, used),
            "used": round(used, 4) if used is not None else None}


def cap_reached(text):
    """Whether a model error a bot's turn ended with is the Gateway's cap (`cap_reached`), not a rate limit."""
    return CAP_REACHED in str(text or "")


def key_refused(text):
    """Whether a model error a bot's turn ended with is the Gateway refusing the key (revoked or unknown)."""
    text = str(text or "")
    return "key_revoked" in text or ("invalid_key" in text and LABEL in text)
