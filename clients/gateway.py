"""The Tico AI Gateway, as the app sees it: one base URL and one gateway key, for Decide and for the Tico models.

    <base>/decisions          Tico Decide 1.0 (clients/judge.py, the server's decisions)
    <base>/chat/completions   Tico Cheap / Mid / Power 1.0, OpenAI chat shape with streaming (bots, through pi)
    <base>/key                the key's own state this month: active or revoked, and how much of its cap is used

`check(key, base)` reads `<base>/key` and says, in words a person reads, whether the connection works. It never
returns or logs the key, and it keeps only the Gateway's code, never its text.

The Gateway's error codes, the same on every endpoint, in `error.code` (nested, chat) or `error` (a flat string,
decisions): 401 `revoked_key` and `unknown_key` (both read "key not recognised"), 429 `cap_reached` (no retry this UTC
month, whatever Retry-After says) and 429 `rate_limited` (retry after Retry-After). `<base>/key` answers 6 times a
minute per key, so the server asks it gently (backend/gateway_connection.py `check_soon`).
"""

import datetime
import json
import re
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://updates.tico.team/v1/gateway"
DECISIONS = "/decisions"
LABEL = "Tico AI Gateway"
TIMEOUT = 8
# The share of a key's monthly cap from which its holders are warned (Dev 183).
WARN_AT = 0.8
CAP_REACHED, RATE_LIMITED = "cap_reached", "rate_limited"
REVOKED_KEY, UNKNOWN_KEY = "revoked_key", "unknown_key"
# States that say nothing about the key: the Gateway was down or asked to slow down. They never replace a real answer.
TRANSIENT = ("unreachable", "busy")
# States where the connection is not working: the Gateway refuses the key, or the key may not go to its URL.
REFUSED = ("revoked", "unknown", "bad_url")
CAP_MESSAGE = "Gateway cap reached"
# What each state says to the people who own the connection.
MESSAGES = {
    "connected": "Connected.",
    "warning": "This key has used {percent}% of its monthly Gateway cap. Bots stop at 100% until next month.",
    "cap_reached": "Gateway cap reached: this key has used its monthly cap. Bots on Tico models stop until next "
                   "month, or until Tico raises the cap.",
    # One message for a revoked and an unknown key: the Gateway's answer doesn't say which keys ever existed.
    "revoked": "Not working: key not recognised. Check you pasted the whole key, or ask Tico for a new one and save it "
               "here.",
    "unknown": "Not working: key not recognised. Check you pasted the whole key, or ask Tico for a new one and save it "
               "here.",
    "unreachable": "Not checked: the Gateway did not answer. Bots keep trying; check again later.",
    "busy": "Not checked: the Gateway asked to slow down. Checking again in a minute.",
    "bad_url": "Not working: the Gateway URL must start with https:// (plain http:// only for localhost). Fix it where "
               "it is set; the key is not sent until then.",
    "not_ready": "Not ready: the Gateway at this URL doesn't serve Tico models yet. Check the Gateway URL, or wait "
                 "until Tico turns them on.",
}
# What a bot says when the Gateway answered its model call with 404: no chat endpoint there (yet), or a wrong URL.
NOT_SERVED = "The Tico AI Gateway doesn't serve Tico models at its URL yet"
NOT_FOUND = "not_found"
# How long the Gateway's `rate_limited` asks a caller to wait (its Retry-After): more refused keys extend the block.
RETRY_AFTER = 3600
UNAVAILABLE = "upstream_unavailable"
# A Tico model on a runner that runs bots as itself (runner-rs/src/gateway_relay.rs NOT_ISOLATED): it never asks.
NOT_ISOLATED = "not_isolated"
# Refusals of the request as it was sent, and a runner that can't run Tico models: asking again gets the same answer,
# so a bot's run is not retried.
FINAL = ("unknown_model", "unsupported_field", "bad_request", "upstream_refused", "no_route", NOT_FOUND, NOT_ISOLATED)
# Every code a bot's model call can end with, as the runner names it at the end of its error
# (runner-rs/src/hosts/pi.rs `plain_error`).
CODES = (CAP_REACHED, REVOKED_KEY, UNKNOWN_KEY, RATE_LIMITED, "unknown_model", "unsupported_field", "bad_request",
         "upstream_refused", "no_route", UNAVAILABLE, NOT_ISOLATED)
TICO_MODELS = "Tico Cheap 1.1, Tico Mid 1.0, Tico Power 1.0"
# The only hosts the key goes to over plain HTTP: this computer, named exactly (a fake Gateway, a local proxy).
LOOPBACK = ("localhost", "127.0.0.1", "::1")


def base_url(url=""):
    """The Gateway's base URL from a setting: Settings' URL, `TICO_GATEWAY_URL` or Tico HQ's. A decisions URL (how
    TICO_GATEWAY_URL names it for Decide) and a trailing slash are taken back to the base, so one setting serves
    both endpoints."""
    url = str(url or "").strip().rstrip("/")
    if url.endswith(DECISIONS):
        url = url[: -len(DECISIONS)].rstrip("/")
    return url or DEFAULT_BASE


def safe_base(url=""):
    """`base_url(url)` if the key may be sent there, else ValueError: HTTPS, or plain HTTP to an exact loopback host,
    with no user, password, query or fragment. The host is parsed, not prefix-matched: `http://localhost.acme.example`
    is a remote host over plain HTTP."""
    base = base_url(url)
    if any(ch in base for ch in " \t\r\n\0\\?#@"):
        raise ValueError("The Gateway URL has spaces, a query or a password")
    try:
        parts = urllib.parse.urlsplit(base)
        host = parts.hostname or ""
        parts.port
    except ValueError:
        raise ValueError("The Gateway URL is not a URL") from None
    if parts.scheme == "https" and host or parts.scheme == "http" and host in LOOPBACK:
        return base
    raise ValueError("The Gateway URL starts with https://")


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


class _Unredirected(urllib.request.HTTPRedirectHandler):
    """Never follows a redirect: urllib's would resend the key's Authorization header to wherever it points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _send(request, timeout):
    """`check`'s transport, built for each call so it reads the environment's proxy as it is now. A redirect is
    answered as the HTTP error it is. Plain HTTP (only ever to this computer, `safe_base`) goes straight there: a proxy
    would receive the key in clear. HTTPS may use the environment's proxy, which only tunnels it."""
    handlers = [_Unredirected]
    if urllib.parse.urlsplit(request.full_url).scheme == "http":
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers).open(request, timeout=timeout)


def state_of_error(status, code):
    """The connection's state from an error answer of `<base>/key`: its HTTP status and the Gateway's code. A
    redirect is a URL that is not the Gateway's."""
    if code == REVOKED_KEY:
        return "revoked"
    if code == UNKNOWN_KEY or status in (401, 403):
        return "unknown"
    if code == CAP_REACHED:
        return "cap_reached"
    if code == RATE_LIMITED or status == 429:
        return "busy"
    return "not_ready" if status == 404 or 300 <= status < 400 else "unreachable"


def describe(state, used=None):
    """What the connection says for `state`, with the used share as a whole percent."""
    percent = int(min(max(used or WARN_AT, 0), 1) * 100)
    return MESSAGES.get(state, MESSAGES["unreachable"]).format(percent=percent)


def check(key, url="", timeout=TIMEOUT, opener=None):
    """{state, working, message, used} for a gateway key. `working` is False only for a key the Gateway refuses or a
    URL the key may not go to (`safe_base`, checked before anything is sent); an unreachable Gateway is not called
    broken. `opener(request, timeout)` is the tests' seam."""
    if not key:
        return {"state": "none", "working": False, "message": "No gateway key saved.", "used": None}
    try:
        base = safe_base(url)
    except ValueError:
        return {"state": "bad_url", "working": False, "message": describe("bad_url"), "used": None}
    opener = opener or _send
    request = urllib.request.Request(base + "/key", method="GET", headers={
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
        state, used = state_of_error(exc.code, code), None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        state, used = "unreachable", None
    return {"state": state, "working": state not in REFUSED, "message": describe(state, used),
            "used": round(used, 4) if used is not None else None}


def month_after(moment):
    """The start of the UTC month after `moment` (an aware datetime): when a key's spent cap is lifted."""
    moment = moment.astimezone(datetime.timezone.utc)
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return datetime.datetime(year, month, 1, tzinfo=datetime.timezone.utc)


def cap_reached(text):
    """Whether a model error a bot's turn ended with is the Gateway's cap (`cap_reached`), not a rate limit."""
    return CAP_REACHED in str(text or "")


def run_code(text):
    """The Gateway's code a bot's model error ends with, `not_found` for a 404 from it, or ''. Only read for runs on a
    Tico model."""
    text = str(text or "")
    named = [code for code in CODES if f"({code})" in text or f": {code})" in text]
    if named:
        return named[0]
    bare = [code for code in CODES if code in text]
    return bare[0] if bare else NOT_FOUND if not_served(text) else ""


def run_status(code, text=""):
    """What a bot's status says about a run the Gateway refused with `code`, before what happens to the run."""
    if code == "unknown_model":
        return f"The Tico AI Gateway doesn't offer this model. Tico models: {TICO_MODELS}"
    if code == "no_route":
        return "No Tico route for this request"
    if code == NOT_FOUND:
        return NOT_SERVED + "; check the Gateway URL in Settings > AI providers"
    if code == UNAVAILABLE:
        return "Tico AI Gateway is unavailable, try again"
    if code == NOT_ISOLATED:
        return ("Tico models need a runner that runs bots as their own user (Tico's Docker runner). This computer "
                "can't, so pick another model or move the bot")
    # unsupported_field, bad_request, upstream_refused: the Gateway's own (fixed) words on what it refused.
    said = re.search(r"refused the request as sent: (.+?) \(" + re.escape(code) + r"\)", str(text or ""))
    return "The Tico AI Gateway refused the request as sent" + (f": {said[1][:200]}" if said else "")


def rate_limited(text):
    """Whether a model error is the Gateway's `rate_limited` (too many refused keys from this address)."""
    return f"({RATE_LIMITED})" in str(text or "") and LABEL in str(text or "")


def not_served(text):
    """Whether a model error a bot's turn ended with is a 404 from the Gateway: no chat endpoint there yet, or a wrong
    URL. Only read for runs on a Tico model."""
    text = str(text or "").lower()
    return "model_not_found" in text or re.search(r"\b404\b", text) is not None or "not found" in text


def key_refused(text):
    """Whether a model error a bot's turn ended with is the Gateway refusing the key (revoked or unknown)."""
    text = str(text or "")
    return REVOKED_KEY in text or UNKNOWN_KEY in text
