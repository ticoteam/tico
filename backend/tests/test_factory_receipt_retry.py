"""The factory's check that a refused review move is retried within a tick of the builder's clean receipt. The retry
pass runs before the bots in each tick, so a receipt posted on the run's last tick has no pass after it: the check
leaves it out rather than reading the run's end as a retry that never came (PR #3092: receipt_wording seed 1 failed
when a shifted Find stuck moved Ticket 7's clean receipt onto the last tick)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from evals.factory.cases.receipt_wording import retried_after_receipt

T0 = datetime(2026, 10, 20, 22, 25, tzinfo=timezone.utc)
REFUSED = {"error": {"code": "review_gate", "detail": "Review refused: failed test receipt for the current head"}}


def run_with(clean_at, moved_at, end):
    """One builder whose Review 2 move was refused at T0, a clean receipt at `clean_at`, the ticket entering Review 2
    at `moved_at` (None: never), and the run's last tick at `end`."""
    builder = SimpleNamespace(
        posted=[(T0, "t7", "a" * 40, "failing"), (clean_at, "t7", "b" * 40, "clean")],
        moves=[(T0, "t7", "Review 2", str(REFUSED))],
    )
    rows = [{"created_at": moved_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "name": "Review 2"}] if moved_at else []
    world = SimpleNamespace(
        sql=lambda query, args=(): rows,
        title=lambda tid: "Ticket 7",
        H=SimpleNamespace(
            parse_ts=lambda text: datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        ),
    )
    return SimpleNamespace(
        scenario=SimpleNamespace(tick_minutes=5), t=SimpleNamespace(builders=[builder]), w=world, end=end
    )


def test_a_clean_receipt_on_the_last_tick_is_not_counted_late():
    last = T0 + timedelta(hours=4, minutes=35)
    ok, detail = retried_after_receipt(run_with(last, None, last))
    assert not ok and "did not happen" in detail


def test_a_clean_receipt_with_a_tick_after_it_must_still_be_retried():
    clean = T0 + timedelta(hours=1)
    ok, detail = retried_after_receipt(run_with(clean, None, clean + timedelta(minutes=5)))
    assert not ok and "Ticket 7 Review 2 never" in detail
    ok, _ = retried_after_receipt(run_with(clean, clean + timedelta(minutes=5), clean + timedelta(minutes=5)))
    assert ok
