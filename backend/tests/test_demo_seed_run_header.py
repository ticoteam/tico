"""A demo build waits for every run header, so a slow machine reads the demo clock in the same order as a fast one.

Merge queue run 45349954837 (2026-11-24): two builds of one date differed by a microsecond from a 09:00 schedule
pass on. A claim gives its run header BUDGET_SECONDS of the machine's time (backend/run_header.py) and answers
without it after that, while the header's build goes on reading hubdb.now beside the claim's next steps."""

import threading
import time
from datetime import datetime, timezone

from backend import demo_seed, run_header

APP_BUDGET = run_header.BUDGET_SECONDS


def test_a_claim_in_the_demo_build_waits_for_a_slow_run_header(monkeypatch):
    def slow(*_):
        # Ready only once the app's whole budget has run out since this header started, which is after the claim's
        # app deadline (taken before the header was handed to its worker): a claim on the app budget answers None.
        past_the_app_budget = time.monotonic() + APP_BUDGET
        never_set = threading.Event()
        while (left := past_the_app_budget - time.monotonic()) > 0:
            never_set.wait(left)
        return "the header"

    monkeypatch.setattr(run_header, "_build", slow)
    budget = run_header.BUDGET_SECONDS
    with demo_seed.fixed(demo_seed.Clock(datetime(2026, 11, 4, 9, tzinfo=timezone.utc))):
        assert run_header.build(None, None, {"id": "attempt"}, "runner") == "the header"
    assert run_header.BUDGET_SECONDS == budget, "a served app keeps its own budget after the build"
