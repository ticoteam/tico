"""The Growth page's cards as they were the default before the Growth line (backend/growth_line.py) took the month
numbers and channels: the Funnel, six Number cards, Channels, Retention and the Growth board. A team that pinned them
keeps them, so the tests of those cards read them from this layout."""

import pytest

from backend import cards

CLASSIC = [
    ("growth_funnel", "large"),
    *(
        ("growth_number", "small", {"metric": m})
        for m in ("new_customers", "cac", "spend", "ltv_cac", "payback_months", "kept_d30")
    ),
    ("growth_channels", "large"),
    ("growth_retention", "medium"),
    ("growth_board", "medium"),
]


@pytest.fixture
def classic_cards(monkeypatch):
    """The Growth page's unsaved layout is the classic one for this test."""
    monkeypatch.setitem(cards.DEFAULTS, "growth", CLASSIC)
