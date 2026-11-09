"""Helpers that other tests share, moved out of test_finance_banking.py so they import no test file."""

from backend.tests.helpers.finance_fixtures import MERCURY, OWNER, account, act


LATER = "2026-10-15T12:30:00.000000Z"


def _card_for_eve(store, fake):
    """A Cloud budget on 6100 and a 200.00 card for eve on it, synced to Mercury (provider id card-eve)."""
    with store.transaction() as c:
        b = act(
            c,
            OWNER,
            "budget_create",
            {"name": "Cloud", "account_id": account(c, "6100"), "amount": 100_000, "at_limit": "decline"},
        )
        made = act(
            c,
            OWNER,
            "card_create",
            {
                "holder": "human:eve",
                "budget_id": b.result["budget_id"],
                "amount": 20_000,
                "provider_user_id": "user-eve",
            },
        )
        assert made.status == "committed", made
    return str(b.result["budget_id"]), str(made.result["card_id"])


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
    fake.events_list.append(
        {
            "id": event_id,
            "resourceType": "transaction",
            "resourceId": raw["id"],
            "operationType": "create" if version == 1 else "update",
            "resourceVersion": version,
        }
    )


# ------------------------------------------------------------------------------------------------ review fixes
def _txn(raw_id, account, amount, kind, day="2026-10-14", **extra):
    return {
        **MERCURY["txn_bank_fee"],
        "id": raw_id,
        "accountId": account,
        "amount": amount,
        "kind": kind,
        "createdAt": f"{day}T10:00:00Z",
        "postedAt": f"{day}T10:00:00Z",
        "counterpartyName": "Acme",
        **extra,
    }
