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
