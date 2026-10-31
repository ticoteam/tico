"""Finance wired into Tico (phase 0, part B): setup on an existing install, the daily Gateway usage posting, run holds
from claim to run end, a budget cutoff as a usage limit, warn and cutoff events, and the Gateway cap worker."""
import io
import json
import urllib.error
from datetime import datetime, timezone
from decimal import Decimal

from backend import usage_limits
from backend.finance import actions as FA
from backend.finance import gateway_sync
from backend.finance.store import tico as finance_tico
from backend.tests.test_api import api, get, headers  # noqa: F401
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, budget, finance_template, spend, store  # noqa: F401

NOON = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def _turn(c, bot, finished, cost, model="tico-medium", billing="api"):
    tid = f"t{bot}{finished}{model}"
    c.execute("INSERT INTO turns(id, bot, started, finished, model, est_cost_usd, billing) VALUES(?,?,?,?,?,?,?)",
              (tid, bot, finished, finished, model, cost, billing))
    return tid


def _meter(as_of, **prices):
    rows = [{"model": model, "category": "image" if model.endswith("image") else "text", "charged": True, "calls": 1,
             "price_usd": Decimal(price)} for model, price in prices.items()]
    rows.append({"model": "tico-free", "category": "text", "charged": False, "calls": 9, "price_usd": Decimal("9")})
    return {"month": "2026-10", "rows": rows, "as_of": as_of}


def _usage_lines(c):
    """{(teammate or "", account code): cents} over every usage entry."""
    out = {}
    for code, amount, mate in c.execute(
            "SELECT a.code, l.amount, coalesce(t.value, '') FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
            "JOIN fin_accounts a ON a.id=l.account_id LEFT JOIN fin_line_tags t ON t.line_id=l.id AND t.kind='teammate' "
            "WHERE e.source='usage'"):
        out[(mate, code)] = out.get((mate, code), 0) + amount
    return out


def test_setup_on_an_existing_install_is_idempotent(store):
    with store.transaction() as c:
        assert FA.ensure_setup(c, "ana@acme.example")
        counts = [c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("fin_accounts", "fin_budgets")]
        assert FA.ensure_setup(c, "ana@acme.example")
        assert [c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("fin_accounts", "fin_budgets")] == counts


def test_usage_posting_matches_gateway_billed_runs_once(books):
    with books.transaction() as c:
        _turn(c, "coder", "2026-10-15T13:00:00.000000Z", 0.30)
        _turn(c, "coder", "2026-10-15T14:00:00.000000Z", 0.20)
        _turn(c, "seller", "2026-10-15T14:30:00.000000Z", 0.10, model="tico-fast")
        _turn(c, "seller", "2026-10-15T14:40:00.000000Z", 5.00, model="claude-sonnet-4-5")     # own key
        _turn(c, "seller", "2026-10-15T14:50:00.000000Z", 1.00, billing="subscription")       # a sign-in
        meter = _meter("2026-10-15T16:00:00Z", **{"tico-medium": "0.50", "tico-fast": "0.10", "tico-image": "0.07"})
        assert FA.usage_snapshot(c, meter, fetched_at="2026-10-15T16:01:00.000000Z")
        posted = FA.post_usage(c, "2026-10-15T16:05:00.000000Z")
        assert sorted((p["line"], p["amount"]) for p in posted) == [("ai", 60), ("video", 7)]
        assert _usage_lines(c) == {("bot:coder", "5100"): 50, ("bot:seller", "5100"): 10, ("", "5200"): 7,
                                   ("", "1100"): -67}
        assert FA.usage_snapshot(c, meter, fetched_at="2026-10-15T17:00:00.000000Z") is None
        assert FA.post_usage(c, "2026-10-15T17:05:00.000000Z") == []
        # The next day's meter carries a sub-cent remainder until it rounds up.
        FA.usage_snapshot(c, _meter("2026-10-16T16:00:00Z", **{"tico-medium": "0.504", "tico-fast": "0.10",
                                                                "tico-image": "0.07"}), fetched_at=AT)
        assert FA.post_usage(c, "2026-10-16T16:05:00.000000Z") == []
        FA.usage_snapshot(c, _meter("2026-10-17T16:00:00Z", **{"tico-medium": "0.516", "tico-fast": "0.10",
                                                                "tico-image": "0.07"}), fetched_at=AT)
        assert [(p["line"], p["amount"]) for p in FA.post_usage(c, "2026-10-17T16:05:00.000000Z")] == [("ai", 2)]
        assert sum(v for (_, code), v in _usage_lines(c).items() if code == "5100") == 62


def test_a_run_hold_settles_once_and_the_posting_takes_it_in(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})
        _turn(c, "coder", "2026-10-14T10:00:00.000000Z", 0.40)                      # recent history: hold 40 cents
        held = FA.run_hold(c, "coder", "job1", at=AT)
        assert held.hold_id and not held.refused
        assert FA.budget_state(c, ai, AT).held == 40
        run = _turn(c, "coder", "2026-10-15T13:00:00.000000Z", 0.25)
        assert FA.run_ended(c, "job1", run, "2026-10-15T13:00:01.000000Z") == "settled"
        assert FA.run_ended(c, "job1", run, "2026-10-15T13:00:02.000000Z") == "none"
        assert FA.budget_state(c, ai, AT).held == 25
        own = FA.run_hold(c, "coder", "job2", at=AT)
        assert own.hold_id
        mine = _turn(c, "coder", "2026-10-15T13:30:00.000000Z", 3.00, model="claude-sonnet-4-5")
        assert FA.run_ended(c, "job2", mine, "2026-10-15T13:30:01.000000Z") == "released"
        FA.usage_snapshot(c, _meter("2026-10-15T16:00:00Z", **{"tico-medium": "0.65"}), fetched_at=AT)
        FA.post_usage(c, "2026-10-15T16:05:00.000000Z")
        state = FA.budget_state(c, ai, "2026-10-15T16:05:00.000000Z")
        assert (state.actual, state.held) == (65, 0)


def test_a_decline_budget_at_its_cutoff_pauses_the_bots_it_covers(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 100})
        assert usage_limits.blocked(c, "coder", at=NOON) is None
        spend(c, "5100", 100, teammate="bot:coder")
        met = usage_limits.over(c, "seller", at=NOON)
        assert met["scope"] == "budget" and met["name"] == "AI services" and "spent" not in met
        assert usage_limits.blocked(c, "coder", at=NOON)
        assert FA.run_hold(c, "coder", "job3", at=AT).refused
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})
        assert usage_limits.blocked(c, "coder", at=NOON) is None


def test_warn_and_cutoff_fire_once_per_period_and_tell_the_owner(books):
    def notices(c):
        return c.execute("SELECT count(*) FROM messages WHERE to_actor=? AND kind='notice'", (OWNER,)).fetchone()[0]

    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 100})
        before = notices(c)
        spend(c, "5100", 85)
        assert FA.drain_events(c, AT) == 1
        spend(c, "5100", 5)
        assert FA.drain_events(c, AT) == 0
        spend(c, "5100", 20)
        spend(c, "5100", 20)
        assert FA.drain_events(c, AT) == 1
        assert notices(c) - before == 2
        names = [r[0] for r in c.execute("SELECT name FROM fin_events WHERE subject=? ORDER BY name", (ai,))]
        assert names == ["finance.budget.cutoff", "finance.budget.warn"]


class _Gateway:
    """A fake Gateway: answers from a list, records what was sent."""

    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def __call__(self, request, timeout):
        self.sent.append((request.get_method(), request.full_url, request.data))
        status, body = self.answers.pop(0)
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "no", {}, io.BytesIO(json.dumps(body).encode()))
        response = io.BytesIO(json.dumps(body).encode())
        response.status = status
        return response


def test_the_cap_worker_withholds_in_rehearsal_and_waits_out_a_404(books, monkeypatch):
    monkeypatch.setattr(finance_tico, "gateway_connected", lambda c, settings: True)
    monkeypatch.setattr(finance_tico, "gateway_target", lambda c, vault, settings: ("gk_test", "http://127.0.0.1:9"))
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 100})
        spend(c, "5100", 100)
    settings = books.settings

    def step(gateway, now, rehearsal=False):
        return gateway_sync.cap_step(books, None, settings, rehearsal=rehearsal, opener=gateway, now=now)

    quiet = _Gateway()
    assert step(quiet, "2026-10-15T12:00:00.000000Z", rehearsal=True) == "withheld" and quiet.sent == []
    missing = _Gateway((404, {"error": "not_found"}))
    assert step(missing, "2026-10-15T14:00:00.000000Z") == "not_deployed"
    assert step(missing, "2026-10-15T14:10:00.000000Z") == "waiting" and len(missing.sent) == 1
    live = _Gateway((200, {"spent_usd": 12.5, "cap_usd": 100}),
                    (200, {"spent_usd": 12.5, "cap_usd": 100, "self_cap_usd": 12.5, "effective_cap_usd": 12.5}))
    assert step(live, "2026-10-15T15:10:00.000000Z") == "pushed"
    assert json.loads(live.sent[1][2]) == {"self_cap_usd": 12.5} and live.sent[1][1].endswith("/key/cap")
    assert step(live, "2026-10-15T15:20:00.000000Z") == "steady"
    with books.transaction() as c:
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})
    cleared = _Gateway((200, {"spent_usd": 12.5, "cap_usd": 100, "self_cap_usd": None}))
    assert step(cleared, "2026-10-15T15:30:00.000000Z") == "pushed"
    assert json.loads(cleared.sent[0][2]) == {"self_cap_usd": None}
    assert gateway_sync.usage_step(books, None, settings, opener=_Gateway((404, {})),
                                   now="2026-10-15T15:40:00.000000Z") == "not_deployed"


def test_routes_read_in_the_callers_view_and_a_retry_replays_only_the_status(api):
    store = api.app.state.store
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)
    ai = next(b for b in get(api, "finance/budgets")["budgets"] if b["name"] == "AI services")
    assert get(api, "finance/budgets", token="ben-test")["budgets"] == []          # a manager of nothing sees none
    body = {"kind": "budget_update", "payload": {"budget_id": ai["id"], "amount": 5_000}}
    dry = api.post("/api/v2/finance/actions", json={**body, "dry_run": True}, headers=headers()).json()
    assert dry["dry_run"] and dry["status"] == "committed"
    key = headers()
    first = api.post("/api/v2/finance/actions", json=body, headers=key).json()
    assert first["status"] == "committed" and "result" in first
    again = api.post("/api/v2/finance/actions", json=body, headers=key).json()
    assert again["action_id"] == first["action_id"] and "result" not in again
    assert get(api, "finance/actions/" + first["action_id"])["status"] == "committed"
    get(api, "finance/actions/" + first["action_id"], token="ben-test", expected=404)
    amount = next(b for b in get(api, "finance/budgets")["budgets"] if b["id"] == ai["id"])["state"]["amount"]
    assert amount == 5_000
    assert "view_version" in get(api, "finance/pnl") and "view_version" in get(api, "finance/expenses")
