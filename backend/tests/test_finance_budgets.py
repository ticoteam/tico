"""Budgets and holds: trees that never over-allocate, holds that reserve in the whole tree (seeded property test),
settle and release that never count twice, settlement that never fails for budget reasons, and two holds racing for
the last dollars."""
import random
import threading

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance.store import budgets as SB
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, budget, finance_template, spend, store  # noqa: F401


def _tree(c, parent_amount=10_000, kids=(("web", 6_000), ("sales", 4_000)), under=None):
    """A Software budget with a child per team, under `under` if given; the ids (parent, {team: child})."""
    soft = account(c, "6100")
    root = act(c, OWNER, "budget_create", {"name": "Software", "account_id": soft, "amount": parent_amount,
                                           "at_limit": "decline", "parent_id": under})
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
        assert act(c, OWNER, "budget_update", {"budget_id": kids["web"], "amount": 5_000}).status == "committed"
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "amount": 9_000}).status == "committed"
        assert FA.budget_for(c, account_id=soft, teammate="human:eve", at=AT) == kids["web"]
        assert FA.budget_for(c, account_id=soft, team="eng", at=AT) == pid
        assert FA.budget_for(c, account_id=account(c, "6900"), at=AT) == budget(c, "Other expenses")
        assert FA.budget_for(c, account_id=account(c, "1000"), at=AT) is None


def test_a_child_that_would_pass_its_parent_asks_the_parent_owner(books):
    with books.transaction() as c:
        pid, kids = _tree(c)
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "owner": "human:ben"}).status == "committed"
        grow = act(c, OWNER, "budget_update", {"budget_id": kids["web"], "amount": 6_001})
        assert (grow.status, grow.approver) == ("needs_approval", "human:ben")
        H.approval_decide(c, "human:ben", grow.approval_id, "approved")
        done = FA.approval_decided(c, grow.approval_id, AT)
        assert done is not None and done.status == "committed"
        web = SB.versions(c, [kids["web"]])[kids["web"]]
        assert max(web, key=lambda v: v.version).amount == 6_001


def _latest(c, budget_id):
    """The version in force last: the highest (effective day, version)."""
    return max(SB.versions(c, [budget_id])[budget_id], key=lambda v: (v.effective_from, v.version))


def test_approving_a_child_that_passes_its_parent_raises_the_parent_by_the_excess(books):
    with books.transaction() as c:
        pid, kids = _tree(c)
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "owner": "human:ben"}).status == "committed"
        change = {"budget_id": kids["web"], "amount": 6_500}          # 500 over the parent's 10,000 with sales at 4,000
        preview = act(c, OWNER, "budget_update", change, dry_run=True)
        assert (preview.status, preview.approver) == ("needs_approval", "human:ben")
        summary = str(preview.impact["summary"])
        assert "Software web" in summary and "raise Software" in summary
        grow = act(c, OWNER, "budget_update", change)
        assert (grow.status, grow.approver) == ("needs_approval", "human:ben")
        H.approval_decide(c, "human:ben", grow.approval_id, "approved")
        done = FA.approval_decided(c, grow.approval_id, AT)
        assert done is not None and done.status == "committed"
        assert (_latest(c, kids["web"]).amount, _latest(c, pid).amount) == (6_500, 10_500)
        assert _latest(c, pid).effective_from == _latest(c, kids["web"]).effective_from


def test_a_declined_raise_changes_neither_budget(books):
    with books.transaction() as c:
        pid, kids = _tree(c)
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "owner": "human:ben"}).status == "committed"
        grow = act(c, OWNER, "budget_update", {"budget_id": kids["web"], "amount": 6_500})
        H.approval_decide(c, "human:ben", grow.approval_id, "declined")
        done = FA.approval_decided(c, grow.approval_id, AT)
        assert done is not None and done.status == "denied"
        assert (_latest(c, kids["web"]).amount, _latest(c, pid).amount) == (6_000, 10_000)


def test_a_raise_that_would_pass_the_grandparent_waits_for_that_raise(books):
    with books.transaction() as c:
        company = act(c, OWNER, "budget_create", {"name": "Company", "account_id": account(c, "6000"),
                                                  "amount": 10_000, "at_limit": "decline"})
        assert company.status == "committed"
        cid = str(company.result["budget_id"])
        pid, kids = _tree(c, under=cid)
        refused = act(c, OWNER, "budget_update", {"budget_id": kids["web"], "amount": 6_001})
        assert refused.status == "failed" and "raise Company first" in refused.reason
        assert _latest(c, kids["web"]).amount == 6_000 and _latest(c, pid).amount == 10_000
        assert act(c, OWNER, "budget_update", {"budget_id": cid, "amount": 10_001}).status == "committed"
        # Software's owner is OWNER, so the change is theirs to make and it commits with the raise.
        assert act(c, OWNER, "budget_update", {"budget_id": kids["web"], "amount": 6_001}).status == "committed"
        assert (_latest(c, kids["web"]).amount, _latest(c, pid).amount) == (6_001, 10_001)


def test_a_new_child_that_passes_its_parent_asks_the_parent_owner_and_raises_it(books):
    with books.transaction() as c:
        pid, kids = _tree(c, kids=(("web", 6_000),))
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "owner": "human:ben"}).status == "committed"
        body = {"name": "Software sales", "account_id": account(c, "6100"), "team": "sales", "parent_id": pid,
                "amount": 4_500}
        ask = act(c, OWNER, "budget_create", body)
        assert (ask.status, ask.approver) == ("needs_approval", "human:ben")
        H.approval_decide(c, "human:ben", ask.approval_id, "approved")
        done = FA.approval_decided(c, ask.approval_id, AT)
        assert done is not None and done.status == "committed"
        sales = str(done.result["budget_id"])
        assert _latest(c, sales).amount == 4_500 and _latest(c, pid).amount == 10_500


def test_a_raise_asked_for_one_total_is_asked_again_when_the_total_has_moved(books):
    with books.transaction() as c:
        pid, kids = _tree(c)
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "owner": "human:ben"}).status == "committed"
        grow = act(c, OWNER, "budget_update", {"budget_id": kids["web"], "amount": 6_500})
        assert grow.status == "needs_approval"
        # Sales come down while the approval waits, so web at 6,500 fits without a raise and the one asked for is void.
        assert act(c, OWNER, "budget_update", {"budget_id": kids["sales"], "amount": 3_000}).status == "committed"
        H.approval_decide(c, "human:ben", grow.approval_id, "approved")
        done = FA.approval_decided(c, grow.approval_id, AT)
        assert done is not None and done.status == "needs_approval"
        assert (_latest(c, kids["web"]).amount, _latest(c, pid).amount) == (6_000, 10_000)


def test_random_holds_never_let_children_pass_the_parent(books):
    rng = random.Random(7)
    with books.transaction() as c:
        pid, kids = _tree(c, 10_000, (("web", None), ("sales", 6_000)))   # web is bounded only by its parent
        live: dict[str, int] = {}   # in the order placed: hold ids are random, so the seed alone fixes the run
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
                hid = rng.choice(list(live))
                FA.hold_settle(c, hid, rng.randint(0, live.pop(hid)), at=AT)   # at most what was held
            else:
                hid = rng.choice(list(live))
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
        assert names == {"finance.budget.warn", "finance.budget.cutoff"}


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


def test_impacts_and_errors_show_only_numbers_the_actor_may_see(books):
    with books.transaction() as c:
        pid, kids = _tree(c, 10_000, (("eng", 6_000), ("sales", None)))
        spend(c, "6100", 5_000, team="sales")             # Software has 5,000 left; Engineering 6,000
        for right in ("finance.book", "finance.budgets"):
            act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": right})
        act(c, OWNER, "view_grant", {"subject": "human:ben", "what": "bs_lines", "account_ids": [account(c, "1000")]})
        dry = act(c, "human:ben", "journal", {"date": "2026-10-03", "lines": [
            {"account_id": account(c, "6100"), "amount": 100, "tags": {"team": "web"}},
            {"account_id": account(c, "1000"), "amount": -100}]}, dry_run=True)
        assert [b["budget_id"] for b in dry.impact["budgets"]] == [kids["eng"]]       # not company-wide Software
        over = act(c, "human:ben", "allocation_set", {"budget_id": kids["eng"], "target_kind": "teammate",
                                                      "target_id": "bot:coder", "amount": 5_500})
        assert over.code == "invalid" and not any(ch.isdigit() for ch in over.reason)
        assert "5000" in act(c, OWNER, "allocation_set", {"budget_id": kids["eng"], "target_kind": "teammate",
                                                         "target_id": "bot:coder", "amount": 5_500}).reason


def test_an_allocation_serves_only_spend_on_its_own_branch(books):
    with books.transaction() as c:
        pid, kids = _tree(c, 10_000, (("web", 5_000), ("sales", 5_000)))
        act(c, OWNER, "allocation_set", {"budget_id": kids["sales"], "target_kind": "teammate",
                                         "target_id": "bot:coder", "amount": 3_000})
        spend(c, "6100", 3_000, team="sales")             # Software: 10,000 - 3,000 spent - 3,000 set aside
        d = FA.hold_place(c, budget_scope=kids["web"], amount=4_500, currency="USD", source="card", source_id="w1",
                          expires_at=None, target="teammate:bot:coder", at=AT)
        assert not d.allowed and d.limit_budget_id == pid


def test_a_settled_hold_draws_its_allocation_down_once(books):
    with books.transaction() as c:
        soft = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 100})
        bid = str(soft.result["budget_id"])
        act(c, OWNER, "allocation_set", {"budget_id": bid, "target_kind": "teammate", "target_id": "bot:coder", "amount": 100})
        d = FA.hold_place(c, budget_scope=bid, amount=100, currency="USD", source="card", source_id="t1",
                          expires_at=None, target="teammate:bot:coder", at=AT)
        assert d.allowed
        FA.post_entry(c, {"date": "2026-10-10", "source": "import", "source_id": "t1", "lines": [
            {"account_id": account(c, "6100"), "amount": 100, "tags": {"teammate": "bot:coder"}}, {"account_id": account(c, "1000"), "amount": -100}]},
            hold_ids=[str(d.hold_id)], at=AT)
        s = FA.budget_state(c, bid, AT)
        assert (s.actual, s.held, s.remaining) == (100, 0, 0)


def test_rollover_carries_from_the_first_version(books):
    with books.transaction() as c:
        r = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 1_000,
                                            "rollover": "unused", "effective_from": "2025-01-01"})
        assert r.status == "committed", r
        assert FA.budget_state(c, str(r.result["budget_id"]), AT).amount == 22_000   # Oct 2026 + 21 months unused


def test_asking_again_for_a_source_resizes_its_hold_or_says_why_not(books):
    with books.transaction() as c:
        ai = budget(c, "AI services")
        act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 1_000})

        def again(amount):
            return FA.hold_place(c, budget_scope=ai, amount=amount, currency="USD", source="run", source_id="r1",
                                 expires_at=None, at=AT)
        first = again(400)
        grown = again(900)
        assert grown.allowed and grown.hold_id == first.hold_id and "900" in grown.reason
        refused = again(1_200)
        assert not refused.allowed and refused.hold_id == first.hold_id and "kept" in refused.reason
        assert FA.budget_state(c, ai, AT).held == 900
        assert again(300).allowed and FA.budget_state(c, ai, AT).held == 300


def test_period_hold_sums_search_the_budget_index_by_period(books):
    from backend.finance.store import budgets as SB
    seen: list[str] = []
    with books.transaction() as c:
        ai = budget(c, "AI services")
        c.set_trace_callback(seen.append)
        SB.held(c, [ai], "2026-10-01", "2026-10-31")
        SB.held(c, [ai], "2026-10-01", "2026-10-31", target="teammate:bot:coder")
        SB.target_used(c, [ai], "2026-10-01", "2026-10-31", "teammate:bot:coder", absorbed=True)
        c.set_trace_callback(None)
        sums = [q for q in seen if "FROM fin_holds" in q]
        assert len(sums) == 3
        for q in sums:
            plan = " ".join(str(r[3]) for r in c.execute("EXPLAIN QUERY PLAN " + q))
            assert "fin_holds_budget (budget_id=? AND status=? AND held_on>? AND held_on<?)" in plan, plan
