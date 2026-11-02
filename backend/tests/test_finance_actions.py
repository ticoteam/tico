"""The write path: idempotency, dry runs, denials, and the approval flow end to end (approve commits once, a changed
payload voids and asks again, decline denies, a withdrawn or expired approval ends the action)."""
from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401


def _software(c):
    r = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 50_000})
    return str(r.result["budget_id"])


def _coder_asks(c, parent, key="coder-1", amount=5_000, **kw):
    """The coder bot (no finance.budgets right) asks for its own budget: its manager, Cara, decides."""
    return act(c, "bot:coder", "budget_create", {"name": "Coder tools", "account_id": account(c, "6100"),
                                                 "teammate": "bot:coder", "parent_id": parent, "amount": amount}, key, **kw)


def _count(c, table):
    return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_same_key_same_result_and_dry_runs_write_nothing(books):
    with books.transaction() as c:
        body = {"code": "6300", "name": "Travel", "type": "expense"}
        before = {t: _count(c, t) for t in ("fin_actions", "fin_accounts", "events", "approvals")}
        dry = act(c, OWNER, "account_create", body, "a1", dry_run=True)
        assert dry.dry_run and dry.status == "committed" and dry.action_id is None
        assert {t: _count(c, t) for t in before} == before
        first = act(c, OWNER, "account_create", body, "a1")
        again = act(c, OWNER, "account_create", body, "a1")
        assert first.status == again.status == "committed" and first.action_id == again.action_id
        # A replay carries the id and status only: the account is read fresh with the caller's view.
        assert first.result["account_id"] and again.result == {}
        assert _count(c, "fin_accounts") == before["fin_accounts"] + 1
        assert act(c, OWNER, "account_create", {**body, "name": "Trips"}, "a1").code == "conflict"
        refused = act(c, OWNER, "bill_create", {"amount": 1}, "p1")
        assert (refused.status, refused.code) == ("failed", "invalid")
        assert act(c, OWNER, "bill_create", {"amount": 1}, "p1").action_id == refused.action_id
        assert act(c, OWNER, "teleport", {}, "t1").code == "invalid"
        audit = c.execute("SELECT detail_json FROM events WHERE action='finance.account_create' AND target=?",
                          (first.action_id,)).fetchone()
        assert audit and '"committed"' in audit[0]


def test_rights_are_checked_and_granted_only_by_admins(books):
    with books.transaction() as c:
        body = {"date": "2026-10-03", "lines": [{"account_id": account(c, "6900"), "amount": 10},
                                                {"account_id": account(c, "1000"), "amount": -10}]}
        assert act(c, "human:eve", "journal", body).code == "denied"
        assert act(c, "human:eve", "right_grant", {"teammate": "human:fay", "right": "finance.book"}).code == "denied"
        assert act(c, OWNER, "right_grant", {"teammate": OWNER, "right": "finance.book"}).code == "denied"
        assert act(c, OWNER, "right_grant", {"teammate": "human:eve", "right": "finance.book"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": "human:eve", "what": "everything"}).status == "committed"
        assert act(c, "human:eve", "journal", body).status == "committed"
        # The Assistant acting for Eve has Eve's rights; for Fay it has none.
        assert act(c, FA.Principal("bot:coder", for_actor="human:eve"), "journal", body).status == "committed"
        assert act(c, FA.Principal("bot:coder", for_actor="human:fay"), "journal", body).code == "denied"
        assert act(c, OWNER, "right_revoke", {"teammate": "human:eve", "right": "finance.book"}).status == "committed"
        assert act(c, "human:eve", "journal", body).code == "denied"


def test_dry_run_reports_the_approval_and_budget_impact(books):
    with books.transaction() as c:
        parent = _software(c)
        dry = _coder_asks(c, parent, dry_run=True)
        assert dry.status == "needs_approval" and dry.approver == "human:cara"
        assert dry.rule == "default:budget-request" and _count(c, "approvals") == 0
        spend = act(c, OWNER, "journal", {"date": "2026-10-03", "lines": [
            {"account_id": account(c, "6100"), "amount": 1_000}, {"account_id": account(c, "1000"), "amount": -1_000}]},
            dry_run=True)
        assert spend.impact["budgets"] == [{"budget_id": parent, "name": "Software", "remaining": 50_000,
                                            "remaining_after": 49_000, "excluded": False}]


def test_approval_commits_once(books):
    with books.transaction() as c:
        parent = _software(c)
        asked = _coder_asks(c, parent)
        assert (asked.status, asked.approver) == ("needs_approval", "human:cara")
        row = H.approval(c, asked.approval_id)
        assert row["kind"] == "finance" and row["payload"]["action_id"] == asked.action_id
        assert FA.approval_decided(c, asked.approval_id, AT).status == "needs_approval"
        H.approval_decide(c, "human:cara", asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        again = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == again.status == "committed" and done.result["budget_id"] and again.result == {}
        assert _count(c, "fin_budgets") == 5
        assert H.approval(c, asked.approval_id)["consumed_at"]
        assert act(c, "bot:coder", "x", {}, "coder-1").code == "conflict"
        assert _coder_asks(c, parent).action_id == asked.action_id
        assert FA.action(c, str(asked.action_id)).status == "committed"


def test_a_changed_payload_voids_the_approval_and_asks_again(books):
    with books.transaction() as c:
        asked = _coder_asks(c, _software(c))
        H.approval_decide(c, "human:cara", asked.approval_id, "approved")
        c.execute("UPDATE approvals SET payload_hash='tampered' WHERE id=?", (asked.approval_id,))
        redo = FA.approval_decided(c, asked.approval_id, AT)
        assert redo.status == "needs_approval" and redo.approval_id != asked.approval_id
        assert _count(c, "fin_budgets") == 4
        assert FA.approval_decided(c, asked.approval_id, AT) is None
        H.approval_decide(c, "human:cara", redo.approval_id, "approved")
        assert FA.approval_decided(c, redo.approval_id, AT).status == "committed"


def test_decline_someone_else_and_a_lost_right_never_commit(books):
    with books.transaction() as c:
        parent = _software(c)
        no = _coder_asks(c, parent, "k-no")
        H.approval_decide(c, "human:cara", no.approval_id, "declined")
        assert (FA.approval_decided(c, no.approval_id, AT).status) == "denied"
        other = _coder_asks(c, parent, "k-other", 4_000)
        H.approval_decide(c, "human:cara", other.approval_id, "approved")
        c.execute("UPDATE approvals SET decided_by='human:dan' WHERE id=?", (other.approval_id,))
        # Decided by someone other than the person it was sent to: the approval is void and asked again (§5), not denied.
        asked_again = FA.approval_decided(c, other.approval_id, AT)
        assert asked_again.status == "needs_approval" and asked_again.approval_id != other.approval_id
        late = _coder_asks(c, parent, "k-late", 3_000)
        H.approval_decide(c, "human:cara", late.approval_id, "approved")
        c.execute("UPDATE fin_budgets SET active=0 WHERE id=?", (parent,))
        assert FA.approval_decided(c, late.approval_id, AT).status == "failed"
        assert _count(c, "fin_budgets") == 4


def test_withdrawn_and_expired_approvals_end_the_action(books):
    with books.transaction() as c:
        parent = _software(c)
        gone = _coder_asks(c, parent, "k-gone")
        H.approval_withdraw(c, gone.approval_id, "No decision in 14 days.")
        old = _coder_asks(c, parent, "k-old", 4_000)
        c.execute("UPDATE approvals SET created='2026-09-01T00:00:00.000000Z' WHERE id=?", (old.approval_id,))
        H.approval_withdraw(c, old.approval_id, "No decision in 14 days.")
        done = {r.action_id: r.status for r in FA.approvals_sweep(c, AT)}
        assert done == {gone.action_id: "withdrawn", old.action_id: "expired"}
        assert FA.approvals_sweep(c, AT) == []
        fresh = _coder_asks(c, parent, "k-again")
        assert fresh.status == "needs_approval" and fresh.action_id != gone.action_id


def test_default_gates_send_close_and_large_bot_entries_to_one_person(books):
    with books.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.book"})
        close = act(c, "human:ben", "close_period", {"month": "2026-09"})
        assert (close.status, close.approver, close.rule) == ("needs_approval", OWNER, "default:close")
        act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.book"})
        act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "everything"})
        big = {"date": "2026-10-03", "lines": [{"account_id": account(c, "6900"), "amount": 600_000},
                                               {"account_id": account(c, "1000"), "amount": -600_000}]}
        asked = act(c, "bot:coder", "journal", big)
        assert (asked.status, asked.approver) == ("needs_approval", OWNER)
        FA.setup(c, OWNER, AT, finance_approver="human:dan")
        assert act(c, "bot:coder", "journal", big).approver == "human:dan"
        assert act(c, "bot:coder", "account_create", {"code": "6400", "name": "Ads", "type": "expense"}).status == \
            "needs_approval"
        grant = act(c, "bot:coder", "view_grant", {"subject": "human:eve", "what": "expenses", "whose": "team",
                                                   "whose_team": "web"})
        assert grant.code == "denied"            # the coder leads no team


def test_a_changed_approver_voids_the_decision_and_asks_the_new_one(books):
    with books.transaction() as c:
        asked = _coder_asks(c, _software(c))
        H.approval_decide(c, "human:cara", asked.approval_id, "approved")
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='coder'")
        redo = FA.approval_decided(c, asked.approval_id, AT)
        assert (redo.status, redo.approver) == ("needs_approval", "human:ben") and _count(c, "fin_budgets") == 4


def test_defaults_are_fixed_when_asked_not_when_approved(books):
    with books.transaction() as c:
        parent = _software(c)
        asked = _coder_asks(c, parent)
        stored = c.execute("SELECT payload_json FROM fin_actions WHERE id=?", (asked.action_id,)).fetchone()[0]
        assert '"effective_from": "2026-10-01"' in stored
        assert _coder_asks(c, parent).action_id == asked.action_id         # a retry without it is the same request
        H.approval_decide(c, "human:cara", asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, "2026-10-20T09:00:00.000000Z")
        assert done.status == "committed" and done.result["effective_from"] == "2026-10-01"


def test_the_bot_gate_counts_the_whole_entry(books):
    with books.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.book"})
        act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "everything"})
        split = {"date": "2026-10-03", "lines": [{"account_id": account(c, "6900"), "amount": 300_000},
                                                 {"account_id": account(c, "6100"), "amount": 300_000},
                                                 {"account_id": account(c, "1000"), "amount": -300_000},
                                                 {"account_id": account(c, "2000"), "amount": -300_000}]}
        assert act(c, "bot:coder", "journal", split).status == "needs_approval"


def test_a_recheck_refusal_that_named_a_sibling_hides_its_name_once_the_asker_cannot_see_it(books):
    body = {"name": "Eng tools", "team": "eng", "amount": 5_000}
    with books.transaction() as c:
        parent = _software(c)
        grant = act(c, OWNER, "view_grant", {"subject": "human:eve", "what": "everything"})
        assert grant.status == "committed"
        asked = act(c, "human:eve", "budget_create", {**body, "account_id": account(c, "6100"),
                                                      "parent_id": parent}, "eng-ask")
        assert (asked.status, asked.approver) == ("needs_approval", "human:cara")
        # Before the manager decides, an overlapping sibling is added; the recheck at approval refuses naming it.
        payroll = act(c, OWNER, "budget_create", {"name": "Payroll", "team": "eng", "amount": 5_000,
                                                  "account_id": account(c, "6100"), "parent_id": parent})
        assert payroll.status == "committed"
        H.approval_decide(c, "human:cara", asked.approval_id, "approved")
        closed = FA.approval_decided(c, asked.approval_id, AT)
        assert closed.status == "failed" and "Payroll" in closed.reason
        assert act(c, OWNER, "view_revoke", {"grant_id": grant.result["grant_id"]}).status == "committed"
        stored = c.execute("SELECT reason FROM fin_actions WHERE id=?", (asked.action_id,)).fetchone()[0]
        assert "Payroll" not in stored
        replay = act(c, "human:eve", "budget_create", {**body, "account_id": account(c, "6100"),
                                                       "parent_id": parent}, "eng-ask")
        assert replay.replayed and "Payroll" not in replay.reason
        assert "Payroll" not in FR.action_for(c, "human:eve", asked.action_id)["reason"]
        assert act(c, OWNER, "view_grant", {"subject": "human:eve", "what": "everything"}).status == "committed"
        again = act(c, "human:eve", "budget_create", {**body, "account_id": account(c, "6100"),
                                                      "parent_id": parent}, "eng-ask")
        assert again.replayed and "Payroll" in again.reason


def test_an_approval_that_no_longer_fits_stores_no_figure(books):
    """A set-aside a teammate without finance.budgets asks for is decided when its manager approves. If the budget was
    cut meanwhile, the refusal is stored without the figure; the owner reads the figure from the budget's name."""
    with books.transaction() as c:
        parent = _software(c)
        assert act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "pnl"}, "coder-view").status == "committed"
        asked = act(c, "bot:coder", "allocation_set", {"budget_id": parent, "target_kind": "teammate",
                                                       "target_id": "bot:coder", "amount": 5_000}, "coder-set")
        assert (asked.status, asked.approver) == ("needs_approval", "human:cara"), asked
        assert act(c, OWNER, "budget_update", {"budget_id": parent, "amount": 1_000}, "cut").status == "committed"
        H.approval_decide(c, "human:cara", asked.approval_id, "approved")
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status in ("failed", "denied") and done.code == "invalid", done
        stored = FA.action(c, str(asked.action_id)).reason
        assert not any(ch.isdigit() for ch in stored), stored
        assert "Software" in FR.action_for(c, OWNER, asked.action_id)["reason"]
