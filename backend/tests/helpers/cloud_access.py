"""Helpers that other tests share, moved out of test_cloud_access.py so they import no test file."""

BILLING_URL = "https://app.tico.dev/billing/boatyard"


def signals(access="open", reason=None, **extra):
    return {
        "plan": "trial",
        "access": access,
        "reason": reason,
        "balance_micros": 0 if access != "open" else 2_000_000,
        "trial_ends": "2026-11-12T00:00:00Z",
        "trial_days_left": 14,
        "card": None,
        "auto_recharge": False,
        "period_end": None,
        "price_micros": 20_000_000,
        "billing_url": BILLING_URL,
        **extra,
    }
