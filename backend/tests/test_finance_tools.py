"""The hub_finance_* tools (docs/finance-tools.md): each call runs as its caller, through the Finance routes, with
results cut to the payload budget."""
import json

from backend.finance import actions as FA
from backend.finance import reads as FR
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
    assert other["key"] == hubtools.FIN_OTHER and other["label"] == f"Other · {50 - shown}"
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


def test_a_report_folds_entity_rows_and_keeps_every_period_column():
    columns = [{"start": f"2026-{1 + d // 28:02}-{1 + d % 28:02}", "end": "", "label": f"Day {d}"} for d in range(150)]
    rows = [{"account_id": f"a{i}", "label": f"Account {i}", "actual": [i] * 150} for i in range(100)]

    class Reports:
        def get(self, path, **query):
            return {"kind": "pnl", "columns": [dict(x) for x in columns], "rows": [dict(r) for r in rows]}
    out = hubtools.finance_report(Reports(), {"kind": "pnl", "period": "day"})
    assert len(json.dumps(out)) <= hubtools.FINANCE_CHARS
    assert out["columns"] == columns
    assert out["rows"][-1]["key"] == hubtools.FIN_OTHER and len(out["rows"]) < len(rows)
    assert all(len(r["actual"]) == 150 for r in out["rows"])


def test_a_report_folds_onto_the_servers_other_row_and_keeps_vendors_named_other_and_unattributed():
    assert hubtools.FIN_OTHER == FR.OTHER
    names = ["other", "unattributed", FR.NONE] + [f"v{i}" for i in range(17)]          # 20 shown, 11 folded already
    rows = [{"key": k, "label": "Ünïcødé vendor " + "é" * 400, "amount": 1_000 - i} for i, k in enumerate(names)]
    tail = {"key": FR.OTHER, "label": "Other · 11", "amount": 5_500, "count": 11}

    class Reports:
        def get(self, path, **query):
            return {"by": "vendor", "rows": [dict(r) for r in rows] + [dict(tail)], "total": 0}
    out = hubtools.finance_report(Reports(), {"kind": "spend_by", "by": "vendor"})
    assert len(json.dumps(out)) <= hubtools.FINANCE_CHARS
    keys = [r["key"] for r in out["rows"]]
    assert len(keys) == len(set(keys)) and keys[-1] == FR.OTHER and keys[:3] == ["other", "unattributed", FR.NONE]
    folded = out["rows"][-1]
    assert folded["count"] == 31 - (len(keys) - 1) and folded["label"] == f"Other · {folded['count']}"
    assert sum(r["amount"] for r in out["rows"]) == sum(r["amount"] for r in rows) + tail["amount"]


def test_one_huge_row_is_cut_inside_and_a_result_that_cannot_fit_says_so():
    huge = {"id": "itm_1", "kind": "bill_to_enter", "label": "z" * 100_000,
            "lines": [{"id": f"ln_{i}", "memo": "w" * 200, "amount": i} for i in range(500)]}
    out = hubtools.finance_inbox(_Api([huge, _row(2)], key="items"), {})
    assert len(json.dumps(out)) <= hubtools.FINANCE_CHARS
    first = out["items"][0]
    assert first["id"] == "itm_1" and first["lines"][-1]["key"] == hubtools.FIN_OTHER
    assert first["lines"][-1]["amount"] + sum(x["amount"] for x in first["lines"][:-1]) == sum(range(500))

    class Actions:
        def get(self, path, **query):
            return {"action_id": "act_1", "result": {"amounts": list(range(20_000))}}
    marker = hubtools.finance_action(Actions(), {"action_id": "act_1"})
    assert marker["error"] == "too_big" and marker["cap"] == hubtools.FINANCE_CHARS


def test_inbox_pages_one_kind_through_the_cli_and_the_tool_passing_its_cursor(monkeypatch):
    from clients import hubcli, remotecli

    class Inbox(_Api):
        def get(self, path, **query):
            assert query.get("kind") == "uncategorized", query
            return super().get(path, **query)
    api = Inbox([{"id": f"fd_{i}", "kind": "uncategorized", "label": f"Row {i}"} for i in range(250)], key="items")
    args = hubcli.parser().parse_args(["finance", "inbox", "--kind", "uncategorized", "--cursor", "200"])
    out = remotecli.via_tool(api, args)
    assert [r["id"] for r in out["items"]][0] == "fd_200" and len(out["items"]) == 50 and out["next_cursor"] is None
    assert api.calls == [("finance/inbox", {"kind": "uncategorized", "cursor": "200"})]
