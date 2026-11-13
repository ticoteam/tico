"""The write path: idempotency, dry runs, denials, and the OK flow end to end (an OK commits once, a changed payload
voids it and asks again, a send back denies, a withdrawn OK ends the action, and nobody but the person asked gives it)."""

import sqlite3
from contextlib import closing

from backend import devcopy
import pytest

from backend import hubdb as H
from backend import task_asks
from backend.config import Settings
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.store import Store
from backend.tests.helpers.finance_fixtures import open_views, AT, OWNER, account, act, books, finance_template, store  # noqa: F401
from backend.tests.helpers.finance_oks import give, send_back, withdraw


def _software(c):
    r = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 50_000})
    return str(r.result["budget_id"])


def _coder_asks(c, parent, key="coder-1", amount=5_000, **kw):
    """The coder bot (no finance.budgets right) asks for its own budget: its manager, Cara, decides."""
    return act(
        c,
        "bot:coder",
        "budget_create",
        {
            "name": "Coder tools",
            "account_id": account(c, "6100"),
            "teammate": "bot:coder",
            "parent_id": parent,
            "amount": amount,
        },
        key,
        **kw,
    )


def _count(c, table):
    return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_same_key_same_result_and_dry_runs_write_nothing(books):
    with books.transaction() as c:
        body = {"code": "6300", "name": "Travel", "type": "expense"}
        before = {t: _count(c, t) for t in ("fin_actions", "fin_accounts", "events", "ok_grants")}
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
        audit = c.execute(
            "SELECT detail FROM events WHERE action='finance.account_create' AND target=?", (first.action_id,)
        ).fetchone()
        assert audit and '"committed"' in audit[0]


def test_rights_are_checked_and_granted_only_by_admins(books):
    with books.transaction() as c:
        body = {
            "date": "2026-10-03",
            "lines": [
                {"account_id": account(c, "6900"), "amount": 10},
                {"account_id": account(c, "1000"), "amount": -10},
            ],
        }
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
        assert dry.rule == "default:budget-request" and _count(c, "ok_grants") == 0
        spend = act(
            c,
            OWNER,
            "journal",
            {
                "date": "2026-10-03",
                "lines": [
                    {"account_id": account(c, "6100"), "amount": 1_000},
                    {"account_id": account(c, "1000"), "amount": -1_000},
                ],
            },
            dry_run=True,
        )
        assert spend.impact["budgets"] == [
            {"budget_id": parent, "name": "Software", "remaining": 50_000, "remaining_after": 49_000, "excluded": False}
        ]


def test_approval_commits_once(books):
    with books.transaction() as c:
        parent = _software(c)
        asked = _coder_asks(c, parent)
        assert (asked.status, asked.approver) == ("needs_approval", "human:cara"), asked
        row = task_asks.grant(c, asked.approval_id)
        assert row["subject"] == "finance:" + asked.action_id and row["content"]["action_id"] == asked.action_id
        assert row["asked_by_id"] == "bot:coder"
        assert FA.approval_decided(c, asked.approval_id, AT).status == "needs_approval"
        give(c, "human:cara", asked.approval_id)
        done = FA.approval_decided(c, asked.approval_id, AT)
        again = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status == again.status == "committed" and done.result["budget_id"] and again.result == {}
        assert _count(c, "fin_budgets") == 6
        assert task_asks.standing(c, asked.approval_id) == task_asks.USED
        assert H.task(c, task_asks.task_of(c, asked.approval_id))["status"] == "done"
        assert act(c, "bot:coder", "x", {}, "coder-1").code == "conflict"
        assert _coder_asks(c, parent).action_id == asked.action_id
        assert FA.action(c, str(asked.action_id)).status == "committed"


def test_a_changed_payload_voids_the_approval_and_asks_again(books):
    with books.transaction() as c:
        asked = _coder_asks(c, _software(c))
        give(c, "human:cara", asked.approval_id)
        c.execute("UPDATE ok_grants SET content_hash='tampered' WHERE requirement_id=?", (asked.approval_id,))
        redo = FA.approval_decided(c, asked.approval_id, AT)
        assert redo.status == "needs_approval" and redo.approval_id != asked.approval_id
        assert _count(c, "fin_budgets") == 5
        assert FA.approval_decided(c, asked.approval_id, AT) is None
        give(c, "human:cara", redo.approval_id)
        assert FA.approval_decided(c, redo.approval_id, AT).status == "committed"


def test_decline_someone_else_and_a_lost_right_never_commit(books):
    with books.transaction() as c:
        parent = _software(c)
        no = _coder_asks(c, parent, "k-no")
        send_back(c, "human:cara", no.approval_id)
        assert (FA.approval_decided(c, no.approval_id, AT).status) == "denied"
        other = _coder_asks(c, parent, "k-other", 4_000)
        give(c, "human:cara", other.approval_id)
        c.execute("UPDATE ok_grants SET ok_by_id='human:dan' WHERE requirement_id=?", (other.approval_id,))
        # Given by someone other than the person it was asked of: the OK is void and asked again (§5), not denied.
        asked_again = FA.approval_decided(c, other.approval_id, AT)
        assert asked_again.status == "needs_approval" and asked_again.approval_id != other.approval_id
        late = _coder_asks(c, parent, "k-late", 3_000)
        give(c, "human:cara", late.approval_id)
        c.execute("UPDATE fin_budgets SET active=0 WHERE id=?", (parent,))
        assert FA.approval_decided(c, late.approval_id, AT).status == "failed"
        assert _count(c, "fin_budgets") == 5


def test_the_hourly_sweep_never_claims_a_given_ok_only_the_click_does(books):
    """The lead's rule on Codex's upkeep review, item 2: a background job never claims an OK. An OK given without its
    click's own step (ON_OK "finance") stays unused and its action waits: the sweep commits nothing. The click
    (ok_given, as task_asks.give_ok runs it) claims the OK and commits, once."""
    with books.transaction() as c:
        parent = _software(c)
        asked = _coder_asks(c, parent, "k-sweep")
        before = _count(c, "fin_budgets")
        give(c, "human:cara", asked.approval_id)  # given, but no click step ran
        assert FA.ok_sweep(c, AT) == []
        assert FA.action(c, str(asked.action_id)).status == "needs_approval"
        assert task_asks.grant(c, asked.approval_id)["used_at"] is None
        assert _count(c, "fin_budgets") == before
        # What the click's step (ok_given) runs, at the books' own clock.
        assert FA.approval_decided(c, asked.approval_id, AT).status == "committed"
        assert FA.action(c, str(asked.action_id)).status == "committed"
        assert task_asks.grant(c, asked.approval_id)["used_at"] is not None
        assert FA.ok_sweep(c, AT) == [] and _count(c, "fin_budgets") == before + 1


def test_a_withdrawn_ok_ends_the_action(books):
    with books.transaction() as c:
        parent = _software(c)
        gone = _coder_asks(c, parent, "k-gone")
        withdraw(c, gone.approval_id)
        waiting = _coder_asks(c, parent, "k-wait", 4_000)
        done = {r.action_id: r.status for r in FA.ok_sweep(c, AT)}
        assert done == {gone.action_id: "withdrawn"}
        assert FA.ok_sweep(c, AT) == [] and FA.action(c, str(waiting.action_id)).status == "needs_approval"
        assert H.task(c, task_asks.task_of(c, gone.approval_id))["status"] == "closed"
        fresh = _coder_asks(c, parent, "k-again")
        assert fresh.status == "needs_approval" and fresh.action_id != gone.action_id


def test_only_the_person_asked_gives_the_ok_by_their_own_click(books):
    """Money is a safety gate: the bot that asked, a bot acting for someone, another person, a click that is not the
    person's own, or an OK for content they were not shown never commits the action."""
    with books.transaction() as c:
        asked = _coder_asks(c, _software(c))
        rid = str(asked.approval_id)
        found = task_asks.grant(c, rid)
        for who in ("bot:coder", "human:dan", OWNER):
            shown = task_asks.show(c, who, rid, found["content"], own_click=True)
            with pytest.raises(H.Refused):
                task_asks.give_ok(c, who, rid, shown, own_click=True)
        shown = task_asks.show(c, "human:cara", rid, found["content"], own_click=False)
        with pytest.raises(H.Refused):
            task_asks.give_ok(c, "human:cara", rid, shown, own_click=False)
        with pytest.raises(H.Refused):
            task_asks.give_ok(c, "human:cara", rid, task_asks.content_hash({"other": 1}), own_click=True)
        with pytest.raises(H.Refused):  # never shown in her own session
            task_asks.give_ok(c, "human:cara", rid, found["content_hash"], own_click=True)
        assert FA.approval_decided(c, rid, AT).status == "needs_approval" and _count(c, "fin_budgets") == 5
        give(c, "human:cara", rid)
        assert FA.approval_decided(c, rid, AT).status == "committed" and _count(c, "fin_budgets") == 6


def test_the_ok_commits_in_its_own_transaction(books, monkeypatch):
    """Finance's step of an OK (task_asks.ON_OK "finance", FA.ok_given) commits the action when the OK is given."""
    decided = FA.approval_decided
    monkeypatch.setattr(FA, "approval_decided", lambda c, rid, at=None: decided(c, rid, at or AT))  # the books' day
    with books.transaction() as c:
        asked = _coder_asks(c, _software(c))
        give(c, "human:cara", asked.approval_id)
        FA.ok_given(c, task_asks.grant(c, str(asked.approval_id)))
        done = FA.action(c, str(asked.action_id))
        assert done.status == "committed" and _count(c, "fin_budgets") == 6, (done.code, done.reason)


def test_default_gates_send_close_and_large_bot_entries_to_one_person(books):
    with books.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.book"})
        close = act(c, "human:ben", "close_period", {"month": "2026-09"})
        assert (close.status, close.approver, close.rule) == ("needs_approval", OWNER, "default:close")
        act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.book"})
        act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "everything"})
        big = {
            "date": "2026-10-03",
            "lines": [
                {"account_id": account(c, "6900"), "amount": 600_000},
                {"account_id": account(c, "1000"), "amount": -600_000},
            ],
        }
        asked = act(c, "bot:coder", "journal", big)
        assert (asked.status, asked.approver) == ("needs_approval", OWNER)
        FA.setup(c, OWNER, AT, finance_approver="human:dan")
        open_views(c)
        assert act(c, "bot:coder", "journal", big).approver == "human:dan"
        assert (
            act(c, "bot:coder", "account_create", {"code": "6400", "name": "Ads", "type": "expense"}).status
            == "needs_approval"
        )
        grant = act(
            c,
            "bot:coder",
            "view_grant",
            {"subject": "human:eve", "what": "expenses", "whose": "team", "whose_team": "web"},
        )
        assert grant.code == "denied"  # the coder leads no team


def _provider_rows(c):
    """A books connection and its outbox row, the rows a dev copy must not keep acting on."""
    c.execute(
        "INSERT INTO fin_connections(id,provider,purpose,created_at,created_by) "
        "VALUES('fc1','quickbooks','books',?,'morgan')",
        (H.now(),),
    )
    c.execute(
        "INSERT INTO fin_outbox(id,connection_id,op,idempotency_key,created_at,created_by) "
        "VALUES('ob1','fc1','journal.create','k1',?,'morgan')",
        (H.now(),),
    )


def test_a_dev_copy_scrub_empties_the_provider_rows_with_keys_on(tmp_path):
    store = Store(Settings(db_path=tmp_path / "hub.sqlite"))
    store.initialize(seed_market=False)
    with closing(sqlite3.connect(store.settings.db_path)) as c:
        c.execute("PRAGMA foreign_keys=ON")
        _provider_rows(c)
        removed = FA.scrub_dev_copy(c, H.now())
        assert removed["fin_connections"] == 1 and removed["fin_outbox"] == 1 and removed["fin_actions"] == 0
        assert [t for t in FA.DEV_COPY_EMPTIED if _count(c, t)] == []
        c.commit()


def test_a_dev_copy_build_reports_the_rows_finance_emptied(tmp_path):
    source = Store(Settings(db_path=tmp_path / "live" / "hub.sqlite"))
    source.initialize(seed_market=False)
    with closing(sqlite3.connect(source.settings.db_path)) as c:
        c.execute("PRAGMA foreign_keys=ON")
        _provider_rows(c)
        c.commit()
    for conn in source.pool:
        conn.close()
    source.pool.clear()
    dest = tmp_path / "copy" / "hub.sqlite"
    removed, _ = devcopy.build(source.settings.db_path, dest, days=30)
    assert removed["fin_connections"] == 1 and removed["fin_outbox"] == 1
    with closing(sqlite3.connect(dest)) as c:
        assert [t for t in FA.DEV_COPY_EMPTIED if _count(c, t)] == []


def test_a_dev_copy_scrub_with_one_emptied_table_missing_still_empties_the_others(tmp_path):
    store = Store(Settings(db_path=tmp_path / "hub.sqlite"))
    store.initialize(seed_market=False)
    with closing(sqlite3.connect(store.settings.db_path)) as c:
        c.execute("PRAGMA foreign_keys=ON")
        _provider_rows(c)
        c.commit()
        c.execute("PRAGMA foreign_keys=OFF")  # as devcopy's copy runs: a missing child is not checked
        c.execute("DROP TABLE fin_cards")
        removed = FA.scrub_dev_copy(c, H.now())
        assert "fin_cards" not in removed and removed["fin_connections"] == 1
        assert _count(c, "fin_connections") == 0 and _count(c, "fin_outbox") == 0
        c.commit()


def test_a_dev_copy_build_still_empties_finance_when_one_of_its_tables_is_missing(tmp_path):
    source = Store(Settings(db_path=tmp_path / "live" / "hub.sqlite"))
    source.initialize(seed_market=False)
    with closing(sqlite3.connect(source.settings.db_path)) as c:
        c.execute("PRAGMA foreign_keys=ON")
        _provider_rows(c)
        c.execute("DROP TABLE fin_cards")
        c.commit()
    for conn in source.pool:
        conn.close()
    source.pool.clear()
    dest = tmp_path / "copy" / "hub.sqlite"
    removed, _ = devcopy.build(source.settings.db_path, dest, days=30)
    assert removed["fin_connections"] == 1
    with closing(sqlite3.connect(dest)) as c:
        assert _count(c, "fin_connections") == 0


def test_a_changed_approver_voids_the_decision_and_asks_the_new_one(books):
    with books.transaction() as c:
        asked = _coder_asks(c, _software(c))
        give(c, "human:cara", asked.approval_id)
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='coder'")
        redo = FA.approval_decided(c, asked.approval_id, AT)
        assert (redo.status, redo.approver) == ("needs_approval", "human:ben") and _count(c, "fin_budgets") == 5


def test_defaults_are_fixed_when_asked_not_when_approved(books):
    with books.transaction() as c:
        parent = _software(c)
        asked = _coder_asks(c, parent)
        stored = c.execute("SELECT payload FROM fin_actions WHERE id=?", (asked.action_id,)).fetchone()[0]
        assert '"effective_from": "2026-10-01"' in stored
        assert _coder_asks(c, parent).action_id == asked.action_id  # a retry without it is the same request
        give(c, "human:cara", asked.approval_id)
        done = FA.approval_decided(c, asked.approval_id, "2026-10-20T09:00:00.000000Z")
        assert done.status == "committed" and done.result["effective_from"] == "2026-10-01"


def test_the_bot_gate_counts_the_whole_entry(books):
    with books.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.book"})
        act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "everything"})
        split = {
            "date": "2026-10-03",
            "lines": [
                {"account_id": account(c, "6900"), "amount": 300_000},
                {"account_id": account(c, "6100"), "amount": 300_000},
                {"account_id": account(c, "1000"), "amount": -300_000},
                {"account_id": account(c, "2000"), "amount": -300_000},
            ],
        }
        assert act(c, "bot:coder", "journal", split).status == "needs_approval"


def test_a_recheck_refusal_that_named_a_sibling_hides_its_name_once_the_asker_cannot_see_it(books):
    body = {"name": "Eng tools", "team": "eng", "amount": 5_000}
    with books.transaction() as c:
        parent = _software(c)
        grant = act(c, OWNER, "view_grant", {"subject": "human:eve", "what": "everything"})
        assert grant.status == "committed"
        asked = act(
            c, "human:eve", "budget_create", {**body, "account_id": account(c, "6100"), "parent_id": parent}, "eng-ask"
        )
        assert (asked.status, asked.approver) == ("needs_approval", "human:cara"), asked
        # Before the manager decides, an overlapping sibling is added; the recheck at approval refuses naming it.
        payroll = act(
            c,
            OWNER,
            "budget_create",
            {"name": "Payroll", "team": "eng", "amount": 5_000, "account_id": account(c, "6100"), "parent_id": parent},
        )
        assert payroll.status == "committed"
        give(c, "human:cara", asked.approval_id)
        closed = FA.approval_decided(c, asked.approval_id, AT)
        assert closed.status == "failed" and "Payroll" in closed.reason
        assert act(c, OWNER, "view_revoke", {"grant_id": grant.result["grant_id"]}).status == "committed"
        stored = c.execute("SELECT reason FROM fin_actions WHERE id=?", (asked.action_id,)).fetchone()[0]
        assert "Payroll" not in stored
        replay = act(
            c, "human:eve", "budget_create", {**body, "account_id": account(c, "6100"), "parent_id": parent}, "eng-ask"
        )
        assert replay.replayed and "Payroll" not in replay.reason
        assert "Payroll" not in FR.action_for(c, "human:eve", asked.action_id)["reason"]
        assert act(c, OWNER, "view_grant", {"subject": "human:eve", "what": "everything"}).status == "committed"
        again = act(
            c, "human:eve", "budget_create", {**body, "account_id": account(c, "6100"), "parent_id": parent}, "eng-ask"
        )
        assert again.replayed and "Payroll" in again.reason


def test_an_approval_that_no_longer_fits_stores_no_figure(books):
    """A set-aside a teammate without finance.budgets asks for is decided when its manager approves. If the budget was
    cut meanwhile, the refusal is stored without the figure; the owner reads the figure from the budget's name."""
    with books.transaction() as c:
        parent = _software(c)
        assert act(c, OWNER, "view_grant", {"subject": "bot:coder", "what": "pnl"}, "coder-view").status == "committed"
        asked = act(
            c,
            "bot:coder",
            "allocation_set",
            {"budget_id": parent, "target_kind": "teammate", "target_id": "bot:coder", "amount": 5_000},
            "coder-set",
        )
        assert (asked.status, asked.approver) == ("needs_approval", "human:cara"), asked
        assert act(c, OWNER, "budget_update", {"budget_id": parent, "amount": 1_000}, "cut").status == "committed"
        give(c, "human:cara", asked.approval_id)
        done = FA.approval_decided(c, asked.approval_id, AT)
        assert done.status in ("failed", "denied") and done.code == "invalid", done
        stored = FA.action(c, str(asked.action_id)).reason
        assert not any(ch.isdigit() for ch in stored), stored
        assert "Software" in FR.action_for(c, OWNER, asked.action_id)["reason"]
