"""Isolate one row of background work without losing the rest of its transaction."""

import json
import logging
import sqlite3
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone


_logged = OrderedDict()
_clock = time.monotonic


@contextmanager
def isolated(c, kind, row_id, failures=None):
    health_available = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_health'").fetchone()
    c.execute("SAVEPOINT background_row")
    service = f"background:{kind}:{row_id}"
    fatal = False
    try:
        yield
    except Exception as exc:
        # SQLite may already have rolled back the transaction (full disk, I/O). Keep
        # that original error. Storage failures stop the whole batch; row SQL errors do not.
        code = getattr(exc, "sqlite_errorcode", 0) & 0xff
        if not c.in_transaction or code in (sqlite3.SQLITE_FULL, sqlite3.SQLITE_IOERR,
                                            sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB,
                                            sqlite3.SQLITE_READONLY):
            fatal = True
            raise
        c.execute("ROLLBACK TO background_row")
        if getattr(exc, "rule", None) == "duplicate":
            # An existing ask already covers this row: the work is done, not failed.
            if health_available:
                c.execute("DELETE FROM service_health WHERE service=?", (service,))
            return
        if failures is not None:
            failures.append({kind: row_id, "error": type(exc).__name__})
        key, now = (kind, str(row_id)), _clock()
        previous = _logged.get(key)
        if previous is None or now - previous >= 3600:
            logger = logging.getLogger("tico.scheduler")
            logger.error("%s failed for %s: %s", kind, row_id, str(exc)[:300], exc_info=previous is None)
            _logged[key] = now
            _logged.move_to_end(key)
            while len(_logged) > 2048:
                _logged.popitem(last=False)
        if health_available:
            c.execute("INSERT INTO service_health VALUES(?,NULL,?,?) "
                      "ON CONFLICT(service) DO UPDATE SET last_error=excluded.last_error,detail_json=excluded.detail_json",
                      (service, f"{kind}: {str(exc)[:300]}",
                       json.dumps({"kind": kind, "row": row_id, "error": str(exc)[:300],
                                   "failed_at": datetime.now(timezone.utc).isoformat(),
                                   "action": "Check this row's settings and the server log."})))
    else:
        if health_available:
            c.execute("DELETE FROM service_health WHERE service=?", (service,))
    finally:
        if c.in_transaction and not fatal:
            c.execute("RELEASE background_row")
