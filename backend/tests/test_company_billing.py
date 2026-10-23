"""Settings > Billing (backend/company_billing.py, Billing 5/5): owners and admins only, read from Tico HQ with the
install's gateway key, whole micro-dollars only, and the statement's CSV as HQ writes it. HQ is a fake opener."""

import io
import json
import urllib.error

import pytest

from backend import company_billing
from backend.tests.test_api import api, get  # noqa: F401

KEY = "tgw-synthetic-fixture-key-0042"
URL = "https://gateway.acme.example/v1/gateway"
CSV = "month,line,amount_micros\n2026-11,opening balance,0\n2026-11,credits: grant,20000000\n" \
      "2026-11,debits: gateway,1250000\n2026-11,expired,0\n2026-11,closing balance,18750000\n"
ANSWER = {
    "state": "active", "balance_micros": 18_750_000, "min_balance_micros": 0, "unbilled_micros": 0,
    "last_day_micros": 1_250_000, "enforcement": "enforce", "record_only": False, "grace_until": None,
    "months": ["2026-11"], "month": "2026-11", "booking_url": "https://cal.acme.example/tico",
    "credits": [{"credit_id": "cr_1", "source": "grant", "label": "Grant", "amount_micros": 20_000_000,
                 "left_micros": 18_750_000, "expires": None, "created": "2026-11-02T09:00:00.000000Z",
                 "expired": False, "used_up": False, "expired_micros": 0, "current": True}],
    "usage": [{"period": "2026-11-02", "total_micros": 1_250_000,
               "lines": [{"product": "gateway", "sku": "tico-mid-1.0:input", "amount_micros": 1_250_000}]}],
    "statement": {"month": "2026-11", "opening_micros": 0, "credits_micros": 20_000_000,
                  "credits_by_source": {"grant": 20_000_000}, "debits_micros": 1_250_000,
                  "debits_by_product": {"gateway": 1_250_000, "hosting": 0, "support": 0}, "expired_micros": 0,
                  "closing_micros": 18_750_000}}


class Answer(io.BytesIO):
    def __init__(self, body, kind="application/json"):
        super().__init__(body.encode() if isinstance(body, str) else body)
        self.status, self.headers = 200, {"Content-Type": kind}


class FakeHQ:
    def __init__(self):
        self.calls, self.answers = [], {}

    def __call__(self, request, timeout):
        self.calls.append((request.full_url, request.get_header("Authorization")))
        path = request.full_url[len(URL):]
        status, body, kind = self.answers.get(path.split("?")[0], (404, {"error": "not_found"}, "application/json"))
        text = body if isinstance(body, str) else json.dumps(body)
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "refused", {}, io.BytesIO(text.encode()))
        return Answer(text, kind)


@pytest.fixture
def hq(api, monkeypatch):  # noqa: F811
    fake = FakeHQ()
    fake.answers = {"/billing": (200, ANSWER, "application/json"),
                    "/billing/usage": (200, {"day": "2026-11-02", "hours": [dict(ANSWER["usage"][0], period="2026-11-02T09")]},
                                       "application/json"),
                    "/billing/statements/2026-11.csv": (200, CSV, "text/csv; charset=utf-8")}
    monkeypatch.setattr(company_billing, "OPENER", fake)
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    monkeypatch.setenv("TICO_GATEWAY_URL", URL)
    return fake


def test_owner_and_admins_read_billing_with_the_gateway_key_and_members_are_refused(api, hq):  # noqa: F811
    shown = get(api, "billing")
    assert shown["balance_micros"] == 18_750_000 and shown["state"] == "active"
    assert shown["credits"][0]["label"] == "Grant" and shown["booking_url"] == "https://cal.acme.example/tico"
    assert hq.calls == [(URL + "/billing", "Bearer " + KEY)]
    assert get(api, "billing", "ben-test")["balance_micros"] == 18_750_000          # an admin
    for path in ("billing", "billing/usage?day=2026-11-02", "billing/statements/2026-11.csv"):
        r = api.get("/api/v2/" + path, headers={"Authorization": "Bearer cara-test"})   # a member
        assert r.status_code == 403, r.text
    assert len(hq.calls) == 2                                                        # a refusal never asks HQ
    assert KEY not in json.dumps(shown)


def test_the_statement_csv_is_hqs_and_matches_the_screen(api, hq):  # noqa: F811
    r = api.get("/api/v2/billing/statements/2026-11.csv", headers={"Authorization": "Bearer ana-test"})
    assert r.status_code == 200 and r.text == CSV
    assert r.headers["content-type"].startswith("text/csv") and "no-store" in r.headers["cache-control"]
    statement = get(api, "billing?month=2026-11")["statement"]
    lines = {line.split(",")[1]: int(line.split(",")[2]) for line in CSV.strip().split("\n")[1:]}
    assert lines["opening balance"] == statement["opening_micros"]
    assert lines["closing balance"] == statement["closing_micros"] == statement["opening_micros"] \
        + statement["credits_micros"] - statement["debits_micros"] - statement["expired_micros"]


def test_a_day_opens_into_hours(api, hq):  # noqa: F811
    shown = get(api, "billing/usage?day=2026-11-02")
    assert [hour["period"] for hour in shown["hours"]] == ["2026-11-02T09"]
    assert hq.calls[-1][0] == URL + "/billing/usage?day=2026-11-02"


def test_a_month_or_day_that_is_not_one_never_reaches_hq(api, hq):  # noqa: F811
    for path in ("billing?month=2026-11%26x=1", "billing/usage?day=2026-11-02%2F..", "billing/statements/..%2Fkey.csv"):
        r = api.get("/api/v2/" + path, headers={"Authorization": "Bearer ana-test"})
        assert r.status_code in (404, 422), (path, r.status_code)
    assert hq.calls == []


def test_no_gateway_key_no_account_and_a_refused_key_say_so(api, hq, monkeypatch):  # noqa: F811
    hq.answers["/billing"] = (404, {"error": "no_account"}, "application/json")
    assert get(api, "billing") == {"state": "no_account", "booking_url": None}
    # a new company's empty page still offers HQ's booking link
    hq.answers["/billing"] = (404, {"error": "no_account", "booking_url": "https://cal.acme.example/tico"}, "application/json")
    assert get(api, "billing") == {"state": "no_account", "booking_url": "https://cal.acme.example/tico"}
    hq.answers["/billing"] = (401, {"error": "revoked_key"}, "application/json")
    assert get(api, "billing") == {"state": "refused"}
    monkeypatch.delenv("TICO_GATEWAY_KEY")
    calls = len(hq.calls)
    assert get(api, "billing") == {"state": "no_gateway"} and len(hq.calls) == calls


def test_money_that_is_not_whole_micro_dollars_is_not_shown(api, hq):  # noqa: F811
    hq.answers["/billing"] = (200, {**ANSWER, "balance_micros": 18.75}, "application/json")
    assert get(api, "billing") == {"state": "unreachable"}
    hq.answers["/billing"] = (200, {**ANSWER, "booking_url": "javascript:alert(1)"}, "application/json")
    assert get(api, "billing")["booking_url"] is None
