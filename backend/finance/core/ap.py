"""Bills, invoices and posting rules without I/O (spec §4.1, §4.2, §6.1–6.3): document lines, due dates, aging
buckets, reminder stages, how a payment's share of a document is recognized in the cash view, and what a posting rule
says about a feed row."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import cast

from . import dates
from . import gates as G
from . import ledger as L
from . import validate as V
from .errors import invalid

BILL_STATUSES = ("draft", "open", "scheduled", "paid", "void")
INVOICE_STATUSES = ("draft", "sent", "partial", "paid", "void")
OPEN_BILL = ("open", "scheduled")
OPEN_INVOICE = ("sent", "partial")
CONTACT_KINDS = ("vendor", "customer", "both")
ORIGINS = ("manual", "email", "upload")
SUBJECTS = ("entry", "feed", "bill", "invoice")
AGING = ("current", "1_30", "31_60", "61_90", "over_90")
# Reminder stages: a bill a week out, a day out and on the day; an invoice a day, a week, a month... late.
BILL_STAGES = (("due_in_7", 7), ("due_in_1", 1), ("due_today", 0))
INVOICE_STAGES = (("overdue_1", 1), ("overdue_7", 7), ("overdue_30", 30), ("overdue_60", 60), ("overdue_90", 90))
# A vendor renewal: a month out, then a week out (the tightest stage reached is the one raised).
RENEWAL_STAGES = (("renewal_in_30", 30), ("renewal_in_7", 7))
MAX_TERMS = 365
MAX_PREPAID_MONTHS = 120
SHARE_WHOLE = 10_000          # posting-rule splits are in basis points
DEFAULT_PRIORITY = 100


@dataclass(frozen=True)
class DocLine:
    account_id: str
    amount: int
    description: str = ""
    tags: Mapping[str, str] = field(default_factory=lambda: cast(dict[str, str], {}))
    taxable: bool | None = None         # an invoice line only: whether sales tax applies to it (None: not said)
    item_id: str = ""                   # the shared item the line sells or buys, if any (backend/records)
    po_line_id: str = ""                # a bill line only: the purchase order line this bill line bills
    prepaid: tuple[str, int] | None = None  # a bill line only: (first month YYYY-MM, months) it is expensed over


def doc_lines(raw: object, *, invoice: bool = False) -> list[DocLine]:
    """A bill's or invoice's lines: each a positive amount with an account (a bill's may leave it out for the vendor's
    default or a posting rule to fill), a description and tags. An invoice line may also say whether it is taxable,
    and a bill line may name the purchase order line it bills, or a prepaid schedule instead."""
    out: list[DocLine] = []
    allowed = ("account_id", "item_id", "amount", "description", "tags",
               *(("taxable",) if invoice else ("po_line_id", "prepaid")))
    for item in V.items(raw, "lines", L.MAX_LINES):
        q = V.obj(item, "a line")
        V.only(q, allowed)
        amount = V.amount(q)
        if amount <= 0:
            raise invalid("a document line's amount is more than 0")
        taxable = V.flag(q, "taxable") if "taxable" in q else None
        po_line_id = V.opt_text(q, "po_line_id", 80) or ""
        prepaid = _prepaid(q["prepaid"], amount) if "prepaid" in q else None
        if prepaid and po_line_id:
            raise invalid("a prepaid line does not bill a purchase order line")
        out.append(DocLine(V.opt_text(q, "account_id", 80) or "", amount, V.opt_text(q, "description") or "",
                           L.tags(q.get("tags")), taxable, V.opt_text(q, "item_id", 80) or "", po_line_id, prepaid))
    return out


def _prepaid(raw: object, amount: int) -> tuple[str, int]:
    """A prepaid schedule: the month the expense starts (YYYY-MM) and how many months it is spread over, with at
    least a cent for each."""
    p = V.obj(raw, "prepaid")
    V.only(p, ("start", "months"))
    months = V.integer(p, "months", 1, MAX_PREPAID_MONTHS)
    if amount < months:
        raise invalid("a prepaid line's amount is at least a cent a month")
    return V.month(p, "start"), months


def prepaid_month(start: str, n: int) -> str:
    """The month `n` months after a prepaid's start (0 is the start), as YYYY-MM."""
    return dates.add_months(dates.month(start), n).isoformat()[:7]


def prepaid_amounts(amount: int, months: int) -> list[int]:
    """A prepaid's expense each month: the amount in equal parts, the cents left over going to the last month."""
    per = amount // months
    return [per] * (months - 1) + [amount - per * (months - 1)]


def due_date(issued: str, due: str | None, terms_days: int | None) -> str | None:
    """The due date given, else the issue date plus the terms, else none."""
    if due:
        if due < issued:
            raise invalid("due_date is before the document's date")
        return due
    if terms_days is None:
        return None
    return (dates.day(issued) + timedelta(days=terms_days)).isoformat()


def days_late(due: str | None, today: date) -> int:
    """Days past due (negative: days left); 0 with no due date."""
    return 0 if not due else (today - dates.day(due)).days


def aging(due: str | None, today: date) -> str:
    """The aging bucket an open document falls in on `today`."""
    late = days_late(due, today)
    if late <= 0:
        return "current"
    return "1_30" if late <= 30 else "31_60" if late <= 60 else "61_90" if late <= 90 else "over_90"


def bill_stage(due: str | None, today: date) -> str | None:
    """The tightest reminder stage an open bill has reached (none before a week out or with no due date)."""
    if not due:
        return None
    left = (dates.day(due) - today).days
    reached = [name for name, days in BILL_STAGES if left <= days]
    return reached[-1] if reached else None


def renewal_stage(renewal: str | None, today: date) -> str | None:
    """The tightest renewal stage a vendor has reached; none without a date or once the date has passed."""
    if not renewal:
        return None
    left = (dates.day(renewal) - today).days
    if left < 0:
        return None
    reached = [name for name, days in RENEWAL_STAGES if left <= days]
    return reached[-1] if reached else None


def invoice_stage(due: str | None, today: date) -> str | None:
    """The latest overdue stage an open invoice has reached."""
    late = days_late(due, today)
    reached = [name for name, days in INVOICE_STAGES if late >= days]
    return reached[-1] if reached else None


def recognized(lines: Sequence[int], paid: int) -> list[int]:
    """How much of each document line the cash view has recognized once `paid` of the lines' total is settled: in
    proportion, the cents rounding leaves going to the lines with the largest remainders (then the first), so the parts
    always sum to `paid` exactly and cash and accrual agree once a document is fully paid."""
    total = sum(lines)
    if total <= 0:
        return [0 for _ in lines]
    paid = max(0, min(paid, total))
    base = [x * paid // total for x in lines]
    short = paid - sum(base)
    order = sorted(range(len(lines)), key=lambda i: (-(lines[i] * paid % total), i))
    for i in order[:short]:
        base[i] += 1
    return base


def shares(lines: Sequence[int], before: int, after: int) -> list[int]:
    """Each line's cash-view change when a document's settled amount moves from `before` to `after`."""
    return [b - a for a, b in zip(recognized(lines, before), recognized(lines, after), strict=True)]


# ------------------------------------------------------------------------------------------------ posting rules
@dataclass(frozen=True)
class Split:
    account_id: str
    share: int                # basis points of the row
    tags: Mapping[str, str] = field(default_factory=lambda: cast(dict[str, str], {}))


@dataclass(frozen=True)
class Posting:
    """A posting rule's outcome: post to an account (or a split), with tags and a contact, or ask a person."""
    outcome: str              # post | ask
    account_id: str = ""
    tags: Mapping[str, str] = field(default_factory=lambda: cast(dict[str, str], {}))
    contact_id: str = ""
    split: tuple[Split, ...] = ()


def parse_posting(raw: object) -> Posting:
    p = V.obj(raw, "outcome")
    kind = V.choice(p, "outcome", ("post", "ask"))
    if kind == "ask":
        V.only(p, ("outcome",))
        return Posting("ask")
    V.only(p, ("outcome", "account_id", "tags", "contact_id", "split"))
    split: list[Split] = []
    if p.get("split") is not None:
        for item in V.items(p.get("split"), "split", 20):
            q = V.obj(item, "a split")
            V.only(q, ("account_id", "share", "tags"))
            split.append(Split(V.text(q, "account_id", 80), V.integer(q, "share", 1, SHARE_WHOLE),
                               L.tags(q.get("tags"))))
        if sum(x.share for x in split) != SHARE_WHOLE:
            raise invalid("a split's shares add up to 10000 (basis points)")
    account = V.opt_text(p, "account_id", 80) or ""
    if not account and not split:
        raise invalid("a posting rule posts to account_id or a split")
    return Posting("post", account, L.tags(p.get("tags")), V.opt_text(p, "contact_id", 80) or "", tuple(split))


def posting_json(x: Posting) -> dict[str, object]:
    if x.outcome == "ask":
        return {"outcome": "ask"}
    out: dict[str, object] = {"outcome": "post"}
    if x.account_id:
        out["account_id"] = x.account_id
    if x.tags:
        out["tags"] = dict(x.tags)
    if x.contact_id:
        out["contact_id"] = x.contact_id
    if x.split:
        out["split"] = [{"account_id": s.account_id, "share": s.share, **({"tags": dict(s.tags)} if s.tags else {})}
                        for s in x.split]
    return out


def split_amounts(amount: int, posting: Posting) -> list[tuple[str, int, Mapping[str, str]]]:
    """(account, amount, tags) for each part of a row; the last part takes the rounding, so the parts sum exactly."""
    if not posting.split:
        return [(posting.account_id, amount, posting.tags)]
    out: list[tuple[str, int, Mapping[str, str]]] = []
    left = amount
    for n, s in enumerate(posting.split):
        part = left if n == len(posting.split) - 1 else amount * s.share // SHARE_WHOLE
        left -= part
        out.append((s.account_id, part, {**posting.tags, **s.tags}))
    return out


@dataclass(frozen=True)
class PostingRule:
    id: str
    name: str
    when: Mapping[str, object]
    then: Posting
    priority: int = DEFAULT_PRIORITY


def first_match(rules: Sequence[PostingRule], facts: Mapping[str, object]) -> PostingRule | None:
    """The first rule by priority (then id) whose conditions hold for a row's facts."""
    for rule in sorted(rules, key=lambda r: (r.priority, r.id)):
        if G.matches(rule.when, facts):
            return rule
    return None


def failing_posting_examples(when: Mapping[str, object], then: Posting,
                             examples: Sequence[Mapping[str, object]]) -> list[str]:
    """Examples this rule gets wrong, in words. An example is {input, expect: post|ask|none, account_id?}: none says
    the rule does not apply to that row."""
    bad: list[str] = []
    for n, ex in enumerate(examples, 1):
        facts = cast(Mapping[str, object], ex.get("input") or {})
        expect = str(ex.get("expect") or "")
        got = then.outcome if G.matches(when, facts) else "none"
        if got != expect or (expect == "post" and ex.get("account_id") not in (None, then.account_id)):
            bad.append(f"example {n} gives {got}, not {expect}")
    return bad


def parse_posting_examples(raw: object) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for item in V.items(raw, "examples", G.MAX_EXAMPLES):
        q = V.obj(item, "an example")
        V.only(q, ("input", "expect", "account_id"))
        facts = dict(V.obj(q.get("input"), "an example's input"))
        ex: dict[str, object] = {"input": facts, "expect": V.choice(q, "expect", ("post", "ask", "none"))}
        if q.get("account_id") is not None:
            ex["account_id"] = V.text(q, "account_id", 80)
        out.append(ex)
    return out
