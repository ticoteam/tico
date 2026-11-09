"""An ad platform's budget only warns (backend/finance/budgeting.py warn_only): a budget whose vendor is Google Ads or Meta
(store/ads.py vendor_platform) never declines, asks or holds a payment. Each such budget made with another at-limit
takes warn. Its amount, owner and scope stay. Idempotent: a warn budget is never touched.
"""

from datetime import datetime, timezone

from backend.finance.store import ads as SAD
from backend.finance.store import ap as SAP


def up(conn):
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"fin_budgets", "fin_contacts", "fin_vendor_aliases", "fin_vendor_labels"} <= have:
        return
    rows = conn.execute("SELECT id, book_id, vendor FROM fin_budgets "
                        "WHERE vendor<>'' AND catch_all=0 AND at_limit<>'warn'").fetchall()
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    for bid, book, vendor in rows:
        keys = SAP.vendor_keys(conn, book, vendor) or [vendor]
        if any(SAD.vendor_platform(conn, book, k) for k in keys):
            conn.execute("UPDATE fin_budgets SET at_limit='warn', updated_at=? WHERE id=?", (at, bid))
