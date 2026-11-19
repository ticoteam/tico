"""Health's update.history: the releases this server ran and when it first started on each, oldest first, read by bots
and people as the rest of Health is (backend/health.py `release_history`, written by backend/releases.py
`record_start`)."""

import json

from backend import releases
from backend.tests.helpers.tasks_board import api, bot_token, headers  # noqa: F401


def started(api, monkeypatch, version, at):
    """The server starts on `version` at `at`, as store startup calls record_start."""
    monkeypatch.setenv("TICO_VERSION", version)
    with api.app.state.store.transaction() as c:
        releases.record_start(c, at)


def stored(api, entries):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (releases.HISTORY, json.dumps(entries)))


def history(api, token="ana-test", query=""):
    response = api.get("/api/v2/health" + query, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()["update"]["history"]


def test_a_fresh_install_lists_the_release_it_runs(api, monkeypatch):
    """AC-8: the startup write gives a new install one entry, its running release."""
    stored(api, [])
    started(api, monkeypatch, "0.6.20", "2026-11-17T08:03:11.000000Z")
    assert history(api) == [{"version": "0.6.20", "since": "2026-11-17T08:03:11Z"}]


def test_a_new_release_is_added_last_and_a_restart_on_the_same_one_adds_nothing(api, monkeypatch):
    """AC-1 and AC-2."""
    stored(api, [{"version": "0.6.18", "since": "2026-11-17T05:41:09.000000Z"}])
    started(api, monkeypatch, "0.6.19", "2026-11-17T07:12:30.000000Z")
    before = history(api)
    assert before[-1] == {"version": "0.6.19", "since": "2026-11-17T07:12:30Z"} and len(before) == 2
    started(api, monkeypatch, "0.6.19", "2026-11-17T09:00:00.000000Z")
    assert history(api) == before


def test_a_rollback_is_its_own_entry(api, monkeypatch):
    """AC-3: 0.6.20 back to 0.6.19 shows both, each with its own start."""
    stored(api, [{"version": "0.6.19", "since": "2026-11-17T07:12:30.000000Z"}])
    started(api, monkeypatch, "0.6.20", "2026-11-17T08:03:11.000000Z")
    started(api, monkeypatch, "0.6.19", "2026-11-17T08:30:00.000000Z")
    assert history(api)[-2:] == [
        {"version": "0.6.20", "since": "2026-11-17T08:03:11Z"},
        {"version": "0.6.19", "since": "2026-11-17T08:30:00Z"},
    ]


def test_500_are_kept_and_the_oldest_is_dropped_within_30_kb(api, monkeypatch):
    """AC-4 and the guard: at 500 entries Health grows by no more than 30 KB."""
    stored(api, [])
    without = len(api.get("/api/v2/health", headers=headers()).content)
    full = [
        {"version": f"0.{n // 100}.{n % 100}", "since": f"2026-10-{1 + n // 20:02d}T00:00:00.000000Z"}
        for n in range(500)
    ]
    stored(api, full)
    started(api, monkeypatch, "1.0.0", "2026-11-18T00:00:00.000000Z")
    listed = history(api)
    assert len(listed) == releases.HISTORY_KEPT == 500
    assert listed[0] == {"version": "0.0.1", "since": "2026-10-01T00:00:00Z"}
    assert listed[-1] == {"version": "1.0.0", "since": "2026-11-18T00:00:00Z"}
    assert len(api.get("/api/v2/health", headers=headers()).content) - without <= 30 * 1024


def test_a_bot_reads_it_with_only_version_and_since(api, monkeypatch):
    """AC-5: a signed-in bot (not the owner) reads it; anything else an entry carries stays out."""
    stored(api, [{"version": "0.6.19", "since": "2026-11-17T07:12:30.000000Z", "actor": "human:ana", "host": "box"}])
    entries = history(api, bot_token(api))
    assert entries == [{"version": "0.6.19", "since": "2026-11-17T07:12:30Z"}]


def test_since_keeps_the_entries_from_that_time_and_a_malformed_one_is_refused(api):
    """AC-6."""
    stored(
        api,
        [
            {"version": "0.6.17", "since": "2026-11-16T23:59:59.000000Z"},
            {"version": "0.6.18", "since": "2026-11-17T00:00:00.000000Z"},
            {"version": "0.6.19", "since": "2026-11-17T07:12:30.000000Z"},
        ],
    )
    assert [r["version"] for r in history(api, query="?since=2026-11-17T00:00:00Z")] == ["0.6.18", "0.6.19"]
    assert [r["version"] for r in history(api, query="?since=2026-11-17T07:00:00")] == ["0.6.19"]  # no zone: UTC
    refused = api.get("/api/v2/health?since=yesterday", headers=headers())
    assert refused.status_code == 400 and "ISO time" in refused.text


def test_500_prerelease_entries_with_microseconds_stay_within_30_kb(api, monkeypatch):
    """The guard with ordinary prerelease versions: since is listed to the whole second, versions stay whole."""
    stored(api, [{"version": "0.7.0-rc.39", "since": "2026-11-17T08:03:11.123456Z"} for _ in range(500)])
    listed = history(api, bot_token(api))
    assert len(listed) == 500 and {r["version"] for r in listed} == {"0.7.0-rc.39"}
    assert len(json.dumps(listed, separators=(",", ":")).encode()) <= 30 * 1024


def test_since_is_listed_to_the_whole_second_and_the_stored_record_keeps_microseconds(api):
    stored(api, [{"version": "0.6.20", "since": "2026-11-17T08:03:11.123456Z"}])
    assert history(api, bot_token(api)) == [{"version": "0.6.20", "since": "2026-11-17T08:03:11Z"}]
    with api.app.state.store.transaction() as c:
        assert releases.history(c)[0]["since"] == "2026-11-17T08:03:11.123456Z"


def test_since_at_the_entrys_second_keeps_it(api):
    """Inclusive at the second: since=08:03:11Z keeps an entry stored at 08:03:11.123456Z; a later second drops it."""
    stored(api, [{"version": "0.6.20", "since": "2026-11-17T08:03:11.123456Z"}])
    bot = bot_token(api)
    assert [r["version"] for r in history(api, bot, "?since=2026-11-17T08:03:11Z")] == ["0.6.20"]
    assert history(api, bot, "?since=2026-11-17T08:03:12Z") == []
    # A fractional cutoff before the stored start in the same second keeps it; one after it drops it.
    assert history(api, bot, "?since=2026-11-17T08:03:11.100000Z") == [
        {"version": "0.6.20", "since": "2026-11-17T08:03:11Z"}
    ]
    assert history(api, bot, "?since=2026-11-17T08:03:11.200000Z") == []
    assert api.get("/api/v2/health?since=2026-13-01", headers=headers()).status_code == 400
