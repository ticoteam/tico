"""Codex review findings (combined fix B): one regression per finding. A teammate expenses a prepaid month only on a bill
they see and on lines they may write; a bill emailed to the bookkeeper is in an inbox only for a viewer who sees bills;
a card retried with its idempotency key replays once the Mercury user for it was found from the holder's email.
Synthetic fixtures (Acme), a fake Mercury."""
from backend.finance import reads as FR
from backend.finance.core.errors import CONFLICT, DENIED, NOT_FOUND
from backend.tests.test_finance_ap import _bill, _vendor
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, store, sync_once)

DEC_15 = "2026-12-15T12:00:00.000000Z"      # November has come for a line starting in November


def _entered_prepaid(c, tags: dict[str, str] | None = None) -> tuple[str, str]:
    """An approved bill of one 1,800 prepaid line over November to January, tagged as given; (bill id, line id)."""
    line: dict[str, object] = {"account_id": account(c, "6100"), "amount": 1_800,
                               "prepaid": {"start": "2026-11", "months": 3}}
    if tags is not None:
        line["tags"] = tags
    bid = _bill(c, _vendor(c), [line])
    assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
    return bid, str(c.execute("SELECT id FROM fin_bill_lines WHERE bill_id=?", (bid,)).fetchone()[0])


def _balance_of(c, code: str) -> int:
    return int(c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id "
                         "JOIN fin_entries e ON e.id=l.entry_id WHERE a.code=? AND e.status='posted'",
                         (code,)).fetchone()[0])


def test_a_teammate_who_cannot_see_the_bill_cannot_expense_its_prepaid_month(books):
    """The month is a bill action: the asker must see the bill (its payables view), not only hold finance.book. A line
    tagged to someone else is hidden from a teammate with the default own view, so the month is not found to them."""
    with books.transaction() as c:
        _bid, line = _entered_prepaid(c, {"teammate": "human:ben"})
        assert act(c, OWNER, "right_grant", {"teammate": "human:cara", "right": "finance.book"}).status == "committed"
        got = act(c, "human:cara", "prepaid_amortize", {"bill_line_id": line, "month": "2026-11"}, at=DEC_15)
        assert (got.status, got.code) == ("failed", NOT_FOUND), got
        assert c.execute("SELECT count(*) FROM fin_prepaid_postings").fetchone()[0] == 0
        assert _balance_of(c, "1150") == 1_800


def test_a_teammate_who_sees_the_bill_but_not_its_line_tags_cannot_expense_a_month_of_it(books):
    """A payables view shows the bill, but the month posts Dr the line's expense account with its tags, so the asker must
    also be able to write that account and those tags (may_write). Refused as denied; the owner can expense the month."""
    with books.transaction() as c:
        _bid, line = _entered_prepaid(c, {"teammate": "human:ben"})
        assert act(c, OWNER, "view_grant", {"subject": "human:cara", "what": "payables"}).status == "committed"
        assert act(c, OWNER, "right_grant", {"teammate": "human:cara", "right": "finance.book"}).status == "committed"
        got = act(c, "human:cara", "prepaid_amortize", {"bill_line_id": line, "month": "2026-11"}, at=DEC_15)
        assert (got.status, got.code) == ("denied", DENIED), got
        assert _balance_of(c, "1150") == 1_800
        owner = act(c, OWNER, "prepaid_amortize", {"bill_line_id": line, "month": "2026-11"}, at=DEC_15)
        assert owner.status == "committed", owner
        assert _balance_of(c, "1150") == 1_200


def test_a_bill_emailed_to_the_bookkeeper_is_in_an_inbox_only_for_a_viewer_who_sees_bills(books):
    """A finance.ap right alone shows nothing of the bills side (the bill_to_enter rule); the emailed bills, which are
    not yet bills, follow it too. The owner sees the one emailed bill."""
    with books.transaction() as c:
        c.execute("INSERT INTO bills_inbound(id,delivery,received_at,sender,subject,name,mime,sha256,state,file_id,"
                  "updated_at) VALUES('bil_1','dlv_1','2026-10-30T10:00:00Z','ap@acme.example','Invoice 1042',"
                  "'invoice.pdf','application/pdf',?,'needs_entry','fil_1',?)", ("a" * 64, AT))
        assert act(c, OWNER, "right_grant", {"teammate": "human:fay", "right": "finance.ap"}).status == "committed"
    with books.read() as c:
        assert [x["id"] for x in FR.inbox(c, OWNER, kind="bill_email", at=AT)["items"]] == ["bil_1"]
        assert FR.inbox(c, "human:fay", kind="bill_email", at=AT)["items"] == []
        assert "bill_email" not in FR.inbox(c, "human:fay", at=AT)["counts"]


def _budget(c, amount: int = 50_000) -> str:
    made = act(c, OWNER, "budget_create", {"name": "Tools", "account_id": account(c, "6100"), "amount": amount,
                                           "at_limit": "decline"})
    assert made.status == "committed", (made.code, made.reason)
    return str(made.result["budget_id"])


def test_a_card_retried_with_its_key_replays_though_its_mercury_user_was_found_not_named(books):
    """A card for a teammate is issued to the Mercury user with the holder's email. That user is resolved by the planner
    and stored with the request, so a retry with the same key that names no user is the same request: it replays the
    committed card. A retry naming a different user is a different request and is refused as a conflict."""
    fake = FakeMercury()
    connect(books, fake)
    fake.queue["create_card"] = [{**MERCURY["card_created"], "id": "card-ben", "lastFour": "3333"}]
    with books.transaction() as c:
        request = {"holder": "human:ben", "budget_id": _budget(c), "amount": 20_000}
        first = act(c, OWNER, "card_create", dict(request), key="card-ben-1")
        assert first.status == "committed", (first.code, first.reason)
        again = act(c, OWNER, "card_create", dict(request), key="card-ben-1")
        assert (again.status, again.replayed, again.action_id) == ("committed", True, first.action_id), again
        other = act(c, OWNER, "card_create", {**request, "provider_user_id": "user-dan"}, key="card-ben-1")
        assert (other.status, other.code) == ("failed", CONFLICT), other
        assert c.execute("SELECT count(*) FROM fin_cards").fetchone()[0] == 1
    sync_once(books, fake)
    assert fake.sent("create_card") == [("user-ben", 20_000)]
