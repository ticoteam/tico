"""Budgets and holds: trees that never over-allocate, holds that reserve in the whole tree (seeded property test),
settle and release that never count twice, settlement that never fails for budget reasons, and two holds racing for
the last dollars."""
import random
import threading

from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, budget, finance_template, spend, store  # noqa: F401


def _tree(c, parent_amount=10_000, kids=(("web", 6_000), ("sales", 4_000))):
    """A Software budget with a child per team; the ids (parent, {team: child})."""
    soft = account(c, "6100")
    root = act(c, OWNER, "budget_create", {"name": "Software", "account_id": soft, "amount": parent_amount,
                                           "at_limit": "decline"})
    assert root.status == "committed", root
    pid = str(root.result["budget_id"])
    out = {}
    for team, amount in kids:
        body = {"name": f"Software {team}", "account_id": soft, "team": team, "parent_id": pid, "at_limit": "decline"}
        r = act(c, OWNER, "budget_create", body if amount is None else {**body, "amount": amount})
        assert r.status == "committed", r
        out[team] = str(r.result["budget_id"])
    return pid, out


def test_children_never_pass_the_parent_and_siblings_never_overlap(books):
    with books.transaction() as c:
        pid, kids = _tree(c)
        soft = account(c, "6100")
        over = act(c, OWNER, "budget_create", {"name": "More", "account_id": soft, "team": "eng", "parent_id": pid,
                                               "amount": 1})
        assert over.code == "invalid"           # eng contains web: overlap
        assert "overlaps" in over.reason or "total" in over.reason
        lower = act(c, OWNER, "budget_update", {"budget_id": pid, "amount": 9_999})
        assert lower.code == "invalid" and "children" in lower.reason
        grow = act(c, OWNER, "budget_update", {"budget_id": kids["web"], "amount": 6_001})
        assert grow.code == "invalid"
        assert act(c, OWNER, "budget_update", {"budget_id": kids["web"], "amount": 5_000}).status == "committed"
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "amount": 9_000}).status == "committed"
        assert FA.budget_for(c, account_id=soft, teammate="human:eve", at=AT) == kids["web"]
        assert FA.budget_for(c, account_id=soft, team="eng", at=AT) == pid
        assert FA.budget_for(c, account_id=account(c, "6900"), at=AT) == budget(c, "Other expenses")
        assert FA.budget_for(c, account_id=account(c, "1000"), at=AT) is None


def test_random_holds_never_let_children_pass_the_parent(books):
    rng = random.Random(7)
    with books.transaction() as c:
        pid, kids = _tree(c, 10_000, (("web", None), ("sales", 6_000)))   # web is bounded only by its parent
        live: dict[str, int] = {}
        allowed = refused = 0
        for n in range(200):
            roll = rng.random()
            if roll < 0.6 or not live:
                team = rng.choice(["web", "sales"])
                d = FA.hold_place(c, budget_scope=kids[team], amount=rng.randint(1, 2_500), currency="USD",
                                  source="run", source_id=f"r{n}", expires_at=None, at=AT)
                if d.allowed:
                    live[str(d.hold_id)] = int(c.execute("SELECT amount FROM fin_holds WHERE id=?",
                                                         (d.hold_id,)).fetchone()[0])
                    allowed += 1
                else:
                    assert d.action == "decline"
                    refused += 1
            elif roll < 0.8:
                hid = rng.choice(sorted(live))
                FA.hold_settle(c, hid, rng.randint(0, live.pop(hid)), at=AT)   # at most what was held
            else:
                hid = rng.choice(sorted(live))
                del live[hid]
                assert FA.hold_release(c, hid, at=AT)
            parent = FA.budget_state(c, pid, AT)
            children = [FA.budget_state(c, k, AT) for k in kids.values()]
            assert parent.held == sum(s.held for s in children)
            assert parent.held <= 10_000 and children[1].held <= 6_000
        assert allowed > 20 and refused > 20


def test_settle_and_release_never_count_twice(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        assert act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000}).status == "committed"
        d = FA.hold_place(c, budget_scope=ai, amount=400, currency="USD", source="run", source_id="r1",
                          expires_at=None, at=AT)
        again = FA.hold_place(c, budget_scope=ai, amount=400, currency="USD", source="run", source_id="r1",
                              expires_at=None, at=AT)
        assert d.allowed and again.hold_id == d.hold_id
        assert FA.budget_state(c, ai, AT).held == 400
        FA.hold_settle(c, str(d.hold_id), 300, at=AT)
        assert not FA.hold_release(c, str(d.hold_id), at=AT)
        assert (FA.budget_state(c, ai, AT).held, FA.budget_state(c, ai, AT).actual) == (300, 0)
        posted = spend(c, "5100", 300, source_id="day-1")
        FA.hold_settle(c, str(d.hold_id), 300, entry_id=str(posted["entry_id"]), at=AT)
        s = FA.budget_state(c, ai, AT)
        assert (s.held, s.actual, s.remaining) == (0, 300, 700)


def test_settlement_past_the_budget_is_accepted_and_shows_as_over(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})
        d = FA.hold_place(c, budget_scope={"account_id": account(c, "5100"), "teammate": "bot:coder"}, amount=900,
                          currency="USD", source="run", source_id="big", expires_at=None, at=AT)
        assert d.allowed and d.budget_id == ai
        assert FA.hold_settle(c, str(d.hold_id), 2_500, at=AT)
        spend(c, "5100", 400)
        s = FA.budget_state(c, ai, AT)
        assert s.remaining == 1_000 - 2_500 - 400 and s.at_limit
        refused = FA.hold_place(c, budget_scope=ai, amount=1, currency="USD", source="run", source_id="next",
                                expires_at=None, at=AT)
        assert not refused.allowed and refused.action == "decline" and refused.limit_budget_id == ai
        names = {r[0] for r in c.execute("SELECT name FROM fin_events WHERE subject=?", (ai,))}
        assert names == {"finance.budget.warn", "finance.budget.limit"}


def test_expired_holds_release_and_other_expenses_asks(books):
    with books.transaction() as c:
        other = budget(c, "Other expenses")
        act(c, OWNER, "budget_update", {"budget_id": other, "amount": 500})
        d = FA.hold_place(c, budget_scope={"account_id": account(c, "6900")}, amount=500, currency="USD",
                          source="run", source_id="x", expires_at="2026-10-15T13:00:00Z", at=AT)
        assert d.allowed and d.budget_id == other
        ask = FA.hold_place(c, budget_scope=other, amount=1, currency="USD", source="run", source_id="y",
                            expires_at=None, at=AT)
        assert (ask.allowed, ask.action) == (False, "ask")
        assert FA.holds_expire(c, "2026-10-15T14:00:00Z") == 1
        assert FA.budget_state(c, other, AT).held == 0


def test_allocations_set_aside_and_rollover_carries_unused(books):
    with books.transaction() as c:
        pid, kids = _tree(c, 10_000, (("web", 10_000),))
        assert act(c, OWNER, "allocation_set", {"budget_id": kids["web"], "target_kind": "teammate",
                                                 "target_id": "bot:coder", "amount": 4_000}).status == "committed"
        assert FA.budget_state(c, pid, AT).remaining == 6_000
        too_much = act(c, OWNER, "allocation_set", {"budget_id": kids["web"], "target_kind": "teammate",
                                                     "target_id": "human:eve", "amount": 6_001})
        assert too_much.code == "invalid"
        mine = FA.hold_place(c, budget_scope={"account_id": account(c, "6100"), "teammate": "bot:coder"},
                             amount=9_000, currency="USD", source="run", source_id="coder", expires_at=None, at=AT)
        assert mine.allowed                       # its own 4,000 plus the 6,000 free
        other = FA.hold_place(c, budget_scope={"account_id": account(c, "6100"), "teammate": "human:eve"},
                              amount=1_001, currency="USD", source="run", source_id="eve", expires_at=None, at=AT)
        assert not other.allowed
        roll = act(c, OWNER, "budget_create", {"name": "Video roll", "account_id": account(c, "5200"),
                                               "amount": 1_000, "rollover": "unused", "parent_id": None,
                                               "effective_from": "2026-08-01"})
        assert roll.code == "invalid"             # Video production already has a root budget


def test_budget_vs_actual_prorates_a_monthly_budget_over_weeks(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 31_000, "effective_from": "2026-10-01"})
        spend(c, "5100", 1_200, day="2026-10-06")
        out = FR.budget_vs_actual(c, OWNER, "2026-10-05", "2026-10-11", "week", at=AT)
        row = next(r for r in out["budgets"] if r["id"] == ai)
        assert row["budget"] == [7_000] and row["actual"] == [1_200]


def test_two_holds_racing_for_the_last_dollars(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})
    barrier = threading.Barrier(2)
    results: list[FA.HoldDecision] = []

    def claim(n: int) -> None:
        barrier.wait()
        with books.transaction() as c:
            results.append(FA.hold_place(c, budget_scope=ai, amount=600, currency="USD", source="run",
                                         source_id=f"race{n}", expires_at=None, at=AT))
    threads = [threading.Thread(target=claim, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(r.allowed for r in results) == [False, True]
    with books.read() as c:
        assert FA.budget_state(c, ai, AT).held == 600
