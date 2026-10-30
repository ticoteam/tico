"""Settings > Billing: the company's balance, credits, usage and monthly statements, read from Tico HQ (Billing 5/5).

HQ keeps the ledger (ticoteam/tico-services, billing/company.py). This install reads its own company's part of it with
its gateway key (backend/gateway_connection.py), at `<Gateway URL>/billing`: HQ answers for the account that key's
install is bound to and nothing else, and refuses every write. Nothing here is stored: each page load asks HQ, on the
request's thread, never inside a transaction.

    read(key, base, month)          balance, status, credits, usage by day, the month's statement, the booking link
    day(key, base, day)             one day's usage by hour
    statement_csv(key, base, month) the month's statement as CSV, as HQ writes it
    cloud(key, base)                a Tico Cloud company's access (backend/cloud_access.py), at `<Gateway URL>/cloud`
    servers(key, base)              the company's hosted servers (Settings > Computers), at `<Gateway URL>/servers`
    start(key, base, server_id)     start one of them again; HQ refuses while the company is paused for credit

Only the owner and admins see the page; the routes refuse everyone else (backend/app.py). Money is whole
micro-dollars end to end: an answer with a float anywhere is refused rather than shown. The key goes only in the
request's header, only to a URL `clients/gateway.py safe_base` allows, and never follows a redirect.

A Tico Cloud company's answer carries `cloud` (HQ billing/company.py): its access (open, frozen or locked), plan, trial
end and billing page, checked field by field (backend/cloud_access.py `parse`) and None for every other company. The
route keeps it (cloud_access.from_billing), so opening the page after paying opens the app at once.

What the page shows when it can't show billing (`state`): `no_gateway` (this install has no gateway key: billing comes
with Tico AI Gateway), `no_account` (HQ knows the key but no company bill for it yet: the empty state, with HQ's
booking link), `refused` (HQ
refused the key) and `unreachable`.

Hosted servers (paused-start, d8f8efaa): a hosted company's Tico server and bot computers, each on or stopped, with a
Start for a stopped one. HQ decides each start (Billing 4/5 `may_start`): while the company is paused for credit it
answers `paused`, shown as PAUSED with a link to its account page (`credit_url`, https only) to ask for more credit. A
company staff shut down answers `deactivated`. An install HQ hosts nothing for answers `none`, and the section stays
hidden.
"""

import hashlib
import json
import re
import time
import urllib.error
import urllib.request

from clients import gateway as G

from . import cloud_access
from .store import Problem

PATH = "/billing"
WAIT = 10                    # seconds for HQ's answer
MAX_BYTES = 2_000_000        # a month of usage by day is small; anything past this is not HQ's answer
MONTH = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
DAY = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])")
STATES = ("active", "low", "paused", "grace")
OPENER = None                # tests replace it with a function (request, timeout) -> response: nothing leaves the test
REFUSED = ("unknown_key", "revoked_key")
SERVERS = "/servers"
CLOUD = "/cloud"             # a Tico Cloud company's access (backend/cloud_access.py)
SERVER_ID = re.compile(r"pv-[0-9a-f]{16}")
KINDS = ("tico", "bot_computer")
PAUSED = "Paused: out of credit. Add credit to start this computer."
PAUSE_TTL = 60               # seconds a pause bit is reused: the bot page asks on every refresh
CLOCK = time.monotonic       # tests replace it
_pause = {}                  # (sha256 of the gateway key, base) -> (expires, paused)


def _no_float(text):
    raise ValueError("money is whole micro-dollars")


def _micros(value):
    if type(value) is not int:
        raise ValueError("money is whole micro-dollars")
    return value


def _get(key, base, path, method="GET", root=PATH):
    """(status, body bytes, content type) of a GET (or an empty POST) to HQ, or ("unreachable", b"", "")."""
    request = urllib.request.Request(base + root + path, method=method, data=b"" if method == "POST" else None, headers={
        "Authorization": "Bearer " + key, "Accept": "application/json, text/csv"})
    opener = OPENER or G._send
    try:
        with opener(request, timeout=WAIT) as response:
            body = response.read(MAX_BYTES + 1)
            kind = response.headers.get("Content-Type", "") if response.headers else ""
            status = response.status if hasattr(response, "status") else 200
    except urllib.error.HTTPError as error:
        try:
            body = error.read(MAX_BYTES + 1)
        except Exception:
            body = b""
        return error.code, body, ""
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return "unreachable", b"", ""
    if len(body) > MAX_BYTES:
        return "unreachable", b"", ""
    return status, body, kind


def _payload(body):
    try:
        payload = json.loads(body or b"{}", parse_float=_no_float, parse_constant=_no_float)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _code(body):
    error = _payload(body).get("error")
    return error if isinstance(error, str) else ""


def _booking(value):
    """The booking link "Talk to us to add credit" opens (or a paused start's account page): https only, or None."""
    return value if isinstance(value, str) and value.startswith("https://") and len(value) <= 300 \
        and not any(ch.isspace() for ch in value) else None


def _ask(key, base, path):
    """HQ's answer to a read: (body bytes, content type), or raises Problem / returns a state for the page."""
    if not key:
        return None, {"state": "no_gateway"}
    try:
        base = G.safe_base(base)
    except ValueError:
        return None, {"state": "unreachable"}
    status, body, kind = _get(key, base, path)
    if status == 200:
        return (body, kind), None
    code = _code(body) if isinstance(status, int) else ""
    if status == 404 and code in ("no_account", "unknown_account"):
        return None, {"state": "no_account", "booking_url": _booking(_payload(body).get("booking_url"))}
    if status == 401 or code in REFUSED:
        return None, {"state": "refused"}
    if status == 422:
        raise Problem("invalid", "That month or day is not one Tico HQ can read", 422)
    return None, {"state": "unreachable"}


def _period(row, width):
    if not isinstance(row, dict) or not isinstance(row.get("period"), str) or len(row["period"]) != width:
        raise ValueError("a usage period")
    lines = row.get("lines")
    if not isinstance(lines, list):
        raise ValueError("usage lines")
    return {"period": row["period"], "total_micros": _micros(row.get("total_micros")),
            "lines": [{"product": str(line.get("product") or "")[:40], "sku": str(line.get("sku") or "")[:120],
                       "amount_micros": _micros(line.get("amount_micros"))} for line in lines if isinstance(line, dict)]}


def _credit(row):
    if not isinstance(row, dict):
        raise ValueError("a credit")
    return {"label": str(row.get("label") or "Credit")[:60], "source": str(row.get("source") or "")[:40],
            "amount_micros": _micros(row.get("amount_micros")), "left_micros": _micros(row.get("left_micros")),
            "expired_micros": _micros(row.get("expired_micros", 0)), "expires": row.get("expires") or None,
            "created": str(row.get("created") or ""), "expired": row.get("expired") is True,
            "used_up": row.get("used_up") is True, "current": row.get("current") is True}


def _statement(row):
    if not isinstance(row, dict):
        raise ValueError("a statement")
    by_source = row.get("credits_by_source") or {}
    by_product = row.get("debits_by_product") or {}
    if not isinstance(by_source, dict) or not isinstance(by_product, dict):
        raise ValueError("a statement")
    return {"month": str(row.get("month") or ""), "opening_micros": _micros(row.get("opening_micros")),
            "credits_micros": _micros(row.get("credits_micros")),
            "credits_by_source": {str(k)[:40]: _micros(v) for k, v in by_source.items()},
            "debits_micros": _micros(row.get("debits_micros")),
            "debits_by_product": {str(k)[:40]: _micros(v) for k, v in by_product.items()},
            "expired_micros": _micros(row.get("expired_micros")), "closing_micros": _micros(row.get("closing_micros"))}


def parse(body):
    """HQ's billing answer, field by field, so nothing odd reaches the page. Raises ValueError for anything else."""
    data = json.loads(body, parse_float=_no_float, parse_constant=_no_float)
    if not isinstance(data, dict) or data.get("state") not in STATES:
        raise ValueError("not a billing answer")
    months = [m for m in data.get("months") or [] if isinstance(m, str) and MONTH.fullmatch(m)][:24]
    grace = data.get("grace_until")
    return {"state": data["state"], "balance_micros": _micros(data.get("balance_micros")),
            "min_balance_micros": _micros(data.get("min_balance_micros")),
            "last_day_micros": _micros(data.get("last_day_micros", 0)),
            "record_only": data.get("record_only") is not False, "grace_until": grace if isinstance(grace, str) else None,
            "month": str(data.get("month") or ""), "months": months,
            "credits": [_credit(row) for row in data.get("credits") or []],
            "usage": [_period(row, 10) for row in data.get("usage") or []],
            "statement": _statement(data.get("statement")), "booking_url": _booking(data.get("booking_url")),
            "cloud": cloud_access.parse(data.get("cloud"))}


def cloud(key, base):
    """HQ's word on a Tico Cloud company's access, at `<Gateway URL>/cloud`: (200, payload), (404, {}) for a company
    that is not a cloud one (`not_cloud`, or an HQ without the route), or (another status or "unreachable", {})."""
    if not key:
        return "no_gateway", {}
    try:
        base = G.safe_base(base)
    except ValueError:
        return "unreachable", {}
    status, body, _ = _get(key, base, "", root=CLOUD)
    if status != 200:
        return status, {}
    try:
        return 200, json.loads(body, parse_float=_no_float, parse_constant=_no_float)
    except ValueError:
        return "unreachable", {}


def read(key, base, month=""):
    if month and not MONTH.fullmatch(month):
        raise Problem("invalid", "month is YYYY-MM", 422)
    answer, state = _ask(key, base, "?month=" + month if month else "")
    if state:
        return state
    try:
        return parse(answer[0])
    except (ValueError, TypeError, AttributeError):
        return {"state": "unreachable"}


def paused(key, base):
    """Whether HQ says the company is paused for credit (the bit the bot pause banner shows), kept PAUSE_TTL seconds per
    gateway key and base. Only this bit is cached: `read` is uncached, the billing page shows its balance live."""
    slot = (hashlib.sha256((key or "").encode()).hexdigest(), base)
    now = CLOCK()
    hit = _pause.get(slot)
    if hit and hit[0] > now:
        return hit[1]
    billing = read(key, base)
    value = billing.get("state") == "paused" and billing.get("record_only") is False
    _pause[slot] = (now + PAUSE_TTL, value)
    return value


def day(key, base, when):
    if not DAY.fullmatch(when or ""):
        raise Problem("invalid", "day is YYYY-MM-DD", 422)
    answer, state = _ask(key, base, "/usage?day=" + when)
    if state:
        return state
    try:
        data = json.loads(answer[0], parse_float=_no_float, parse_constant=_no_float)
        return {"state": "ok", "day": when, "hours": [_period(row, 13) for row in data.get("hours") or []]}
    except (ValueError, TypeError, AttributeError):
        return {"state": "unreachable"}


def statement_csv(key, base, month):
    """The month's statement as CSV text, exactly as HQ writes it (the same numbers as on screen)."""
    if not MONTH.fullmatch(month or ""):
        raise Problem("invalid", "month is YYYY-MM", 422)
    answer, state = _ask(key, base, "/statements/%s.csv" % month)
    if state:
        raise Problem(state["state"], "Tico HQ did not give the statement. Try again later.", 502)
    body, kind = answer
    if not kind.startswith("text/csv") or not body.startswith(b"month,line,amount_micros\n"):
        raise Problem("unreachable", "Tico HQ did not give the statement. Try again later.", 502)
    return body.decode("utf-8")


def _hq(key, base, path, method="GET"):
    """(status, payload) from HQ's servers routes, or (None, a state for the page)."""
    if not key:
        return None, {"state": "no_gateway"}
    try:
        base = G.safe_base(base)
    except ValueError:
        return None, {"state": "unreachable"}
    status, body, _ = _get(key, base, path, method=method, root=SERVERS)
    if not isinstance(status, int):
        return None, {"state": "unreachable"}
    code = _code(body)
    if status == 401 or code in REFUSED:
        return None, {"state": "refused"}
    if status == 404 and code in ("no_account", "not_configured"):
        return None, {"state": "none"}
    return status, _payload(body)


def servers(key, base):
    """The company's hosted servers: {state: "ok", servers: [{server_id, kind, power}]}, or a state."""
    status, data = _hq(key, base, "")
    if status is None:
        return data
    if status != 200 or not isinstance(data.get("servers"), list):
        return {"state": "unreachable"}
    if data.get("deactivated") is True:
        return {"state": "deactivated"}
    rows = [{"server_id": row["server_id"], "kind": row["kind"], "power": "on" if row.get("power") == "on" else "stopped"}
            for row in data["servers"] if isinstance(row, dict) and isinstance(row.get("server_id"), str)
            and SERVER_ID.fullmatch(row["server_id"]) and row.get("kind") in KINDS]
    return {"state": "ok", "servers": rows} if rows else {"state": "none"}


def start(key, base, server_id):
    """Start one stopped hosted server: {state: "started"}, {state: "paused", message, credit_url}, or another state
    (`deactivated`, `not_found`, `not_ready`, `refused`, `unreachable`)."""
    if not SERVER_ID.fullmatch(server_id or ""):
        raise Problem("invalid", "That is not one of this company's servers", 422)
    status, data = _hq(key, base, "/%s/start" % server_id, method="POST")
    if status is None:
        return data
    if status == 200:
        return {"state": "started"}
    code = data.get("error") if isinstance(data.get("error"), str) else ""
    if status == 409 and code == "paused":
        return {"state": "paused", "message": PAUSED, "credit_url": _booking(data.get("credit_url"))}
    if code in ("deactivated", "not_found", "not_ready"):
        return {"state": code}
    return {"state": "unreachable"}
