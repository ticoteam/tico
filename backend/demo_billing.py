"""The demo's Settings > Billing: a sample of Tico HQ's answer, built from the demo's own usage.

The demo never reaches Tico HQ, so its balance, credits, usage and statement come from the runs it shows: the same
rows the usage page sums (backend/usage.py `query`, the same list prices), in the shape HQ sends. The answer goes
through company_billing.parse, the parser HQ's answers go through, and the statement CSV is written from the same
lines, so the page and the download cannot disagree.

Model runs billed by API (not on a subscription, as on the usage page) are Tico models, the product HQ bills them
as. The company starts with one launch credit, granted in the month of its first run and spent by the runs after it.
Whole micro-dollars throughout: each day's total is the usage page's day figure to the micro, and its hours and
models are shared out of that total so they add up exactly.
"""

import json
from datetime import date, datetime, timezone

from .store import Problem
from . import company_billing, usage

GRANT = 500_000_000          # the launch credit, in micro-dollars ($500)
MIN_BALANCE = 50_000_000     # below this the page says the balance is low ($50)
PRODUCT = "gateway"          # Tico models, the product HQ bills model runs as
FIRST = date(2000, 1, 1)     # every run the demo has, whatever its date
LAST = date(2999, 12, 31)
HOUR_MODEL = "substr(t.started_at, 1, 13) || '|' || coalesce(t.model, '')"


def _split(total, shares):
    """`total` whole micro-dollars in parts, one per share (dollars), that add up to `total` exactly."""
    weight = sum(shares)
    if not shares or weight <= 0:
        return [0] * len(shares)
    exact = [total * share / weight for share in shares]
    parts = [int(part) for part in exact]
    for index in sorted(range(len(shares)), key=lambda i: exact[i] - parts[i], reverse=True)[:total - sum(parts)]:
        parts[index] += 1
    return parts


def _lines(total, models):
    """The usage lines of `total`: one per model, shared out by its dollars (`models` is {model: dollars})."""
    names = sorted(models)
    parts = _split(total, [models[name] for name in names])
    return [{"product": PRODUCT, "sku": name, "amount_micros": part} for name, part in zip(names, parts, strict=True) if part]


def _ledger(c, slugs):
    """The API spend of the runs on these bots: whole micro-dollars by day and by hour, and each day's models."""
    by_hour = {}
    for row in usage.query(c, HOUR_MODEL, "", slugs, FIRST, LAST):
        if row["api_usd"]:
            hour, _, model = row["k"].partition("|")
            by_hour.setdefault(hour, {})[model] = float(row["api_usd"])
    models = {}
    for hour, by_model in by_hour.items():
        day_models = models.setdefault(hour[:10], {})
        for model, dollars in by_model.items():
            day_models[model] = day_models.get(model, 0.0) + dollars
    days, hours = {}, {}
    for day, day_models in sorted(models.items()):
        total = round(sum(day_models.values()) * 1_000_000)
        if not total:
            continue
        days[day] = total
        hour_list = sorted(hour for hour in by_hour if hour[:10] == day)
        parts = _split(total, [sum(by_hour[hour].values()) for hour in hour_list])
        hours[day] = [{"period": hour, "total_micros": part, "lines": _lines(part, by_hour[hour])}
                      for hour, part in zip(hour_list, parts, strict=True) if part]
    return {"days": days, "models": models, "hours": hours}


def _statement(days, grant, month, debits):
    """The month's statement: what the launch credit left on its first day, the grant if this is its month, the
    month's debits, and the closing balance (the balance at the end of the newest month)."""
    before = sum(micros for day, micros in days.items() if day < month + "-01")
    credits = GRANT if grant == month else 0
    opening = (GRANT if grant < month else 0) - before
    return {"month": month, "opening_micros": opening, "credits_micros": credits,
            "credits_by_source": {"grant": credits} if credits else {}, "debits_micros": debits,
            "debits_by_product": {PRODUCT: debits}, "expired_micros": 0,
            "closing_micros": opening + credits - debits}


def read(c, slugs, month=""):
    """Settings > Billing for the company, the same shape as company_billing.read."""
    if month and not company_billing.MONTH.fullmatch(month):
        raise Problem("invalid", "month is YYYY-MM", 422)
    ledger = _ledger(c, slugs)
    days, models = ledger["days"], ledger["models"]
    newest = max(days)[:7] if days else datetime.now(timezone.utc).strftime("%Y-%m")
    grant = min(days)[:7] if days else newest
    month = month or newest
    month_days = sorted(day for day in days if day.startswith(month))
    debits = sum(days[day] for day in month_days)
    balance = GRANT - sum(days.values())
    year, number = grant.split("-")
    body = {
        "state": "low" if balance < MIN_BALANCE else "active", "balance_micros": balance,
        "min_balance_micros": MIN_BALANCE, "last_day_micros": days[max(days)] if days else 0,
        "record_only": False, "grace_until": None, "month": month,
        "months": sorted({day[:7] for day in days}) or [newest],
        "credits": [{"label": "Launch credit", "source": "grant", "amount_micros": GRANT,
                     "left_micros": max(balance, 0), "expired_micros": 0, "expires": f"{int(year) + 1:04d}-{number}-01",
                     "created": f"{grant}-01", "expired": False, "used_up": balance <= 0, "current": balance > 0}],
        "usage": [{"period": day, "total_micros": days[day], "lines": _lines(days[day], models[day])}
                  for day in month_days],
        "statement": _statement(days, grant, month, debits), "booking_url": None, "cloud": None,
    }
    return company_billing.parse(json.dumps(body))


def day(c, slugs, when):
    """One day's usage by hour, the same shape as company_billing.day."""
    if not company_billing.DAY.fullmatch(when or ""):
        raise Problem("invalid", "day is YYYY-MM-DD", 422)
    return {"state": "ok", "day": when, "hours": _ledger(c, slugs)["hours"].get(when, [])}


def statement_csv(c, slugs, month):
    """The month's statement as CSV, with the same lines and signs as the statement on the page."""
    if not company_billing.MONTH.fullmatch(month or ""):
        raise Problem("invalid", "month is YYYY-MM", 422)
    statement = read(c, slugs, month)["statement"]
    rows = [("opening", statement["opening_micros"])]
    rows += [("credits:" + source, micros) for source, micros in statement["credits_by_source"].items()]
    rows += [("debits:" + product, -micros) for product, micros in statement["debits_by_product"].items()]
    if statement["expired_micros"]:
        rows.append(("expired", -statement["expired_micros"]))
    rows.append(("closing", statement["closing_micros"]))
    return "month,line,amount_micros\n" + "".join(f"{statement['month']},{line},{amount}\n" for line, amount in rows)
