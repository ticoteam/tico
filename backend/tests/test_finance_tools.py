"""The hub_finance_* tools (docs/finance-tools.md): each call runs as its caller, through the Finance routes, with
results cut to the payload budget."""
import json

from backend.finance import actions as FA
from backend.tests.test_api import api, get, setup_attempt  # noqa: F401
from backend.tests.test_mcp import call
from clients import hubtools


def as_bot(api, slug):
    """A run's token for bot:<slug>, the credential a bot's hub calls carry."""
    return setup_attempt(api, slug)[2]["token"]


def _books(api):
    store = api.app.state.store
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)
    return store


def test_tools_run_as_the_calling_bot_with_its_own_rights_and_view(api):
    _books(api)
    bot = as_bot(api, "finance")
    err, out = call(api, "hub_finance_act", {"kind": "payment_create", "operation_id": "pay-1", "payload": {
        "recipient_id": "rcp_none", "amount": 5_000, "money_account_id": "ma_none"}}, token=bot)
    assert not err and out["status"] == "denied" and "finance.pay" in out["reason"], out
    err, cash = call(api, "hub_finance_metric", {"name": "cash"})
    assert not err and "as_of" in cash
    err, refused = call(api, "hub_finance_metric", {"name": "cash"}, token=bot)
    assert err, refused
    err, queue = call(api, "hub_finance_inbox", {}, token=bot)
    assert not err and queue["items"] == []
    err, found = call(api, "hub_finance_search", {"q": "services"}, token=bot)
    assert not err and found["results"] == []


def test_act_takes_the_operation_id_as_its_key_and_a_replay_rereads_only_the_status(api):
    _books(api)
    ai = next(b for b in get(api, "finance/budgets")["budgets"] if b["name"] == "AI services")
    ask = {"kind": "budget_update", "payload": {"budget_id": ai["id"], "amount": 5_000}, "operation_id": "budget-1"}
    err, first = call(api, "hub_finance_act", ask)
    assert not err and first["status"] == "committed" and "result" in first
    err, again = call(api, "hub_finance_act", ask)
    assert not err and again["action_id"] == first["action_id"] and again["status"] == "committed"
    assert "result" not in again and "impact" not in again
    err, status = call(api, "hub_finance_action", {"action_id": first["action_id"]})
    assert not err and status["status"] == "committed"
    err, missing = call(api, "hub_finance_act", {k: v for k, v in ask.items() if k != "operation_id"})
    assert err and "operation_id" in json.dumps(missing)
    err, kinds = call(api, "hub_finance_act", {"kind": "help"})
    assert not err and {"payment_create", "bill_create", "categorize"} <= {k["kind"] for k in kinds["kinds"]}
    err, one = call(api, "hub_finance_act", {"kind": "help", "payload": {"kind": "bill_create"}})
    assert not err and one["fields"][0]["name"] == "vendor_id"


class _Api:
    """A Finance route stand-in: pages of `rows` by an offset cursor, counting the calls."""

    def __init__(self, rows, key="results"):
        self.rows, self.key, self.calls = rows, key, []

    def get(self, path, **query):
        self.calls.append((path, query))
        start, most = int(query.get("cursor") or 0), int(query.get("limit") or 50)
        page = self.rows[start:start + most]
        more = start + most < len(self.rows)
        return {self.key: page, "next_cursor": str(start + most) if more else None, "currency": "USD"}


def _row(i):
    return {"id": f"fd_{i:04}", "kind": "feed", "label": f"Vendor {i} " + "x" * 300, "amount": -100 * i}


def test_a_big_page_fits_the_budget_with_an_other_row_and_a_cursor_that_carries_on():
    api = _Api([_row(i) for i in range(500)])
    out = hubtools.finance_search(api, {"kind": "feed", "limit": 50})
    assert len(json.dumps(out)) <= hubtools.FINANCE_CHARS
    other = out["results"][-1]
    shown = len(out["results"]) - 1
    assert other["key"] == "other" and other["label"] == f"Other · {50 - shown}"
    assert out["next_cursor"] == str(shown) and api.calls[-1][1]["limit"] == shown
    big = hubtools.finance_search(_Api([_row(i) for i in range(500)]), {"kind": "feed", "limit": 200})
    assert hubtools.FINANCE_CHARS < len(json.dumps(big)) <= hubtools.FINANCE_MAX_CHARS
    small = hubtools.finance_inbox(_Api([_row(i) for i in range(3)], key="items"), {})
    assert len(small["items"]) == 3 and small["next_cursor"] is None


def test_a_big_report_folds_its_tail_into_one_other_row_that_adds_up():
    rows = [{"account_id": f"a{i}", "label": f"Account {i} " + "y" * 100, "amount": i, "columns": [i, 2 * i]}
            for i in range(1000)]

    class Reports:
        def get(self, path, **query):
            return {"kind": "pnl", "rows": [dict(r) for r in rows]}
    out = hubtools.finance_report(Reports(), {"kind": "pnl"})
    assert len(json.dumps(out)) <= hubtools.FINANCE_CHARS
    other = out["rows"][-1]
    kept = out["rows"][:-1]
    assert other["count"] == 1000 - len(kept) and other["label"] == f"Other · {other['count']}"
    assert other["amount"] + sum(r["amount"] for r in kept) == sum(r["amount"] for r in rows)
    assert other["columns"][1] == 2 * other["amount"]
