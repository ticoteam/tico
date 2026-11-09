"""Helpers that other tests share, moved out of test_company_billing.py so they import no test file."""

import io
import json
import urllib.error
import pytest
from backend import company_billing


KEY = "tgw-synthetic-fixture-key-0042"


URL = "https://gateway.acme.example/v1/gateway"


CSV = (
    "month,line,amount_micros\n2026-11,opening balance,0\n2026-11,credits: grant,20000000\n"
    "2026-11,debits: gateway,1250000\n2026-11,expired,0\n2026-11,closing balance,18750000\n"
)


ANSWER = {
    "state": "active",
    "balance_micros": 18_750_000,
    "min_balance_micros": 0,
    "unbilled_micros": 0,
    "last_day_micros": 1_250_000,
    "enforcement": "enforce",
    "record_only": False,
    "grace_until": None,
    "months": ["2026-11"],
    "month": "2026-11",
    "booking_url": "https://cal.acme.example/tico",
    "credits": [
        {
            "credit_id": "cr_1",
            "source": "grant",
            "label": "Grant",
            "amount_micros": 20_000_000,
            "left_micros": 18_750_000,
            "expires": None,
            "created": "2026-11-02T09:00:00.000000Z",
            "expired": False,
            "used_up": False,
            "expired_micros": 0,
            "current": True,
        }
    ],
    "usage": [
        {
            "period": "2026-11-02",
            "total_micros": 1_250_000,
            "lines": [{"product": "gateway", "sku": "tico-medium-1.0:input", "amount_micros": 1_250_000}],
        }
    ],
    "statement": {
        "month": "2026-11",
        "opening_micros": 0,
        "credits_micros": 20_000_000,
        "credits_by_source": {"grant": 20_000_000},
        "debits_micros": 1_250_000,
        "debits_by_product": {"gateway": 1_250_000, "hosting": 0, "support": 0},
        "expired_micros": 0,
        "closing_micros": 18_750_000,
    },
}


class Answer(io.BytesIO):
    def __init__(self, body, kind="application/json"):
        super().__init__(body.encode() if isinstance(body, str) else body)
        self.status, self.headers = 200, {"Content-Type": kind}


class FakeHQ:
    def __init__(self):
        self.calls, self.answers = [], {}

    def __call__(self, request, timeout):
        self.calls.append((request.full_url, request.get_header("Authorization")))
        path = request.full_url[len(URL) :]
        status, body, kind = self.answers.get(path.split("?")[0], (404, {"error": "not_found"}, "application/json"))
        text = body if isinstance(body, str) else json.dumps(body)
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "refused", {}, io.BytesIO(text.encode()))
        return Answer(text, kind)


@pytest.fixture
def hq(api, monkeypatch):  # noqa: F811
    fake = FakeHQ()
    fake.answers = {
        "/billing": (200, ANSWER, "application/json"),
        "/billing/usage": (
            200,
            {"day": "2026-11-02", "hours": [dict(ANSWER["usage"][0], period="2026-11-02T09")]},
            "application/json",
        ),
        "/billing/statements/2026-11.csv": (200, CSV, "text/csv; charset=utf-8"),
    }
    monkeypatch.setattr(company_billing, "OPENER", fake)
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    monkeypatch.setenv("TICO_GATEWAY_URL", URL)
    return fake
