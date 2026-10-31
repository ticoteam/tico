"""Setting Finance up on an install: the book, the default chart, this fiscal year's monthly periods, the default views
and the three default budgets, none with an amount until a person sets one (the company usage cap keeps its own pause in
backend/usage_limits.py). Safe to run again: it fills in what is missing and changes nothing a person set."""
import sqlite3
from dataclasses import dataclass

from .context import APPROVER_KEY, OWNER_KEY, now
from .core import budgets as B
from .core import dates, money
from .core.errors import invalid
from .core.ledger import Account, Book
from .store import access as SA
from .store import budgets as SB
from .store import ledger as SL
from .store import tico

# (code, name, type, subtype, parent code). AI services and Video production are what Tico itself spends through the
# Gateway; Other expenses is where an unknown expense lands.
CHART = (
    ("1000", "Bank", "asset", "bank", ""),
    ("1100", "Gateway credit", "asset", "prepaid", ""),
    ("1200", "Accounts receivable", "asset", "receivable", ""),
    ("2000", "Accounts payable", "liability", "payable", ""),
    ("3000", "Owner's equity", "equity", "", ""),
    ("4000", "Revenue", "income", "", ""),
    ("5000", "Cost of revenue", "expense", "cogs", ""),
    ("5100", "AI services", "expense", "ai", "5000"),
    ("5200", "Video production", "expense", "video", "5000"),
    ("6000", "Operating expenses", "expense", "", ""),
    ("6100", "Software", "expense", "software", "6000"),
    ("6900", "Other expenses", "expense", "other", "6000"),
)
AI, VIDEO = "5100", "5200"
# (name, account code or "" for the catch-all, at-limit action)
BUDGETS = (("AI services", AI, "decline"), ("Video production", VIDEO, "decline"), ("Other expenses", "", "ask"))


@dataclass(frozen=True)
class SetupResult:
    book_id: str
    created: bool
    accounts: dict[str, str]          # code -> id
    budgets: dict[str, str]           # name -> id


def _actor(owner_id: str) -> str:
    text = owner_id.strip()
    if not text:
        raise invalid("setup names the owner")
    return text if text.startswith(("human:", "bot:")) else "human:" + text


def setup(c: sqlite3.Connection, owner_id: str, at: str | None = None, *, base_currency: str = "USD",
          finance_approver: str = "") -> SetupResult:
    """Set Finance up for `owner_id` (who owns it when Tico has no owner record). Runs in the caller's transaction."""
    at = at or now()
    by = _actor(owner_id)
    created = False
    book = SL.book(c)
    if book is None:
        book = Book(id=SL.new_id(), name="Books", base_currency=money.currency(base_currency))
        SL.insert_book(c, book, by, at)
        created = True
    if SL.setting(c, OWNER_KEY) is None:
        SL.set_setting(c, OWNER_KEY, by, by, at)
    if finance_approver:
        SL.set_setting(c, APPROVER_KEY, _actor(finance_approver), by, at)
    accounts = {a.code: a for a in SL.accounts(c, book.id).values()}
    for code, name, kind, subtype, parent in CHART:
        if code not in accounts:
            made = Account(id=SL.new_id(), book_id=book.id, code=code, name=name, type=kind, subtype=subtype,
                           parent_id=accounts[parent].id if parent in accounts else "")
            SL.insert_account(c, made, by, at)
            accounts[code] = made
    first = dates.fiscal_bounds(dates.day(at), 12, book.fiscal_year_start)[0]
    for n in range(12):
        start = dates.add_months(first, n)
        SL.insert_period(c, book.id, start.isoformat(), dates.month_end(start).isoformat(), by, at)
    SA.seed_defaults(c, by, at)
    if SA.view_version(c) == 0:
        SA.bump_view(c, "setup", at)
    existing = SB.budgets(c, book.id)
    named = {(b.account_id, b.catch_all): b.id for b in existing.values() if not b.parent_id}
    ids: dict[str, str] = {}
    for name, code, at_limit in BUDGETS:
        account_id = accounts[code].id if code else ""
        key = (account_id, not code)
        if key in named:
            ids[name] = named[key]
            continue
        b = B.Budget(id=SL.new_id(), book_id=book.id, name=name, period="month", account_id=account_id,
                     catch_all=not code, owner=tico.owner_person(c, by), warn_pct=80, at_limit=at_limit,
                     created_at=at)
        SB.insert_budget(c, b, by, at)
        ids[name] = b.id
    for old in existing.values():
        if old.account_id == accounts[AI].id and not old.parent_id:
            _unseed(c, old)
    if created:
        tico.audit(c, by, "finance.setup", book.id, {"accounts": len(accounts), "budgets": sorted(ids)})
    return SetupResult(book_id=book.id, created=created, accounts={k: v.id for k, v in accounts.items()},
                       budgets=ids)


def _unseed(c: sqlite3.Connection, b: B.Budget) -> None:
    """Drop the amount an earlier setup copied from the company usage cap into a company-wide AI services budget,
    active or not (a person may have switched it off and on again): its only version, written by setup itself with the
    budget. The copy never followed the cap, so a raised or removed cap left bots paused. Its events go too, so the
    first amount a person sets arms warn and cutoff again."""
    if b.team or b.teammate or b.vendor:
        return
    if SB.seeded_version(c, b.id):
        SB.drop_versions(c, b.id)
