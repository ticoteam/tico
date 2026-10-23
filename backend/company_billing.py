"""Settings > Billing: the company's balance, credits, usage and monthly statements, read from Tico HQ (Billing 5/5).

HQ keeps the ledger (ticoteam/tico-services, billing/company.py). This install reads its own company's part of it with
its gateway key (backend/gateway_connection.py), at `<Gateway URL>/billing`: HQ answers for the account that key's
install is bound to and nothing else, and refuses every write. Nothing here is stored: each page load asks HQ, on the
request's thread, never inside a transaction.

    read(key, base, month)          balance, status, credits, usage by day, the month's statement, the booking link
    day(key, base, day)             one day's usage by hour
    statement_csv(key, base, month) the month's statement as CSV, as HQ writes it

Only the owner and admins see the page; the routes refuse everyone else (backend/app.py). Money is whole
micro-dollars end to end: an answer with a float anywhere is refused rather than shown. The key goes only in the
request's header, only to a URL `clients/gateway.py safe_base` allows, and never follows a redirect.

What the page shows when it can't show billing (`state`): `no_gateway` (this install has no gateway key: billing comes
with Tico AI Gateway), `no_account` (HQ knows the key but no company bill for it yet: the empty state, with HQ's
booking link), `refused` (HQ
refused the key) and `unreachable`.
"""

import json
import re
import urllib.error
import urllib.request

from clients import gateway as G

from .store import Problem

PATH = "/billing"
WAIT = 10                    # seconds for HQ's answer
MAX_BYTES = 2_000_000        # a month of usage by day is small; anything past this is not HQ's answer
MONTH = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
DAY = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])")
STATES = ("active", "low", "paused", "grace")
OPENER = None                # tests replace it with a function (request, timeout) -> response: nothing leaves the test
REFUSED = ("unknown_key", "revoked_key")


def _no_float(text):
    raise ValueError("money is whole micro-dollars")


def _micros(value):
    if type(value) is not int:
        raise ValueError("money is whole micro-dollars")
    return value


def _get(key, base, path):
    """(status, body bytes, content type) of a GET to HQ, or ("unreachable", b"", "")."""
    request = urllib.request.Request(base + PATH + path, method="GET", headers={
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
    """The booking link "Talk to us to add credit" opens: https only, or None."""
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
            "statement": _statement(data.get("statement")), "booking_url": _booking(data.get("booking_url"))}


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
