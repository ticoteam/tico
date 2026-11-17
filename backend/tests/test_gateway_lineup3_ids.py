"""Gateway lineup 3's ids: every old id maps to this lineup's once (backend/gateway_tiers.py `new_id`)."""

import pytest

from backend import gateway_tiers as T

OLD_TO_NEW = (
    ("tico-sales-medium-fast-1.0", "tico-medium-fast-1.0"),
    ("tico-general-medium-1.0", "tico-medium-1.0"),
    ("tico-general-low", "tico-low"),
    ("tico-engineering-high-1.0", "tico-high-1.0"),
    ("tico-data-low-fast", "tico-low-fast"),
    ("tico-operations-high-fast", "tico-high-fast"),
    ("tico-legal-medium-1.1", "tico-medium-1.1"),
    ("TICO-FINANCE-LOW-1.0", "tico-low-1.0"),
    ("tico-cheap-1.1", "tico-low-1.0"),
    ("tico-cheap-1.0", "tico-low-1.0"),
    ("tico-mid-1.0", "tico-medium-1.0"),
    ("tico-power-1.0", "tico-high-1.0"),
    ("tico-decide-1.0", "tico-decide-low-1.0"),
    ("tico-classify-1.0", "tico-decide-low"),
    ("tico-classify-low", "tico-decide-low"),
    ("tico-classify-low-1.0", "tico-decide-low"),
)
UNCHANGED = (
    "tico-meeting",
    "tico-meeting-1.0",
    "tico-decide-low",
    "tico-decide-low-1.1",
    "tico-xhigh-fast-1.0",
    "claude-opus-5-5",
    "tico-sales-huge-1.0",
    "tico-site",
    "",
)


@pytest.fixture(autouse=True)
def fresh_catalog():
    T.reset()
    yield
    T.reset()


@pytest.mark.parametrize(("old", "new"), OLD_TO_NEW)
def test_every_old_id_shape_maps_to_this_lineups_id_and_the_new_id_to_itself(old, new):
    assert T.new_id(old) == new and T.new_id(new) == new


def test_every_id_of_this_lineup_and_any_other_value_maps_to_itself():
    ids = [row["id"] for kind in ("models", "decisions") for row in T.SNAPSHOT[kind]]
    ids += [v["id"] for kind in ("models", "decisions") for row in T.SNAPSHOT[kind] for v in row["versions"]]
    for value in [*ids, *UNCHANGED]:
        assert T.new_id(value) == value, value
    assert T.new_id(None) is None
