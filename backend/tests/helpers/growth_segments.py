"""The segments team and customers shared by backend/tests/test_growth_segments.py and backend/tests/test_growth_core.py:
Consumer (revenue 20 a month at 50% margin) and Rental (churn waits 30 days of inactivity; cohorts of 10 are enough).
Search ads' spend is for Consumer, partners' for Rental, brand's is company-wide."""

from datetime import timedelta

from backend.tests.helpers.api import post, put
from backend.tests.helpers.growth_computed import HEADER, as_csv, customer

FULL = HEADER + ["net_d90", "net_d180", "net_d365", "attribution"]


def people(start, n, first, attribution="", **more):
    """`n` customers from key `start`, one a day from `first` (a date)."""
    return [
        {**customer(start + i, (first + timedelta(days=i % 28)).isoformat(), **more), "attribution": attribution}
        for i in range(n)
    ]


def team(api):
    post(api, "growth/segments", {"slug": "consumer", "name": "Consumer", "revenue_month": 20, "margin_pct": 50})
    post(
        api,
        "growth/segments",
        {"slug": "rental", "name": "Rental", "churn_days": 30, "min_cohort": 10, "customer_rule": "a booked stay"},
    )
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads", "segment": "consumer"})
    post(api, "growth/channels", {"slug": "partners", "name": "Partners", "segment": "Rental"})
    post(api, "growth/channels", {"slug": "brand", "name": "Brand"})
    for ch, spend in (("search-ads", 1200), ("partners", 400), ("brand", 700)):
        put(api, "growth/cohorts", {"channel": ch, "month": "2026-09", "spend": spend})


def load(api, rows, expected=200):
    return post(api, "growth/customers", {"csv": as_csv(rows, FULL)}, expected=expected)
