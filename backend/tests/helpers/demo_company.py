"""The demo company build's clock, shared by its tests (moved out of test_demo_company.py)."""

from datetime import datetime, timezone

NOW = datetime(2026, 11, 6, 18, 30, tzinfo=timezone.utc)  # a Friday evening: the week is complete
