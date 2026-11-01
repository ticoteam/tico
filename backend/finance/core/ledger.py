"""The books: accounts, entries, lines and their tags, and the four posting invariants (spec §4.1).

1. An entry's lines sum to zero in the book's base currency, each line's base amount being its original amount at its
   fx rate.
2. Posted entries and lines never change; a correction is a reversing entry (also a database trigger).
3. Nothing posts into a closed period (also a database trigger).
4. A line's account is active and in the entry's book.
"""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from . import dates, money
from . import validate as V
from .errors import UNBALANCED, FinanceError, invalid

ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")
PNL_TYPES = ("income", "expense")
SOURCES = ("manual", "import", "bank", "card", "bill", "invoice", "payment", "usage", "sync")
# `accrual`: left out of the cash view (depreciation, accruals, a write-off). Bills and invoices are accrual by source:
# their cash view comes from settlements instead.
BASES = ("both", "accrual")
DOC_SOURCES = ("bill", "invoice")
# Tags on a line, one value per kind. Vendor is not a tag: it is the line's contact.
# location, project and class name a shared record (backend/records, fin_dimensions); the others are text.
TAG_KINDS = ("team", "teammate", "budget", "project", "custom", "location", "class")
MAX_LINES = 500


@dataclass(frozen=True)
class Account:
    id: str
    book_id: str
    code: str
    name: str
    type: str
    subtype: str = ""
    parent_id: str = ""
    active: bool = True


@dataclass(frozen=True)
class Line:
    """One posting line. `amount` is base-currency minor units (debit +, credit -); `original_amount` is in
    `currency` and `fx_rate` turns it into `amount`."""
    account_id: str
    amount: int
    currency: str
    original_amount: int
    fx_rate: Decimal = Decimal(1)
    contact_id: str = ""
    description: str = ""
    tags: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    role: str = ""          # what the line is to Tico, set only by Tico's own postings (MONEY_ROLE), never a request
    external_line: str = ""  # the provider's id for the line, so its tags follow it across a revision


NAME_MAX = 80               # a vendor name's length: names are capped to it before they are keyed, never keys after
KEY_MAX = 3 * NAME_MAX      # a key's: folding turns a character into up to three (ß is ss)


def name_key(name: str) -> str:
    """A vendor's plain name as a key (a line's contact_id when no contact has it, a budget's vendor, an alias):
    case-folded, then stripped, so ÉCOLE and école are one vendor. The only place a vendor is normalised. The key is
    whole (folding can lengthen it; cutting it after could make two names one) and folding it again changes nothing.
    A contact's id is its own key (already folded)."""
    return name.casefold().strip()


MONEY_ROLE = "money"        # a feed row's own line: the money account's side (for a card, the card program's)


@dataclass(frozen=True)
class Entry:
    book_id: str
    entry_date: str
    memo: str
    source: str
    source_id: str
    lines: tuple[Line, ...]
    basis: str = "both"

    def cash_view(self) -> bool:
        """Whether the cash P&L takes this entry as posted (else a correction leaves its P&L lines out)."""
        return self.basis == "both" and self.source not in DOC_SOURCES


@dataclass(frozen=True)
class Book:
    id: str
    name: str
    base_currency: str
    basis: str = "cash"
    fiscal_year_start: int = 1
    mode: str = "native"                # native: Tico is the books; mirror: a provider's books are (spec §10)
    mirror_connection_id: str = ""


def tags(raw: object) -> dict[str, str]:
    """A line's tags as {kind: value}; an empty value drops the kind."""
    if raw is None:
        return {}
    out: dict[str, str] = {}
    for kind, value in V.obj(raw, "tags").items():
        if kind not in TAG_KINDS:
            raise invalid(f"a tag is {'|'.join(TAG_KINDS)}, not {kind}")
        if value is None or value == "":
            continue
        if not isinstance(value, str) or len(value) > 200:
            raise invalid(f"tag {kind} is a text value")
        if "|" in value:                  # a rollup key joins tags with |, so a value holding one could be misread
            raise invalid(f"tag {kind} has no | in it")
        out[kind] = value.strip()
    return out


def tagset(tag_map: Mapping[str, str], vendor: str = "") -> str:
    """The canonical key a rollup row is kept under: every tag and the vendor, sorted. "" is a line with none. Values
    hold no "|" (tags() refuses one), so a reader can split the key back into its tags."""
    parts = [f"{kind}={tag_map[kind]}" for kind in sorted(tag_map) if tag_map[kind]]
    if vendor:
        parts.append(f"vendor={vendor}")
    return "|".join(parts)


def line(raw: object, base: str) -> Line:
    """A line from a request, in the book's base currency. Other currencies are refused until FX is specified; the
    original-amount and rate columns stay so it can arrive without a migration."""
    p = V.obj(raw, "a line")
    V.only(p, ("account_id", "amount", "currency", "original_amount", "fx_rate", "contact_id", "description", "tags"))
    account_id = V.text(p, "account_id", 80)
    cur = money.currency(p.get("currency") or base)
    if cur != base:
        raise invalid(f"lines are in the book's currency ({base}); {cur} is not supported yet")
    if "fx_rate" in p and money.rate(p.get("fx_rate")) != 1:
        raise invalid("in the base currency, fx_rate is 1")
    amount = V.amount(p) if "amount" in p else V.amount(p, "original_amount")
    if "original_amount" in p and V.amount(p, "original_amount") != amount:
        raise invalid("in the base currency, original_amount equals amount")
    if amount == 0:
        raise invalid("a line moves money: amount is not 0")
    return Line(account_id=account_id, amount=amount, currency=cur, original_amount=amount, fx_rate=Decimal(1),
                contact_id=vendor_text(p), description=V.opt_text(p, "description") or "", tags=tags(p.get("tags")))


def vendor_text(p: V.Payload, key: str = "contact_id") -> str:
    """A vendor (a line's, a budget's): a contact's id or a name of up to NAME_MAX characters, or a key already folded
    (name_key), which folding may have made longer (up to KEY_MAX): a line Tico posts again (a reclass) keeps its
    stored key, and a budget can be made on a vendor key a report shows."""
    value = V.opt_text(p, key, KEY_MAX) or ""
    if len(value) > NAME_MAX and name_key(value) != value:
        raise invalid(f"{key} is longer than {NAME_MAX} characters")
    return value


def entry(raw: object, book: Book) -> Entry:
    """A journal entry from a request: a date, a memo, a source (`manual` or `import`) and balanced lines."""
    p = V.obj(raw)
    source = V.choice(p, "source", ("manual", "import"), "manual")
    lines = tuple(line(item, book.base_currency) for item in V.items(p.get("lines"), "lines", MAX_LINES))
    return Entry(book_id=book.id, entry_date=V.day(p, "date"), memo=V.opt_text(p, "memo") or "", source=source,
                 source_id=V.opt_text(p, "source_id", 200) or "", lines=lines,
                 basis=V.choice(p, "basis", BASES, "both"))


def check(e: Entry, book: Book, accounts: Mapping[str, Account], closed: bool, reversing: bool = False,
          importing: bool = False) -> None:
    """Invariants 1, 3 and 4 for an entry about to post. `closed` says whether its date is in a closed period. A
    reversal may post to an account deactivated since the original, or the original could never be undone; a provider's
    history may name an account it retired, which is taken in as it was (`importing`)."""
    if e.source not in SOURCES:
        raise invalid(f"a source is {'|'.join(SOURCES)}")
    if e.basis not in BASES:
        raise invalid(f"a basis is {'|'.join(BASES)}")
    dates.day(e.entry_date)
    if closed:
        raise FinanceError("period_closed", f"the period holding {e.entry_date} is closed")
    if len(e.lines) < 2:
        raise invalid("an entry has at least two lines")
    total = 0
    for item in e.lines:
        account = accounts.get(item.account_id)
        if account is None or account.book_id != e.book_id:
            raise invalid("a line's account is not in this book")
        if not account.active and not (reversing or importing):
            raise invalid(f"account {account.code} {account.name} is inactive")
        money.minor(item.amount)
        if item.currency != book.base_currency:
            raise invalid(f"lines are in the book's currency ({book.base_currency}); {item.currency} is not supported yet")
        if item.original_amount != item.amount or item.fx_rate != 1:
            raise invalid("in the base currency, original_amount equals amount at fx_rate 1")
        total += item.amount
    if total != 0:
        raise FinanceError(UNBALANCED, f"the lines sum to {total}, not 0, in {book.base_currency}")


def reversal(lines: Iterable[Line]) -> tuple[Line, ...]:
    """The lines that undo `lines`: every amount negated, tags, contacts and roles kept."""
    return tuple(Line(account_id=x.account_id, amount=-x.amount, currency=x.currency,
                      original_amount=-x.original_amount, fx_rate=x.fx_rate, contact_id=x.contact_id,
                      description=x.description, tags=dict(x.tags), role=x.role) for x in lines)


PNL_SECTIONS = ("revenue", "cost_of_revenue", "operating", "other")
OTHER_SUBTYPES = ("other_income", "other_expense")


def pnl_section(accounts: Mapping[str, Account], account: Account) -> str:
    """Where a P&L line sits: revenue, cost of revenue (a `cogs` line or one under it), operating expenses, or other
    income and expense (an `other_income` / `other_expense` line or one under it)."""
    at: Account | None = account
    seen: set[str] = set()
    while at is not None and at.id not in seen:
        seen.add(at.id)
        if at.subtype in OTHER_SUBTYPES:
            return "other"
        if at.subtype == "cogs" and account.type == "expense":
            return "cost_of_revenue"
        at = accounts.get(at.parent_id) if at.parent_id else None
    return "revenue" if account.type == "income" else "operating"


def descendants(accounts: Mapping[str, Account], root: str) -> set[str]:
    """`root` and every account below it in the chart."""
    children: dict[str, list[str]] = {}
    for account in accounts.values():
        if account.parent_id:
            children.setdefault(account.parent_id, []).append(account.id)
    found, todo = {root}, [root]
    while todo:
        for child in children.get(todo.pop(), []):
            if child not in found:
                found.add(child)
                todo.append(child)
    return found


def ancestors(accounts: Mapping[str, Account], account_id: str) -> list[str]:
    """`account_id` and its parents, nearest first."""
    out: list[str] = []
    at = account_id
    while at and at in accounts and at not in out:
        out.append(at)
        at = accounts[at].parent_id
    return out


def check_account(new: Account, accounts: Mapping[str, Account]) -> None:
    """A new or changed account: a known type, a unique code in its book, and a parent of the same type that does not
    make a loop."""
    if new.type not in ACCOUNT_TYPES:
        raise invalid(f"an account type is {'|'.join(ACCOUNT_TYPES)}")
    if not new.code or not new.name:
        raise invalid("an account has a code and a name")
    if any(a.code == new.code and a.book_id == new.book_id and a.id != new.id for a in accounts.values()):
        raise invalid(f"account code {new.code} is taken")
    if new.parent_id:
        parent = accounts.get(new.parent_id)
        if parent is None or parent.book_id != new.book_id:
            raise invalid("the parent account is not in this book")
        if parent.type != new.type:
            raise invalid("a parent account has the same type")
        if new.id in ancestors({**accounts, new.id: new}, new.parent_id):
            raise invalid("an account cannot sit under itself")
