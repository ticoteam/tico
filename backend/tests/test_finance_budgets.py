"""Budgets and holds: trees that never over-allocate, holds that reserve in the whole tree (seeded property test),
settle and release that never count twice, settlement that never fails for budget reasons, and two holds racing for
the last dollars."""
import json
import random
import threading
from dataclasses import replace
from datetime import date

import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import budgeting
from backend.finance import reads as FR
from backend.finance.context import load
from backend.finance.core import budgets as B
from backend.finance.core.errors import FinanceError
from backend.finance.store import budgets as SB
from backend.finance.store import ledger as SL
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


def _sees_parent(c, who: str) -> None:
    """Give `who` the company's expenses, so the parent budget's figures are true for them. A change that passes the
    parent then asks the parent's owner (§4.4). Without the view, figure_route hides the fit and the install owner
    decides every amount."""
    assert act(c, OWNER, "view_grant", {"subject": who, "what": "expenses", "whose": "company"}).status == "committed"


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


def test_a_parent_raise_asks_the_parent_owner_over_the_asker_s_manager(books):
    # Cara lacks finance.budgets, so her manager (Ben) would decide the change; the raise it needs is Dan's to approve.
    with books.transaction() as c:
        pid, kids = _tree(c)
        _sees_parent(c, "human:cara")
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "owner": "human:dan"}).status == "committed"
        grow = act(c, "human:cara", "budget_update", {"budget_id": kids["web"], "amount": 6_500})
        assert (grow.status, grow.approver) == ("needs_approval", "human:dan")
        H.approval_decide(c, "human:dan", grow.approval_id, "approved")
        done = FA.approval_decided(c, grow.approval_id, AT)
        assert done is not None and done.status == "committed"
        assert (_latest(c, kids["web"]).amount, _latest(c, pid).amount) == (6_500, 10_500)


def test_a_requester_who_owns_the_parent_is_asked_their_manager_not_themselves(books):
    # Cara owns Software and lacks finance.budgets: she cannot approve her own raise, so Ben (her manager) decides it.
    with books.transaction() as c:
        pid, kids = _tree(c)
        _sees_parent(c, "human:cara")
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "owner": "human:cara"}).status == "committed"
        grow = act(c, "human:cara", "budget_update", {"budget_id": kids["web"], "amount": 6_500})
        assert (grow.status, grow.approver) == ("needs_approval", "human:ben")
        H.approval_decide(c, "human:ben", grow.approval_id, "approved")
        done = FA.approval_decided(c, grow.approval_id, AT)
        assert done is not None and done.status == "committed"
        assert (_latest(c, kids["web"]).amount, _latest(c, pid).amount) == (6_500, 10_500)


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


def test_a_raise_asked_for_one_total_is_judged_again_when_the_total_has_moved(books):
    with books.transaction() as c:
        pid, kids = _tree(c)
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "owner": "human:ben"}).status == "committed"
        grow = act(c, OWNER, "budget_update", {"budget_id": kids["web"], "amount": 6_500})
        assert grow.status == "needs_approval"
        # Sales come down while the approval waits, so web at 6,500 fits without a raise: nobody needs to decide it.
        assert act(c, OWNER, "budget_update", {"budget_id": kids["sales"], "amount": 3_000}).status == "committed"
        H.approval_decide(c, "human:ben", grow.approval_id, "approved")
        done = FA.approval_decided(c, grow.approval_id, AT)
        assert done is not None and done.status == "committed"
        assert (_latest(c, kids["web"]).amount, _latest(c, pid).amount) == (6_500, 10_000)


def test_a_raise_that_no_longer_fits_is_asked_again_of_the_manager_not_the_parent_owner(books):
    with books.transaction() as c:
        pid, kids = _tree(c)
        _sees_parent(c, "human:cara")
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "owner": "human:dan"}).status == "committed"
        grow = act(c, "human:cara", "budget_update", {"budget_id": kids["web"], "amount": 6_500})
        assert (grow.status, grow.approver) == ("needs_approval", "human:dan")
        assert act(c, OWNER, "budget_update", {"budget_id": kids["sales"], "amount": 3_000}).status == "committed"
        H.approval_decide(c, "human:dan", grow.approval_id, "approved")
        # No raise is needed now, so Dan's approval is void: Cara's manager decides the change itself.
        again = FA.approval_decided(c, grow.approval_id, AT)
        assert (again.status, again.approver) == ("needs_approval", "human:ben")
        H.approval_decide(c, "human:ben", again.approval_id, "approved")
        done = FA.approval_decided(c, again.approval_id, AT)
        assert done is not None and done.status == "committed"
        assert (_latest(c, kids["web"]).amount, _latest(c, pid).amount) == (6_500, 10_000)


def test_a_raise_that_grows_while_it_waits_is_asked_again_for_the_larger_raise(books):
    with books.transaction() as c:
        pid, kids = _tree(c)
        _sees_parent(c, "human:cara")
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "owner": "human:dan"}).status == "committed"
        web = act(c, "human:cara", "budget_update", {"budget_id": kids["web"], "amount": 6_500})
        assert (web.status, web.approver) == ("needs_approval", "human:dan")
        sales = act(c, OWNER, "budget_update", {"budget_id": kids["sales"], "amount": 4_300})
        assert (sales.status, sales.approver) == ("needs_approval", "human:dan")
        H.approval_decide(c, "human:dan", sales.approval_id, "approved")
        assert FA.approval_decided(c, sales.approval_id, AT).status == "committed"     # parent 10,300
        H.approval_decide(c, "human:dan", web.approval_id, "approved")
        # Sales grew while this waited, so web at 6,500 now needs the parent at 10,800, not the 10,500 asked for.
        again = FA.approval_decided(c, web.approval_id, AT)
        assert (again.status, again.approver) == ("needs_approval", "human:dan")
        H.approval_decide(c, "human:dan", again.approval_id, "approved")
        done = FA.approval_decided(c, again.approval_id, AT)
        assert done is not None and done.status == "committed"
        assert (_latest(c, kids["web"]).amount, _latest(c, kids["sales"]).amount, _latest(c, pid).amount) == \
            (6_500, 4_300, 10_800)


def test_a_scoped_requesters_approval_never_carries_the_raise_amount(books):
    with books.transaction() as c:
        pid, kids = _tree(c)
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "owner": "human:dan"}).status == "committed"
        grow = act(c, "human:cara", "budget_update", {"budget_id": kids["web"], "amount": 6_500})
        assert grow.status == "needs_approval"
        # The raise is 10,500, and 10,500 less Cara's 6,500 would show sales' 4,000. Neither she nor Dan's view holds it.
        payload = json.dumps(H.approval(c, grow.approval_id)["payload"])
        assert "raise_to" not in payload and "raise_check" not in payload
        assert "10500" not in payload and "105.00" not in payload


def test_a_grandparent_refusal_names_no_budget_a_scoped_asker_cannot_see(books):
    with books.transaction() as c:
        company = act(c, OWNER, "budget_create", {"name": "Company", "account_id": account(c, "6000"),
                                                  "amount": 10_000, "at_limit": "decline"})
        pid, kids = _tree(c, under=str(company.result["budget_id"]))
        # Company is outside Cara's view, so her change gets the one outcome for any amount (no refusal that names it).
        asked = act(c, "human:cara", "budget_update", {"budget_id": kids["web"], "amount": 6_500})
        assert asked.status == "needs_approval" and "Company" not in repr(asked)
        owner = act(c, OWNER, "budget_update", {"budget_id": kids["web"], "amount": 6_500})
        assert owner.status == "failed" and "raise Company first" in owner.reason


def test_a_manager_gets_one_outcome_for_every_set_aside_under_a_parent_they_cannot_see(books):
    """Web (100.00) is Cara's under Software (100.00), which covers Sales too and which she cannot see. Sales has spent
    40.00 on Software's line, so Software has 60.00 left for the owner. Her set-asides of 60.00 and 60.01 used to differ:
    the first fit and the second was refused, which gave Software's 60.00 away. Now both wait for an approver with no
    amount, and the owner still sees the refusal of 60.01."""
    with books.transaction() as c:
        pid, kids = _tree(c, parent_amount=10_000, kids=(("web", 10_000),))
        spend(c, "6100", 4_000, team="sales")
        assert act(c, OWNER, "right_grant", {"teammate": "human:cara", "right": "finance.budgets"}).status == "committed"
        ask = {"budget_id": kids["web"], "target_kind": "gateway_key", "target_id": "gw-1"}
        six = act(c, "human:cara", "allocation_set", {**ask, "amount": 6_000}, "dry-6000", dry_run=True)
        seven = act(c, "human:cara", "allocation_set", {**ask, "amount": 6_001}, "dry-6001", dry_run=True)
        owner_six = act(c, OWNER, "allocation_set", {**ask, "amount": 6_000}, "owner-6000", dry_run=True)
        owner_seven = act(c, OWNER, "allocation_set", {**ask, "amount": 6_001}, "owner-6001", dry_run=True)
    assert (six.status, seven.status) == ("needs_approval", "needs_approval"), (six, seven)
    assert (six.approver, seven.approver) == (OWNER, OWNER)
    assert owner_six.status == "committed"
    assert (owner_seven.code, owner_seven.reason) == ("invalid", "it does not fit: Software has 6000 left")


def test_a_grandparent_hidden_by_private_lines_is_not_named_when_the_rest_is_visible(books):
    with books.transaction() as c:
        pay = str(act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                                   "parent_id": account(c, "6000")}).result["account_id"])
        assert act(c, OWNER, "private_lines_set", {"account_id": pay, "others_see": "totals"}).status == "committed"
        company = act(c, OWNER, "budget_create", {"name": "Company", "account_id": account(c, "6000"),
                                                  "amount": 10_000, "at_limit": "decline"})
        _, kids = _tree(c, under=str(company.result["budget_id"]))
        assert act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "everything"}).status == "committed"
        # Dan sees Software and its teams, but Company covers the private payroll line, so its name stays out.
        refused = act(c, "human:dan", "budget_update", {"budget_id": kids["web"], "amount": 6_001})
        assert refused.status == "failed" and refused.reason == "it does not fit: not enough left"
        owner = act(c, OWNER, "budget_update", {"budget_id": kids["web"], "amount": 6_001})
        assert owner.status == "failed" and "raise Company first" in owner.reason


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
        # Software is outside Ben's view, so his set-aside over what Software has left is asked, with no amount.
        over = act(c, "human:ben", "allocation_set", {"budget_id": kids["eng"], "target_kind": "teammate",
                                                      "target_id": "bot:coder", "amount": 5_500})
        assert over.status == "needs_approval" and over.approver == OWNER
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


def test_an_overlap_refusal_names_a_sibling_only_where_the_asker_may_see_it(books):
    with books.transaction() as c:
        acct = account(c, "6100")
        tools = {"name": "Tools", "account_id": acct, "amount": 50_000}
        root = str(act(c, OWNER, "budget_create", tools).result["budget_id"])
        eng = str(act(c, OWNER, "budget_create", {"name": "Eng tools", "account_id": acct, "team": "eng",
                                                  "parent_id": root, "amount": 10_000}).result["budget_id"])
        ctx = load(c, AT)
        budgets = ctx.budgets()
        twin = replace(budgets[eng], id="twin", name="Twin")          # the same scope under the same parent
        with pytest.raises(FinanceError) as hidden:
            B.check(twin, budgets, ctx.accounts, ctx.org)
        assert hidden.value.message == "it overlaps another budget"
        with pytest.raises(FinanceError) as shown:
            B.check(twin, budgets, ctx.accounts, ctx.org, seen=lambda b: True)
        assert shown.value.message == "it overlaps budget Eng tools"


def test_a_fit_refusal_keeps_no_amounts_and_a_replay_gives_the_viewer_its_sentence_back(books):
    with books.transaction() as c:
        acct = account(c, "6100")
        tools = {"name": "Tools", "account_id": acct, "amount": 50_000}
        root = str(act(c, OWNER, "budget_create", tools).result["budget_id"])
        eng = str(act(c, OWNER, "budget_create", {"name": "Eng tools", "account_id": acct, "team": "eng",
                                                  "parent_id": root, "amount": 10_000}).result["budget_id"])
        # A child under Eng tools: lowering Eng tools below it is refused outright (it is not routed to an owner).
        act(c, OWNER, "budget_create", {"name": "Web tools", "account_id": acct, "team": "web",
                                        "parent_id": eng, "amount": 6_000})
        refused = act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 5_000}, key="too-small")
        assert refused.status == "failed" and "Eng tools" in refused.reason and "6000" in refused.reason, refused
        stored = c.execute("SELECT reason FROM fin_actions WHERE id=?", (refused.action_id,)).fetchone()[0]
        assert stored == "it does not fit: not enough left"
        replay = act(c, OWNER, "budget_update", {"budget_id": eng, "amount": 5_000}, key="too-small")
        assert replay.replayed and replay.reason == refused.reason


def test_a_rule_that_names_the_finance_approver_never_lets_them_approve_their_own_raise(books):
    # Cara owns Software and is the finance approver; a rule asks a finance admin for every budget change. Her raise
    # goes to the owner, the finance admin who is not the requester, and nothing moves until the owner decides.
    with books.transaction() as c:
        pid, kids = _tree(c)
        _sees_parent(c, "human:cara")
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "owner": "human:cara"}).status == "committed"
        FA.setup(c, OWNER, AT, finance_approver="human:cara")
        rule = act(c, OWNER, "rule_create", {"name": "Budget changes", "when": {"kind": "budget_update"},
                                             "outcome": {"outcome": "needs_approval", "who": "finance_admin"},
                                             "examples": [{"input": {"kind": "budget_update"},
                                                           "expect": "needs_approval"}]})
        assert rule.status == "committed", rule
        grow = act(c, "human:cara", "budget_update", {"budget_id": kids["web"], "amount": 6_500})
        assert (grow.status, grow.approver) == ("needs_approval", OWNER)
        assert (_latest(c, kids["web"]).amount, _latest(c, pid).amount) == (6_000, 10_000)
        H.approval_decide(c, OWNER, grow.approval_id, "approved")
        done = FA.approval_decided(c, grow.approval_id, AT)
        assert done is not None and done.status == "committed"
        assert (_latest(c, kids["web"]).amount, _latest(c, pid).amount) == (6_500, 10_500)


def test_a_rule_test_never_reads_the_fingerprint_of_a_hidden_raise(books):
    # The stored budget change carries its parent-raise fingerprint; a probe rule over it must not see it.
    with books.transaction() as c:
        pid, kids = _tree(c)
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "owner": "human:ben"}).status == "committed"
        asked = act(c, OWNER, "budget_update", {"budget_id": kids["web"], "amount": 6_500})
        assert asked.status == "needs_approval"
        stored = c.execute("SELECT payload_json FROM fin_actions WHERE id=?", (asked.action_id,)).fetchone()[0]
        assert "raise_check" in stored
        probe = act(c, OWNER, "rule_test", {"when": {"raise_check": {"matches": "^"}}, "outcome": {"outcome": "deny"}},
                    dry_run=True)
        assert probe.status == "committed", probe
        assert probe.impact["test"]["changed_count"] == 0


def test_a_rule_test_lists_only_the_actions_a_draft_would_change(books):
    """An action a rule already denied is not a change when the draft denies it too; a committed one is. A limit that
    still fits leaves a committed action committed, so it is not a change either."""
    with books.transaction() as c:
        journal = {"date": "2026-10-15", "memo": "rule test", "lines": [
            {"account_id": account(c, "6900"), "amount": 500}, {"account_id": account(c, "1000"), "amount": -500}]}
        kept = act(c, OWNER, "journal", journal)
        assert kept.status == "committed", kept
        frozen = act(c, OWNER, "rule_create", {"kind": "gate", "name": "Frozen journals", "when": {"kind": "journal"},
                                               "outcome": {"outcome": "deny", "reason": "frozen"},
                                               "examples": [{"input": {"kind": "journal"}, "expect": "deny"}]})
        assert frozen.status == "committed", frozen
        refused = act(c, OWNER, "journal", journal)
        assert refused.status == "denied", refused
        deny = {"when": {"kind": "journal"}, "outcome": {"outcome": "deny", "reason": "test"}}
        tested = act(c, OWNER, "rule_test", deny, dry_run=True).impact["test"]
        changed = {x.get("action_id") for x in tested["changed"]}
        assert kept.action_id in changed and refused.action_id not in changed, tested
        fits = {"when": {"kind": "journal"}, "outcome": {"outcome": "limit", "limit": 10_000_000, "per": "action"}}
        fit = act(c, OWNER, "rule_test", fits, dry_run=True).impact["test"]
        assert kept.action_id not in {x.get("action_id") for x in fit["changed"]}, fit


def test_a_set_aside_adds_only_the_part_its_target_does_not_already_hold(books):
    """A live 4,000 hold for a Gateway key already counts against a 10,000 budget, so setting that key aside at 7,000 adds
    3,000 to the reserve (4,000 held and 3,000 unspent), not the whole 7,000. Totals up to 10,000 fit; 10,001 does not."""
    with books.transaction() as c:
        bid = str(act(c, OWNER, "budget_create", {"name": "Gateway", "account_id": account(c, "6900"),
                                                  "amount": 10_000}).result["budget_id"])
        assert FA.hold_place(c, budget_scope=bid, amount=4_000, currency="USD", source="run", source_id="gw-run",
                             expires_at=None, target="gateway_key:gw-1", at=AT).allowed
        ask = {"budget_id": bid, "target_kind": "gateway_key", "target_id": "gw-1"}
        assert act(c, OWNER, "allocation_set", {**ask, "amount": 7_000}).status == "committed"
        s = FA.budget_state(c, bid, AT)
        assert (s.held, s.remaining) == (7_000, 3_000), s
        assert act(c, OWNER, "allocation_set", {**ask, "amount": 10_000}, dry_run=True).status == "committed"
        over = act(c, OWNER, "allocation_set", {**ask, "amount": 10_001}, dry_run=True)
        assert over.code == "invalid" and "3000 left" in over.reason, over


def test_a_set_aside_on_a_parent_counts_a_child_hold_once(books):
    """A 4,000 hold for a Gateway key sits on a child of a 10,000 budget. Setting the key aside on the parent at 7,000
    reserves 3,000 more there, since the child's hold is already in the parent's use: the parent holds 7,000 in all."""
    with books.transaction() as c:
        pid, kids = _tree(c, 10_000, (("web", 5_000),))
        assert FA.hold_place(c, budget_scope=kids["web"], amount=4_000, currency="USD", source="run", source_id="gw-run",
                             expires_at=None, target="gateway_key:gw-1", at=AT).allowed
        ask = {"budget_id": pid, "target_kind": "gateway_key", "target_id": "gw-1"}
        assert act(c, OWNER, "allocation_set", {**ask, "amount": 7_000}).status == "committed"
        s = FA.budget_state(c, pid, AT)
        assert (s.held, s.remaining) == (7_000, 3_000), s
        over = act(c, OWNER, "allocation_set", {**ask, "amount": 10_001}, dry_run=True)
        assert over.code == "invalid" and "3000 left" in over.reason, over


def test_one_expense_draws_one_allocation_when_a_child_is_replaced(books):
    """Eve has 5,000 set aside on Software's Web child; that child ends and a replacement Web child sets aside 5,000 for her
    too. Her 5,000 Web expense is one spend, so it draws one of the two set-asides: Software has 0 left (10,000 = 5,000
    spent + 5,000 set aside). It used to draw both, and Software showed 5,000 left."""
    with books.transaction() as c:
        pid, kids = _tree(c, 10_000, (("web", 5_000),))
        assert act(c, OWNER, "allocation_set", {"budget_id": kids["web"], "target_kind": "teammate",
                                                 "target_id": "human:eve", "amount": 5_000}).status == "committed"
        assert act(c, OWNER, "budget_update", {"budget_id": kids["web"], "active": False}).status == "committed"
        new = act(c, OWNER, "budget_create", {"name": "Software web", "account_id": account(c, "6100"), "amount": 5_000,
                                               "team": "web", "parent_id": pid, "at_limit": "decline"})
        assert new.status == "committed", new
        assert act(c, OWNER, "allocation_set", {"budget_id": str(new.result["budget_id"]), "target_kind": "teammate",
                                                 "target_id": "human:eve", "amount": 5_000}).status == "committed"
        spend(c, "6100", 5_000, team="web", teammate="human:eve")
        assert FA.budget_state(c, pid, AT).remaining == 0


def _child(c, pid, name, amount, team="", vendor=""):
    """A child of `pid` on account 6100, optionally scoped to a team or a vendor; its id."""
    body = {"name": name, "account_id": account(c, "6100"), "amount": amount, "parent_id": pid, "at_limit": "decline"}
    made = act(c, OWNER, "budget_create", {**body, **({"team": team} if team else {}), **({"vendor": vendor} if vendor else {})})
    assert made.status == "committed", made
    return str(made.result["budget_id"])


def _allot(c, budget_id, amount):
    """Set Eve's allocation on a budget."""
    made = act(c, OWNER, "allocation_set", {"budget_id": budget_id, "target_kind": "teammate",
                                             "target_id": "human:eve", "amount": amount})
    assert made.status == "committed", made


def test_a_line_one_teammate_posts_is_drawn_once_across_three_generations(books):
    """The reviewer's chain under Software (20,000): Eve's 10,000 on an ended Engineering child, 5,000 on an ended Web child
    and 5,000 on that Web child's active replacement. Her Web line (5,000) is drawn by the replacement, which takes precedence
    over the ended Web child; her Engineering line (5,000) by Engineering, the only child whose scope takes it. Software:
    actual 10,000, held 10,000 (Engineering keeps 5,000 unused, the ended Web child 5,000), remaining 0. Subtracting each
    rival's whole actual drew the Web line twice (held 15,000, remaining -5,000)."""
    with books.transaction() as c:
        pid, _ = _tree(c, 20_000, ())
        eng = _child(c, pid, "Engineering", 10_000, team="eng")
        _allot(c, eng, 10_000)
        assert act(c, OWNER, "budget_update", {"budget_id": eng, "active": False}).status == "committed"
        old = _child(c, pid, "Web old", 5_000, team="web")
        _allot(c, old, 5_000)
        assert act(c, OWNER, "budget_update", {"budget_id": old, "active": False}).status == "committed"
        new = _child(c, pid, "Web new", 5_000, team="web")
        _allot(c, new, 5_000)
        spend(c, "6100", 5_000, team="web", teammate="human:eve")
        spend(c, "6100", 5_000, team="eng", teammate="human:eve")
        state = FA.budget_state(c, pid, AT)
        assert (state.actual, state.held, state.remaining) == (10_000, 10_000, 0), state


def test_a_line_an_ended_budget_shares_goes_to_the_active_one_on_the_same_account(books):
    """Codex's case under Opex (20,000, on account 6000): Eve's 5,000 on an ended Software child (account 6100, any vendor) and 5,000 on an
    active Slack child (the same account, vendor Slack). Her GitHub line is Software's alone; her Slack line is the active
    Slack child's, which takes precedence. Software has nothing left to reserve, so Opex has 10,000 left and a 7,500 spend
    fits. Subtracting Slack's whole actual from Software left a phantom 5,000 reserve (remaining 5,000)."""
    with books.transaction() as c:
        pid = str(act(c, OWNER, "budget_create", {"name": "Opex", "account_id": account(c, "6000"),
                                                  "amount": 20_000, "at_limit": "decline"}).result["budget_id"])
        soft = _child(c, pid, "Software", 5_000)
        _allot(c, soft, 5_000)
        assert act(c, OWNER, "budget_update", {"budget_id": soft, "active": False}).status == "committed"
        slack = _child(c, pid, "Slack", 5_000, vendor="Slack")
        _allot(c, slack, 5_000)
        spend(c, "6100", 5_000, teammate="human:eve", vendor="GitHub")
        spend(c, "6100", 5_000, teammate="human:eve", vendor="Slack")
        state = FA.budget_state(c, pid, AT)
        assert (state.actual, state.remaining) == (10_000, 10_000), state


def test_lowering_a_parent_below_an_ended_childs_retained_allocation_is_refused(books):
    """A 10,000 parent with a 10,000 Web child that holds Eve's 8,000 and has spent 5,000 for her. Ending Web keeps the whole
    8,000 against the parent (its unused 3,000 and the 5,000 spent), so lowering the parent to 3,000 is refused ("reserve
    80.00"); lowering it to 8,000 commits. Only the unused 3,000 used to count, so 3,000 was allowed."""
    with books.transaction() as c:
        pid, kids = _tree(c, 10_000, (("web", 10_000),))
        _allot(c, kids["web"], 8_000)
        spend(c, "6100", 5_000, team="web", teammate="human:eve")
        assert act(c, OWNER, "budget_update", {"budget_id": kids["web"], "active": False}).status == "committed"
        lowered = act(c, OWNER, "budget_update", {"budget_id": pid, "amount": 3_000})
        assert lowered.code == "invalid" and "reserve 80.00" in lowered.reason, lowered
        assert FA.budget_state(c, pid, AT).amount == 10_000
        assert act(c, OWNER, "budget_update", {"budget_id": pid, "amount": 8_000}).status == "committed"


def _checking(c) -> str:
    """A checking account on the 1000 line; its id."""
    money = SL.new_id()
    c.execute("INSERT INTO fin_money_accounts(id, book_id, kind, name, provider, account_id, created_at, created_by) "
              "VALUES(?,?,?,?,?,?,?,?)", (money, SL.book(c).id, "bank", "Checking", "mercury", account(c, "1000"), AT, OWNER))  # type: ignore[union-attr]
    return money


def _feed_row(c, money: str, amount: int, counterparty: str) -> str:
    """A bank feed row (what the bank brings in) on that checking account; its id."""
    fid = SL.new_id()
    c.execute("INSERT INTO fin_feed_txns(id, money_account_id, provider_id, txn_date, amount, currency, counterparty, "
              "status, created_at, created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (fid, money, f"txn-{fid[:8]}", "2026-10-10", amount, SL.book(c).base_currency, counterparty,  # type: ignore[union-attr]
               "new", AT, OWNER))
    return fid


def _private_bank_entry(c, fid: str, vendor: str) -> None:
    """The bank entry a feed row posts: 1,000 of Software under `vendor`, 1,000 on a private payroll account (so the row
    stands for a private line), and 2,000 out of the bank. The row is linked to the entry as posted."""
    entry = FA.post_entry(c, {"date": "2026-10-10", "source": "bank", "source_id": f"{fid}:1", "lines": [
        {"account_id": account(c, "6100"), "amount": 1_000, "contact_id": vendor, "tags": {"teammate": "human:eve"}},
        {"account_id": account(c, "6200"), "amount": 1_000},
        {"account_id": account(c, "1000"), "amount": -2_000}]}, at=AT)
    c.execute("UPDATE fin_feed_txns SET entry_id=?, status='posted' WHERE id=?", (entry["entry_id"], fid))


def test_a_vendor_under_its_old_name_and_its_contact_id_nets_each_private_line_once(books):
    """Codex's case: two private bank entries, each with 1,000 of Software and a 1,000 payroll line. One posts the vendor
    under its old name (posted before the contact took the name), the other under its contact id. A 5,000 teammate
    allocation on a Software budget scoped to the vendor. The owner sees 2,000 spent and 3,000 held. A reader without
    payroll detail sees nothing spent and 5,000 held. Netting each vendor group under every one of its keys took each
    hidden line out twice (held 7,000, remaining 3,000), which exposed the 2,000 that is not theirs to see."""
    with books.transaction() as c:
        pay = str(act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                                   "parent_id": account(c, "6000")}).result["account_id"])
        assert act(c, OWNER, "private_lines_set", {"account_id": pay, "others_see": "totals"}).status == "committed"
        money = _checking(c)
        _private_bank_entry(c, _feed_row(c, money, -2_000, "Acme Cloud"), "Acme Cloud")
        contact = str(act(c, OWNER, "contact_create", {"name": "Acme Cloud", "kind": "vendor"}).result["contact_id"])
        assert act(c, OWNER, "contact_update", {"contact_id": contact, "name": "Acme Hosting"}).status == "committed"
        _private_bank_entry(c, _feed_row(c, money, -2_000, "Acme Hosting"), contact)
        root = act(c, OWNER, "budget_create", {"name": "Cloud", "account_id": account(c, "6100"), "amount": 10_000,
                                               "at_limit": "decline"})
        assert root.status == "committed", root
        bid = _child(c, str(root.result["budget_id"]), "Software", 10_000, vendor=contact)
        _allot(c, bid, 5_000)
        owner = budgeting.state(load(c, AT), bid, date(2026, 10, 15))
        gus = load(c, AT).view("human:gus")
        seen = budgeting.state(load(c, AT), bid, date(2026, 10, 15), read=budgeting.Netted(gus))
        assert (owner.actual, owner.held) == (2_000, 3_000), owner
        assert (seen.actual, seen.held, seen.remaining) == (0, 5_000, 5_000), seen
