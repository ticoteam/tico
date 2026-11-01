"""The 30-minute spend window (redesign/CONTRACT.md §1, backend/usage_limits.py): it replaces the hour for new limits; an
hourly limit becomes the half hour at the same rate; a bot over it takes no new turn until the window ends. A child
group's limit may not exceed its parent's."""
from datetime import datetime, timedelta, timezone

from backend import usage_limits
from backend.tests.test_api import api, claim, get, headers, post  # noqa: F401  (the api fixture)
from backend.tests.test_usage_limits import finish, fleet, groups, paused, put, start


def test_an_hour_becomes_the_half_hour_at_the_same_rate(api):
    assert usage_limits.half(4) == 2 and usage_limits.half(3.5) == 1.8                # up to the next $0.10
    put(api, "usage/limits/ops", {"hourly_usd": 4})
    row = get(api, "usage/limits")["bots"]["ops"]
    assert row["per_30m_usd"] == 2 and row["hourly_usd"] is None
    put(api, "usage/limits/ops", {"per_30m_usd": 2.5})
    assert get(api, "usage/limits")["bots"]["ops"]["per_30m_usd"] == 2.5
    put(api, "usage/limits/ops", {"per_30m_usd": 2, "hourly_usd": 4}, expected=422)   # one or the other


def test_a_bot_over_its_30_minute_limit_takes_no_new_turn_until_the_window_ends(api):
    r = fleet(api)
    put(api, "usage/limits/ops", {"per_30m_usd": 1})
    finish(api, r, start(api, r), 500_000)                                              # $5 against a $1 window
    post(api, "chat/ops", {"text": "More."})
    assert claim(api, r) is None
    assert paused(api)["focus"] == "Paused: over its 30-minute limit"
    with api.app.state.store.read() as c:
        started = c.execute("SELECT min(started_at) FROM turns WHERE bot='ops'").fetchone()[0]
        at = datetime.fromisoformat(started[:19]).replace(tzinfo=timezone.utc)
        assert usage_limits.blocked(c, "ops", at=at) == "per_30m"
        assert usage_limits.blocked(c, "ops", at=at + timedelta(minutes=30)) is None    # the next window is empty


def test_the_window_is_the_half_hour_the_time_falls_in(api):
    at = datetime(2026, 10, 28, 10, 10, tzinfo=timezone.utc)
    key, start_, end = usage_limits.bounds(at)["per_30m"]
    assert (start_, end) == ("2026-10-28T10:00:00", "2026-10-28T10:30:00")
    late = datetime(2026, 10, 28, 10, 40, tzinfo=timezone.utc)
    assert usage_limits.bounds(late)["per_30m"][1:] == ("2026-10-28T10:30:00", "2026-10-28T11:00:00")


def test_a_child_group_limit_may_not_exceed_its_parent(api):
    groups(api, "product")                                                              # Product nested under Engineering
    put(api, "usage/pools/groups/eng", {"daily_usd": 1})
    put(api, "usage/pools/groups/product", {"daily_usd": 3}, expected=422)
    put(api, "usage/pools/groups/product", {"daily_usd": 0.5})


def stored(api, table, key, value):
    with api.app.state.store.read() as c:
        row = c.execute(f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchone()
        return dict(row) if row else None


def test_a_save_changes_only_the_fields_it_sends(api):
    groups(api, "eng")
    put(api, "usage/limits/ops", {"per_30m_usd": 2})
    put(api, "usage/limits/ops", {"daily_usd": 20})                                     # the half hour stays
    row = get(api, "usage/limits")["bots"]["ops"]
    assert row["per_30m_usd"] == 2 and row["daily_usd"] == 20
    put(api, "usage/limits/ops", {"per_30m_usd": None})                                 # an explicit null clears
    row = get(api, "usage/limits")["bots"]["ops"]
    assert row["per_30m_usd"] is None and row["daily_usd"] == 20
    put(api, "usage/pools/groups/eng", {"per_30m_usd": 3})
    put(api, "usage/pools/groups/eng", {"daily_usd": 40})
    assert stored(api, "usage_pools", "scope", "group:eng")["per_30m_usd"] == 3
    put(api, "usage/limits", {"per_30m_usd": 5})
    put(api, "usage/limits", {"daily_usd": 50})
    default = get(api, "usage/limits")["default"]
    assert default["per_30m_usd"] == 5 and default["daily_usd"] == 50
