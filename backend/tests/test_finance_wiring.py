"""Finance wired into Tico (phase 0, part B): setup on an existing install, the daily Gateway usage posting, run holds
from claim to run end, a budget cutoff as a usage limit, warn and cutoff events, and the Gateway cap worker."""
import io
import json
import urllib.error
from datetime import datetime, timezone
from decimal import Decimal

from backend import hubdb as H
from backend import usage_limits
from backend.finance import actions as FA
from backend.finance import gateway_sync
from backend.finance import reads as FR
from backend.finance.store import tico as finance_tico
from backend.tests.test_api import api, get, headers  # noqa: F401
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, budget, finance_template, spend, store  # noqa: F401

NOON = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def _turn(c, bot, finished, cost, model="tico-medium", billing="api", tid=None):
    tid = tid or f"t{bot}{finished}{model}"
    c.execute("INSERT INTO turns(id, bot, started_at, finished_at, model, est_cost_usd, billing) VALUES(?,?,?,?,?,?,?)",
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


def test_a_video_production_cutoff_pauses_no_bot(books):
    with books.transaction() as c:
        act(c, OWNER, "budget_update", {"budget_id": budget(c, "Video production"), "amount": 100})
        spend(c, "5200", 100, teammate="bot:coder")
        assert usage_limits.blocked(c, "coder", at=NOON) is None
        assert not FA.run_hold(c, "coder", "job4", at=AT).refused
        assert FR.video_cutoff(c, "bot:coder", AT).name == "Video production"


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


def _events(c, bid, name=None):
    sql = "SELECT name FROM fin_events WHERE subject=?" + (" AND name=?" if name else "")
    return [r[0] for r in c.execute(sql, (bid, name) if name else (bid,))]


def _job(c, jid, state):
    c.execute("INSERT INTO messages(id) VALUES(?)", ("m" + jid,))
    c.execute("INSERT INTO jobs(id, message_id, bot, state, created_at) VALUES(?,?,?,?,?)", (jid, "m" + jid, "coder", state, AT))


def test_bots_get_only_their_own_recorded_cost_and_image_runs_land_on_video(books):
    with books.transaction() as c:
        _turn(c, "coder", "2026-10-15T13:00:00.000000Z", 0.10)
        _turn(c, "seller", "2026-10-15T13:10:00.000000Z", 0.07, model="tico-image")
        FA.usage_snapshot(c, _meter("2026-10-15T16:00:00Z", **{"tico-medium": "1.00", "tico-image": "0.07"}),
                          fetched_at=AT)
        FA.post_usage(c, "2026-10-15T16:05:00.000000Z")
        assert _usage_lines(c) == {("bot:coder", "5100"): 10, ("", "5100"): 90, ("bot:seller", "5200"): 7,
                                   ("", "1100"): -107}


def test_a_flat_meter_still_takes_in_a_run_it_already_carried(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})
        assert FA.run_hold(c, "coder", "job1", at=AT).hold_id
        FA.usage_snapshot(c, _meter("2026-10-15T13:00:00Z", **{"tico-medium": "0.60"}), fetched_at=AT)
        FA.post_usage(c, "2026-10-15T13:05:00.000000Z")                    # the run is still going
        run = _turn(c, "coder", "2026-10-15T13:30:00.000000Z", 0.60)
        assert FA.run_ended(c, "job1", run, "2026-10-15T13:30:01.000000Z") == "settled"
        FA.usage_snapshot(c, _meter("2026-10-15T14:00:00Z", **{"tico-medium": "0.60"}), fetched_at=AT)
        FA.post_usage(c, "2026-10-15T14:05:00.000000Z")
        st = FA.budget_state(c, ai, "2026-10-15T14:05:00.000000Z")
        assert st.actual + st.held == 60


def test_octobers_posting_never_takes_in_a_november_run(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})
        held = FA.run_hold(c, "coder", "jobnov", at="2026-11-01T01:00:00.000000Z")
        run = _turn(c, "coder", "2026-11-01T01:30:00.000000Z", 0.30)
        FA.run_ended(c, "jobnov", run, "2026-11-01T01:30:01.000000Z")
        FA.usage_snapshot(c, {**_meter("2026-11-02T00:00:00Z", **{"tico-medium": "0.50"}), "month": "2026-10"},
                          fetched_at="2026-11-02T00:00:00.000000Z")
        assert FA.post_usage(c, "2026-11-02T00:05:00.000000Z")
        assert c.execute("SELECT entry_id FROM fin_holds WHERE id=?", (held.hold_id,)).fetchone()[0] is None


def test_a_posting_that_takes_in_its_hold_crosses_nothing(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 100})
        FA.run_hold(c, "coder", "job1", at=AT)
        run = _turn(c, "coder", "2026-10-15T13:00:00.000000Z", 0.60)
        FA.run_ended(c, "job1", run, "2026-10-15T13:00:01.000000Z")
        FA.usage_snapshot(c, _meter("2026-10-15T16:00:00Z", **{"tico-medium": "0.60"}), fetched_at=AT)
        FA.post_usage(c, "2026-10-15T16:05:00.000000Z")
        assert _events(c, ai) == []


def test_a_month_missed_over_its_turn_is_still_read_until_its_final_posting(books):
    with books.transaction() as c:
        FA.usage_snapshot(c, _meter("2026-10-30T12:00:00Z", **{"tico-medium": "0.50"}),
                          fetched_at="2026-10-30T12:00:00.000000Z")
        FA.post_usage(c, "2026-10-30T12:05:00.000000Z")
        assert "2026-10" in gateway_sync.due_months(c, "2026-11-04T12:00:00.000000Z")
        FA.usage_snapshot(c, _meter("2026-11-04T12:00:00Z", **{"tico-medium": "0.70"}),
                          fetched_at="2026-11-04T12:00:00.000000Z")
        FA.post_usage(c, "2026-11-04T12:05:00.000000Z")
        assert "2026-10" not in gateway_sync.due_months(c, "2026-11-05T12:00:00.000000Z")


def test_an_older_snapshot_is_set_aside_and_a_larger_same_time_one_replaces(books):
    with books.transaction() as c:
        FA.usage_snapshot(c, _meter("2026-10-15T14:00:00Z", **{"tico-medium": "0.50"}), fetched_at=AT)
        FA.post_usage(c, "2026-10-15T14:05:00.000000Z")
        FA.usage_snapshot(c, _meter("2026-10-15T13:00:00Z", **{"tico-medium": "0.40"}),
                          fetched_at="2026-10-15T14:10:00.000000Z")
        assert FA.post_usage(c, "2026-10-15T14:15:00.000000Z") == []
        late = c.execute("SELECT posted_at, ignored_at FROM fin_usage_snapshots WHERE as_of LIKE '2026-10-15T13%'")
        assert [tuple(r) for r in late] == [(None, "2026-10-15T14:15:00.000000Z")]
        assert FA.usage_snapshot(c, _meter("2026-10-15T14:00:00Z", **{"tico-medium": "0.70"}),
                                 fetched_at="2026-10-15T14:20:00.000000Z")
        assert [p["amount"] for p in FA.post_usage(c, "2026-10-15T14:25:00.000000Z")] == [20]


def test_a_running_jobs_hold_outlives_its_expiry_and_a_late_run_still_counts(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})
        _job(c, "job1", "running")
        running = FA.run_hold(c, "coder", "job1", at=AT)
        late = FA.run_hold(c, "coder", "job2", at=AT)
        assert FA.expire_holds(c, "2026-10-16T01:00:00.000000Z") == 1
        assert c.execute("SELECT status FROM fin_holds WHERE id=?", (running.hold_id,)).fetchone()[0] == "held"
        assert c.execute("SELECT status FROM fin_holds WHERE id=?", (late.hold_id,)).fetchone()[0] == "released"
        run = _turn(c, "coder", "2026-10-16T02:00:00.000000Z", 0.30)
        assert FA.run_ended(c, "job2", run, "2026-10-16T02:00:01.000000Z") == "settled"
        assert FA.budget_state(c, ai, "2026-10-16T02:00:01.000000Z").held == 5 + 30


def test_a_bots_own_allocation_is_its_to_spend(books):
    from backend.finance import jobs
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 100})
        assert act(c, OWNER, "allocation_set", {"budget_id": ai, "target_kind": "teammate", "target_id": "bot:coder",
                                                "amount": 100}).status == "committed"
        assert usage_limits.blocked(c, "coder", at=NOON) is None
        assert usage_limits.blocked(c, "seller", at=NOON)
        assert jobs.cap_wanted(c, AT)[0] == "clear"


def test_a_budget_event_reaches_watch_rules_by_its_budget_id(books, monkeypatch):
    from backend import rules
    seen = []
    monkeypatch.setattr(rules, "judge_event", lambda c, name, ctx, **kw: seen.append((name, ctx)) or [])
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 100})
        spend(c, "5100", 85)
        FA.drain_events(c, AT)
    assert [(name, ctx["type"]) for name, ctx in seen] == [("finance.budget.warn", ai)]


def test_a_zero_budget_is_at_its_cutoff(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 0})
        spend(c, "5100", 1)
        assert _events(c, ai, "finance.budget.cutoff") == ["finance.budget.cutoff"]


def test_a_raise_arms_warn_and_cutoff_again(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 100})
        spend(c, "5100", 105)
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 200})
        spend(c, "5100", 100)
        assert sorted(_events(c, ai)) == ["finance.budget.cutoff"] * 2 + ["finance.budget.warn"] * 2


def test_a_retry_after_a_revoked_grant_shows_no_amount(api):
    store = api.app.state.store
    with store.transaction() as c:
        FA.ensure_setup(c, store.settings.owner_email)
        ai = budget(c, "AI services")
        FA.act(c, "human:ana", "budget_update", {"budget_id": ai, "amount": 1_000}, "raise")
        grant = FA.act(c, "human:ana", "view_grant", {"subject": "human:ben", "what": "expenses"}, "grant")
    body = {"kind": "allocation_set", "payload": {"budget_id": ai, "target_kind": "teammate", "target_id": "human:ben",
                                                  "amount": 5_000}}
    key = headers("ben-test")
    first = api.post("/api/v2/finance/actions", json=body, headers=key).json()
    assert "1000" in first["reason"]
    with store.transaction() as c:
        FA.act(c, "human:ana", "view_revoke", {"grant_id": grant.result["grant_id"]}, "revoke")
    again = api.post("/api/v2/finance/actions", json=body, headers=key).json()
    assert again["action_id"] == first["action_id"] and "1000" not in json.dumps(again)
    seen = get(api, "finance/actions/" + first["action_id"], token="ben-test")
    assert seen["status"] == first["status"] and "1000" not in json.dumps(seen)
    with store.transaction() as c:                      # the receipt lapses: Finance replays the action itself
        c.execute("DELETE FROM idempotency WHERE actor='human:ben'")
    lapsed = api.post("/api/v2/finance/actions", json=body, headers=key).json()
    assert lapsed["action_id"] == first["action_id"] and "1000" not in json.dumps(lapsed)
    get(api, "finance/actions/" + first["action_id"], token="cara-test", expected=404)
    with store.transaction() as c:                      # cara is named to decide it: she reads what she decides
        c.execute("UPDATE fin_actions SET approval_to='human:cara' WHERE id=?", (first["action_id"],))
    assert "1000" in get(api, "finance/actions/" + first["action_id"], token="cara-test")["reason"]


def _attempt(c, jid, aid, state="running"):
    _job(c, jid, state)
    c.execute("INSERT OR IGNORE INTO runners(id, label, operator, token_hash, created_at) VALUES('r1', 'r1', 'ana', 'h', ?)",
              (AT,))
    c.execute("INSERT INTO attempts(id, job_id, bot, runner_id, generation, token_hash, state, lease_until, created_at) "
              "VALUES(?,?,?,?,?,?,?,?,?)", (aid, jid, "coder", "r1", 1, "h" + aid, "running", AT, AT))


def _hold(c, hid):
    return tuple(c.execute("SELECT status, entry_id FROM fin_holds WHERE id=?", (hid,)).fetchone())


def test_a_reason_naming_a_budget_above_hides_once_that_budget_is_out_of_view(books):
    with books.transaction() as c:
        soft = account(c, "6100")
        root = act(c, OWNER, "budget_create", {"name": "Software", "account_id": soft, "amount": 1_000,
                                               "at_limit": "decline"}).result["budget_id"]
        web = act(c, OWNER, "budget_create", {"name": "Software web", "account_id": soft, "team": "web",
                                              "parent_id": root, "amount": 500, "at_limit": "decline"}).result["budget_id"]
        spend(c, "6100", 600)
        grant = act(c, OWNER, "view_grant", {"subject": "human:cara", "what": "expenses"})
        made = act(c, "human:cara", "allocation_set", {"budget_id": web, "target_kind": "teammate",
                                                       "target_id": "human:eve", "amount": 450})
        assert "400" in made.reason
        act(c, OWNER, "view_revoke", {"grant_id": grant.result["grant_id"]})
        assert FR.action_for(c, "human:cara", made.action_id)["reason"] == ""
        assert "400" in FR.action_for(c, OWNER, made.action_id)["reason"]


def test_an_image_hold_does_not_count_beside_the_ai_posting(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 100})
        _attempt(c, "jimg", "aimg")
        FA.run_hold(c, "coder", "jimg", at=AT)
        _turn(c, "coder", "2026-10-15T13:00:00.000000Z", 0.60, model="tico-image", tid="aimg")
        assert FA.run_ended(c, "jimg", "aimg", "2026-10-15T13:00:01.000000Z") == "settled"
        FA.usage_snapshot(c, _meter("2026-10-15T16:00:00Z", **{"tico-medium": "0.50", "tico-image": "0.60"}),
                          fetched_at=AT)
        FA.post_usage(c, "2026-10-15T16:05:00.000000Z")
        assert _events(c, ai) == []


def test_a_replaced_snapshot_assigns_nothing_twice(books):
    with books.transaction() as c:
        _turn(c, "coder", "2026-10-15T13:00:00.000000Z", 0.50)
        FA.usage_snapshot(c, _meter("2026-10-15T14:00:00Z", **{"tico-medium": "0.50"}), fetched_at=AT)
        FA.post_usage(c, "2026-10-15T14:05:00.000000Z")
        FA.usage_snapshot(c, _meter("2026-10-15T14:00:00Z", **{"tico-medium": "0.70"}),
                          fetched_at="2026-10-15T14:10:00.000000Z")
        FA.post_usage(c, "2026-10-15T14:15:00.000000Z")
        assert _usage_lines(c) == {("bot:coder", "5100"): 50, ("", "5100"): 20, ("", "1100"): -70}


def test_no_settled_hold_outlives_its_months_final_posting(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})
        # Claimed on Oct 31, still running at October's final posting, ended in November.
        late = FA.run_hold(c, "coder", "joct", at="2026-10-31T23:00:00.000000Z").hold_id
        FA.usage_snapshot(c, _meter("2026-11-04T00:00:00Z", **{"tico-medium": "0.50"}) | {"month": "2026-10"},
                          fetched_at="2026-11-04T00:00:00.000000Z")
        FA.post_usage(c, "2026-11-04T00:05:00.000000Z")
        run = _turn(c, "coder", "2026-11-05T00:00:00.000000Z", 0.30)
        FA.run_ended(c, "joct", run, "2026-11-05T00:00:01.000000Z")
        FA.usage_snapshot(c, _meter("2026-11-06T00:00:00Z", **{"tico-medium": "0.30"}) | {"month": "2026-11"},
                          fetched_at="2026-11-06T00:00:00.000000Z")
        FA.post_usage(c, "2026-11-06T00:05:00.000000Z")
        assert _hold(c, late)[1] is not None
    with books.transaction() as c:
        # Settled in September, but September's meter billed nothing: the final posting frees it.
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000, "effective_from": "2026-09-01"})
        lone = FA.run_hold(c, "coder", "jsep", at="2026-09-20T10:00:00.000000Z").hold_id
        run = _turn(c, "coder", "2026-09-20T11:00:00.000000Z", 0.20)
        FA.run_ended(c, "jsep", run, "2026-09-20T11:00:01.000000Z")
        FA.usage_snapshot(c, {"month": "2026-09", "rows": [], "as_of": "2026-10-04T00:00:00Z"},
                          fetched_at="2026-10-04T00:00:00.000000Z")
        FA.post_usage(c, "2026-10-04T00:05:00.000000Z")
        assert _hold(c, lone) == ("released", None)


def test_expiry_never_releases_a_live_runs_hold_past_its_page(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})
        _job(c, "live1", "running")
        _job(c, "live2", "leased")
        first = FA.run_hold(c, "coder", "live1", at="2026-10-15T10:00:00.000000Z").hold_id
        second = FA.run_hold(c, "coder", "live2", at="2026-10-15T11:00:00.000000Z").hold_id
        dead = FA.run_hold(c, "coder", "dead", at=AT).hold_id
        at = "2026-10-16T01:00:00.000000Z"
        assert FA.expire_holds(c, at, most=1) == 1
        assert [_hold(c, h)[0] for h in (first, second, dead)] == ["held", "held", "released"]


def test_a_claim_may_spend_what_is_left_of_the_bots_own_allocation(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 100})
        act(c, OWNER, "allocation_set", {"budget_id": ai, "target_kind": "teammate", "target_id": "bot:coder",
                                         "amount": 100})
        spend(c, "5100", 97, teammate="bot:coder")
        held = FA.run_hold(c, "coder", "job1", at=AT)
        assert not held.refused and held.hold_id
        assert c.execute("SELECT amount FROM fin_holds WHERE id=?", (held.hold_id,)).fetchone()[0] == 3


def test_the_requester_of_an_approved_action_needs_the_view_to_read_its_reason(books):
    with books.transaction() as c:
        soft = account(c, "6100")
        root = act(c, OWNER, "budget_create", {"name": "Software", "account_id": soft, "amount": 1_000,
                                               "at_limit": "decline"}).result["budget_id"]
        web = act(c, OWNER, "budget_create", {"name": "Software web", "account_id": soft, "team": "web",
                                              "parent_id": root, "amount": 500, "at_limit": "decline"}).result["budget_id"]
        grant = act(c, OWNER, "view_grant", {"subject": "human:cara", "what": "expenses"})
        asked = act(c, "human:cara", "allocation_set", {"budget_id": web, "target_kind": "teammate",
                                                        "target_id": "human:eve", "amount": 300})
        assert (asked.status, asked.approver) == ("needs_approval", "human:ben")
        spend(c, "6100", 900)
        H.approval_decide(c, "human:ben", asked.approval_id, "approved")
        failed = FA.approval_decided(c, asked.approval_id, AT)
        assert failed.status == "failed" and "100" in failed.reason
        act(c, OWNER, "view_revoke", {"grant_id": grant.result["grant_id"]})
        assert FR.action_for(c, "human:cara", asked.action_id)["reason"] == ""
        assert "100" in FR.action_for(c, "human:ben", asked.action_id)["reason"]


def test_a_run_billed_in_october_is_taken_in_by_octobers_posting_though_it_settled_in_november(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})
        _attempt(c, "jlate", "alate")
        held = FA.run_hold(c, "coder", "jlate", at="2026-10-31T23:00:00.000000Z").hold_id
        _turn(c, "coder", "2026-10-31T23:59:00.000000Z", 0.30, tid="alate")
        assert FA.run_ended(c, "jlate", "alate", "2026-11-01T00:01:00.000000Z") == "settled"
        FA.usage_snapshot(c, _meter("2026-11-01T00:30:00Z", **{"tico-medium": "0.30"}),
                          fetched_at="2026-11-01T00:30:00.000000Z")
        FA.post_usage(c, "2026-11-01T00:35:00.000000Z")
        assert _hold(c, held)[1] is not None


def test_an_unchanged_final_read_still_closes_the_month(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})
        held = FA.run_hold(c, "coder", "joct", at="2026-10-20T10:00:00.000000Z").hold_id
        run = _turn(c, "coder", "2026-10-20T11:00:00.000000Z", 0.20)
        FA.run_ended(c, "joct", run, "2026-10-20T11:00:01.000000Z")
        zero = {"month": "2026-10", "rows": [], "as_of": "2026-10-31T23:59:59Z"}
        assert FA.usage_snapshot(c, zero, fetched_at="2026-11-01T00:30:00.000000Z")
        FA.post_usage(c, "2026-11-01T00:35:00.000000Z")
        assert _hold(c, held) == ("settled", None)
        assert FA.usage_snapshot(c, zero, fetched_at="2026-11-04T00:30:00.000000Z") is None
        FA.post_usage(c, "2026-11-04T00:35:00.000000Z")
        assert _hold(c, held) == ("released", None)
        assert "2026-10" not in gateway_sync.due_months(c, "2026-11-05T12:00:00.000000Z")


def test_a_claim_fits_what_is_left_along_the_whole_budget_path(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 100})
        act(c, OWNER, "budget_create", {"name": "AI web", "account_id": account(c, "5100"), "team": "web",
                                        "parent_id": ai, "amount": 50, "at_limit": "decline"})
        spend(c, "5100", 47, team="web")
        spend(c, "5100", 51, team="sales")
        held = FA.run_hold(c, "coder", "job1", at=AT)        # web has 3 left, AI services 2
        assert not held.refused and held.hold_id
        assert c.execute("SELECT amount FROM fin_holds WHERE id=?", (held.hold_id,)).fetchone()[0] == 2


def test_a_quarter_budgets_gateway_cut_is_made_again_each_month(books):
    from backend.finance import jobs
    with books.transaction() as c:
        ai = budget(c, "AI services")
        c.execute("UPDATE fin_budgets SET period='quarter' WHERE id=?", (ai,))
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 100})
        spend(c, "5100", 100)
        assert jobs.cap_wanted(c, AT) == ("cut", "2026-10")
        assert jobs.cap_wanted(c, "2026-11-02T00:00:00Z") == ("cut", "2026-11")


def test_a_one_time_budget_at_its_cutoff_pauses_its_bots_with_no_end(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        c.execute("UPDATE fin_budgets SET period='once' WHERE id=?", (ai,))
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 100})
        spend(c, "5100", 100, day="2026-10-15")
        met = usage_limits.over(c, "coder", at=NOON)
        assert met["scope"] == "budget" and met["until"] == ""
        assert usage_limits.why(met) == "Paused: AI services budget reached"
        assert "used up; it starts again when a person raises it" in usage_limits.warning(c, "bot:coder", at=NOON)
