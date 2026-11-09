"""Helpers that other tests share, moved out of test_finance_payments.py so they import no test file."""

from backend.finance.mercury import Answer
from backend.tests.helpers.finance_fixtures import ACME_ADDRESS, AT, MERCURY, OWNER, account, act, sync_once


HOURS = [f"2026-10-15T{h:02d}:30:00.000000Z" for h in range(13, 24)]


def _payee(store, fake, *, at=AT):
    """Acme Supplies by ACH, synced to Mercury."""
    with store.transaction() as c:
        made = act(
            c,
            OWNER,
            "recipient_add",
            {
                "name": "Acme Supplies",
                "method": "ach",
                "account_number": "000099998888",
                "routing_number": "000000000",
                "address": ACME_ADDRESS,
            },
        )
        assert made.status == "committed", made
    sync_once(store, fake, at)
    with store.read() as c:
        payload = c.execute("SELECT payload FROM fin_outbox WHERE op='recipient_create'").fetchone()[0]
        assert "000099998888" not in payload  # erased once Mercury has it
    return str(made.result["recipient_id"])


def _checking(c):
    return str(c.execute("SELECT id FROM fin_money_accounts WHERE provider_account_id='acct-chk'").fetchone()[0])


def _lost_send(books, fake, at=HOURS[1]):
    """A 500.00 payment on 6100 whose send Mercury accepted but whose answer never came back: unknown."""
    rid = _payee(books, fake)
    with books.transaction() as c:
        made = act(
            c,
            OWNER,
            "payment_create",
            {
                "recipient_id": rid,
                "amount": 50_000,
                "account_id": account(c, "6100"),
                "tags": {"team": "eng"},
                "money_account_id": _checking(c),
            },
        )
        pid = str(made.result["payment_id"])
    fake.queue["request_send_money"] = [Answer(None, {})]
    sync_once(books, fake, at)
    with books.read() as c:
        key = c.execute("SELECT idempotency_key FROM fin_payments WHERE id=?", (pid,)).fetchone()[0]
    return pid, key


def _debit(fake, txn_id, request_id, event_id, **extra):
    fake.txns[txn_id] = {
        **MERCURY["txn_bank_fee"],
        "id": txn_id,
        "amount": -500.0,
        "kind": "outgoingPayment",
        "counterpartyName": "Acme Supplies",
        "requestId": request_id,
        "postedAt": "2026-10-16T10:00:00Z",
        **extra,
    }
    fake.events_list.append(
        {
            "id": event_id,
            "resourceType": "transaction",
            "resourceId": txn_id,
            "operationType": "create",
            "resourceVersion": 1,
        }
    )
    # Mercury's balance moves with the debit it posts: the books carry the row, so the balance they reconcile to does too.
    recorded = next(a["currentBalance"] for a in MERCURY["accounts"]["accounts"] if a["id"] == "acct-chk")
    fake.balances["acct-chk"] = fake.balances.get("acct-chk", recorded) - 500.0


def _reconcile(c, provider_id):
    """Reconcile checking through Oct 16 with this feed row cleared; its id."""
    fid = str(c.execute("SELECT id FROM fin_feed_txns WHERE provider_id=?", (provider_id,)).fetchone()[0])
    args = {"money_account_id": _checking(c), "statement_end": "2026-10-16", "cleared": [fid]}
    off = act(c, OWNER, "reconcile", {**args, "statement_balance": 0})
    if off.result["status"] != "done":
        c.execute("DELETE FROM fin_reconciliations")
        done = act(c, OWNER, "reconcile", {**args, "statement_balance": -int(off.result["difference"])})
        assert done.result["status"] == "done", done
    return fid
