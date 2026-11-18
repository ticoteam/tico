"""Running out of credit on Tico Cloud is said, not silent: HQ's billing can stop the cloud
machines while every bot would otherwise read idle with its turns queued.

A machine HQ refused for credit (its 402) makes every page say Out of credit even while HQ's company-wide word is
still open; each bot on it reads stopped for credit, a message to it says so, and its chat line names it. Before that,
a balance falling to under two days of spend says Low credit. HQ is a fake (helpers/cloud_machines.py)."""

from datetime import datetime, timedelta, timezone

from backend import cloud_access, events, holds, thread_bot_status
from backend.tests.helpers.cloud_access import signals
from backend.tests.helpers.cloud_machines import FakeHQ
from backend.tests.helpers.fresh_install import booted, environment, rows, signed_in  # noqa: F401


def _cloud_company(environment, monkeypatch):
    api = environment()
    done = api.post("/api/v2/setup/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    cloud_access.apply(api.app.state.store, cloud_access.parse(signals("open")))
    hq, manager = FakeHQ(), api.app.state.cloud_machines
    monkeypatch.setattr(manager, "client", hq)
    manager.tick()
    return api


def _shown(api):
    status = api.get("/api/status", headers=signed_in()).json()["cloud_access"]
    page = api.get("/api/v2/cloud-access", headers=signed_in()).json()["cloud"]
    assert status == page
    return status


def test_a_machine_refused_for_credit_is_out_of_credit_everywhere_while_hq_still_says_open(environment, monkeypatch):
    api = _cloud_company(environment, monkeypatch)
    store = api.app.state.store
    assert _shown(api)["access"] == "open"
    with store.transaction() as c:
        c.execute("UPDATE cloud_machines SET problem='out_of_credit' WHERE bot='_shared'")
    shown = _shown(api)
    assert (shown["access"], shown["reason"]) == ("frozen", "out_of_credit")
    with store.read() as c:
        row = events.credit_overlay(c, {"bot": "botops", "state": "idle"})
        assert (row["state"], row["stopped"]) == ("paused", "out_of_credit")
        # A running turn keeps its row: it is finishing on a machine that was up.
        assert events.credit_overlay(c, {"bot": "botops", "state": "running"})["state"] == "running"
        assert holds.warning(c, "bot:botops") == holds.CREDIT_WARNING
        blocked = thread_bot_status._blocked(c, "botops", thread_bot_status._location(c, "botops"), None)
        assert blocked == (thread_bot_status.OUT_OF_CREDIT, {"label": "Fix", "href": "#/settings/billing"})
    # The same word again (no credit added) changes nothing.
    cloud_access.apply(store, cloud_access.current(store))
    assert _shown(api)["access"] == "frozen"
    # Credit landed: HQ's balance rose, so every refused machine may start again, none of them waiting on a bot.
    with store.transaction() as c:
        c.execute("UPDATE cloud_machines SET next_try_at=? WHERE bot='_shared'", (2**40,))
    before = cloud_access.current(store) or {}
    cloud_access.apply(
        store,
        cloud_access.parse(signals("open", balance_micros=(before.get("balance_micros") or 0) + 20_000_000)),
    )
    with store.read() as c:
        machine = c.execute("SELECT problem, next_try_at FROM cloud_machines WHERE bot='_shared'").fetchone()
    assert (machine["problem"], machine["next_try_at"]) == ("", None)
    assert _shown(api)["access"] == "open"
    with store.read() as c:
        assert events.credit_overlay(c, {"bot": "botops", "state": "idle"})["state"] == "idle"
        assert holds.warning(c, "bot:botops") is None


def test_a_frozen_company_stops_its_cloud_bots_too(environment, monkeypatch):
    api = _cloud_company(environment, monkeypatch)
    cloud_access.apply(api.app.state.store, cloud_access.parse(signals("frozen", "out_of_credit")))
    store = api.app.state.store
    with store.read() as c:
        assert events.credit_overlay(c, {"bot": "botops", "state": "idle"})["stopped"] == "out_of_credit"
    # A frozen company's machines are marked; open again, none keeps the mark.
    with store.transaction() as c:
        c.execute("UPDATE cloud_machines SET problem='out_of_credit'")
    cloud_access.apply(store, cloud_access.parse(signals("open")))
    assert _shown(api)["access"] == "open"


def _at(hours):
    return (datetime(2026, 11, 17, tzinfo=timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _answer(store, dollars, hours):
    return cloud_access.apply(
        store, cloud_access.parse(signals("open", balance_micros=int(dollars * 1_000_000))), _at(hours)
    )


def _low_events(store):
    with store.read() as c:
        return c.execute("SELECT count(*) FROM events WHERE action='cloud.low_credit'").fetchone()[0]


def test_low_credit_is_read_from_the_falling_balance_and_said_once(api):
    store = api.app.state.store
    # $10 a day from $30. A first answer, or one under an hour on, reads no spend.
    _answer(store, 30, 0)
    assert cloud_access.view(_answer(store, 29.9, 0.5))["days_left"] is None
    for hour in range(1, 25):
        found = _answer(store, 30 - hour * 10 / 24, hour)
    assert found["spend_day_micros"] == 10_000_000
    assert cloud_access.view(found)["days_left"] is None, "two days left is not low yet"
    assert _low_events(store) == 0
    found = _answer(store, 19, 26)
    assert cloud_access.view(found)["days_left"] == 1
    assert cloud_access.view(_answer(store, 8, 52))["days_left"] == 0
    assert _low_events(store) == 1, "said once, as it starts"
    # Credit landed: the balance rose, the spend is kept, and $108 is weeks of it.
    found = _answer(store, 108, 53)
    assert found["spend_day_micros"] > 0 and cloud_access.view(found)["days_left"] is None
    # Frozen has no low-credit line: it says Out of credit itself.
    frozen = cloud_access.apply(store, cloud_access.parse(signals("frozen", "out_of_credit")), _at(54))
    assert cloud_access.view(frozen)["days_left"] is None


def test_an_unreadable_sample_or_clock_keeps_the_last_spend(api):
    kept, rate = cloud_access.spend({"samples": [{"at": "not a time"}], "spend_day_micros": 5}, 100, "also not")
    assert (kept, rate) == ([], 5)
