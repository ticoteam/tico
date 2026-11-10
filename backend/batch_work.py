"""Isolate one row of background work without losing the rest of its transaction."""

import hashlib
import json
import logging
import sqlite3
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone


_logged = OrderedDict()
_clock = time.monotonic

# Errors of the storage or the writer lock, not of the row being worked on: they stop the batch or the claim.
STORAGE_CODES = (
    sqlite3.SQLITE_FULL,
    sqlite3.SQLITE_IOERR,
    sqlite3.SQLITE_CORRUPT,
    sqlite3.SQLITE_NOTADB,
    sqlite3.SQLITE_READONLY,
    sqlite3.SQLITE_BUSY,
    sqlite3.SQLITE_LOCKED,
)


def storage_error(exc):
    """Whether `exc` is a storage failure rather than the row's own error."""
    return (getattr(exc, "sqlite_errorcode", 0) & 0xFF) in STORAGE_CODES


SHARED_ATTR = "shared_check"


@contextmanager
def shared_check(scope="bot"):
    """Marks an error a check raises that belongs to no one job: "bot" for a check of one bot (its budget, back-off or
    login hold), "team" for the team's defaults. The claim passes a bot over on its error and fails on a team's. The
    first mark is kept, so a team read inside a bot's check stays a team error. The error itself is unchanged."""
    try:
        yield
    except Exception as exc:
        if not getattr(exc, SHARED_ATTR, None):
            setattr(exc, SHARED_ATTR, scope)
        raise


def shared_scope(exc):
    """ "bot" or "team" for an error out of a shared_check, else None."""
    return getattr(exc, SHARED_ATTR, None)


def report(kind, row_id, exc, logger="tico.scheduler"):
    """Log one row's failure: with its traceback the first time, then at most once an hour for the same row."""
    key, now = (kind, str(row_id)), _clock()
    previous = _logged.get(key)
    if previous is None or now - previous >= 3600:
        logging.getLogger(logger).error("%s failed for %s: %s", kind, row_id, str(exc)[:300], exc_info=previous is None)
        _logged[key] = now
        _logged.move_to_end(key)
        while len(_logged) > 2048:
            _logged.popitem(last=False)


@contextmanager
def isolated(c, kind, row_id, failures=None, private=False):
    """One row's work in its own savepoint: its error rolls back only that row, is logged, goes on `failures` and on a
    `background:<kind>:<row>` service_health row until the row next succeeds. service_health is readable by everyone
    (sql.py), so `private` work (a row that is a task: its id or title must reach only who reads it) keeps the row id
    and the error's words out of it: the service names a digest of the row, and the detail holds the error type only.
    The server log still names the row."""
    health_available = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_health'").fetchone()
    c.execute("SAVEPOINT background_row")
    shown = hashlib.sha256(str(row_id).encode()).hexdigest()[:16] if private else row_id
    service = f"background:{kind}:{shown}"
    fatal = False
    try:
        yield
    except Exception as exc:
        # SQLite may already have rolled back the transaction (full disk, I/O). Keep
        # that original error. Storage failures stop the whole batch; row SQL errors do not.
        if not c.in_transaction or storage_error(exc):
            fatal = True
            raise
        c.execute("ROLLBACK TO background_row")
        if getattr(exc, "rule", None) == "duplicate":
            # An existing ask already covers this row: the work is done, not failed.
            if health_available:
                c.execute("DELETE FROM service_health WHERE service=?", (service,))
            return
        if failures is not None:
            failures.append({kind: type(exc).__name__} if private else {kind: row_id, "error": type(exc).__name__})
        report(kind, row_id, exc)
        words = type(exc).__name__ if private else str(exc)[:300]
        detail = {"kind": kind, "error": words} if private else {"kind": kind, "row": row_id, "error": words}
        action = "Check the server log." if private else "Check this row's settings and the server log."
        if health_available:
            c.execute(
                "INSERT INTO service_health VALUES(?,NULL,?,?) "
                "ON CONFLICT(service) DO UPDATE SET last_error=excluded.last_error,detail=excluded.detail",
                (
                    service,
                    f"{kind}: {words}",
                    json.dumps(
                        {
                            **detail,
                            "failed_at": datetime.now(timezone.utc).isoformat(),
                            "action": action,
                        }
                    ),
                ),
            )
    else:
        if health_available:
            c.execute("DELETE FROM service_health WHERE service=?", (service,))
    finally:
        if c.in_transaction and not fatal:
            c.execute("RELEASE background_row")
