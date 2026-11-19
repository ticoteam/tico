"""A posting rule with apply_from posts past rows still waiting for a category, as a new row would be."""

from backend.tests.helpers.finance_banking import _txn
from backend.tests.helpers.finance_fixtures import (
    OWNER,
    FakeMercury,
    account,
    act,
    books,  # noqa: F401
    close_month,
    connect,
    finance_template,  # noqa: F401
    reopen_rows,
    store,  # noqa: F401
    sync_once,
)


def _rule(c, name, counterparty, code, **extra):
    payload = {
        "kind": "posting",
        "name": name,
        "when": {"counterparty": counterparty},
        "outcome": {"outcome": "post", "account_id": account(c, code)},
        "examples": [{"input": {"counterparty": counterparty}, "expect": "post"}],
        **extra,
    }
    done = act(c, OWNER, "rule_create", payload)
    assert done.status == "committed", done
    return done.result


def _feed(c, provider_id):
    return c.execute("SELECT id, status, entry_id FROM fin_feed_txns WHERE provider_id=?", (provider_id,)).fetchone()


def _codes(c, entry_id):
    return sorted(
        (str(r[0]), int(r[1]))
        for r in c.execute(
            "SELECT a.code, l.amount FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id WHERE l.entry_id=?",
            (entry_id,),
        )
    )


def _asks(c, feed_id):
    return c.execute(
        "SELECT count(*) FROM fin_events WHERE name='finance.feed.needs_category' AND subject=? AND drained_at IS NULL",
        (feed_id,),
    ).fetchone()[0]


def _past(books, *rows):
    fake = FakeMercury()
    connect(books, fake)
    for raw in rows:
        fake.txns[raw["id"]] = raw
    sync_once(books, fake)
    return fake


def test_a_new_rule_with_apply_from_posts_a_past_default_debit_and_a_past_waiting_credit(books):
    _past(
        books,
        _txn("cloud-1", "acct-chk", -40.0, "other", day="2026-10-02", counterpartyName="Example Cloud"),
        _txn("client-1", "acct-chk", 250.0, "other", day="2026-10-03", counterpartyName="Example Client"),
        _txn("cloud-0", "acct-chk", -30.0, "other", day="2026-09-20", counterpartyName="Example Cloud"),
    )
    with books.transaction() as c:
        debit, credit, early = _feed(c, "cloud-1"), _feed(c, "client-1"), _feed(c, "cloud-0")
        assert ("6900", 4_000) in _codes(c, debit[2]) and _asks(c, debit[0]) == 1  # the ingest default
        assert tuple(credit[1:]) == ("new", None) and _asks(c, credit[0]) == 1
        done = _rule(c, "Cloud", "Example Cloud", "6100", apply_from="2026-10-01")
        assert {k: done[k] for k in ("kind", "reposted", "skipped_closed", "capped")} == {
            "kind": "posting",
            "reposted": 1,
            "skipped_closed": 0,
            "capped": False,
        }
        assert _rule(c, "Client", "Example Client", "4000", apply_from="2026-10-01")["reposted"] == 1
        now = _feed(c, "cloud-1")
        assert now[1] == "posted" and now[2] != debit[2]
        assert ("6100", 4_000) in _codes(c, now[2]) and "6900" not in dict(_codes(c, now[2]))
        old = c.execute("SELECT status FROM fin_entries WHERE id=?", (debit[2],)).fetchone()[0]
        assert old == "reversed"  # the Other expenses entry is taken back, so the books stay balanced
        assert _asks(c, debit[0]) == 0
        paid = _feed(c, "client-1")
        assert paid[1] == "posted" and ("4000", -25_000) in _codes(c, paid[2]) and _asks(c, credit[0]) == 0
        assert _feed(c, "cloud-0")[2] == early[2]  # dated before apply_from: untouched
        assert c.execute("SELECT coalesce(sum(amount), 0) FROM fin_lines").fetchone()[0] == 0


def test_apply_from_leaves_a_row_a_person_categorized(books):
    _past(books, _txn("cloud-1", "acct-chk", -40.0, "other", day="2026-10-02", counterpartyName="Example Cloud"))
    with books.transaction() as c:
        fid = _feed(c, "cloud-1")[0]
        kept = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6900")})
        assert kept.status == "committed", kept
        entry = _feed(c, "cloud-1")[2]
        assert _rule(c, "Cloud", "Example Cloud", "6100", apply_from="2026-10-01")["reposted"] == 0
        assert _feed(c, "cloud-1")[2] == entry and ("6900", 4_000) in _codes(c, entry)


def test_a_higher_priority_older_rule_still_wins_a_row_both_match(books):
    _past(books, _txn("cloud-1", "acct-chk", -40.0, "other", day="2026-10-02", counterpartyName="Example Cloud"))
    with books.transaction() as c:
        assert "reposted" not in _rule(c, "First", "Example Cloud", "6150", priority=10)
        assert ("6900", 4_000) in _codes(c, _feed(c, "cloud-1")[2])  # a rule alone changes no past row
        assert _rule(c, "Later", "Example Cloud", "6100", priority=50, apply_from="2026-10-01")["reposted"] == 1
        assert ("6150", 4_000) in _codes(c, _feed(c, "cloud-1")[2])


def test_a_row_in_a_closed_period_is_skipped_and_counted(books):
    _past(
        books,
        _txn("cloud-sep", "acct-chk", -30.0, "other", day="2026-09-20", counterpartyName="Example Cloud"),
        _txn("cloud-oct", "acct-chk", -40.0, "other", day="2026-10-02", counterpartyName="Example Cloud"),
    )
    with books.transaction() as c:
        assert close_month(c, "2026-09").status == "committed"
        reopen_rows(c)  # not reconciled any more; the month stays closed
        entry = _feed(c, "cloud-sep")[2]
        done = _rule(c, "Cloud", "Example Cloud", "6100", apply_from="2026-09-01")
        assert (done["reposted"], done["skipped_closed"]) == (1, 1)
        assert _feed(c, "cloud-sep")[2] == entry
        assert ("6100", 4_000) in _codes(c, _feed(c, "cloud-oct")[2])


def test_without_apply_from_past_rows_stay_and_a_gate_rule_refuses_it(books):
    _past(
        books,
        _txn("cloud-1", "acct-chk", -40.0, "other", day="2026-10-02", counterpartyName="Example Cloud"),
        _txn("client-1", "acct-chk", 250.0, "other", day="2026-10-03", counterpartyName="Example Client"),
    )
    with books.transaction() as c:
        entry = _feed(c, "cloud-1")[2]
        rid = _rule(c, "Cloud", "Example Cloud", "6100")["rule_id"]
        _rule(c, "Client", "Example Client", "4000")
        assert _feed(c, "cloud-1")[2] == entry and tuple(_feed(c, "client-1")[1:]) == ("new", None)
        moved = act(c, OWNER, "rule_update", {"rule_id": rid, "apply_from": "2026-10-01"})
        assert moved.status == "committed" and moved.result["reposted"] == 1  # rule_update takes it too
        gate = {
            "kind": "gate",
            "name": "Journals",
            "when": {"kind": "journal"},
            "outcome": {"outcome": "deny", "reason": "frozen"},
            "examples": [{"input": {"kind": "journal"}, "expect": "deny"}],
            "apply_from": "2026-10-01",
        }
        refused = act(c, OWNER, "rule_create", gate)
        assert refused.status != "committed" and "apply_from is for posting rules" in refused.reason, refused


# ------------------------------------------------------------------------------------------------ what it leaves alone
def _apply(c, counterparty, code, day="2026-10-01"):
    return _rule(c, f"Rule {counterparty}", counterparty, code, apply_from=day)


def test_a_deposit_waiting_for_its_stripe_payout_is_left_waiting(books):
    _past(books, _txn("dep-1", "acct-chk", 96.8, "externalTransfer", day="2026-10-05", counterpartyName="STRIPE"))
    with books.transaction() as c:
        assert tuple(_feed(c, "dep-1")[1:]) == ("new", None)
        assert _apply(c, "STRIPE", "4000")["reposted"] == 0
        assert tuple(_feed(c, "dep-1")[1:]) == ("new", None)


def test_a_refund_that_could_be_of_several_debits_keeps_waiting_with_its_ask(books):
    _past(
        books,
        _txn("shop-1", "acct-chk", -40.0, "other", day="2026-10-01", counterpartyName="Example Shop"),
        _txn("shop-2", "acct-chk", -60.0, "other", day="2026-10-02", counterpartyName="Example Shop"),
        _txn("shop-back", "acct-chk", 30.0, "other", day="2026-10-05", counterpartyName="Example Shop"),
    )
    with books.transaction() as c:
        back = _feed(c, "shop-back")[0]
        assert _asks(c, back) == 1
        assert _apply(c, "Example Shop", "6100")["reposted"] == 2  # the two debits; the refund stays for a person
        assert tuple(_feed(c, "shop-back")[1:]) == ("new", None) and _asks(c, back) == 1


def test_a_debit_once_held_for_two_bills_is_not_expensed_when_one_is_voided(books):
    from backend.tests.helpers.finance_ap import _bill, _vendor

    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        vendor = _vendor(c, "Example Supplies")
        bills = [
            _bill(c, vendor, [{"account_id": account(c, "6100"), "amount": 12_345}], number=f"B-{n}") for n in (1, 2)
        ]
        for b in bills:
            assert act(c, OWNER, "bill_approve", {"bill_id": b}).status == "committed"
    fake.txns["sup-1"] = _txn(
        "sup-1", "acct-chk", -123.45, "other", day="2026-10-14", counterpartyName="Example Supplies"
    )
    sync_once(books, fake)
    with books.transaction() as c:
        assert tuple(_feed(c, "sup-1")[1:]) == ("new", None)  # either bill may be the one it pays
        assert act(c, OWNER, "bill_void", {"bill_id": bills[0], "reason": "entered twice"}).status == "committed"
        assert _apply(c, "Example Supplies", "6150")["reposted"] == 0
        assert tuple(_feed(c, "sup-1")[1:]) == ("new", None)
        assert (
            c.execute(
                "SELECT COUNT(*) FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id WHERE a.code='6150'"
            ).fetchone()[0]
            == 0
        )


def test_a_row_a_contact_default_put_on_other_expenses_on_purpose_stays(books):
    fake = FakeMercury()
    connect(books, fake)
    with books.transaction() as c:
        made = act(
            c,
            OWNER,
            "contact_create",
            {"name": "Example Hosting", "kind": "vendor", "default_account_id": account(c, "6900")},
        )
        assert made.status == "committed", made
    fake.txns["host-1"] = _txn(
        "host-1", "acct-chk", -40.0, "other", day="2026-10-02", counterpartyName="Example Hosting"
    )
    sync_once(books, fake)
    with books.transaction() as c:
        fid, _, entry = _feed(c, "host-1")
        assert ("6900", 4_000) in _codes(c, entry) and _asks(c, fid) == 0  # no ask: not the automatic default
        assert _apply(c, "Example Hosting", "6100")["reposted"] == 0
        assert _feed(c, "host-1")[2] == entry


def test_a_debit_carrying_a_canceled_payments_key_in_its_note_is_left_for_a_person(books, monkeypatch):
    from backend.finance import banking

    monkeypatch.setattr(
        banking, "_canceled", lambda ctx, money, t: {"id": "pay-made-up"} if "tico:key-1" in t.note else None
    )
    _past(
        books,
        _txn(
            "sent-1", "acct-chk", -50.0, "other", day="2026-10-02", counterpartyName="Example Payee", note="tico:key-1"
        ),
    )
    with books.transaction() as c:
        assert tuple(_feed(c, "sent-1")[1:]) == ("new", None)
        assert _apply(c, "Example Payee", "6100")["reposted"] == 0
        assert tuple(_feed(c, "sent-1")[1:]) == ("new", None)


def test_a_reversed_row_and_a_zero_row_are_left_alone(books):
    _past(
        books,
        _txn("back-1", "acct-chk", 250.0, "other", day="2026-10-03", counterpartyName="Example Client"),
        _txn("zero-1", "acct-chk", 75.0, "other", day="2026-10-04", counterpartyName="Example Client"),
    )
    with books.transaction() as c:
        c.execute("UPDATE fin_feed_txns SET provider_status='reversed' WHERE provider_id='back-1'")
        c.execute("UPDATE fin_feed_txns SET amount=0 WHERE provider_id='zero-1'")
        done = _apply(c, "Example Client", "4000")
        assert (done["reposted"], done.get("failed", 0)) == (0, 0)
        assert tuple(_feed(c, "back-1")[1:]) == ("new", None) and tuple(_feed(c, "zero-1")[1:]) == ("new", None)


def test_a_row_that_raises_is_undone_alone_and_the_others_post(books, monkeypatch):
    from backend.finance import banking

    _past(
        books,
        _txn("cloud-1", "acct-chk", -40.0, "other", day="2026-10-02", counterpartyName="Example Cloud"),
        _txn("cloud-2", "acct-chk", -45.0, "other", day="2026-10-03", counterpartyName="Example Cloud"),
    )
    real = banking._settle

    def flaky(ctx, row, money, card, t):
        if t.provider_id == "cloud-1":
            raise RuntimeError("made-up failure")
        return real(ctx, row, money, card, t)

    monkeypatch.setattr(banking, "_settle", flaky)
    with books.transaction() as c:
        bad = _feed(c, "cloud-1")
        done = _apply(c, "Example Cloud", "6100")
        assert (done["reposted"], done["failed"]) == (1, 1)
        assert _feed(c, "cloud-1")[2] == bad[2] and ("6900", 4_000) in _codes(c, bad[2]) and _asks(c, bad[0]) == 1
        assert ("6100", 4_500) in _codes(c, _feed(c, "cloud-2")[2])


def _brex_charge(books, line_code=""):
    """A past Brex card charge on the card's default line: its budget's line when `line_code` gives the card a budget
    on that line (before the charge comes in), else Other expenses."""
    from backend.tests.test_finance_brex import FakeBrex, brex_pass, card_txn, connect_brex

    fake = FakeBrex()
    connect_brex(books, fake)
    fake.card_txns.append(card_txn("ctx_first", "PURCHASE", -300, day="2026-10-04", merchant="EXAMPLE SHOP"))
    assert brex_pass(books, fake) == "ok"  # the card comes in with its first charge
    if line_code:
        with books.transaction() as c:
            c.execute(
                "UPDATE fin_cards SET holder=?, budget_id=(SELECT b.id FROM fin_budgets b JOIN fin_accounts a ON "
                "a.id=b.account_id WHERE a.code=? LIMIT 1)",
                (OWNER, line_code),
            )
    fake.card_txns.append(card_txn("ctx_made_up", "PURCHASE", -1_200, day="2026-10-05", merchant="EXAMPLE CAFE"))
    assert brex_pass(books, fake, at="2026-10-15T13:00:00.000000Z") == "ok"


def _card_use(c):
    from backend.finance.store import budgets as SB

    card_id, b = c.execute("SELECT id, budget_id FROM fin_cards").fetchone()
    return SB.card_used(c, [b], card_id, "2026-10-01", "2026-12-31")


def test_apply_from_posts_a_past_card_charge_on_its_default_and_its_card_use_counts_once(books):
    with books.read() as c:
        code = c.execute(
            "SELECT a.code FROM fin_budgets b JOIN fin_accounts a ON a.id=b.account_id WHERE b.at_limit='warn' AND "
            "a.type='expense' AND a.code NOT IN ('6100', '6900') LIMIT 1"
        ).fetchone()[0]
    _brex_charge(books, code)
    with books.transaction() as c:
        fid, _status, entry = _feed(c, "ctx_made_up")
        assert (code, 1_200) in _codes(c, entry)  # the card's default: its budget's line
        used = _card_use(c)
        assert used >= 1_200
        done = _rule(c, "Cafe", "EXAMPLE CAFE", "6100", apply_from="2026-10-01")
        assert done["reposted"] == 1
        posted = _feed(c, "ctx_made_up")
        assert posted[2] != entry and ("6100", 1_200) in _codes(c, posted[2])
        holder = c.execute(
            "SELECT t.value FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id JOIN fin_line_tags t ON "
            "t.line_id=l.id AND t.kind='teammate' WHERE l.entry_id=? AND a.code='6100'",
            (posted[2],),
        ).fetchone()
        assert holder is not None and holder[0] == OWNER  # the card's holder stays on the charge
        assert _card_use(c) == used  # the reversal and the new entry net: the charge counts once
        assert c.execute("SELECT count(*) FROM fin_holds WHERE status='held'").fetchone()[0] == 0


def test_apply_from_posts_a_past_mercury_debit_card_row_on_the_default(books):
    _past(
        books,
        _txn("dc-1", "acct-chk", -25.0, "debitCardTransaction", day="2026-10-04", counterpartyName="Example Diner"),
    )
    with books.transaction() as c:
        fid, _status, entry = _feed(c, "dc-1")
        assert ("6900", 2_500) in _codes(c, entry) and _asks(c, fid) == 1
        assert _rule(c, "Diner", "Example Diner", "6100", apply_from="2026-10-01")["reposted"] == 1
        posted = _feed(c, "dc-1")
        assert ("6100", 2_500) in _codes(c, posted[2]) and _asks(c, fid) == 0


def test_apply_from_leaves_a_card_charge_a_person_categorized(books):
    _brex_charge(books)
    with books.transaction() as c:
        fid = _feed(c, "ctx_made_up")[0]
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6900")}).status == "committed"
        kept = _feed(c, "ctx_made_up")[2]
        assert _rule(c, "Cafe", "EXAMPLE CAFE", "6100", apply_from="2026-10-01")["reposted"] == 0
        assert _feed(c, "ctx_made_up")[2] == kept
