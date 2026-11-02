"""SQL for spreadsheet history imports (spec §4.1): one row per imported file, found again by its content hash. An
import is live while every one of its months still stands; a reversed month lets the file post again once all of
them are reversed, and a partly reversed one is refused until the rest are."""
import sqlite3
from collections.abc import Mapping
from typing import Any

# The live entries of the import named by b.id: posted originals in its batch's source-id range.
_LIVE_MONTHS = ("(SELECT count(*) FROM fin_entries e WHERE e.book_id=b.book_id AND e.source='import' "
                "AND e.status='posted' AND e.reverses_id IS NULL AND e.source_id>='raw_totals:'||b.id||':' "
                "AND e.source_id<'raw_totals:'||b.id||';')")
# Each line of an entry with the team and teammate it carries ('' for none).
_TAGGED = ("SELECT l.account_id, "
           "coalesce((SELECT t.value FROM fin_line_tags t WHERE t.line_id=l.id AND t.kind='team'), ''), "
           "coalesce((SELECT t.value FROM fin_line_tags t WHERE t.line_id=l.id AND t.kind='teammate'), '') "
           "FROM fin_entries e JOIN fin_lines l ON l.entry_id=e.id ")


def insert(c: sqlite3.Connection, row: Mapping[str, object]) -> None:
    c.execute("INSERT INTO fin_raw_imports(id, book_id, content_hash, basis, source_name, periods, row_count, gross, "
              "action_id, created_at, created_by) VALUES(:id, :book_id, :content_hash, :basis, :source_name, :periods, "
              ":row_count, :gross, :action_id, :created_at, :created_by)", row)


def live_by_digest(c: sqlite3.Connection, book_id: str, digest: str) -> Any:
    """The import of this content whose every month still stands, if there is one."""
    return c.execute(f"SELECT b.* FROM fin_raw_imports b WHERE b.book_id=? AND b.content_hash=? "
                     f"AND {_LIVE_MONTHS}=b.periods ORDER BY b.created_at, b.id LIMIT 1", (book_id, digest)).fetchone()


def batches(c: sqlite3.Connection, book_id: str, most: int) -> list[Any]:
    """The imports, newest first, each with how many of its months still stand (live_months)."""
    return c.execute(f"SELECT b.*, {_LIVE_MONTHS} AS live_months FROM fin_raw_imports b WHERE b.book_id=? "
                     f"ORDER BY b.created_at DESC, b.id LIMIT ?", (book_id, most)).fetchall()


def live_entry_in(c: sqlite3.Connection, book_id: str, start: str, end: str) -> Any:
    """A standing history-import entry dated from start to end: a month that already holds raw totals."""
    return c.execute("SELECT id, source_id FROM fin_entries WHERE book_id=? AND source='import' AND status='posted' "
                     "AND reverses_id IS NULL AND source_id>='raw_totals:' AND source_id<'raw_totals;' "
                     "AND entry_date>=? AND entry_date<=? ORDER BY entry_date, id LIMIT 1",
                     (book_id, start, end)).fetchone()


# The sources whose entries are itemized transactions. A month that holds one takes no raw totals: the import would
# count its expenses a second time (§4.1, §16.4).
ITEMIZED = ("bank", "card", "bill", "invoice", "payment", "usage", "sync")


def itemized_in(c: sqlite3.Connection, book_id: str, start: str, end: str) -> Any:
    """A standing entry of itemized transactions dated from start to end, if there is one."""
    marks = ",".join("?" * len(ITEMIZED))
    return c.execute(f"SELECT id, source FROM fin_entries WHERE book_id=? AND source IN ({marks}) AND status='posted' "
                     "AND reverses_id IS NULL AND entry_date>=? AND entry_date<=? ORDER BY entry_date, id LIMIT 1",
                     (book_id, *ITEMIZED, start, end)).fetchone()


def _tags(rows: list[Any]) -> list[tuple[str, dict[str, str]]]:
    return [(str(r[0]), {k: str(v) for k, v in (("team", r[1]), ("teammate", r[2])) if v}) for r in rows]


def batch_lines(c: sqlite3.Connection, book_id: str, batch: str) -> list[tuple[str, dict[str, str]]]:
    """The account and team or teammate tags of every line an import's standing entries carry."""
    return _tags(c.execute(_TAGGED + "WHERE e.book_id=? AND e.source='import' AND e.status='posted' "
                           "AND e.reverses_id IS NULL AND e.source_id>='raw_totals:'||?||':' "
                           "AND e.source_id<'raw_totals:'||?||';'", (book_id, batch, batch)).fetchall())


def entry_lines(c: sqlite3.Connection, entry_id: str) -> list[tuple[str, dict[str, str]]]:
    """The account and team or teammate tags of one entry's lines."""
    return _tags(c.execute(_TAGGED + "WHERE e.id=?", (entry_id,)).fetchall())


def live_months(c: sqlite3.Connection, book_id: str, first: str, last: str) -> list[Any]:
    """The months from first to last (YYYY-MM) that hold a standing history import, with the basis it states."""
    return c.execute("SELECT substr(e.entry_date, 1, 7) AS month, b.basis AS basis FROM fin_entries e "
                     "JOIN fin_raw_imports b ON b.book_id=e.book_id AND e.source_id>='raw_totals:'||b.id||':' "
                     "AND e.source_id<'raw_totals:'||b.id||';' WHERE e.book_id=? AND e.source='import' "
                     "AND e.status='posted' AND e.reverses_id IS NULL AND substr(e.entry_date, 1, 7) BETWEEN ? AND ? "
                     "ORDER BY month", (book_id, first, last)).fetchall()


def live_span(c: sqlite3.Connection, book_id: str) -> tuple[str, str] | None:
    """The first and the last month (YYYY-MM) holding a standing raw-totals entry, or None when there is none."""
    row = c.execute("SELECT min(substr(entry_date, 1, 7)), max(substr(entry_date, 1, 7)) FROM fin_entries WHERE "
                    "book_id=? AND source='import' AND status='posted' AND reverses_id IS NULL AND "
                    "source_id>='raw_totals:' AND source_id<'raw_totals;'", (book_id,)).fetchone()
    return (str(row[0]), str(row[1])) if row is not None and row[1] else None
