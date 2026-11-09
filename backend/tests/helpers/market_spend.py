"""Helpers that other tests share, moved out of test_market_spend.py so they import no test file."""

from backend.tests.helpers.api import post, put


B = 1_000_000_000


def evidence(api, quote="Analysts count 40,000 small landlords.", token="ana-test"):
    return post(api, "market/evidence", {"source_kind": "news", "quote": quote, "our_read": "A count."}, token=token)[
        "evidence"
    ]["id"]


def defined(api, **more):
    """A market with a root, and two top-level segments with a sub-segment under the first."""
    put(
        api,
        "market/definition",
        {
            "root_name": "Rental software",
            "description": "Software for small landlords.",
            "year": 2026,
            "params": {"adoption": {"value": 0.1, "judgement": True}},
            **more,
        },
    )
    first = post(api, "market/segments", {"name": "Screening"})["segment"]["id"]
    second = post(api, "market/segments", {"name": "Payments"})["segment"]["id"]
    sub = post(api, "market/segments", {"name": "Credit checks", "parent_id": first})["segment"]["id"]
    return first, second, sub


def size(api, segment, spend=B, low=None, high=None, expected=200, token="ana-test", **more):
    body = {
        "segment_id": segment,
        "method": "owner",
        "spend_usd": spend,
        "low_usd": spend / 2 if low is None else low,
        "high_usd": spend * 2 if high is None else high,
        **more,
    }
    out = post(api, "market/sizes", body, token=token, expected=expected)
    return out["size"] if expected == 200 else out
