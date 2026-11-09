"""Finance vendor identity (backend/finance/vendors.py): every bank or card row gets the vendor key its counterparty
normalises to (core/merchant.merchant_key), and an alias a vendor_merge adds records what it came from (merged_from).

The columns come from store/schema.ADDED, which `create` adds to an install made before them (the same statements as
any later phase). Rows with no key yet are keyed from their counterparty text; a row with no counterparty stays "".
Idempotent: only empty keys are set, and the columns are added only when missing.
"""

from backend.finance.core.merchant import merchant_key
from backend.finance.store import schema as SCH


def up(conn):
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "fin_feed_txns" not in have:
        return
    SCH.create(conn)
    rows = conn.execute(
        "SELECT id, counterparty FROM fin_feed_txns WHERE vendor_key='' AND counterparty<>''"
    ).fetchall()
    conn.executemany(
        "UPDATE fin_feed_txns SET vendor_key=? WHERE id=? AND vendor_key=''",
        [(merchant_key(str(counterparty)), str(fid)) for fid, counterparty in rows],
    )
