"""Helpers that other tests share, moved out of test_demo_company.py so they import no test file."""

from datetime import datetime, timezone

NOW = datetime(2026, 11, 6, 18, 30, tzinfo=timezone.utc)  # a Friday evening: the week is complete
