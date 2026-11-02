"""Finance: Tico's own books for October 2026, four budgets (one near its limit), six invoices, bills, finance rules
and one bookkeeper proposal waiting for Ana.

Every entry goes through the Finance actions the app runs (`FA.act`) with an explicit `at`, so the times repeat with
the build. Gateway usage is posted the way Gateway usage is (`FA.post_entry`, source "usage"). Nothing reaches a bank,
a payment processor or a mailbox: invoices are sent by hand (delivery "caller") and the book has no bank feed.
Finance's own clock is the wall clock, so the seed never relies on it: `at` is always passed.
"""

from .. import bookkeeper, hubdb
from ..finance import actions as FA
from ..finance.store import ledger as SL

OWNER = "human:ana"
BOOKKEEPER = "bot:bookkeeper"
ACCOUNTS_PAYABLE = "bot:accounts-payable"
FINANCE_LEAD = "bot:finance-lead"

CONTENT = {
    "owner_email": "ana@acme.example",
    # Amounts are cents. The book opens on September 30; every October entry dates within the month.
    "opening": {"date": "2026-09-30", "amount": 1850000, "memo": "Opening balance, September 30"},
    # code, name, type, parent (a group in the default chart)
    "chart": [
        ("4100", "Store sales", "income", "4000"),
        ("4200", "Marketplace sales", "income", "4000"),
        ("4300", "Retail partner sales", "income", "4000"),
        ("4400", "Business installs", "income", "4000"),
        ("4500", "Scene Library subscriptions", "income", "4000"),
        ("5300", "Units from manufacturer", "expense", "5000"),
        ("5400", "Freight in", "expense", "5000"),
        ("5500", "Installer partner payouts", "expense", "5000"),
        ("5600", "Render cloud hosting", "expense", "5000"),
        ("6200", "Marketplace fees", "expense", "6000"),
        ("6300", "Paid advertising", "expense", "6000"),
    ],
    "gateway_topup": {"date": "2026-10-02", "amount": 120000, "memo": "Gateway credit top-up"},
    "store_weeks": [
        {"date": "2026-10-04", "amount": 421035, "memo": "Store sales, week of October 4"},
        {"date": "2026-10-11", "amount": 388010, "memo": "Store sales, week of October 11"},
        {"date": "2026-10-18", "amount": 490560, "memo": "Store sales, week of October 18"},
        {"date": "2026-10-25", "amount": 452045, "memo": "Store sales, week of October 25"},
    ],
    "marketplace": {
        "name": "Crate and Shelf Marketplace",
        "payouts": [
            {"date": "2026-10-10", "gross": 310000, "fees": 54000, "memo": "Marketplace payout, sales through October 9"},
            {"date": "2026-10-24", "gross": 330000, "fees": 58000, "memo": "Marketplace payout, sales through October 23"},
        ],
    },
    "subscriptions": {
        "date": "2026-10-28",
        "memo": "Scene Library subscriptions, October",
        "lines": [("Monthly plans: 38 at $4.99", 18962), ("Season passes: 7 at $29", 20300)],
    },
    "usage": {
        "date": "2026-10-31",
        "source_id": "gateway-usage-2026-10",
        "memo": "Gateway usage, October",
        "by_teammate": [
            ("bot:sales-lead", 21460),
            ("bot:support-lead", 9640),
            ("bot:marketing-lead", 18825),
            ("bot:product-manager", 31210),
        ],
    },
    "vendors": [
        {"key": "lumen", "name": "Lumen Assembly Co.", "email": "accounts@lumen.example"},
        {"key": "keelson", "name": "Keelson Freight Forwarding", "email": "billing@keelson.example"},
        {"key": "fernway", "name": "Fernway Ads", "email": "invoices@fernway.example"},
        {"key": "kiln", "name": "Kiln Render Cloud", "email": "billing@kilnrender.example"},
        {"key": "ostrander", "name": "Ostrander Install Crews", "email": "pay@ostrander.example"},
        {"key": "quillwork", "name": "Quillwork Software", "email": "billing@quillwork.example"},
    ],
    "customers": [
        {"key": "homestead", "name": "Homestead Works Stores", "email": "buying@homeworks.example"},
        {"key": "harborvine", "name": "Harbor & Vine Hotel", "email": "facilities@harborvine.example"},
        {"key": "tidewater", "name": "Tidewater Coffee Co.", "email": "accounts@tidewater.example"},
        {"key": "ridgeline", "name": "Ridgeline Dental Group", "email": "office@ridgeline.example"},
        {"key": "mapleridge", "name": "Maple Ridge Parks Dept", "email": "ap@mapleridge.example"},
    ],
    # Drafted by the bookkeeper and sent by Ana. The first is paid; ACME-1003 is overdue and ar-followup chases it.
    "invoices": [
        {"customer": "homestead", "number": "ACME-1001", "date": "2026-10-02", "due": "2026-11-01", "amount": 456000,
         "account": "4300", "description": "Retail order, 120 starter kits", "paid": "2026-10-27"},
        {"customer": "harborvine", "number": "ACME-1002", "date": "2026-10-09", "due": "2026-11-08", "amount": 285000,
         "account": "4400", "description": "Install: lobby and guest floors"},
        {"customer": "tidewater", "number": "ACME-1003", "date": "2026-10-14", "due": "2026-10-28", "amount": 124000,
         "account": "4400", "description": "Install: counter and patio lighting"},
        {"customer": "ridgeline", "number": "ACME-1004", "date": "2026-10-20", "due": "2026-11-19", "amount": 186500,
         "account": "4400", "description": "Install: reception and treatment rooms"},
        {"customer": "mapleridge", "number": "ACME-1005", "date": "2026-10-22", "due": "2026-11-21", "amount": 360000,
         "account": "4400", "description": "Install: trail-head and pavilion"},
        {"customer": "homestead", "number": "ACME-1006", "date": "2026-10-26", "due": "2026-11-25", "amount": 228000,
         "account": "4300", "description": "Retail restock, 60 starter kits"},
    ],
    # Drafted by the named actor. Ana approves each one, and pays the ones marked paid.
    "bills": [
        {"vendor": "lumen", "number": "LA-20417", "by": ACCOUNTS_PAYABLE, "date": "2026-10-03", "due": "2026-11-02",
         "amount": 864000, "account": "5300", "description": "Production run, 1,440 units at $6.00",
         "approve": "2026-10-04", "paid": "2026-10-30"},
        {"vendor": "keelson", "number": "KF-88213", "by": ACCOUNTS_PAYABLE, "date": "2026-10-16", "due": "2026-11-15",
         "amount": 192000, "account": "5400", "description": "Container 3, ocean freight and drayage",
         "approve": "2026-10-17"},
        {"vendor": "fernway", "number": "FA-1015", "by": BOOKKEEPER, "date": "2026-10-16", "due": "2026-10-31",
         "amount": 270000, "account": "6300", "description": "Paid search and social, October 1-15",
         "approve": "2026-10-16", "paid": "2026-10-30"},
        {"vendor": "fernway", "number": "FA-1031", "by": BOOKKEEPER, "date": "2026-10-31", "due": "2026-11-15",
         "amount": 270000, "account": "6300", "description": "Paid search and social, October 16-31",
         "approve": "2026-10-31"},
        {"vendor": "kiln", "number": "KR-5520", "by": BOOKKEEPER, "date": "2026-10-01", "due": "2026-10-31",
         "amount": 112500, "account": "5600", "description": "Render hosting, October",
         "approve": "2026-10-01", "paid": "2026-10-30"},
        {"vendor": "quillwork", "number": "QW-7741", "by": BOOKKEEPER, "date": "2026-10-01", "due": "2026-10-15",
         "amount": 29900, "account": "6100", "description": "Team seats, October",
         "approve": "2026-10-01", "paid": "2026-10-14"},
        {"vendor": "ostrander", "number": "NC-0412", "by": ACCOUNTS_PAYABLE, "date": "2026-10-29", "due": "2026-11-12",
         "amount": 135000, "account": "5500", "description": "Install crew share, Harbor & Vine and Ridgeline jobs",
         "approve": "2026-10-29"},
    ],
    # Quarter budgets, so Q4 2026 stays current for the whole quarter. (A teammate split of AI services would have to
    # sit under the default month-period budget, so model spend stays on that line and is tagged by teammate.)
    "budgets": [
        {"name": "Paid advertising, Q4", "account": "6300", "owner": "bot:marketing-lead", "amount": 600000,
         "by": OWNER},
        {"name": "Render cloud hosting, Q4", "account": "5600", "owner": "bot:product-manager", "amount": 150000,
         "by": FINANCE_LEAD},
        {"name": "Marketplace fees, Q4", "account": "6200", "owner": OWNER, "amount": 180000, "by": OWNER},
        {"name": "Freight in, Q4", "account": "5400", "owner": ACCOUNTS_PAYABLE, "amount": 250000, "by": OWNER},
    ],
    # Gate and posting rules. `account` is a chart code and becomes an account id when the rule is written.
    "rules": [
        {"kind": "gate", "name": "Spend over $500 needs Ana", "words": "A bill approval over $500 waits for Ana.",
         "when": {"kind": "bill_approve", "amount": {"gt": 50000}},
         "outcome": {"outcome": "needs_approval", "who": "owner"},
         "examples": [{"input": {"kind": "bill_approve", "amount": 50001}, "expect": "needs_approval"},
                      {"input": {"kind": "bill_approve", "amount": 50000}, "expect": "allow"}],
         "by": OWNER},
        {"kind": "posting", "name": "Marketplace fees go to 6200",
         "words": "Fees from the marketplace post to Marketplace fees.",
         "when": {"counterparty": "Crate and Shelf Marketplace"},
         "outcome": {"outcome": "post", "account": "6200"},
         "examples": [{"input": {"kind": "bill", "counterparty": "Crate and Shelf Marketplace", "amount": 54000},
                       "expect": "post", "account": "6200"},
                      {"input": {"kind": "bill", "counterparty": "Keelson Freight Forwarding", "amount": 192000},
                       "expect": "none"}],
         "by": OWNER},
        # The bookkeeper proposes this; a bot's rule needs a finance admin, so it waits for Ana.
        {"kind": "posting", "name": "Freight bills go to 5400",
         "words": "Bills from Keelson Freight Forwarding post to Freight in.",
         "when": {"counterparty": "Keelson Freight Forwarding"},
         "outcome": {"outcome": "post", "account": "5400"},
         "examples": [{"input": {"kind": "bill", "counterparty": "Keelson Freight Forwarding", "amount": 192000},
                       "expect": "post", "account": "5400"},
                      {"input": {"kind": "bill", "counterparty": "Lumen Assembly Co.", "amount": 864000},
                       "expect": "none"}],
         "by": BOOKKEEPER, "expect": "needs_approval"},
    ],
    "chase": {
        "title": "Chase ACME-1003 from Tidewater Coffee Co.",
        "body": ("Invoice ACME-1003 for $1,240.00 was due October 28 and is still unpaid. Send the usual reminder "
                 "and tell Ana if it is not paid by November 9."),
    },
}


def _at(day, hour=10):
    return f"{day}T{hour:02d}:00:00.000000Z"


def _codes(c):
    """Account id by chart code, for this book."""
    return {a.code: a.id for a in SL.accounts(c, SL.book(c).id).values()}


def _act(c, who, kind, payload, tag, day, expect="committed"):
    res = FA.act(c, who, kind, payload, f"demo-finance:{tag}", at=_at(day))
    if res.status != expect:
        raise AssertionError(f"finance demo: {tag} ({kind}) was {res.status}: {res.reason}")
    return res


def _line(account_id, amount, description="", tags=None):
    line = {"account_id": account_id, "amount": amount, "description": description}
    if tags:
        line["tags"] = tags
    return line


def _journal(c, who, tag, day, memo, rows, codes):
    lines = [_line(codes[code], amount, text) for code, amount, text in rows]
    _act(c, who, "journal", {"date": day, "memo": memo, "lines": lines}, tag, day)


def _resolve(value, codes):
    """A rule's account codes become account ids; everything else is copied as it is."""
    if isinstance(value, dict):
        return {("account_id" if k == "account" else k): (codes[v] if k == "account" else _resolve(v, codes))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, codes) for v in value]
    return value


def _setup(c):
    opened = CONTENT["opening"]["date"]
    FA.ensure_setup(c, CONTENT["owner_email"], _at(opened, 8))
    bookkeeper.grant_defaults(c)
    _act(c, OWNER, "right_grant", {"teammate": ACCOUNTS_PAYABLE, "right": "finance.ap"}, "right-ap", opened)
    _act(c, OWNER, "right_grant", {"teammate": FINANCE_LEAD, "right": "finance.budgets"}, "right-budgets", opened)
    for who in (BOOKKEEPER, ACCOUNTS_PAYABLE, FINANCE_LEAD):
        _act(c, OWNER, "view_grant", {"subject_kind": "teammate", "subject": who, "what": "everything"},
             f"view-{who}", opened)
    for code, name, kind, parent in CONTENT["chart"]:
        codes = _codes(c)
        _act(c, OWNER, "account_create", {"code": code, "name": name, "type": kind, "parent_id": codes[parent]},
             f"account-{code}", opened)
    codes = _codes(c)
    for i, bud in enumerate(CONTENT["budgets"]):
        payload = {"name": bud["name"], "period": "quarter", "account_id": codes[bud["account"]],
                   "owner": bud["owner"], "amount": bud["amount"], "effective_from": "2026-10-01",
                   "at_limit": "warn"}
        _act(c, bud["by"], "budget_create", payload, f"budget-{i}", opened)


def _books(c):
    codes = _codes(c)
    op = CONTENT["opening"]
    _journal(c, OWNER, "opening", op["date"], op["memo"],
             [("1000", op["amount"], "Bank balance"), ("3900", -op["amount"], "Opening balance equity")], codes)
    tu = CONTENT["gateway_topup"]
    _journal(c, BOOKKEEPER, "topup", tu["date"], tu["memo"],
             [("1100", tu["amount"], "Gateway credit"), ("1000", -tu["amount"], "Paid from bank")], codes)
    for i, week in enumerate(CONTENT["store_weeks"]):
        _journal(c, BOOKKEEPER, f"store-{i}", week["date"], week["memo"],
                 [("1000", week["amount"], "Card settlements"), ("4100", -week["amount"], "Store sales")], codes)
    for i, pay in enumerate(CONTENT["marketplace"]["payouts"]):
        _journal(c, BOOKKEEPER, f"marketplace-{i}", pay["date"], pay["memo"],
                 [("1000", pay["gross"] - pay["fees"], "Payout after fees"),
                  ("6200", pay["fees"], "Marketplace fees"),
                  ("4200", -pay["gross"], "Marketplace sales")], codes)
    sub = CONTENT["subscriptions"]
    rows = [("1000", sum(amount for _, amount in sub["lines"]), "Subscription payments")]
    rows += [("4500", -amount, label) for label, amount in sub["lines"]]
    _journal(c, BOOKKEEPER, "subscriptions", sub["date"], sub["memo"], rows, codes)
    use = CONTENT["usage"]
    lines = [_line(codes["5100"], amount, "AI services", {"teammate": who}) for who, amount in use["by_teammate"]]
    lines.append(_line(codes["1100"], -sum(amount for _, amount in use["by_teammate"]), "Gateway credit used"))
    FA.post_entry(c, {"date": use["date"], "memo": use["memo"], "source": "usage", "source_id": use["source_id"],
                      "lines": lines}, at=_at(use["date"], 18))


def _billing(c):
    codes = _codes(c)
    vendors, customers = {}, {}
    for v in CONTENT["vendors"]:
        res = _act(c, BOOKKEEPER, "contact_create", {"name": v["name"], "kind": "vendor", "emails": [v["email"]]},
                   f"vendor-{v['key']}", "2026-10-01")
        vendors[v["key"]] = res.result["contact_id"]
    for cu in CONTENT["customers"]:
        res = _act(c, BOOKKEEPER, "contact_create", {"name": cu["name"], "kind": "customer", "emails": [cu["email"]]},
                   f"customer-{cu['key']}", "2026-10-01")
        customers[cu["key"]] = res.result["contact_id"]
    for inv in CONTENT["invoices"]:
        line = _line(codes[inv["account"]], inv["amount"], inv["description"])
        made = _act(c, BOOKKEEPER, "invoice_create",
                    {"customer_id": customers[inv["customer"]], "number": inv["number"],
                     "invoice_date": inv["date"], "due_date": inv["due"], "lines": [line]},
                    f"invoice-{inv['number']}", inv["date"])
        invoice_id = made.result["invoice_id"]
        _act(c, OWNER, "invoice_send", {"invoice_id": invoice_id}, f"send-{inv['number']}", inv["date"])
        if "paid" in inv:
            _act(c, BOOKKEEPER, "invoice_record_payment",
                 {"invoice_id": invoice_id, "amount": inv["amount"], "date": inv["paid"],
                  "account_id": codes["1000"]}, f"paid-{inv['number']}", inv["paid"])
    for bill in CONTENT["bills"]:
        line = _line(codes[bill["account"]], bill["amount"], bill["description"])
        made = _act(c, bill["by"], "bill_create",
                    {"vendor_id": vendors[bill["vendor"]], "number": bill["number"], "bill_date": bill["date"],
                     "due_date": bill["due"], "lines": [line]}, f"bill-{bill['number']}", bill["date"])
        bill_id = made.result["bill_id"]
        _act(c, OWNER, "bill_approve", {"bill_id": bill_id}, f"approve-{bill['number']}", bill["approve"])
        if "paid" in bill:
            _act(c, OWNER, "bill_mark_paid",
                 {"bill_id": bill_id, "amount": bill["amount"], "date": bill["paid"],
                  "account_id": codes["1000"]}, f"paid-{bill['number']}", bill["paid"])


def _rules_and_tasks(c):
    codes = _codes(c)
    for i, rule in enumerate(CONTENT["rules"]):
        payload = {k: rule[k] for k in ("kind", "name", "words", "when", "outcome", "examples")}
        _act(c, rule["by"], "rule_create", _resolve(payload, codes), f"rule-{i}", "2026-10-01",
             expect=rule.get("expect", "committed"))
    chase = CONTENT["chase"]
    hubdb.task_create(c, OWNER, chase["title"], chase["body"], "bot:ar-followup", allow_planned=True)


def seed(b):
    b.write(_setup)
    b.write(_books)
    b.write(_billing)
    b.write(_rules_and_tasks)
