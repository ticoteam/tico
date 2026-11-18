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
    assert history(api) == [{"version": "0.6.20", "since": "2026-11-17T08:03:11.000000Z"}]


def test_a_new_release_is_added_last_and_a_restart_on_the_same_one_adds_nothing(api, monkeypatch):
    """AC-1 and AC-2."""
    stored(api, [{"version": "0.6.18", "since": "2026-11-17T05:41:09.000000Z"}])
    started(api, monkeypatch, "0.6.19", "2026-11-17T07:12:30.000000Z")
    before = history(api)
    assert before[-1] == {"version": "0.6.19", "since": "2026-11-17T07:12:30.000000Z"} and len(before) == 2
    started(api, monkeypatch, "0.6.19", "2026-11-17T09:00:00.000000Z")
    assert history(api) == before


def test_a_rollback_is_its_own_entry(api, monkeypatch):
    """AC-3: 0.6.20 back to 0.6.19 shows both, each with its own start."""
    stored(api, [{"version": "0.6.19", "since": "2026-11-17T07:12:30.000000Z"}])
    started(api, monkeypatch, "0.6.20", "2026-11-17T08:03:11.000000Z")
    started(api, monkeypatch, "0.6.19", "2026-11-17T08:30:00.000000Z")
    assert history(api)[-2:] == [
        {"version": "0.6.20", "since": "2026-11-17T08:03:11.000000Z"},
        {"version": "0.6.19", "since": "2026-11-17T08:30:00.000000Z"},
    ]


def test_500_are_kept_and_the_oldest_is_dropped_within_30_kb(api, monkeypatch):
    """AC-4 and the guard: at 500 entries Health grows by no more than 30 KB."""
    stored(api, [])
    without = len(api.get("/api/v2/health", headers=headers()).content)
    full = [{"version": f"0.{n // 100}.{n % 100}", "since": f"2026-10-{1 + n // 20:02d}T00:00:00.000000Z"} for n in range(500)]
    stored(api, full)
    started(api, monkeypatch, "1.0.0", "2026-11-18T00:00:00.000000Z")
    listed = history(api)
    assert len(listed) == releases.HISTORY_KEPT == 500
    assert listed[0] == full[1] and listed[-1] == {"version": "1.0.0", "since": "2026-11-18T00:00:00.000000Z"}
    assert len(api.get("/api/v2/health", headers=headers()).content) - without <= 30 * 1024


def test_a_bot_reads_it_with_only_version_and_since(api, monkeypatch):
    """AC-5: a signed-in bot (not the owner) reads it; anything else an entry carries stays out."""
    stored(api, [{"version": "0.6.19", "since": "2026-11-17T07:12:30.000000Z", "actor": "human:ana", "host": "box"}])
    entries = history(api, bot_token(api))
    assert entries == [{"version": "0.6.19", "since": "2026-11-17T07:12:30.000000Z"}]


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
