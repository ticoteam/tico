"""The shared outbox mechanics (backend/outbox.py) on a plain table: lease expiry re-claims a cut-off row, the retry
schedule waits and gives up, and a row that was sent is never sent again by a repeat claim."""
import random
import sqlite3

import pytest

from backend import outbox as O

SENDS = O.Table("test_sends", due="ready", flight="sending", due_at="next_at", lease="lease", attempts="attempts",
                error="last_error")
SCHEDULE = O.Waits((5, 300, 1800), jitter=0.1)


@pytest.fixture
def c():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE test_sends (id TEXT PRIMARY KEY, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, "
                 "next_at REAL NOT NULL DEFAULT 0, lease REAL, last_error TEXT NOT NULL DEFAULT '', "
                 "last_status INTEGER)")
    yield conn
    conn.close()


def add(c, row_id, *, state="ready", next_at=0.0):
    c.execute("INSERT INTO test_sends(id, state, next_at) VALUES(?,?,?)", (row_id, state, next_at))


def row(c, row_id):
    return dict(c.execute("SELECT * FROM test_sends WHERE id=?", (row_id,)).fetchone())


def test_a_lease_that_expires_puts_the_row_back_to_due_and_it_is_claimed_again(c):
    add(c, "a")
    assert O.claim(c, SENDS, "a", 100.0)
    assert not O.claim(c, SENDS, "a", 101.0), "a leased row is not handed to a second worker"

    assert O.reclaim_stale(c, SENDS, before=80.0, now=200.0) == 0, "a lease newer than the window is left alone"
    assert row(c, "a")["state"] == "sending"

    assert O.reclaim_stale(c, SENDS, before=180.0, now=300.0) == 1
    back = row(c, "a")
    assert back["state"] == "ready" and back["lease"] is None and back["next_at"] == 300.0
    assert O.claim(c, SENDS, "a", 301.0) and row(c, "a")["lease"] == 301.0


def test_claim_next_takes_only_due_rows_in_due_order(c):
    add(c, "late", next_at=50.0)
    add(c, "early", next_at=10.0)
    add(c, "not_yet", next_at=900.0)
    assert O.claim_next(c, SENDS, 100.0)["id"] == "early"
    assert O.claim_next(c, SENDS, 100.0)["id"] == "late"
    assert O.claim_next(c, SENDS, 100.0) is None, "a row due later is not taken early"


def test_the_retry_schedule_waits_longer_each_time_and_jitters_within_its_band():
    rng = random.Random(7)
    for _ in range(20):
        assert 4.5 <= SCHEDULE.after(1, rng) <= 5.5
        assert 270 <= SCHEDULE.after(2, rng) <= 330
        assert 1620 <= SCHEDULE.after(3, rng) <= 1980
    assert SCHEDULE.after(4, rng) is None, "three waits are four attempts; after the fourth failure nothing is left"
    assert O.Doubling(15, 300).after(1) == 30 and O.Doubling(15, 300).after(2) == 60
    assert O.Doubling(15, 300).after(5) == 300, "the doubling stops at the cap"


def test_repeat_last_schedule_never_gives_up_and_keeps_its_last_wait():
    schedule = O.Waits((60, 300), repeat_last=True)
    assert schedule.max_attempts is None
    assert [schedule.after(n) for n in (1, 2, 3, 9)] == [60, 300, 300, 300]


def test_a_row_gives_up_after_its_last_attempt_and_is_never_claimed_again(c):
    add(c, "a")
    before = O.counts().get(("test_sends", "gave_up"), 0)
    for attempt in range(1, SCHEDULE.max_attempts + 1):
        assert O.claim(c, SENDS, "a", 1000.0 * attempt)
        gave_up = O.settle_failure(c, SENDS, "a", attempts=attempt, error=f"HTTP 500 #{attempt}", schedule=SCHEDULE,
                                   now=1000.0 * attempt, later=lambda now, wait: now + wait, give_up_state="failed",
                                   rng=random.Random(attempt))
        assert gave_up is (attempt == SCHEDULE.max_attempts)
    dead = row(c, "a")
    assert dead["state"] == "failed" and dead["attempts"] == 4 and dead["last_error"] == "HTTP 500 #4"
    assert not O.claim(c, SENDS, "a", 99999.0), "a row that gave up is not sent again"
    assert O.counts()[("test_sends", "gave_up")] == before + 1


def test_a_failed_attempt_returns_the_row_to_due_with_its_next_time_and_keeps_its_id(c):
    add(c, "a")
    assert O.claim(c, SENDS, "a", 10.0)
    gave_up = O.settle_failure(c, SENDS, "a", attempts=1, error="timeout", schedule=O.Waits((60,)), now=10.0,
                               later=lambda now, wait: now + wait, give_up_state="failed", rng=random.Random(1),
                               extra={"last_status": 503})
    assert gave_up is False
    retried = row(c, "a")
    assert retried["state"] == "ready" and retried["lease"] is None and retried["next_at"] == 70.0
    assert retried["last_status"] == 503 and retried["attempts"] == 1
    assert O.claim_next(c, SENDS, 69.0) is None, "not due until its wait is over"
    assert O.claim_next(c, SENDS, 70.0)["id"] == "a", "the repeat keeps the same id, the key the receiver dedupes on"


def test_a_sent_row_is_refused_on_a_repeat_claim(c):
    add(c, "a")
    assert O.claim(c, SENDS, "a", 1.0)
    O.finish(c, SENDS, "a", "delivered", attempts=1, error="", due_at=2.0)
    assert not O.claim(c, SENDS, "a", 3.0), "a delivered row is never claimed again"
    assert O.claim_next(c, SENDS, 99.0) is None
    assert row(c, "a")["state"] == "delivered"


def test_a_release_hands_the_row_back_unsent_with_no_attempt_counted(c):
    add(c, "a")
    assert O.claim(c, SENDS, "a", 5.0)
    assert O.release(c, SENDS, "a")
    back = row(c, "a")
    assert back["state"] == "ready" and back["lease"] is None and back["attempts"] == 0
    assert O.claim(c, SENDS, "a", 6.0), "a released row is claimed again"
    assert not O.release(c, SENDS, "b"), "a row that is not held is not released"
