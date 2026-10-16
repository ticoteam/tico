"""HQ's whole database: one row per install id, and nothing that is not in the columns below.

    installs(install_id, first_seen, last_seen, last_version, last_people, last_bots, first_result, result_from_start)

Dates are UTC days (YYYY-MM-DD), not times. `last_people` and `last_bots` are the last day the install said
"a person used it" and "a bot turn completed"; they stay NULL until it does. `first_result` is the first day it said
"a bot has finished a turn here, ever"; `result_from_start` is 1 when the install's first ping already carried that flag,
so the days from `first_seen` to `first_result` are its real wait for a first working bot (an install that counted
before the flag existed has 0, and is left out of that number). There is no ping log, no address, no user agent and no
other field, so a row cannot say more than that. Support tickets, which a person files on purpose,
are in support.py's own two tables.
"""
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone

RETENTION_DAYS = 395          # 13 months
WINDOW_DAYS = 7
RETAINED_AFTER_DAYS = 30
SUPPRESS_BELOW = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS installs(
 install_id TEXT PRIMARY KEY,
 first_seen TEXT NOT NULL,
 last_seen TEXT NOT NULL,
 last_version TEXT NOT NULL,
 last_people TEXT,
 last_bots TEXT,
 first_result TEXT,
 result_from_start INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS installs_last_seen ON installs(last_seen);
"""


def today():
    return datetime.now(timezone.utc).date()


def bucket(version):
    """The major version; before 1.0 the minor is the one that moves, so 0.2.13 is "0.2"."""
    parts = version.split("-")[0].split(".")
    return "0." + parts[1] if parts[0] == "0" and len(parts) > 1 else parts[0]


class Database:
    def __init__(self, path, clock=today):
        self.clock = clock
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        have = {row[1] for row in self.conn.execute("PRAGMA table_info(installs)")}
        for column, spec in (("first_result", "TEXT"), ("result_from_start", "INTEGER NOT NULL DEFAULT 0")):
            if column not in have:
                self.conn.execute(f"ALTER TABLE installs ADD COLUMN {column} {spec}")

    def record(self, install_id, version, people, bots, first_result=None):
        """`first_result` is None when the install did not send the flag (a release from before it)."""
        day = self.clock().isoformat()
        with self.lock:
            self.conn.execute(
                "INSERT INTO installs(install_id,first_seen,last_seen,last_version,last_people,last_bots,first_result,"
                "result_from_start) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(install_id) DO UPDATE SET "
                "last_seen=excluded.last_seen, last_version=excluded.last_version, "
                "last_people=coalesce(excluded.last_people,last_people), last_bots=coalesce(excluded.last_bots,last_bots), "
                "first_result=coalesce(first_result,excluded.first_result)",
                (install_id, day, day, version, day if people else None, day if bots else None,
                 day if first_result else None, 0 if first_result is None else 1))

    def purge(self):
        """Forget installs not heard from in 13 months. Returns how many."""
        cutoff = (self.clock() - timedelta(days=RETENTION_DAYS)).isoformat()
        with self.lock:
            return self.conn.execute("DELETE FROM installs WHERE last_seen<?", (cutoff,)).rowcount

    def stats(self):
        """Public aggregates. The totals are exact; a per-version count under 5 is folded into "other", and that is
        left out when it is still under 5, so no line of the breakdown can single out an install."""
        now = self.clock()
        week = (now - timedelta(days=WINDOW_DAYS)).isoformat()
        month = (now - timedelta(days=RETAINED_AFTER_DAYS)).isoformat()
        with self.lock:
            one = lambda sql, *a: self.conn.execute(sql, a).fetchone()[0]
            tried = one("SELECT count(*) FROM installs WHERE last_people IS NOT NULL AND last_bots IS NOT NULL")
            active = one("SELECT count(*) FROM installs WHERE last_seen>=?", week)
            retained = one("SELECT count(*) FROM installs WHERE first_seen<=? AND last_bots>=?", month, week)
            reached = one("SELECT count(*) FROM installs WHERE first_result IS NOT NULL")
            waits = sorted(r[0] for r in self.conn.execute(
                "SELECT julianday(first_result)-julianday(first_seen) FROM installs "
                "WHERE result_from_start=1 AND first_result IS NOT NULL"))
            versions = [r[0] for r in self.conn.execute("SELECT last_version FROM installs WHERE last_seen>=?", (week,))]
        counts = {}
        for version in versions:
            counts[bucket(version)] = counts.get(bucket(version), 0) + 1
        shown = {k: v for k, v in sorted(counts.items()) if v >= SUPPRESS_BELOW}
        other = sum(v for v in counts.values() if v < SUPPRESS_BELOW)
        if other >= SUPPRESS_BELOW:
            shown["other"] = other
        # The median wait is shown only once five installs have one, like the version counts.
        median = None
        if len(waits) >= SUPPRESS_BELOW:
            middle = len(waits) // 2
            median = waits[middle] if len(waits) % 2 else (waits[middle - 1] + waits[middle]) / 2
        return {"tried": tried, "active_7d": active, "retained_30d": retained, "first_result": reached,
                "first_result_median_days": median, "by_version": shown, "as_of": now.isoformat()}

    def release_counts(self):
        """Staff only: installs heard from in the last 7 days on each exact release (0.2.24, not 0.2), with no
        suppression. No public route serves this."""
        week = (self.clock() - timedelta(days=WINDOW_DAYS)).isoformat()
        with self.lock:
            rows = self.conn.execute("SELECT last_version, count(*) FROM installs WHERE last_seen>=? GROUP BY last_version",
                                     (week,)).fetchall()
        key = lambda kv: [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.\-]", kv[0])]
        return dict(sorted(((r[0], r[1]) for r in rows), key=key))
