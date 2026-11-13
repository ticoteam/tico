"""Synthetic customers shared by the computed-retention tests (backend/tests/test_growth_computed.py and
backend/tests/test_growth_customer_sources.py): a search-ads September 2026 cohort of 40, read on 2026-11-13."""

import csv
import io
from datetime import date, timedelta

TODAY = date(2026, 11, 13)
HEADER = ["customer_key", "signed_up", "became_customer", "channel", "segment", "plan", "churned", "revenue_month"]


def as_csv(rows, header=HEADER):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    for row in rows:
        writer.writerow([row.get(h, "") for h in header])
    return out.getvalue()


def customer(n, became, channel="search-ads", churned="", segment="", plan="", revenue=""):
    return {
        "customer_key": f"cus_{n}",
        "became_customer": became,
        "channel": channel,
        "churned": churned,
        "segment": segment,
        "plan": plan,
        "revenue_month": revenue,
    }


def september(kept=30, total=40, **more):
    """search-ads' September 2026: `total` customers, the first `kept` still paying on day 30, the rest churned on day 10."""
    out = []
    for n in range(total):
        became = date(2026, 9, 1) + timedelta(days=n % 30)
        churned = "" if n < kept else (became + timedelta(days=10)).isoformat()
        out.append(customer(1000 + n, became.isoformat(), churned=churned, **more))
    return out
