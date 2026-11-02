"""Reading a CSV file of shared records for import: the header, the rows, and the cell rules every record type shares.
No I/O: Finance's import (backend/finance/record_imports.py) looks names up and commits the rows."""
import csv
import hashlib
import io
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

MAX_BYTES = 1_000_000          # the file as sent; a bigger one is split
MAX_ROWS = 2_000               # data rows per file
MAX_CELL = 500                 # characters in one cell
REPORT_LINES = 200             # lines a response lists in full; its counts always cover every line
# A cell a spreadsheet would read as a formula. Text that starts this way is written with a quote in front of it.
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_MONEY = re.compile(r"^\$?(\d{1,11})(?:\.(\d{1,2}))?$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YES = frozenset({"yes", "y", "true", "1"})
_NO = frozenset({"no", "n", "false", "0"})


class FileRefused(ValueError):
    """The whole file cannot be imported. `code` is `too_large` (a size cap) or `invalid`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RowError(ValueError):
    """One row that cannot become a record. The message says what to change."""


@dataclass(frozen=True)
class Row:
    """One data row: the line it starts on (the header is line 1), its cells by column name, and what the reader already
    found wrong with it."""
    line: int
    cells: Mapping[str, str]
    problems: tuple[str, ...] = ()


def _column(header: str) -> str:
    """A header as a column name: lower case, words joined by underscores."""
    return re.sub(r"\s+", "_", header.strip().lower())


def read(text: str, columns: Sequence[str], required: Sequence[str]) -> tuple[list[Row], list[str]]:
    """The data rows of a CSV file, and the columns it has that this import does not use (ignored, and reported). A
    file over MAX_BYTES or MAX_ROWS, one with no header, one missing a required column, or one that is not valid CSV is
    refused whole. Parsing is strict: a quote that never closes is refused, not read as the rest of the file."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise FileRefused("too_large", f"The file is over {MAX_BYTES // 1_000_000} MB: split it into files of "
                          f"{MAX_ROWS:,} rows or fewer")
    reader = csv.reader(io.StringIO(text.lstrip("﻿")), strict=True)
    try:
        header = next(reader, None)
        if not header or not any(cell.strip() for cell in header):
            raise FileRefused("invalid", "The file has no header row: the first line names the columns")
        names = [_column(cell) for cell in header]
        twice = sorted({name for name in names if name and names.count(name) > 1})
        if twice:
            raise FileRefused("invalid", f"The column {twice[0]} is in the file more than once")
        missing = [name for name in required if name not in names]
        if missing:
            raise FileRefused("invalid", f"The file needs a column named {', '.join(missing)}")
        ignored = sorted({name for name in names if name and name not in columns})
        where = {name: i for i, name in enumerate(names) if name in columns}
        rows: list[Row] = []
        while True:
            start = reader.line_num + 1
            raw = next(reader, None)
            if raw is None:
                break
            if not any(cell.strip() for cell in raw):
                continue
            if len(rows) == MAX_ROWS:
                raise FileRefused("too_large", f"The file has more than {MAX_ROWS:,} rows: split it into smaller files")
            problems: list[str] = []
            cells = {name: "" for name in columns}
            for name, i in where.items():
                value = raw[i].strip() if i < len(raw) else ""
                if len(value) > MAX_CELL:
                    problems.append(f"{name} is over {MAX_CELL} characters")
                cells[name] = value
            if any(cell.strip() for cell in raw[len(names):]):
                problems.append("a value has no column name")
            rows.append(Row(line=start, cells=cells, problems=tuple(problems)))
        return rows, ignored
    except csv.Error as exc:
        raise FileRefused("invalid", f"The file is not valid CSV at line {reader.line_num}: {exc}") from None


def money(text: str, what: str) -> int:
    """A dollar amount as minor units (cents): 89, 89.5, $89.50 or 1,200. Anything else is refused, never rounded."""
    found = _MONEY.match(text.replace(",", ""))
    if found is None:
        raise RowError(f"{what} is a dollar amount such as 89 or 89.50")
    dollars, fraction = found.group(1), (found.group(2) or "").ljust(2, "0")
    return int(dollars) * 100 + int(fraction or "0")


def day(text: str, what: str) -> str:
    """A day as YYYY-MM-DD, or refused."""
    if not _DAY.match(text):
        raise RowError(f"{what} is a day such as 2026-11-30")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise RowError(f"{what} is a day such as 2026-11-30") from None


def whole(text: str, what: str, low: int = 1, high: int = 10_000) -> int:
    """A whole number from `low` to `high`."""
    if not text.isdigit() or not low <= int(text) <= high:
        raise RowError(f"{what} is a whole number from {low} to {high}")
    return int(text)


def flag(text: str, what: str) -> bool:
    """yes or no (true or false, 1 or 0)."""
    value = text.strip().lower()
    if value in _YES:
        return True
    if value in _NO:
        return False
    raise RowError(f"{what} is yes or no")


def match_key(text: str) -> str:
    """How two values count as the same for a duplicate check: case, spacing and punctuation do not count."""
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def address_key(address: Mapping[str, str]) -> str:
    """A street address as one key, or "" when it has no street: two locations at the same address share it."""
    if not match_key(address.get("street", "")):
        return ""
    return "|".join(match_key(address.get(part, "")) for part in ("street", "city", "state", "postal_code"))


def content_key(record_type: str, payload: Mapping[str, object]) -> str:
    """A row's idempotency key. The same record makes the same key, so importing it again finds the first one."""
    body = json.dumps({"type": record_type, "payload": payload}, sort_keys=True, separators=(",", ":"), default=str)
    return "import:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def safe_cell(value: object) -> str:
    """A value as a spreadsheet may open it: text that starts like a formula is kept as text."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def report_csv(header: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """A report as CSV, every cell passed through `safe_cell`."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(value) for value in row])
    return out.getvalue()
