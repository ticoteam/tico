"""Bank and card statement files as Finance reads them (spec step 8): OFX 1.x (SGML) and 2.x (XML), QFX (the same as
OFX), and CSV from a bank's or card's download. Pure: no I/O, no store. A file becomes its rows and its facts, nothing
else; upload (backend/finance/statement_upload.py) decides what posts.

Sign: every row's `amount` is in cents with money in positive and money out negative, the feed's convention for a bank
account and for a card (a charge is negative, a payment or refund positive). A CSV whose export shows charges positive
(American Express's) is flipped here; OFX is read as the standard says (TRNAMT is the money's direction).

Facts: `start` and `end` are ISO days; `opening` and `closing` are the statement's own balances in cents, or None when
the file does not give them. A bank's balance is the balance; a card's `closing` is what is owed, positive, as the card
shows it (OFX gives a card's balance negative when owed, and it is negated here). `opening` is only known from a CSV
whose rows carry a running balance that agrees with the amounts; OFX gives the closing alone.

External ids: an OFX row keeps its FITID (a repeat within one file gets a `#n` suffix). A CSV row has none, so it gets a
stable hash of its date, amount, description and its occurrence among rows alike (so two identical coffees stay two).
"""

import csv
import hashlib
import html
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 5000
MAX_SHOWN_HEADERS = 8

# Column names a bank or card uses, most specific first. A header is matched on its folded text.
DATE_NAMES = (
    "transaction date",
    "trans date",
    "trans. date",
    "date",
    "posting date",
    "posted date",
    "post date",
    "value date",
    "booking date",
    "posted",
)
DESCRIPTION_NAMES = (
    "description",
    "original description",
    "transaction description",
    "payee",
    "merchant",
    "name",
    "details",
    "memo",
    "narrative",
)
AMOUNT_NAMES = ("amount", "amount (usd)", "transaction amount", "amt")
DEBIT_NAMES = ("debit", "debits", "withdrawal", "withdrawals", "money out", "amount out", "paid out")
CREDIT_NAMES = ("credit", "credits", "deposit", "deposits", "money in", "amount in", "paid in")
BALANCE_NAMES = ("balance", "running balance", "running bal.", "running bal", "ending balance", "available balance")
# An export whose headers name these shows charges as positive numbers (American Express's CSV).
FLIPPED_HEADERS = ("extended details", "appears on your statement as")

_OFX_MARK = re.compile(r"<OFX>|OFXHEADER|<STMTTRN>", re.IGNORECASE)
_LEAF = re.compile(r"<([A-Za-z0-9.]+)>([^<\r\n]*)")
_CUT = re.compile(
    r"</STMTTRN>|</BANKTRANLIST>|<LEDGERBAL>|<AVAILBAL>|</STMTRS>|</CCSTMTRS>|</INVSTMTRS>|<INVSTMTRS>",
    re.IGNORECASE,
)
_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_SLASH_ISO = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
_US = re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})")
_NAMED_DATE_FORMATS = ("%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y")
_PLAIN = re.compile(r"\d+(\.\d{1,2})?|\.\d{1,2}")
_CURRENCY_WORDS = re.compile(r"(?i)\b(usd|cad|eur|gbp)\b|[$€£\s ]")


class StatementError(ValueError):
    """A file Finance cannot read as a statement. The message says what is missing, in the file's own terms."""


@dataclass(frozen=True)
class Row:
    date: str
    amount: int
    description: str
    external_id: str


@dataclass(frozen=True)
class Facts:
    format: str
    start: str
    end: str
    opening: int | None
    closing: int | None
    card: bool
    last4: str


@dataclass(frozen=True)
class Parsed:
    rows: tuple[Row, ...]
    facts: Facts


# ------------------------------------------------------------------------------------------------ values


def parse_amount(text: str) -> int:
    """A money text in cents: "$1,234.56", "-1.00", "(1.00)", "1.00-", "−1.00" (Unicode minus), "+5". Raises ValueError
    for anything else, a comma that is not a thousands separator included (a European "1,50" is not read as 150)."""
    s = text.strip().replace("−", "-")
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    elif s.endswith("-"):
        negative, s = True, s[:-1]
    s = _CURRENCY_WORDS.sub("", s)
    if s.startswith("-"):
        negative, s = True, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    if "," in s and not re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d{1,2})?", s):
        raise ValueError(text)
    s = s.replace(",", "")
    if not _PLAIN.fullmatch(s):
        raise ValueError(text)
    try:
        cents = int((Decimal(s) * 100).to_integral_value())
    except InvalidOperation:
        raise ValueError(text) from None
    return -cents if negative else cents


def csv_date(text: str) -> str:
    """A CSV date as an ISO day: ISO (2026-10-02), slashed ISO (2026/10/02), US (10/02/2026, 10-2-26) and named months
    ("Oct 02, 2026"). Raises ValueError for anything else."""
    s = text.strip()
    found: date | None = None
    if m := _ISO.fullmatch(s) or _SLASH_ISO.fullmatch(s):
        found = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    elif m := _US.fullmatch(s):
        year = int(m.group(3))
        year = year + 2000 if len(m.group(3)) == 2 else year
        found = date(year, int(m.group(1)), int(m.group(2)))
    else:
        for fmt in _NAMED_DATE_FORMATS:
            try:
                found = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
    if found is None:
        raise ValueError(text)
    return found.isoformat()


def ofx_date(text: str) -> str:
    """An OFX date (YYYYMMDD, optionally with a time and a zone, which are not kept: the bank's own day is the day)."""
    digits = text.strip()[:8]
    if not re.fullmatch(r"\d{8}", digits):
        raise ValueError(text)
    return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8])).isoformat()


def _fold(text: str) -> str:
    return " ".join(text.replace("﻿", "").strip().strip('"').casefold().split())


def _csv_id(day: str, amount: int, description: str, occurrence: int) -> str:
    key = f"{day}|{amount}|{' '.join(description.split()).casefold()}|{occurrence}"
    return "csv-" + hashlib.sha256(key.encode()).hexdigest()[:24]


def _text(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise StatementError("the file is larger than 4 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return text


# ------------------------------------------------------------------------------------------------ entry


def parse(data: bytes) -> Parsed:
    """A statement file (OFX, QFX or CSV, told apart by their text) as its rows and facts. Raises StatementError."""
    text = _text(data)
    if _OFX_MARK.search(text):
        return _ofx(text)
    return _csv(text)


# ------------------------------------------------------------------------------------------------ OFX / QFX


def _leaves(chunk: str) -> dict[str, str]:
    """The first value of each tag in a chunk: SGML leaves (no closing tag, the value ends at the line) and XML leaves
    (the value ends at the next tag) alike."""
    out: dict[str, str] = {}
    for m in _LEAF.finditer(chunk):
        out.setdefault(m.group(1).upper(), html.unescape(m.group(2).strip()))
    return out


def _section(text: str, opener: str) -> str:
    """The text after the first `opener` tag, up to the next section's closing tag (or the file's end)."""
    at = text.upper().find(opener.upper())
    if at < 0:
        return ""
    rest = text[at + len(opener) :]
    cut = _CUT.search(rest)
    return rest[: cut.start()] if cut else rest


def _ofx(text: str) -> Parsed:
    upper = text.upper()
    if "<INVSTMTRS>" in upper:
        raise StatementError("investment statements are not supported: upload the bank or card statement")
    card = "<CCSTMTRS>" in upper or "<CREDITCARDMSGSRSV1>" in upper
    if "<STMTTRN>" not in upper and "<LEDGERBAL>" not in upper:
        raise StatementError("the OFX file has no account statement in it")
    rows: list[Row] = []
    seen: dict[str, int] = {}
    occurrences: dict[tuple[str, int, str], int] = {}
    parts = re.split(r"<STMTTRN>", text, flags=re.IGNORECASE)[1:]
    if len(parts) > MAX_ROWS:
        raise StatementError(f"the file has more than {MAX_ROWS} transactions")
    for n, part in enumerate(parts, start=1):
        leaves = _leaves(_CUT.split(part, maxsplit=1)[0])
        try:
            day = ofx_date(leaves.get("DTPOSTED", ""))
        except ValueError:
            raise StatementError(f"transaction {n} has no posting date") from None
        if "TRNAMT" not in leaves:
            raise StatementError(f"transaction {n} has no amount")
        try:
            amount = parse_amount(leaves["TRNAMT"])
        except ValueError:
            raise StatementError(f"transaction {n} has an amount that is not money") from None
        description = leaves.get("NAME", "") or leaves.get("MEMO", "")
        fitid = leaves.get("FITID", "")
        if not fitid:
            key = (day, amount, description.casefold())
            fitid = _csv_id(day, amount, description, occurrences.get(key, 0))
            occurrences[key] = occurrences.get(key, 0) + 1
        count = seen.get(fitid, 0)
        seen[fitid] = count + 1
        external = fitid if count == 0 else f"{fitid}#{count + 1}"
        rows.append(Row(day, amount, description, external))
    ledger = _leaves(_section(text, "<LEDGERBAL>"))
    trans = _leaves(_section(text, "<BANKTRANLIST>"))
    closing: int | None = None
    if "BALAMT" in ledger:
        try:
            closing = parse_amount(ledger["BALAMT"])
        except ValueError:
            raise StatementError("the ledger balance is not money") from None
        if card:
            closing = -closing  # a card's balance is negative while it is owed; the card shows what is owed, positive
    last4 = re.sub(r"\D", "", _leaves(text).get("ACCTID", ""))[-4:]
    if not rows and "DTASOF" not in ledger and "DTEND" not in trans:
        raise StatementError("the OFX file has no dates")
    days = [r.date for r in rows]
    end_text = trans.get("DTEND") or ledger.get("DTASOF", "")
    try:
        end = ofx_date(end_text) if end_text else (max(days) if days else "")
        start = ofx_date(trans["DTSTART"]) if trans.get("DTSTART") else (min(days) if days else end)
    except ValueError:
        raise StatementError("the OFX statement dates are not dates") from None
    if not end:
        raise StatementError("the OFX file has no end date")
    facts = Facts("ofx", start, end, None, closing, card, last4)
    return Parsed(tuple(rows), facts)


# ------------------------------------------------------------------------------------------------ CSV


@dataclass(frozen=True)
class _Columns:
    header: int
    date: int
    description: int
    amount: int | None
    debit: int | None
    credit: int | None
    balance: int | None
    flipped: bool


def _find(names: tuple[str, ...], headers: list[str]) -> int | None:
    for name in names:
        if name in headers:
            return headers.index(name)
    return None


def _columns(rows: list[list[str]]) -> _Columns:
    """The header row and the columns in it, or a StatementError naming what the first rows held."""
    for i, row in enumerate(rows[:40]):
        headers = [_fold(cell) for cell in row]
        date_at = _find(DATE_NAMES, headers)
        desc_at = _find(DESCRIPTION_NAMES, headers)
        amount_at = _find(AMOUNT_NAMES, headers)
        debit_at = _find(DEBIT_NAMES, headers)
        credit_at = _find(CREDIT_NAMES, headers)
        if date_at is None or desc_at is None or (amount_at is None and debit_at is None and credit_at is None):
            continue
        flipped = any(h in FLIPPED_HEADERS for h in headers)
        return _Columns(
            header=i,
            date=date_at,
            description=desc_at,
            amount=amount_at,
            debit=debit_at,
            credit=credit_at,
            balance=_find(BALANCE_NAMES, headers),
            flipped=flipped,
        )
    shown = [c for c in (_fold(c) for c in (rows[0] if rows else [])) if c][:MAX_SHOWN_HEADERS]
    found = ", ".join(f'"{c[:40]}"' for c in shown) or "none"
    raise StatementError(
        "no header row with a date, a description and an amount (or debit and credit) column; "
        f"the first row has: {found}"
    )


def _sniff(text: str) -> str:
    """The delimiter: comma, or the semicolon or tab a bank's export may use, whichever the first line uses most."""
    first = next((line for line in text.splitlines() if line.strip()), "")
    counts = {d: first.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ","


def _cell(row: list[str], at: int | None) -> str:
    if at is None or at >= len(row):
        return ""
    return row[at].strip()


def _balances(pairs: list[tuple[int, int]]) -> tuple[int | None, int | None]:
    """(opening, closing) from each row's (amount, running balance) in file order, when the balances agree with the
    amounts in one direction: oldest first, or newest first. Otherwise neither is known."""
    if not pairs:
        return None, None
    amounts = [a for a, _ in pairs]
    bals = [b for _, b in pairs]
    n = len(pairs)
    if all(bals[i] - bals[i - 1] == amounts[i] for i in range(1, n)):
        return bals[0] - amounts[0], bals[-1]
    if all(bals[i - 1] - bals[i] == amounts[i - 1] for i in range(1, n)):
        return bals[-1] - amounts[-1], bals[0]
    return None, None


def _csv(text: str) -> Parsed:
    grid = list(csv.reader(io.StringIO(text), delimiter=_sniff(text)))
    cols = _columns(grid)
    rows: list[Row] = []
    pairs: list[tuple[int, int]] = []
    occurrences: dict[tuple[str, int, str], int] = {}
    balance_ok = cols.balance is not None and not cols.flipped
    width = len(grid[cols.header])
    for line_no, cells in enumerate(grid[cols.header + 1 :], start=cols.header + 2):
        if not any(c.strip() for c in cells):
            continue
        if any(c.strip() for c in cells[width:]):
            # More values than the header names: an unquoted thousands separator, most likely. Read as is, the amount
            # would be wrong, so the file is refused instead.
            raise StatementError(f"line {line_no} has more values than the header: quote any amount with a comma")
        raw_date = _cell(cells, cols.date)
        if not raw_date:
            raise StatementError(f"line {line_no} has no date")
        try:
            day = csv_date(raw_date)
        except ValueError:
            raise StatementError(f"line {line_no}: {raw_date[:20]!r} is not a date") from None
        if len(rows) >= MAX_ROWS:
            raise StatementError(f"the file has more than {MAX_ROWS} transactions")
        if cols.amount is not None and _cell(cells, cols.amount):
            amount_text = _cell(cells, cols.amount)
            try:
                amount = parse_amount(amount_text)
            except ValueError:
                raise StatementError(f"line {line_no}: the amount is not money") from None
        elif cols.debit is not None or cols.credit is not None:
            try:
                money_in = parse_amount(_cell(cells, cols.credit) or "0")
                money_out = parse_amount(_cell(cells, cols.debit) or "0")
            except ValueError:
                raise StatementError(f"line {line_no}: the amount is not money") from None
            amount = abs(money_in) - abs(money_out)
        else:
            raise StatementError(f"line {line_no} has no amount")
        description = " ".join(_cell(cells, cols.description).split())
        key = (day, amount, description.casefold())
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        if balance_ok:
            balance_text = _cell(cells, cols.balance)
            if not balance_text:
                balance_ok = False
            else:
                try:
                    pairs.append((amount, parse_amount(balance_text)))
                except ValueError:
                    balance_ok = False
        if cols.flipped:
            amount = -amount  # an export that shows charges positive: money out is negative in Tico's feed
        rows.append(Row(day, amount, description, _csv_id(day, amount, description, occurrence)))
    if not rows:
        raise StatementError("the file has no transactions under its header")
    days = [r.date for r in rows]
    opening, closing = _balances(pairs) if balance_ok else (None, None)
    facts = Facts("csv", min(days), max(days), opening, closing, False, "")
    return Parsed(tuple(rows), facts)
