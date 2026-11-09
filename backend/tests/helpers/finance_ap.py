"""Helpers that other tests share, moved out of test_finance_ap.py so they import no test file."""

from backend.finance import reads as FR
from backend.finance.core import feeds as F
from backend.tests.helpers.finance_fixtures import AT, MERCURY, OWNER, act


LATER = "2026-10-20T12:00:00.000000Z"


def _vendor(c, name="Acme Supplies", **more) -> str:
    made = act(c, OWNER, "contact_create", {"name": name, "kind": "vendor", **more})
    assert made.status == "committed", made
    return str(made.result["contact_id"])


def _bill(c, vendor, lines, who=OWNER, day="2026-10-10", **more) -> str:
    made = act(c, who, "bill_create", {"vendor_id": vendor, "bill_date": day, "lines": lines, **more})
    assert made.status == "committed", made
    return str(made.result["bill_id"])


def _balance(c, code) -> int:
    return int(
        c.execute(
            "SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id "
            "WHERE a.code=?",
            (code,),
        ).fetchone()[0]
    )


def _pnl(c, basis, start="2026-10-01", end="2026-12-31") -> dict[str, int]:
    found = FR.pnl(c, OWNER, start, end, "year", basis, AT)
    return {str(r["code"]): int(r["total"]) for r in found["rows"] if r["total"]}


def _events(c, name) -> list[tuple[str, str]]:
    return [
        tuple(r)
        for r in c.execute("SELECT subject, period_key FROM fin_events WHERE name=? ORDER BY created_at", (name,))
    ]


def _ingest(c, raw, at=AT):
    from backend.finance import banking
    from backend.finance.context import load

    cid = str(c.execute("SELECT id FROM fin_connections").fetchone()[0])
    return banking.ingest(load(c, at), cid, F.mercury_txn(raw, 0))


def _txn(raw_id, amount, who="Acme Supplies", day="2026-10-14", kind="outgoingPayment", **extra):
    return {
        **MERCURY["txn_bank_fee"],
        "id": raw_id,
        "accountId": "acct-chk",
        "amount": amount,
        "kind": kind,
        "createdAt": f"{day}T10:00:00Z",
        "postedAt": f"{day}T10:00:00Z",
        "counterpartyName": who,
        **extra,
    }
