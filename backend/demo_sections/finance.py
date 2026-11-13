"""Finance: Tico's own books for the previous full month and this month to date, four monthly budgets (one near its
limit), six invoices, bills and finance rules. The Finance Owner sees the books by default from the start, and its
first rule proposal, made four days before it, waits for Ana's OK.

The books are dated from the build's own moment (`b.now`): the previous full month, then this month up to the build, so
the app's default month always has numbers. Nothing is dated after the build (`Calendar.at` stops at it). Every entry
goes through the Finance actions the app runs (`FA.act`) with an explicit `at`, and Gateway usage is posted the way
Gateway usage is (`FA.post_entry`, source "usage"). Finance's own clock is never read. The hub's clock is set to the day
of each hub stamp (tasks, asks) with `_hub_clock`. Nothing reaches a bank, a payment processor or a mailbox:
invoices are sent by hand and the book has no bank feed.
"""

import contextlib
from datetime import date, datetime, timedelta, timezone

from .. import bookkeeper, hubdb
from ..finance import actions as FA
from ..finance import reads as RD

OWNER = "human:ana"
FINANCE_OWNER = "bot:finance-owner"
ACCOUNTS_PAYABLE = "bot:accounts-payable-specialist"

# A date in CONTENT is (month, day): "prev" is the previous full month and "this" the build's month. Days run 1 to 28
# so every month has them. Memos and descriptions may use {date}, {month} and {through} (the day before
# the entry), filled from the entry's own day.
CONTENT = {
    "owner_email": "ana@acme.example",
    # Amounts are cents. The book opens at the end of the month before the previous one.
    "opening": {"amount": 1850000, "memo": "Opening balance, end of {month}"},
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
    "gateway_topup": {"date": ("prev", 2), "amount": 120000, "memo": "Gateway credit top-up"},
    "store_weeks": [
        {"date": ("prev", 4), "amount": 421035, "memo": "Store sales, week of {date}"},
        {"date": ("prev", 11), "amount": 388010, "memo": "Store sales, week of {date}"},
        {"date": ("prev", 18), "amount": 490560, "memo": "Store sales, week of {date}"},
        {"date": ("prev", 25), "amount": 452045, "memo": "Store sales, week of {date}"},
    ],
    "store_this_month": {"date": ("this", 1), "amount": 142380, "memo": "Store sales, first days of {month}"},
    "marketplace": {
        "name": "Crate and Shelf Marketplace",
        "payouts": [
            {
                "date": ("prev", 10),
                "gross": 310000,
                "fees": 54000,
                "memo": "Marketplace payout, sales through {through}",
            },
            {
                "date": ("prev", 24),
                "gross": 330000,
                "fees": 58000,
                "memo": "Marketplace payout, sales through {through}",
            },
            {
                "date": ("this", 1),
                "gross": 330000,
                "fees": 58000,
                "memo": "Marketplace payout, sales through {through}",
            },
        ],
    },
    "subscriptions": {
        "date": ("prev", 28),
        "memo": "Scene Library subscriptions, {month}",
        "lines": [("Monthly plans: 38 at $4.99", 18962), ("Season passes: 7 at $29", 20300)],
    },
    "usage": {
        "date": ("prev", 27),
        "source_id": "gateway-usage",
        "memo": "Gateway usage, {month}",
        "by_teammate": [
            ("bot:growth-manager", 21460),
            ("bot:success-manager", 9640),
            ("bot:growth-manager", 18825),
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
    # Drafted by the bookkeeper and sent by Ana. ACME-1001 is paid; ACME-1003 is overdue and accounts-receivable-specialist chases it.
    # The others fall due later this month, so only ACME-1003 is late on any day in the first half of the month.
    "invoices": [
        {
            "customer": "homestead",
            "number": "ACME-1001",
            "date": ("prev", 2),
            "due": ("prev", 28),
            "amount": 456000,
            "account": "4300",
            "description": "Retail order, 120 starter kits",
            "paid": ("prev", 27),
        },
        {
            "customer": "harborvine",
            "number": "ACME-1002",
            "date": ("prev", 9),
            "due": ("this", 28),
            "amount": 285000,
            "account": "4400",
            "description": "Install: lobby and guest floors",
        },
        {
            "customer": "tidewater",
            "number": "ACME-1003",
            "date": ("prev", 14),
            "due": ("prev", 28),
            "amount": 124000,
            "account": "4400",
            "description": "Install: counter and patio lighting",
        },
        {
            "customer": "ridgeline",
            "number": "ACME-1004",
            "date": ("prev", 20),
            "due": ("this", 20),
            "amount": 186500,
            "account": "4400",
            "description": "Install: reception and treatment rooms",
        },
        {
            "customer": "mapleridge",
            "number": "ACME-1005",
            "date": ("prev", 22),
            "due": ("this", 25),
            "amount": 360000,
            "account": "4400",
            "description": "Install: trail-head and pavilion",
        },
        {
            "customer": "homestead",
            "number": "ACME-1006",
            "date": ("prev", 26),
            "due": ("this", 26),
            "amount": 228000,
            "account": "4300",
            "description": "Retail restock, 60 starter kits",
        },
    ],
    # Drafted by the named actor. Ana approves each one, and pays the ones marked paid.
    "bills": [
        {
            "vendor": "lumen",
            "number": "LA-20417",
            "by": ACCOUNTS_PAYABLE,
            "date": ("prev", 3),
            "due": ("prev", 28),
            "amount": 864000,
            "account": "5300",
            "description": "Production run, 1,440 units at $6.00",
            "approve": ("prev", 4),
            "paid": ("prev", 27),
        },
        {
            "vendor": "fernway",
            "number": "FA-1015",
            "by": FINANCE_OWNER,
            "date": ("prev", 16),
            "due": ("prev", 28),
            "amount": 270000,
            "account": "6300",
            "description": "Paid search and social, first half of {month}",
            "approve": ("prev", 16),
            "paid": ("prev", 27),
        },
        {
            "vendor": "fernway",
            "number": "FA-1031",
            "by": FINANCE_OWNER,
            "date": ("prev", 20),
            "due": ("prev", 28),
            "amount": 270000,
            "account": "6300",
            "description": "Paid search and social, second half of {month}",
            "approve": ("prev", 20),
            "paid": ("prev", 27),
        },
        {
            "vendor": "kiln",
            "number": "KR-5504",
            "by": FINANCE_OWNER,
            "date": ("prev", 1),
            "due": ("prev", 28),
            "amount": 112500,
            "account": "5600",
            "description": "Render hosting, {month}",
            "approve": ("prev", 1),
            "paid": ("prev", 27),
        },
        {
            "vendor": "quillwork",
            "number": "QW-7741",
            "by": FINANCE_OWNER,
            "date": ("prev", 1),
            "due": ("prev", 15),
            "amount": 29900,
            "account": "6100",
            "description": "Team seats, {month}",
            "approve": ("prev", 1),
            "paid": ("prev", 14),
        },
        {
            "vendor": "ostrander",
            "number": "NC-0412",
            "by": ACCOUNTS_PAYABLE,
            "date": ("prev", 27),
            "due": ("this", 28),
            "amount": 135000,
            "account": "5500",
            "description": "Install crew share, Harbor & Vine and Ridgeline jobs",
            "approve": ("prev", 28),
        },
        # This month to date: approved and open, so this month's budgets have spend against them.
        {
            "vendor": "fernway",
            "number": "FA-1042",
            "by": FINANCE_OWNER,
            "date": ("this", 1),
            "due": ("this", 28),
            "amount": 270000,
            "account": "6300",
            "description": "Paid search and social, {month}",
            "approve": ("this", 1),
        },
        {
            "vendor": "kiln",
            "number": "KR-5520",
            "by": FINANCE_OWNER,
            "date": ("this", 1),
            "due": ("this", 28),
            "amount": 112500,
            "account": "5600",
            "description": "Render hosting, {month}",
            "approve": ("this", 1),
        },
        {
            "vendor": "keelson",
            "number": "KF-88213",
            "by": ACCOUNTS_PAYABLE,
            "date": ("this", 1),
            "due": ("this", 28),
            "amount": 192000,
            "account": "5400",
            "description": "Container 3, ocean freight and drayage",
            "approve": ("this", 1),
        },
    ],
    # One monthly budget each, starting this month. Spend this month is judged against them; the previous month is
    # history, so its spend is not a budget's concern.
    "budgets": [
        {
            "name": "Paid advertising",
            "account": "6300",
            "owner": "bot:growth-manager",
            "amount": 300000,
            "by": OWNER,
        },
        {
            "name": "Render cloud hosting",
            "account": "5600",
            "owner": "bot:product-manager",
            "amount": 150000,
            "by": OWNER,
        },
        {"name": "Marketplace fees", "account": "6200", "owner": OWNER, "amount": 100000, "by": OWNER},
        {"name": "Freight in", "account": "5400", "owner": ACCOUNTS_PAYABLE, "amount": 250000, "by": OWNER},
    ],
    # Gate and posting rules. `account` is a chart code and becomes an account id when the rule is written.
    "rules": [
        {
            "kind": "gate",
            "name": "Spend over $500 needs Ana",
            "words": "A bill approval over $500 waits for Ana.",
            "when": {"kind": "bill_approve", "amount": {"gt": 50000}},
            "outcome": {"outcome": "needs_approval", "who": "owner"},
            "examples": [
                {"input": {"kind": "bill_approve", "amount": 50001}, "expect": "needs_approval"},
                {"input": {"kind": "bill_approve", "amount": 50000}, "expect": "allow"},
            ],
            "by": OWNER,
            "on": ("prev", 1),
        },
        {
            "kind": "posting",
            "name": "Marketplace fees go to 6200",
            "words": "Fees from the marketplace post to Marketplace fees.",
            "when": {"counterparty": "Crate and Shelf Marketplace"},
            "outcome": {"outcome": "post", "account": "6200"},
            "examples": [
                {
                    "input": {"kind": "bill", "counterparty": "Crate and Shelf Marketplace", "amount": 54000},
                    "expect": "post",
                    "account": "6200",
                },
                {
                    "input": {"kind": "bill", "counterparty": "Keelson Freight Forwarding", "amount": 192000},
                    "expect": "none",
                },
            ],
            "by": OWNER,
            "on": ("prev", 1),
        },
    ],
    # The Finance Owner's first rule proposal. A bot's rule needs a person's OK, so it waits for Ana. It is made four days
    # before the build.
    "proposal": {
        "kind": "posting",
        "name": "Freight bills go to 5400",
        "by": FINANCE_OWNER,
        "words": "Bills from Keelson Freight Forwarding post to Freight in.",
        "when": {"counterparty": "Keelson Freight Forwarding"},
        "outcome": {"outcome": "post", "account": "5400"},
        "examples": [
            {
                "input": {"kind": "bill", "counterparty": "Keelson Freight Forwarding", "amount": 192000},
                "expect": "post",
                "account": "5400",
            },
            {"input": {"kind": "bill", "counterparty": "Lumen Assembly Co.", "amount": 864000}, "expect": "none"},
        ],
        "proposed": ("ago", 4),
    },
    "chase": {
        "title": "Chase ACME-1003 from Tidewater Coffee Co.",
        "body": (
            "Invoice ACME-1003 for $1,240.00 was due {due} and is still unpaid. Send the usual reminder "
            "and tell Ana if it is not paid by {by}."
        ),
        "invoice": "ACME-1003",
    },
}


class Calendar:
    """The months the books are dated in, counted from the build's own month. A CONTENT date is (month, day), or
    ("ago", days) for the day that many days before the build's."""

    OFFSET = {"prev": -1, "this": 0}

    def __init__(self, now):
        self.now = now  # UTC, to the minute

    def day(self, when):
        if isinstance(when, date):
            return when
        month, day = when
        if month == "ago":
            return self.now.date() - timedelta(days=day)
        index = self.now.year * 12 + self.now.month - 1 + self.OFFSET[month]
        return date(index // 12, index % 12 + 1, day)

    def opening(self):
        """The last day of the month before the previous one: the opening balance is set there."""
        return self.day(("prev", 1)) - timedelta(days=1)

    def at(self, when, hour=10):
        """When an entry dated `when` is made: that day at `hour`, and never after the build's own moment."""
        day = self.day(when)
        stamp = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
        return min(stamp, self.now).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def words(self, text, when):
        """A memo or description with its dates filled in from the entry's own day."""
        day = self.day(when)
        return text.format(date=self.long(day), month=f"{day:%B}", through=self.long(day - timedelta(days=1)))

    @staticmethod
    def long(day):
        return f"{day:%B} {day.day}"


@contextlib.contextmanager
def _hub_clock(b, days):
    """The hub's clock set to `days` before the build for the writes inside: the tasks and asks they stamp."""
    before = b.clock.at
    b.at(days=days)
    try:
        yield
    finally:
        b.clock.at = before


def _codes(c, cal):
    """Account id by chart code, for this book, read through Finance's reads (the books as the Company page reads them,
    at the build's moment)."""
    return {a.code: a.id for a in RD.book_view(c, cal.at(cal.now.date())).accounts.values()}


def _act(c, cal, who, kind, payload, tag, when, expect="committed"):
    res = FA.act(c, who, kind, payload, f"demo-finance:{tag}", at=cal.at(when))
    if res.status != expect:
        raise AssertionError(f"finance demo: {tag} ({kind}) was {res.status}: {res.reason}")
    return res


def _line(account_id, amount, description="", tags=None):
    line = {"account_id": account_id, "amount": amount, "description": description}
    if tags:
        line["tags"] = tags
    return line


def _journal(c, cal, who, tag, when, memo, rows, codes):
    lines = [_line(codes[code], amount, text) for code, amount, text in rows]
    _act(
        c,
        cal,
        who,
        "journal",
        {"date": cal.day(when).isoformat(), "memo": cal.words(memo, when), "lines": lines},
        tag,
        when,
    )


def _resolve(value, codes):
    """A rule's account codes become account ids; everything else is copied as it is."""
    if isinstance(value, dict):
        return {
            ("account_id" if k == "account" else k): (codes[v] if k == "account" else _resolve(v, codes))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_resolve(v, codes) for v in value]
    return value


def _setup(c, cal):
    opened = cal.opening()
    FA.ensure_setup(c, CONTENT["owner_email"], cal.at(opened, hour=8))
    bookkeeper.grant_defaults(c)
    bookkeeper.seed_bot_payment_rule(c, cal.at(opened, hour=8))  # as an install: a bot's payment asks a finance admin
    # The Finance Owner sees the books by default, before its first entry: a journal line on an account the poster
    # cannot see is refused (handlers.may_write).
    if bookkeeper.grant_view(c, OWNER, cal.at(opened, hour=8)) is None:
        raise AssertionError("finance demo: the Finance Owner was not given its default view")
    _act(c, cal, OWNER, "right_grant", {"teammate": ACCOUNTS_PAYABLE, "right": "finance.ap"}, "right-ap", opened)
    for who in (ACCOUNTS_PAYABLE,):
        _act(
            c,
            cal,
            OWNER,
            "view_grant",
            {"subject_kind": "teammate", "subject": who, "what": "everything"},
            f"view-{who}",
            opened,
        )
    for code, name, kind, parent in CONTENT["chart"]:
        codes = _codes(c, cal)
        _act(
            c,
            cal,
            OWNER,
            "account_create",
            {"code": code, "name": name, "type": kind, "parent_id": codes[parent]},
            f"account-{code}",
            opened,
        )
    codes = _codes(c, cal)
    start = cal.day(("this", 1))
    for i, bud in enumerate(CONTENT["budgets"]):
        payload = {
            "name": bud["name"],
            "period": "month",
            "account_id": codes[bud["account"]],
            "owner": bud["owner"],
            "amount": bud["amount"],
            "effective_from": start.isoformat(),
            "at_limit": "warn",
        }
        _act(c, cal, bud["by"], "budget_create", payload, f"budget-{i}", opened)


def _books(c, cal):
    codes = _codes(c, cal)
    op = CONTENT["opening"]
    opened = cal.opening()
    _journal(
        c,
        cal,
        OWNER,
        "opening",
        opened,
        op["memo"],
        [("1000", op["amount"], "Bank balance"), ("3900", -op["amount"], "Opening balance equity")],
        codes,
    )
    tu = CONTENT["gateway_topup"]
    _journal(
        c,
        cal,
        FINANCE_OWNER,
        "topup",
        tu["date"],
        tu["memo"],
        [("1100", tu["amount"], "Gateway credit"), ("1000", -tu["amount"], "Paid from bank")],
        codes,
    )
    for i, week in enumerate(CONTENT["store_weeks"]):
        _journal(
            c,
            cal,
            FINANCE_OWNER,
            f"store-{i}",
            week["date"],
            week["memo"],
            [("1000", week["amount"], "Card settlements"), ("4100", -week["amount"], "Store sales")],
            codes,
        )
    st = CONTENT["store_this_month"]
    _journal(
        c,
        cal,
        FINANCE_OWNER,
        "store-this-month",
        st["date"],
        st["memo"],
        [("1000", st["amount"], "Card settlements"), ("4100", -st["amount"], "Store sales")],
        codes,
    )
    for i, pay in enumerate(CONTENT["marketplace"]["payouts"]):
        _journal(
            c,
            cal,
            FINANCE_OWNER,
            f"marketplace-{i}",
            pay["date"],
            pay["memo"],
            [
                ("1000", pay["gross"] - pay["fees"], "Payout after fees"),
                ("6200", pay["fees"], "Marketplace fees"),
                ("4200", -pay["gross"], "Marketplace sales"),
            ],
            codes,
        )
    sub = CONTENT["subscriptions"]
    rows = [("1000", sum(amount for _, amount in sub["lines"]), "Subscription payments")]
    rows += [("4500", -amount, label) for label, amount in sub["lines"]]
    _journal(c, cal, FINANCE_OWNER, "subscriptions", sub["date"], sub["memo"], rows, codes)
    use = CONTENT["usage"]
    lines = [_line(codes["5100"], amount, "AI services", {"teammate": who}) for who, amount in use["by_teammate"]]
    lines.append(_line(codes["1100"], -sum(amount for _, amount in use["by_teammate"]), "Gateway credit used"))
    FA.post_entry(
        c,
        {
            "date": cal.day(use["date"]).isoformat(),
            "memo": cal.words(use["memo"], use["date"]),
            "source": "usage",
            "source_id": f"{use['source_id']}-{cal.day(use['date']):%Y-%m}",
            "lines": lines,
        },
        at=cal.at(use["date"]),
    )


def _billing(c, cal):
    codes = _codes(c, cal)
    vendors, customers = {}, {}
    for v in CONTENT["vendors"]:
        res = _act(
            c,
            cal,
            FINANCE_OWNER,
            "contact_create",
            {"name": v["name"], "kind": "vendor", "emails": [v["email"]]},
            f"vendor-{v['key']}",
            ("prev", 1),
        )
        vendors[v["key"]] = res.result["contact_id"]
    for cu in CONTENT["customers"]:
        res = _act(
            c,
            cal,
            FINANCE_OWNER,
            "contact_create",
            {"name": cu["name"], "kind": "customer", "emails": [cu["email"]]},
            f"customer-{cu['key']}",
            ("prev", 1),
        )
        customers[cu["key"]] = res.result["contact_id"]
    for inv in CONTENT["invoices"]:
        line = _line(codes[inv["account"]], inv["amount"], cal.words(inv["description"], inv["date"]))
        made = _act(
            c,
            cal,
            FINANCE_OWNER,
            "invoice_create",
            {
                "customer_id": customers[inv["customer"]],
                "number": inv["number"],
                "invoice_date": cal.day(inv["date"]).isoformat(),
                "due_date": cal.day(inv["due"]).isoformat(),
                "lines": [line],
            },
            f"invoice-{inv['number']}",
            inv["date"],
        )
        invoice_id = made.result["invoice_id"]
        _act(c, cal, OWNER, "invoice_send", {"invoice_id": invoice_id}, f"send-{inv['number']}", inv["date"])
        if "paid" in inv:
            _act(
                c,
                cal,
                FINANCE_OWNER,
                "invoice_record_payment",
                {
                    "invoice_id": invoice_id,
                    "amount": inv["amount"],
                    "date": cal.day(inv["paid"]).isoformat(),
                    "account_id": codes["1000"],
                },
                f"paid-{inv['number']}",
                inv["paid"],
            )
    for bill in CONTENT["bills"]:
        line = _line(codes[bill["account"]], bill["amount"], cal.words(bill["description"], bill["date"]))
        made = _act(
            c,
            cal,
            bill["by"],
            "bill_create",
            {
                "vendor_id": vendors[bill["vendor"]],
                "number": bill["number"],
                "bill_date": cal.day(bill["date"]).isoformat(),
                "due_date": cal.day(bill["due"]).isoformat(),
                "lines": [line],
            },
            f"bill-{bill['number']}",
            bill["date"],
        )
        bill_id = made.result["bill_id"]
        _act(c, cal, OWNER, "bill_approve", {"bill_id": bill_id}, f"approve-{bill['number']}", bill["approve"])
        if "paid" in bill:
            _act(
                c,
                cal,
                OWNER,
                "bill_mark_paid",
                {
                    "bill_id": bill_id,
                    "amount": bill["amount"],
                    "date": cal.day(bill["paid"]).isoformat(),
                    "account_id": codes["1000"],
                },
                f"paid-{bill['number']}",
                bill["paid"],
            )


def _rule_payload(rule, codes):
    return _resolve({k: rule[k] for k in ("kind", "name", "words", "when", "outcome", "examples")}, codes)


def _rules_and_tasks(c, cal):
    codes = _codes(c, cal)
    for i, rule in enumerate(CONTENT["rules"]):
        _act(c, cal, rule["by"], "rule_create", _rule_payload(rule, codes), f"rule-{i}", rule["on"])
    chase = CONTENT["chase"]
    due = cal.day(next(i for i in CONTENT["invoices"] if i["number"] == chase["invoice"])["due"])
    body = chase["body"].format(due=cal.long(due), by=cal.long(cal.now.date() + timedelta(days=7)))
    hubdb.task_create(c, OWNER, chase["title"], body, "bot:accounts-receivable-specialist", allow_planned=True)


def _propose_rule(c, cal, rule):
    """The Finance Owner's proposal: a bot's rule waits for a person's OK, so it lands in Ana's Needs you."""
    _act(
        c,
        cal,
        rule["by"],
        "rule_create",
        _rule_payload(rule, _codes(c, cal)),
        "rule-proposal",
        rule["proposed"],
        expect="needs_approval",
    )


def seed(b):
    cal = Calendar(b.now)
    b.write(lambda c: _setup(c, cal))
    b.write(lambda c: _books(c, cal))
    b.write(lambda c: _billing(c, cal))
    b.write(lambda c: _rules_and_tasks(c, cal))
    with _hub_clock(b, days=4):
        b.write(lambda c: _propose_rule(c, cal, CONTENT["proposal"]))
