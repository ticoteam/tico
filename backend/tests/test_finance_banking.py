"""Finance's bank feed against Mercury's recorded answers and a fake Mercury: the adapter (Retry-After, the IP hint,
last 4 only), signatures (a wrong one refused, a replay kept once), connecting (bank assets, IO a liability, payments
only with another approver), a card authorization held and then settled in place and posted once, the IO bill clearing
the card liability, and reconciliation's difference, lock and undo. Migration 65 is in test_finance_ledger."""
import http.client
import http.server
import io
import json
import threading
import time
import urllib.error
from decimal import Decimal

from backend import trigger_presets as P
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance import sync
from backend.finance.core import feeds as F
from backend.finance.mercury import IP_HINT, Answer, Mercury, dollars, error_text
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

    def __call__(self, request, *, timeout):
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


def test_the_adapter_never_follows_a_redirect_so_the_token_stays_with_its_host():
    """A 302 to another host is the answer: a failure, never followed, so the Authorization header goes nowhere else."""
    elsewhere: list[tuple[str, str | None]] = []

    class Other(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            elsewhere.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        do_POST = do_GET

        def log_message(self, *args: object) -> None:
            pass

    other = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Other)
    target = f"http://localhost:{other.server_address[1]}/stolen"

    class Moves(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *args: object) -> None:
            pass

    moves = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Moves)
    for server in (other, moves):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        m = Mercury("tok-acme", base=f"http://127.0.0.1:{moves.server_address[1]}/api/v1")
        read = m.accounts()
        wrote = m.create_recipient({"name": "Acme Supplies"})
        assert (read.status, read.ok, wrote.status, wrote.ok) == (302, False, 302, False)
        assert "redirect" in error_text(wrote, write=True)
        assert elsewhere == []
    finally:
        for server in (other, moves):
            server.shutdown()
            server.server_close()


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


def _card_use(c, card_id, month):
    """What a card used in a month, as the allocation reserve reads it (the ledger, plus authorizations held)."""
    from backend.finance.store import budgets as SB
    budget = str(c.execute("SELECT budget_id FROM fin_cards WHERE id=?", (card_id,)).fetchone()[0])
    return SB.target_used(c, [budget], f"{month}-01", f"{month}-31", f"card:{card_id}", True)


def _card_holds(c):
    return [tuple(r) for r in c.execute("SELECT amount, status FROM fin_holds WHERE source='card'")]


def _arrives(fake, raw, version, event_id):
    """Mercury has the transaction at this version, and an event says so."""
    fake.txns[str(raw["id"])] = dict(raw)
    fake.events_list.append({"id": event_id, "resourceType": "transaction", "resourceId": raw["id"],
                             "operationType": "create" if version == 1 else "update", "resourceVersion": version})


def test_an_authorization_holds_settles_in_place_posts_once_and_the_io_bill_clears_it(books):
    fake = FakeMercury()
    fake.balances["credit-io"] = 0                     # nothing owed before: no opening balance on the IO line
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
    fake.balances["credit-io"] = -45.0              # the owed balance moves with the spend (owed negative)
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
    fake.balances["acct-chk"] = 125_205.5          # both balances move with the bill: checking pays 45.00, IO clears
    fake.balances["credit-io"] = 0
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
    fake.balances["acct-chk"] = 485.0                  # the fee and the deposit are all checking ever held
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
        assert _card_use(c, card_id, "2026-10") == 4_000
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
        assert vendor == "acme supplies"                               # the payee's name, keyed folded
        assert c.execute("SELECT label FROM fin_vendor_labels WHERE name_key=?", (vendor,)).fetchone()[0] == \
            "Acme Supplies"                                            # and read as written
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

    with books.read() as c:
        assert _card_use(c, card_id, "2026-10") == 9_000
        assert FA.budget_state(c, budget_id, AT).held == 20_000 - 9_000
        assert _entries(c, "card") == 3                                          # post, reversal, repost
    _arrives(fake, {**raw, "amount": -90.0, "status": "cancelled"}, 3, "evt-c9-3")
    sync_once(books, fake, "2026-10-15T16:00:00.000000Z")
    with books.read() as c:
        assert _card_use(c, card_id, "2026-10") == 0
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


def test_a_reconciliation_is_done_only_when_the_whole_gl_line_is_the_statement(books):
    """C17: a journal on the bank's GL line the statement doesn't have keeps the reconciliation open, though the
    statement and its feed rows agree; an outstanding row on the books is adjusted for, not a gap."""
    fake = FakeMercury()
    fake.balances["acct-chk"] = -22.0                  # these two rows are the account's whole history
    connect(books, fake)
    fake.txns["txn-fee"] = dict(MERCURY["txn_bank_fee"])                           # -15.00 on Oct 12
    fake.txns["txn-late"] = _txn("txn-late", "acct-chk", -7.0, "wireFee", day="2026-10-20")
    sync_once(books, fake)
    with books.transaction() as c:
        bank = str(c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0])
        stray = act(c, OWNER, "journal", {"date": "2026-10-13", "memo": "stray", "lines": [
            {"account_id": account(c, "6900"), "amount": 10_000}, {"account_id": account(c, "1000"), "amount": -10_000}]})
        assert stray.status == "committed", stray
        off = act(c, OWNER, "reconcile", {"money_account_id": bank, "statement_end": "2026-10-14",
                                          "statement_balance": -1_500})
        assert off.result["status"] == "open" and off.result["difference"] == 0
        assert off.result["book_difference"] == 10_000
        c.execute("DELETE FROM fin_reconciliations")
        assert act(c, OWNER, "journal", {"date": "2026-10-13", "memo": "fix", "lines": [
            {"account_id": account(c, "1000"), "amount": 10_000},
            {"account_id": account(c, "6900"), "amount": -10_000}]}).status == "committed"
        done = act(c, OWNER, "reconcile", {"money_account_id": bank, "statement_end": "2026-10-14",
                                           "statement_balance": -1_500})
        assert (done.result["status"], done.result["book_difference"]) == ("done", 0)


def _opening(c):
    return [tuple(r) for r in c.execute(
        "SELECT e.entry_date, a.code, l.amount FROM fin_entries e JOIN fin_lines l ON l.entry_id=e.id "
        "JOIN fin_accounts a ON a.id=l.account_id WHERE e.source='sync' AND e.source_id LIKE 'opening:%' AND "
        "e.status='posted' AND e.reverses_id IS NULL AND a.code IN ('1000','3900') AND e.id IN (SELECT l2.entry_id "
        "FROM fin_lines l2 JOIN fin_accounts a2 ON a2.id=l2.account_id WHERE a2.code='1000') ORDER BY a.code")]


def test_a_backfill_posts_the_opening_balance_so_the_first_reconcile_is_done_with_no_journal(books):
    """Connect, backfill, reconcile to Mercury's statement: done, no hand journal. The opening is Mercury's balance
    less the backfilled rows, as of the day before the first; one entry per account, adjusted (on an open day once
    its month is closed) when an older row turns up."""
    fake = FakeMercury()                                  # checking holds 125,250.50 today
    fake.txns["txn-old"] = _txn("txn-old", "acct-chk", -1000.0, "wireFee", day="2026-09-03")
    fake.txns["txn-fee"] = dict(MERCURY["txn_bank_fee"])                           # -15.00 on Oct 12
    fake.queue["statements"] = [{"statements": [{**MERCURY["bank_statement"], "endingBalance": 125265.5}]}]
    connect(books, fake)
    sync_once(books, fake)
    with books.transaction() as c:
        assert _opening(c) == [("2026-09-02", "1000", 12_626_550), ("2026-09-02", "3900", -12_626_550)]
        assert c.execute("SELECT type, subtype FROM fin_accounts WHERE code='3900'").fetchone()[:] == ("equity",
                                                                                                      "opening")
        bank = str(c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0])
        assert c.execute("SELECT count(*) FROM fin_entries WHERE source='manual'").fetchone()[0] == 0
        done = act(c, OWNER, "reconcile", {"money_account_id": bank, "statement_end": "2026-09-30"})
        assert (done.result["status"], done.result["difference"], done.result["book_difference"]) == ("done", 0, 0)
        sheet = FR.balance_sheet(c, OWNER, at=AT)
        assert next(r for r in sheet["assets"] if r["code"] == "1000")["balance"] == 12_525_050    # Mercury's own
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
    sync_once(books, fake, LATER)                         # idempotent: still one opening
    with books.transaction() as c:
        assert len(_opening(c)) == 2
        # An older row Mercury's balance already held: the opening gives it back and moves before it; its old
        # month is closed, so the old entry is reversed on an open day.
        raw = _txn("txn-older", "acct-chk", -50.0, "wireFee", day="2026-08-20")
        from backend.finance import banking
        from backend.finance.context import load
        cid = str(c.execute("SELECT id FROM fin_connections").fetchone()[0])
        banking.ingest(load(c, LATER), cid, F.mercury_txn(raw, 0))
        assert _opening(c) == [("2026-08-19", "1000", 12_631_550), ("2026-08-19", "3900", -12_631_550)]
        rev = c.execute("SELECT entry_date FROM fin_entries WHERE source='sync' AND reverses_id IS NOT NULL AND "
                        "source_id LIKE ?", (f"opening:{bank}%",)).fetchall()
        assert [r[0] for r in rev] == ["2026-10-15"]
        sheet = FR.balance_sheet(c, OWNER, at=LATER)
        assert next(r for r in sheet["assets"] if r["code"] == "1000")["balance"] == 12_525_050


def test_a_closed_month_correction_reconciles_that_month_at_the_new_amount(books):
    """Round 3 #2: Sept posts 100.00, Sept closes, Mercury corrects it to 120.00 (reversal and repost land in
    October): reconciling Sept to 120.00 is done, the original still in Sept's rollup notwithstanding."""
    fake = FakeMercury()
    fake.balances["acct-chk"] = -100.0                 # the charge is the account's whole history
    connect(books, fake)
    raw = _txn("txn-sep", "acct-chk", -100.0, "wireFee", day="2026-09-20")
    _arrives(fake, raw, 1, "evt-sep-1")
    sync_once(books, fake)
    with books.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
    fake.balances["acct-chk"] = -120.0             # Mercury's balance moves with the correction
    _arrives(fake, {**raw, "amount": -120.0}, 2, "evt-sep-2")
    sync_once(books, fake, LATER)
    with books.transaction() as c:
        bank = str(c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0])
        done = act(c, OWNER, "reconcile", {"money_account_id": bank, "statement_end": "2026-09-30",
                                           "statement_balance": -12_000})
        assert (done.result["status"], done.result["difference"], done.result["book_difference"]) == ("done", 0, 0)


def test_a_re_dated_settled_card_charge_moves_its_use_to_the_new_month(books):
    """Round 3 #3: a charge first seen settled in October that Mercury re-dates into November counts against the
    card's allocation in November, not October."""
    fake = FakeMercury()
    connect(books, fake)
    budget_id, card_id = _card_for_eve(books, fake)
    sync_once(books, fake)
    raw = _txn("txn-c7", "credit-io", -80.0, "creditCardTransaction", day="2026-10-31", cardId="card-eve")
    _arrives(fake, raw, 1, "evt-c7-1")
    sync_once(books, fake, LATER)
    _arrives(fake, {**raw, "createdAt": "2026-11-02T10:00:00Z", "postedAt": "2026-11-02T10:00:00Z"}, 2, "evt-c7-2")
    sync_once(books, fake, NEXT_DAY)
    with books.read() as c:
        assert (_card_use(c, card_id, "2026-10"), _card_use(c, card_id, "2026-11")) == (0, 8_000)
        assert FA.budget_state(c, budget_id, "2026-10-20T12:00:00.000000Z").held == 20_000      # October: all free
        assert FA.budget_state(c, budget_id, "2026-11-20T12:00:00.000000Z").held == 20_000 - 8_000


def test_a_closed_month_card_correction_counts_only_the_change_in_the_open_month(books):
    """Round 4 #1: a September card charge corrected, re-dated or cancelled after September closes leaves its use in
    September's rollup and counts only the net change in October, so the card's allocation reserve never frees more
    than the books moved (remaining in October stays put)."""
    fake = FakeMercury()
    connect(books, fake)
    budget_id, card_id = _card_for_eve(books, fake)                       # budget 1,000.00; card allocation 200.00
    sync_once(books, fake)
    charge = _txn("txn-c10", "credit-io", -100.0, "creditCardTransaction", day="2026-09-20", cardId="card-eve")
    moved = _txn("txn-c11", "credit-io", -50.0, "creditCardTransaction", day="2026-09-21", cardId="card-eve")
    _arrives(fake, charge, 1, "evt-c10-1")
    _arrives(fake, moved, 1, "evt-c11-1")
    sync_once(books, fake, LATER)
    with books.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"

    def october(c):
        s = FA.budget_state(c, budget_id, "2026-10-20T12:00:00.000000Z")
        return s.actual, s.held, s.remaining

    def uses(c):
        return [_card_use(c, card_id, m) for m in ("2026-09", "2026-10")]
    with books.read() as c:
        assert october(c) == (0, 20_000, 80_000)
    _arrives(fake, {**charge, "amount": -120.0}, 2, "evt-c10-2")                              # 100.00 -> 120.00
    _arrives(fake, {**moved, "createdAt": "2026-10-03T10:00:00Z", "postedAt": "2026-10-03T10:00:00Z"}, 2, "evt-c11-2")
    sync_once(books, fake, NEXT_DAY)
    with books.read() as c:
        # October nets +20.00 (reversal and repost) and 0 for the re-date; only those count against the allocation.
        assert uses(c) == [15_000, 2_000]
        assert october(c) == (2_000, 18_000, 80_000)
    _arrives(fake, {**charge, "amount": -130.0}, 3, "evt-c10-3")                              # again, same month
    sync_once(books, fake, "2026-10-17T12:30:00.000000Z")
    with books.read() as c:
        assert uses(c) == [15_000, 3_000]
        assert october(c) == (3_000, 17_000, 80_000)
    _arrives(fake, {**charge, "amount": -130.0, "status": "cancelled"}, 4, "evt-c10-4")         # the bank cancels it
    sync_once(books, fake, "2026-10-18T12:30:00.000000Z")
    with books.read() as c:
        assert uses(c) == [15_000, -10_000]
        assert october(c) == (-10_000, 30_000, 80_000)
    # Codex round 5 (a): a later update of the cancelled charge changes nothing the books did not.
    _arrives(fake, {**charge, "amount": -130.0, "status": "cancelled", "note": "x"}, 5, "evt-c10-5")
    sync_once(books, fake, "2026-10-18T12:30:00.000000Z")
    with books.read() as c:
        assert uses(c) == [15_000, -10_000] and october(c) == (-10_000, 30_000, 80_000)


def test_card_use_follows_each_entrys_posted_card_line_after_the_card_account_is_remapped(books):
    """Codex round 6 #2: a September charge, September closed, the card program remapped to another liability line,
    then the charge corrected in October: card use reads the card line each entry posted on (old line for the
    reversal, new for the repost), never the current mapping, so October counts +20.00, not +120.00."""
    fake = FakeMercury()
    connect(books, fake)
    budget_id, card_id = _card_for_eve(books, fake)                       # budget 1,000.00; card allocation 200.00
    sync_once(books, fake)
    charge = _txn("txn-c14", "credit-io", -100.0, "creditCardTransaction", day="2026-09-20", cardId="card-eve")
    _arrives(fake, charge, 1, "evt-c14-1")
    sync_once(books, fake, LATER)
    with books.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
        other = act(c, OWNER, "account_create", {"code": "2110", "name": "Acme card program", "type": "liability"})
        program = str(c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='credit-io'")
                      .fetchone()[0])
        moved = act(c, OWNER, "connection_map", {"money_account_id": program,
                                                 "account_id": str(other.result["account_id"])})
        assert moved.status == "committed", moved
    _arrives(fake, {**charge, "amount": -120.0}, 2, "evt-c14-2")
    sync_once(books, fake, NEXT_DAY)
    with books.read() as c:
        assert [_card_use(c, card_id, m) for m in ("2026-09", "2026-10")] == [10_000, 2_000]
        s = FA.budget_state(c, budget_id, "2026-10-20T12:00:00.000000Z")
        assert (s.actual, s.held, s.remaining) == (2_000, 18_000, 80_000)
        # The card side is recorded on the line itself (kept by the reversal), not found by position or mapping.
        money = sorted(tuple(r) for r in c.execute(
            "SELECT a.code, l.amount FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id JOIN fin_accounts a "
            "ON a.id=l.account_id WHERE e.source='card' AND l.role='money'"))
        assert money == [("2100", -10_000), ("2100", 10_000), ("2110", -12_000)]


def test_a_re_categorized_row_nets_out_its_teammate_tag_in_the_expenses_rollup(books):
    """Categorize a feed row to Ana, then categorize it again untagged: the reversal carries Ana's tags, so the
    expenses by teammate show $0 for her and the full amount as Unattributed."""
    fake = FakeMercury()
    connect(books, fake)
    fake.txns[str(MERCURY["txn_bank_fee"]["id"])] = dict(MERCURY["txn_bank_fee"])
    sync_once(books, fake)
    with books.transaction() as c:
        fid = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-fee'").fetchone()[0])
        tagged = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6900"),
                                              "tags": {"teammate": "human:ana"}})
        assert tagged.status == "committed", tagged
        assert {r["key"]: r["amount"] for r in FR.expenses(c, OWNER, "2026-10-01", "2026-10-31", "teammate",
                                                           at=AT)["rows"]}["human:ana"] == 1_500
        untagged = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6900")})
        assert untagged.status == "committed", untagged
        by_mate = FR.expenses(c, OWNER, "2026-10-01", "2026-10-31", "teammate", at=AT)
        assert {r["key"]: r["amount"] for r in by_mate["rows"]}.get("human:ana", 0) == 0
        assert {r["key"]: r["amount"] for r in by_mate["rows"]}[FR.NONE] == 1_500


def test_an_ignored_teammate_tagged_row_nets_out_and_its_drilldown_shows_the_reversed_pair(books):
    """The ignore path: a row tagged to Ana is ignored (its entry reversed under her tags), then categorized untagged.
    Ana nets to $0 and the full amount is Unattributed; her drill-down lists the reversed original and its reversal
    with their status, so nothing reads as a live $42.10 for her."""
    fake = FakeMercury()
    connect(books, fake)
    fake.txns["txn-coffee"] = {**MERCURY["txn_bank_fee"], "id": "txn-coffee", "amount": -42.10,
                               "counterpartyName": "Coffee", "kind": "wireFee"}
    sync_once(books, fake)
    with books.transaction() as c:
        fid = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-coffee'").fetchone()[0])
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6900"),
                                            "tags": {"teammate": "human:ana"}}).status == "committed"
        assert act(c, OWNER, "categorize", {"feed_id": fid, "ignore": True}).status == "committed"
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6900")}).status == "committed"
        by_mate = {r["key"]: r["amount"] for r in FR.expenses(c, OWNER, "2026-10-01", "2026-10-31", "teammate",
                                                              at=AT)["rows"]}
        assert by_mate["human:ana"] == 0 and by_mate[FR.NONE] == 4_210
        drill = FR.line_detail(c, OWNER, account(c, "6900"), "2026-10-01", "2026-10-31", teammates=("human:ana",),
                               at=AT)["entries"]
        assert sorted(e["amount"] for e in drill) == [-4_210, 4_210]
        original = next(e for e in drill if e["amount"] == 4_210)
        reversal = next(e for e in drill if e["amount"] == -4_210)
        assert original["status"] == "reversed" and reversal["status"] == "posted"
        assert reversal["reverses"] == original["entry_id"] and original["reverses"] is None


def test_a_re_categorized_card_charge_still_uses_the_cards_allocation(books):
    """Round 4 #1 (same rule): moving a settled card charge to another line reposts it; its use follows the new entry
    instead of lapsing with the reversed one."""
    fake = FakeMercury()
    connect(books, fake)
    budget_id, card_id = _card_for_eve(books, fake)
    sync_once(books, fake)
    _arrives(fake, _txn("txn-c12", "credit-io", -40.0, "creditCardTransaction", cardId="card-eve"), 1, "evt-c12-1")
    sync_once(books, fake, LATER)
    with books.transaction() as c:
        fid = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id='txn-c12'").fetchone()[0])
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100")}).status == "committed"
        assert FA.budget_state(c, budget_id, "2026-10-20T12:00:00.000000Z").held == 20_000 - 4_000


def test_card_use_follows_the_ledger_across_open_months_and_a_reopened_month(books):
    """Codex round 5 (b) and (c): Sept 100.00 closed, corrected to 120.00 in October, then to 130.00 in November with
    October still open: the books hold Sept +100, Oct -100 (+120 -120), Nov +130, and the card's use is exactly that,
    so November has 70.00 reserved, not 0. Reopening September and correcting again counts where the books post."""
    fake = FakeMercury()
    connect(books, fake)
    budget_id, card_id = _card_for_eve(books, fake)                       # budget 1,000.00; card allocation 200.00
    sync_once(books, fake)
    charge = _txn("txn-c13", "credit-io", -100.0, "creditCardTransaction", day="2026-09-20", cardId="card-eve")
    _arrives(fake, charge, 1, "evt-c13-1")
    sync_once(books, fake, LATER)
    with books.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
    _arrives(fake, {**charge, "amount": -120.0}, 2, "evt-c13-2")
    sync_once(books, fake, NEXT_DAY)
    _arrives(fake, {**charge, "amount": -130.0}, 3, "evt-c13-3")
    sync_once(books, fake, "2026-11-02T12:30:00.000000Z")

    def months(c):
        return [_card_use(c, card_id, m) for m in ("2026-09", "2026-10", "2026-11")]

    def remaining(c, at):
        s = FA.budget_state(c, budget_id, at)
        return s.actual, s.held, s.remaining
    with books.read() as c:
        assert months(c) == [10_000, -10_000, 13_000]
        assert remaining(c, "2026-11-20T12:00:00.000000Z") == (13_000, 7_000, 80_000)
    with books.transaction() as c:
        assert act(c, OWNER, "reopen_period", {"month": "2026-09"}).status == "committed"
    _arrives(fake, {**charge, "amount": -90.0}, 4, "evt-c13-4")             # September is open again
    sync_once(books, fake, "2026-11-03T12:30:00.000000Z")
    with books.read() as c:
        # The November entry is reversed in November and the charge reposts on its own September day.
        assert months(c) == [19_000, -10_000, 0]
        assert remaining(c, "2026-11-20T12:00:00.000000Z") == (0, 20_000, 80_000)


def test_the_client_calls_real_urlopen_over_http_with_the_timeout_by_keyword_and_amounts_as_numbers():
    """Regression: urlopen's 2nd positional arg is data, so a positional timeout broke every real Mercury call, and
    Decimal amounts went out as strings. Runs the default opener against a local HTTP server, no fake opener."""
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self, body: bytes) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            seen.append((self.command, self.path, self.headers.get("Authorization"), self.rfile.read(length)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._answer(json.dumps({"accounts": [{"id": "acct-chk"}]}).encode())

        def do_POST(self) -> None:
            self._answer(json.dumps({"id": "req-1", "status": "pending"}).encode())

        def log_message(self, *args: object) -> None:
            return None

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        m = Mercury("tok-acme", base=f"http://127.0.0.1:{server.server_address[1]}/api/v1")
        listed = m.accounts()
        sent = m.request_send_money("acct-chk", {"amount": dollars(1250), "recipientId": "rcp-1"})
    finally:
        server.shutdown()
        server.server_close()
    assert listed.status == 200 and listed.body == {"accounts": [{"id": "acct-chk"}]}
    assert sent.status == 200 and sent.body == {"id": "req-1", "status": "pending"}
    get, post = seen
    assert get[0] == "GET" and get[2] == "Bearer tok-acme"
    assert post[0] == "POST" and post[1] == "/api/v1/account/acct-chk/request-send-money"
    assert post[2] == "Bearer tok-acme"
    assert json.loads(post[3]) == {"amount": 12.5, "recipientId": "rcp-1"}
    assert isinstance(json.loads(post[3])["amount"], float)


def test_a_response_cut_short_and_an_amount_too_big_to_send_exactly_answer_unreachable_and_never_raise():
    class Short(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "100")                                    # promises 100 bytes...
            self.end_headers()
            self.wfile.write(b'{"accounts": []}')                                       # ...sends 17, then closes

        def log_message(self, *args: object) -> None:
            return None

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Short)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert Mercury("tok", base=f"http://127.0.0.1:{server.server_address[1]}/api/v1").accounts() == Answer(None, {})
    finally:
        server.shutdown()
        server.server_close()
    sent = []
    assert Mercury("tok", opener=lambda r, *, timeout: sent.append(r)).request_send_money(
        "acct", {"amount": Decimal("1e13")}) == Answer(None, {}) and not sent
