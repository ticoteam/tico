"""Finance's bank feed against Mercury's recorded answers and a fake Mercury: the adapter (Retry-After, the IP hint,
last 4 only), signatures (a wrong one refused, a replay kept once), connecting (bank assets, IO a liability, payments
only with another approver), a card authorization held and then settled in place and posted once, the IO bill clearing
the card liability, and reconciliation's difference, lock and undo. Migration 65 is in test_finance_ledger."""
import io
import json
import time
import urllib.error

from backend import trigger_presets as P
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance import sync
from backend.finance.core import feeds as F
from backend.finance.mercury import IP_HINT, Answer, Mercury, error_text
from backend.tests.test_api import api, post  # noqa: F401
from backend.tests.test_triggers import hook
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, MERCURY, OWNER, FakeMercury, account, act, books, connect, finance_template, store, sync_once)

LATER = "2026-10-15T12:30:00.000000Z"
NEXT_DAY = "2026-10-16T12:30:00.000000Z"


class _Opener:
    """urlopen for the adapter: answers (status, body, headers) in order, remembering each request."""

    def __init__(self, *answers):
        self.answers, self.seen = list(answers), []

    def __call__(self, request, timeout):
        self.seen.append(request)
        status, body, headers = self.answers.pop(0)
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "no", headers, io.BytesIO(json.dumps(body).encode()))
        response = io.BytesIO(json.dumps(body).encode())
        response.status = status
        return response


def test_the_adapter_reads_recorded_answers_waits_out_429_and_names_the_ip_allowlist():
    waits = []
    opener = _Opener((429, {}, {"Retry-After": "2"}), (200, MERCURY["accounts"], {}),
                     (200, MERCURY["credit"], {}), (200, MERCURY["users"], {}), (403, {"message": "ip"}, {}))
    m = Mercury("tok-acme", opener=opener, sleep=waits.append)
    found, why = sync.probe(m)
    assert not why and waits == [2.0]
    assert [a["id"] for a in found["accounts"]] == ["acct-chk", "acct-sav"]            # the external account is not ours
    assert found["accounts"][0]["last4"] == "2222" and found["accounts"][0]["current"] == 12_525_050
    assert "000011112222" not in json.dumps(found)                                      # never the full number
    assert found["credit"][0]["current"] == -125_025
    assert all(r.full_url.startswith("https://api.mercury.com/") for r in opener.seen)
    assert opener.seen[0].get_header("Authorization") == "Bearer tok-acme"
    refused = m.card_state("card-eve", "freeze")
    assert refused.refused_write and error_text(refused, write=True) == IP_HINT


def test_signatures_amount_signs_and_statement_signs():
    body = json.dumps(MERCURY["event_txn_updated"]).encode()
    now = 1_792_000_000
    header = F.sign(body, "whsec-acme", now)
    assert F.verify_signature(header, body, "whsec-acme", now + 60) == (True, "")
    assert not F.verify_signature(header, body, "whsec-other", now)[0]
    assert not F.verify_signature(header, body, "whsec-acme", now + 301)[0]
    assert not F.verify_signature(header, body + b" ", "whsec-acme", now)[0]
    ok, _ = P.verify("mercury", {"mercury-signature": header}, body, "whsec-acme", now)
    assert ok and P.body_id("mercury", body) == "evt-0002"
    assert not P.verify("mercury", {"mercury-signature": F.sign(body, "whsec-x", now)}, body, "whsec-acme", now)[0]
    assert not P.verify("mercury", {}, body, "whsec-acme", now)[0]
    pending = F.mercury_txn(MERCURY["txn_card_pending"])
    assert pending.amount == -4_250 and pending.pending and pending.txn_date == "2026-10-14"
    assert F.mercury_txn(MERCURY["txn_deposit"]).amount == 50_000
    # Mercury documents both signs for IO statements: either way, what is owed comes out positive.
    assert F.credit_owed(MERCURY["credit_statement_old"]) == (10_000, 125_025)
    assert F.credit_owed(MERCURY["credit_statement_new"]) == (10_000, 125_025)


def test_a_mercury_webhook_reaches_finance_once_and_a_wrong_signature_is_refused(api):
    store = api.app.state.store
    with store.transaction() as c:
        FA.setup(c, OWNER, AT)
    made = post(api, "triggers", {"name": "Mercury"})["trigger"]
    secret = "whsec_acme_0123456789abcdef"
    assert post(api, "triggers/mercury/signing", {"preset": "mercury", "secret": secret})["trigger"]["preset"] == \
        "mercury"
    body = json.dumps(MERCURY["event_txn_updated"]).encode()
    now = int(time.time())
    assert hook(api, made["url"], body, {"Mercury-Signature": F.sign(body, "whsec_other", now)}).status_code == 401
    assert hook(api, made["url"], body, {}).status_code == 401
    first = hook(api, made["url"], body, {"Mercury-Signature": F.sign(body, secret, now)})
    assert first.status_code == 202 and first.json()["delivery"] == "evt-0002"
    assert hook(api, made["url"], body, {"Mercury-Signature": F.sign(body, secret, now + 1)}).status_code == 200
    with store.read() as c:
        assert [tuple(r) for r in c.execute("SELECT provider_event_id FROM fin_inbox")] == [("evt-0002",)]
        assert c.execute("SELECT count(*) FROM tasks WHERE title LIKE '%Mercury%'").fetchone()[0] == 0


def test_connecting_maps_bank_assets_and_io_liability_and_payments_need_another_approver(books):
    fake = FakeMercury()
    same = connect(books, fake, approver="ana@acme.example", creator="ana@acme.example")
    assert same["payments_enabled"] is False and "other than" in str(same["approval_note"])
    with books.read() as c:
        kinds = {(r[0], r[1], r[2]) for r in c.execute(
            "SELECT m.kind, a.code, a.type FROM fin_money_accounts m JOIN fin_accounts a ON a.id=m.account_id")}
        assert kinds == {("bank", "1000", "asset"), ("bank", "1001", "asset"), ("card_program", "2100", "liability")}
        assert c.execute("SELECT count(*) FROM fin_money_accounts WHERE last4='2222'").fetchone()[0] == 1
    with books.transaction() as c:
        again = act(c, OWNER, "connection_create", {"provider": "mercury", "credential_id": "cred-mercury",
                                                   "probe": sync.probe(fake)[0]})
        assert again.code == "conflict"
        bot = act(c, "bot:coder", "connection_create", {"provider": "mercury", "credential_id": "cred-2",
                                                        "probe": sync.probe(fake)[0]})
        assert bot.status in ("denied", "failed") and bot.code == "denied"


def _card_for_eve(store, fake):
    """A Cloud budget on 6100 and a 200.00 card for eve on it, synced to Mercury (provider id card-eve)."""
    with store.transaction() as c:
        b = act(c, OWNER, "budget_create", {"name": "Cloud", "account_id": account(c, "6100"), "amount": 100_000,
                                            "at_limit": "decline"})
        made = act(c, OWNER, "card_create", {"holder": "human:eve", "budget_id": b.result["budget_id"],
                                             "amount": 20_000, "provider_user_id": "user-eve"})
        assert made.status == "committed", made
    return str(b.result["budget_id"]), str(made.result["card_id"])


def _entries(c, source):
    return c.execute("SELECT count(*) FROM fin_entries WHERE source=?", (source,)).fetchone()[0]


def _card_holds(c):
    return [tuple(r) for r in c.execute("SELECT amount, status FROM fin_holds WHERE source='card'")]


def _arrives(fake, raw, version, event_id):
    """Mercury has the transaction at this version, and an event says so."""
    fake.txns[str(raw["id"])] = dict(raw)
    fake.events_list.append({"id": event_id, "resourceType": "transaction", "resourceId": raw["id"],
                             "operationType": "create" if version == 1 else "update", "resourceVersion": version})


def test_an_authorization_holds_settles_in_place_posts_once_and_the_io_bill_clears_it(books):
    fake = FakeMercury()
    connect(books, fake)
    budget_id, card_id = _card_for_eve(books, fake)
    assert sync_once(books, fake) == "ok"                                 # first sync: the card is made at Mercury
    assert fake.sent("create_card") == [("user-eve", 20_000)]
    with books.read() as c:
        card = c.execute("SELECT provider_card_id, last4, provider_limit FROM fin_cards WHERE id=?", (card_id,)).fetchone()
        assert tuple(card) == ("card-eve", "4242", 20_000)                 # the provider limit is the allocation
    _arrives(fake, MERCURY["txn_card_pending"], 1, "evt-0001")
    sync_once(books, fake, LATER)
    with books.read() as c:
        row = c.execute("SELECT pending, entry_id, card_id FROM fin_feed_txns").fetchone()
        assert tuple(row) == (1, None, card_id)
        assert _card_holds(c) == [(4_250, "held")]                   # an authorization is a hold, not spend
    # Mercury posts it for a little more: a webhook delivery (deduped on its event id) brings version 2.
    fake.txns["txn-card-1"] = dict(MERCURY["txn_card_posted"])
    with books.transaction() as c:
        assert sync.delivery(c, None, MERCURY["event_txn_updated"], LATER) is True
        assert sync.delivery(c, None, MERCURY["event_txn_updated"], LATER) is False        # a replay is kept once
    sync_once(books, fake, LATER)
    with books.transaction() as c:
        rows = c.execute("SELECT pending, amount, status, entry_id FROM fin_feed_txns").fetchall()
        assert len(rows) == 1 and tuple(rows[0][:3]) == (0, -4_500, "posted")
        assert _entries(c, "card") == 1
        assert _card_holds(c) == [(4_250, "settled")]               # the settlement absorbed the hold
        assert FA.budget_state(c, budget_id, LATER).actual == 4_500
        lines = {r[0]: r[1] for r in c.execute(
            "SELECT a.code, l.amount FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id WHERE l.entry_id=?",
            (rows[0][3],))}
        assert lines == {"6100": 4_500, "2100": -4_500}
        sheet = FR.balance_sheet(c, OWNER, at=LATER)
        assert next(r for r in sheet["liabilities"] if r["code"] == "2100")["balance"] == 4_500
    # The same version again, from the Events API catch-up, changes nothing.
    fake.events_list.append(dict(MERCURY["event_txn_updated"]))
    sync_once(books, fake, NEXT_DAY)
    # The IO bill: checking pays the card; both sides become one transfer and the liability is cleared.
    _arrives(fake, MERCURY["txn_card_bill_bank"], 1, "evt-bill-bank")
    _arrives(fake, MERCURY["txn_card_bill_io"], 1, "evt-bill-io")
    sync_once(books, fake, "2026-10-17T12:30:00.000000Z")
    with books.read() as c:
        assert _entries(c, "card") == 1
        sheet = FR.balance_sheet(c, OWNER, at="2026-10-17T12:30:00.000000Z")
        assert next(r for r in sheet["liabilities"] if r["code"] == "2100")["balance"] == 0
        bill = c.execute("SELECT count(DISTINCT entry_id), min(status) FROM fin_feed_txns WHERE provider_id LIKE "
                         "'txn-bill-%'").fetchone()
        assert tuple(bill) == (1, "posted")


def test_a_voided_authorization_releases_its_hold(books):
    fake = FakeMercury()
    connect(books, fake)
    _card_for_eve(books, fake)
    sync_once(books, fake)
    _arrives(fake, MERCURY["txn_card_pending"], 1, "evt-0001")
    sync_once(books, fake, LATER)
    _arrives(fake, {**MERCURY["txn_card_pending"], "status": "cancelled"}, 3, "evt-void")
    sync_once(books, fake, NEXT_DAY)
    with books.read() as c:
        assert _card_holds(c) == [(4_250, "released")]
        assert c.execute("SELECT status FROM fin_feed_txns").fetchone()[0] == "ignored"
        assert _entries(c, "card") == 0


def test_reconciliation_difference_lock_and_undo(books):
    fake = FakeMercury()
    connect(books, fake)
    for key in ("txn_bank_fee", "txn_deposit"):
        fake.txns[str(MERCURY[key]["id"])] = dict(MERCURY[key])
    sync_once(books, fake)
    with books.transaction() as c:
        bank = str(c.execute("SELECT m.id FROM fin_money_accounts m JOIN fin_accounts a ON a.id=m.account_id "
                             "WHERE a.code='1000'").fetchone()[0])
        fee, dep = (str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id=?", (p,)).fetchone()[0])
                    for p in ("txn-fee", "txn-dep"))
        posted = act(c, OWNER, "categorize", {"feed_id": dep, "account_id": account(c, "4000")})
        assert posted.status == "committed", posted
        off = act(c, OWNER, "reconcile", {"money_account_id": bank, "statement_end": "2026-10-14",
                                          "statement_balance": 40_000})
        assert off.status == "committed" and off.result["status"] == "open" and off.result["difference"] == 40_000 - 48_500
        assert c.execute("SELECT count(*) FROM fin_feed_txns WHERE reconciliation_id IS NOT NULL").fetchone()[0] == 0
    with books.transaction() as c:
        c.execute("DELETE FROM fin_reconciliations")
        done = act(c, OWNER, "reconcile", {"money_account_id": bank, "statement_end": "2026-10-14",
                                           "statement_balance": 48_500, "cleared": [fee, dep]})
        assert done.result == {**done.result, "status": "done", "difference": 0, "cleared": 2}
        moved = act(c, OWNER, "categorize", {"feed_id": dep, "account_id": account(c, "4900")
                                             if c.execute("SELECT 1 FROM fin_accounts WHERE code='4900'").fetchone()
                                             else account(c, "6900")})
        assert moved.code == "conflict" and "reconciled" in moved.reason
        entry = str(c.execute("SELECT entry_id FROM fin_feed_txns WHERE id=?", (fee,)).fetchone()[0])
        assert act(c, OWNER, "journal", {"reverses": entry}).status != "committed"
        again = act(c, OWNER, "reconcile", {"money_account_id": bank, "statement_end": "2026-10-13",
                                            "statement_balance": 0})
        assert again.code == "conflict"
        listed = FR.reconciliations(c, OWNER, at=AT)
        assert listed["locked_through"][bank] == "2026-10-14"
        assert FR.reconciliations(c, "bot:coder", at=AT)["reconciliations"] == []
        undone = act(c, OWNER, "reconcile_undo", {"reconciliation_id": done.result["reconciliation_id"]})
        assert undone.status == "committed"
        assert act(c, OWNER, "categorize", {"feed_id": dep, "account_id": account(c, "6900")}).status == "committed"


def test_statements_come_from_the_api_with_the_card_sign_normalized(books):
    fake = FakeMercury()
    connect(books, fake)
    sync_once(books, fake)
    with books.read() as c:
        rows = {r["source"] + ":" + str(r["ending_balance"]) for r in FR.statements(c, OWNER, at=AT)["statements"]}
        assert rows == {"api:12000000", "api:125025"}
        assert FR.statements(c, "bot:coder", at=AT)["statements"] == []


def test_a_rehearsal_copy_calls_nothing(books):
    fake = FakeMercury()
    connect(books, fake)
    assert sync_once(books, fake, rehearsal=True) == "withheld" and fake.calls == [
        ("accounts", None), ("credit", None), ("users", None)]          # only the probe connect() made
    assert Answer(None, {}).ok is False


# ------------------------------------------------------------------------------------------------ review fixes
def _txn(raw_id, account, amount, kind, day="2026-10-14", **extra):
    return {**MERCURY["txn_bank_fee"], "id": raw_id, "accountId": account, "amount": amount, "kind": kind,
            "createdAt": f"{day}T10:00:00Z", "postedAt": f"{day}T10:00:00Z", "counterpartyName": "Acme", **extra}


def _row(c, provider_id):
    return c.execute("SELECT status, entry_id FROM fin_feed_txns WHERE provider_id=?", (provider_id,)).fetchone()


def test_transfers_pair_only_transfer_kinds_and_io_charges_on_unknown_cards_post(books):
    """I and S: a posted non-transfer row is never unposted to pair a transfer; two possible counterparts wait for a
    person; a charge on a card Tico didn't issue posts to Other expenses against the card liability."""
    fake = FakeMercury()
    connect(books, fake)
    fake.txns["txn-fee"] = dict(MERCURY["txn_bank_fee"])                                     # -15.00, posts
    fake.txns["txn-in"] = _txn("txn-in", "acct-sav", 15.0, "internalTransfer", day="2026-10-12")
    fake.txns["txn-in1"] = _txn("txn-in1", "acct-sav", 100.0, "internalTransfer")
    fake.txns["txn-in2"] = _txn("txn-in2", "acct-sav", 100.0, "treasuryTransfer")
    fake.txns["txn-out"] = _txn("txn-out", "acct-chk", -100.0, "internalTransfer")          # two could be its pair
    fake.txns["txn-io"] = _txn("txn-io", "credit-io", -30.0, "creditCardTransaction", cardId="card-unknown")
    sync_once(books, fake)
    with books.read() as c:
        assert _row(c, "txn-fee")[1] is not None                                                 # stays posted
        assert _row(c, "txn-in")[1] is None                                                      # waits, unpaired
        assert all(_row(c, p)[1] is None for p in ("txn-out", "txn-in1", "txn-in2"))             # ambiguous
        io_entry = _row(c, "txn-io")[1]
        codes = sorted(str(r[0]) for r in c.execute(
            "SELECT a.code FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id WHERE l.entry_id=?", (io_entry,)))
        assert codes[1] == "6900" and codes[0] != "1000"                                       # Other vs IO liability


def test_a_settled_card_charge_uses_its_allocation_at_its_actual_amount(books):
    """P: a card charge first seen settled (a backfill, no authorization) still uses the card's allocation, and a
    refund gives it back."""
    fake = FakeMercury()
    connect(books, fake)
    budget_id, card_id = _card_for_eve(books, fake)
    sync_once(books, fake)
    fake.txns["txn-c1"] = _txn("txn-c1", "credit-io", -45.0, "creditCardTransaction", cardId="card-eve")
    fake.txns["txn-c2"] = _txn("txn-c2", "credit-io", 5.0, "creditCardTransaction", cardId="card-eve")
    with books.transaction() as c:
        c.execute("UPDATE fin_connections SET settings_json='{}', cursor=NULL")             # backfill again
    sync_once(books, fake, "2026-10-15T14:00:00.000000Z")
    with books.read() as c:
        used = c.execute("SELECT sum(settled_amount) FROM fin_holds WHERE target=? AND status='settled'",
                         (f"card:{card_id}",)).fetchone()[0]
        assert used == 4_000
        assert FA.budget_state(c, budget_id, AT).held == 20_000 - 4_000                      # reserve less the use


def test_the_same_mercury_accounts_through_a_new_token_are_the_same_money_accounts(books):
    """J: reconnecting with another credential reuses the money accounts by provider id; nothing is taken in twice."""
    fake = FakeMercury()
    connect(books, fake)
    fake.txns["txn-fee"] = dict(MERCURY["txn_bank_fee"])
    sync_once(books, fake)
    with books.read() as c:
        before = sorted(str(r[0]) for r in c.execute("SELECT id FROM fin_money_accounts"))
    found, why = sync.probe(fake)
    assert not why
    with books.transaction() as c:
        again = act(c, OWNER, "connection_create", {"provider": "mercury", "credential_id": "cred-mercury-2",
                                                    "probe": found, "approver_email": "ben@acme.example",
                                                    "token_creator_email": "ana@acme.example"})
        assert again.status == "committed", again
    with books.transaction() as c:
        c.execute("UPDATE fin_connections SET settings_json='{}', cursor=NULL")
    sync_once(books, fake, "2026-10-15T14:00:00.000000Z")
    with books.read() as c:
        assert sorted(str(r[0]) for r in c.execute("SELECT id FROM fin_money_accounts")) == before
        assert c.execute("SELECT count(*) FROM fin_feed_txns WHERE provider_id='txn-fee'").fetchone()[0] == 1
        assert _entries(c, "bank") == 1


def test_a_payment_settles_on_its_payees_contact_so_the_vendor_hold_and_actual_meet(books):
    """K: the settled line carries the recipient's contact, not the bank's counterparty text."""
    from backend.tests.test_finance_payments import _checking, _payee
    fake = FakeMercury()
    connect(books, fake)
    rid = _payee(books, fake)
    with books.transaction() as c:
        act(c, OWNER, "rule_disable", {"rule_id": "rule:new-recipient"})
        made = act(c, OWNER, "payment_create", {"recipient_id": rid, "amount": 20_000, "account_id": account(c, "6100"),
                                                "money_account_id": _checking(c)})
        assert made.status == "committed", made
    sync_once(books, fake, "2026-10-15T14:00:00.000000Z")
    fake.txns["txn-pay"] = _txn("txn-pay", "acct-chk", -200.0, "outgoingPayment", day="2026-10-15",
                                counterpartyName="ACME SUPPLIES LLC", requestId="req-1")
    fake.events_list.append({"id": "evt-pay", "resourceType": "transaction", "resourceId": "txn-pay",
                             "operationType": "create", "resourceVersion": 1})
    sync_once(books, fake, "2026-10-15T16:00:00.000000Z")
    with books.read() as c:
        vendor = c.execute("SELECT l.contact_id FROM fin_lines l JOIN fin_feed_txns f ON f.entry_id=l.entry_id "
                           "WHERE f.provider_id='txn-pay' AND l.amount>0").fetchone()[0]
        assert vendor == "Acme Supplies"
        assert c.execute("SELECT status FROM fin_holds WHERE source='payment'").fetchone()[0] == "settled"


def test_a_row_the_books_dont_have_is_outstanding_never_cleared(books):
    """O: an uncategorized deposit is not in the books, so a reconciliation can't clear or lock it, and can't be done
    while the books miss it."""
    fake = FakeMercury()
    connect(books, fake)
    for key in ("txn_bank_fee", "txn_deposit"):
        fake.txns[str(MERCURY[key]["id"])] = dict(MERCURY[key])
    sync_once(books, fake)
    with books.transaction() as c:
        bank = str(c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0])
        made = act(c, OWNER, "reconcile", {"money_account_id": bank, "statement_end": "2026-10-14",
                                           "statement_balance": 48_500})                  # -15 + 500
        assert made.result["status"] == "open" and made.result["outstanding"] == 1, made
        assert made.result["outstanding_total"] == 50_000 and made.result["cleared"] == 1
        dep = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-dep'").fetchone()[0])
        assert act(c, OWNER, "reconcile", {"money_account_id": bank, "statement_end": "2026-10-14",
                                           "statement_balance": 48_500, "cleared": [dep]}).code == "invalid"
        assert c.execute("SELECT count(*) FROM fin_feed_txns WHERE reconciliation_id IS NOT NULL").fetchone()[0] == 0


def test_an_adjusted_or_voided_settled_card_charge_keeps_one_use_record(books):
    """N3: a charge first seen settled keeps one use record: an adjustment moves it, a date shift to an open day is no
    change, and a void gives it back."""
    fake = FakeMercury()
    connect(books, fake)
    budget_id, card_id = _card_for_eve(books, fake)
    sync_once(books, fake)
    raw = _txn("txn-c9", "credit-io", -80.0, "creditCardTransaction", cardId="card-eve")
    _arrives(fake, raw, 1, "evt-c9-1")
    sync_once(books, fake, "2026-10-15T14:00:00.000000Z")
    _arrives(fake, {**raw, "amount": -90.0}, 2, "evt-c9-2")                     # the bank adjusts it
    sync_once(books, fake, "2026-10-15T15:00:00.000000Z")

    def used(c):
        return c.execute("SELECT count(*), sum(settled_amount) FROM fin_holds WHERE target=? AND status='settled' "
                         "AND source='card'", (f"card:{card_id}",)).fetchone()
    with books.read() as c:
        assert tuple(used(c)) == (1, 9_000)
        assert FA.budget_state(c, budget_id, AT).held == 20_000 - 9_000
        assert _entries(c, "card") == 3                                          # post, reversal, repost
    _arrives(fake, {**raw, "amount": -90.0, "status": "cancelled"}, 3, "evt-c9-3")
    sync_once(books, fake, "2026-10-15T16:00:00.000000Z")
    with books.read() as c:
        assert used(c)[0] == 0
        assert FA.budget_state(c, budget_id, AT).held == 20_000                  # the whole slice is back


def test_a_correction_to_a_charge_in_a_closed_month_reverses_on_an_open_day(books):
    """G-new-2: a posted transaction whose month is now closed is corrected by a reversal and a repost on open days,
    not retried forever; a later version with the same amount and bank date is no change."""
    fake = FakeMercury()
    connect(books, fake)
    raw = _txn("txn-sep", "acct-chk", -15.0, "wireFee", day="2026-09-20")
    _arrives(fake, raw, 1, "evt-sep-1")
    sync_once(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
    _arrives(fake, {**raw, "amount": -25.0}, 2, "evt-sep-2")
    sync_once(books, fake, "2026-10-15T14:00:00.000000Z")
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_inbox WHERE provider_event_id='evt-sep-2'").fetchone()[0] == "done"
        days = sorted(str(r[0]) for r in c.execute("SELECT entry_date FROM fin_entries WHERE source='bank'"))
        assert days == ["2026-09-20", AT[:10], AT[:10]]                         # original, reversal, repost
    _arrives(fake, {**raw, "amount": -25.0, "note": "seen again"}, 3, "evt-sep-3")
    sync_once(books, fake, "2026-10-15T15:00:00.000000Z")
    with books.read() as c:
        assert _entries(c, "bank") == 3                                          # the open-day shift is no change
