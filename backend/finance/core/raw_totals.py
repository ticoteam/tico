"""Spreadsheet history as raw totals (spec §4.1): a CSV of month, line and amount (and an optional team) read into
rows, the content hash that makes a re-import the same import, and each month's lines once the sheet's lines are
mapped to accounts. No I/O: the planner in finance/raw_totals.py reads the books and posts."""
import csv
import hashlib
import io
import json
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from . import dates
from .errors import invalid
from .ledger import MAX_LINES, Account, Line, name_key

BASES = ("cash", "accrual")
COLUMNS = ("month", "line", "amount")
OPTIONAL_COLUMNS = ("team",)
MAX_BYTES = 512 * 1024
MAX_ROWS = 5000
MAX_NAME = 200
MAX_TEAM = 80
MONTH = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")      # ASCII only: \d also matches Arabic-Indic and fullwidth digits
AMOUNT = re.compile(r"-?[0-9]{1,18}")
DEBIT_NORMAL = ("asset", "expense")
OFFSET_SUBTYPE = "opening"             # Opening balance equity: what each month's lines are balanced against
BOM = "\ufeff"


@dataclass(frozen=True)
class Row:
    month: str                         # YYYY-MM
    line: str                          # the sheet's name for the line, as written
    key: str                           # its name_key: what a mapping matches
    amount: int                        # minor units, positive on the account's normal side (see natural)
    team: str                          # "" for none


@dataclass(frozen=True)
class Sheet:
    basis: str
    rows: tuple[Row, ...]              # the nonzero rows, in file order
    zero_rows: int                     # rows of 0: they move nothing and are left out
    digest: str                        # the content hash: the same rows and basis, in any order, under any mapping


def parse(text: str, basis: str) -> Sheet:
    """A CSV with a header (month, line, amount, and optionally team). One row is one month's total for one line; a
    line and team repeated in a month is refused, so the sum is written once. Amounts are whole minor units. A leading
    byte order mark (what a spreadsheet saves as UTF-8 CSV) is not part of the header."""
    if basis not in BASES:
        raise invalid(f"basis is {'|'.join(BASES)}")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise invalid(f"the file is larger than {MAX_BYTES // 1024} KB")
    text = text.removeprefix(BOM)
    try:
        grid = [cells for cells in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in cells)]
    except csv.Error as e:
        raise invalid(f"the CSV does not read: {e}") from None
    head = [cell.strip().lower() for cell in grid[0]] if grid else []
    allowed = (*COLUMNS, *OPTIONAL_COLUMNS)
    if not set(COLUMNS) <= set(head) or len(set(head)) != len(head) or not set(head) <= set(allowed):
        raise invalid(f"the header is {', '.join(COLUMNS)}, and optionally {', '.join(OPTIONAL_COLUMNS)}")
    body = grid[1:]
    if not body:
        raise invalid("the CSV has no rows")
    if len(body) > MAX_ROWS:
        raise invalid(f"the CSV has more than {MAX_ROWS} rows")
    where = {name: head.index(name) for name in head}
    rows: list[Row] = []
    zero_rows = 0
    seen: set[tuple[str, str, str]] = set()
    for n, cells in enumerate(body, 2):
        if len(cells) != len(head):
            raise invalid(f"row {n} has {len(cells)} fields, not {len(head)}")
        row = _row([cell.strip() for cell in cells], where, n)
        if row.amount == 0:
            zero_rows += 1
            continue
        if (row.month, row.key, row.team) in seen:
            raise invalid(f"row {n} repeats {row.month} {row.line}: add the rows into one")
        seen.add((row.month, row.key, row.team))
        rows.append(row)
    if not rows:
        raise invalid("every row is 0: nothing to import")
    return Sheet(basis=basis, rows=tuple(rows), zero_rows=zero_rows, digest=digest(basis, rows))


def _row(cells: list[str], where: Mapping[str, int], n: int) -> Row:
    def cell(name: str) -> str:
        return cells[where[name]] if name in where else ""
    month, line, team, amount = cell("month"), line_name(cell("line")), cell("team"), cell("amount")
    if not MONTH.fullmatch(month):
        raise invalid(f"row {n}: month is YYYY-MM")
    if not line or len(line) > MAX_NAME:
        raise invalid(f"row {n}: line is a name of 1 to {MAX_NAME} characters")
    if len(team) > MAX_TEAM:
        raise invalid(f"row {n}: team is {MAX_TEAM} characters at most")
    if not AMOUNT.fullmatch(amount):
        raise invalid(f"row {n}: amount is whole cents, 290000 for 2,900.00")
    return Row(month=month, line=line, key=name_key(line), amount=int(amount), team=team)


def line_name(name: str) -> str:
    """A line name as the sheet and the mapping both write it: runs of spaces (and other whitespace, such as a tab or a
    no-break space copied from a spreadsheet) collapse to one space, so "Office  Rent" is "Office Rent"."""
    return " ".join(name.split())


def digest(basis: str, rows: Iterable[Row]) -> str:
    """The content hash: the basis and every row's month, line key, team and amount, sorted. Who maps the lines and
    what the file is called are not content, so a re-import with another mapping is the same import."""
    body = {"basis": basis, "rows": sorted((r.month, r.key, r.team, r.amount) for r in rows)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def unmapped(sheet: Sheet, keys: Collection[str]) -> list[str]:
    """The sheet's line names no mapping key names, each once, in file order."""
    out: list[str] = []
    for r in sheet.rows:
        if r.key not in keys and r.line not in out:
            out.append(r.line)
    return out


def cents_warning(sheet: Sheet) -> str:
    """A dry-run note, never a refusal: the amounts are whole cents, so a sheet in dollars posts each total at a
    hundredth of its value. Raised only when every amount ends in 00 across two or more rows. A round-dollar sheet
    that is really in cents trips it too, which costs a read of the dry run and nothing else."""
    amounts = [abs(r.amount) for r in sheet.rows]
    if len(amounts) < 2 or any(amount % 100 for amount in amounts):
        return ""
    return ("Every amount ends in 00. Amounts are whole cents (290000 is 2,900.00). If this sheet is in dollars, "
            "each total posts at a hundredth of its value.")


def unknown_teams(sheet: Sheet, teams: Collection[str]) -> list[str]:
    out: list[str] = []
    for r in sheet.rows:
        if r.team and r.team not in teams and r.team not in out:
            out.append(r.team)
    return out


def natural(amount: int, type_: str) -> int:
    """A row's amount as a line: positive on the account's normal side, a debit for an asset or an expense and a
    credit (negative) for income, a liability or equity."""
    return amount if type_ in DEBIT_NORMAL else -amount


def month_end(month: str) -> str:
    return dates.month_end(dates.month(month)).isoformat()


def lines_by_month(sheet: Sheet, by_key: Mapping[str, Account], opening: Account, base: str
                   ) -> dict[str, tuple[Line, ...]]:
    """Each month's lines, in file order: every row on its mapped account, then one offset on the opening-balance
    equity line for the month's net (none when the month already sums to zero). Every month sums to zero."""
    out: dict[str, list[Line]] = {}
    net: dict[str, int] = {}
    for r in sheet.rows:
        account = by_key[r.key]
        amount = natural(r.amount, account.type)
        out.setdefault(r.month, []).append(Line(account_id=account.id, amount=amount, currency=base,
                                                original_amount=amount, description=r.line,
                                                tags={"team": r.team} if r.team else {}))
        net[r.month] = net.get(r.month, 0) + amount
    for month, total in net.items():
        if total:
            out[month].append(Line(account_id=opening.id, amount=-total, currency=base, original_amount=-total,
                                   description=f"Raw totals {month}"))
    for month, lines in out.items():
        if len(lines) > MAX_LINES:
            raise invalid(f"{month} has more than {MAX_LINES} lines")
    return {month: tuple(lines) for month, lines in out.items()}
