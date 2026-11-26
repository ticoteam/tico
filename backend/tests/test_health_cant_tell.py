"""A Health check that could not read what it checks says "unknown" (the page's "Can't tell") with what it could not
read and since when, never "ok": a stopped Slack service, an unreadable backup location, a stale never-stuck reading,
an OK'd sends read that failed, bots whose tools could not be read."""

import json
import re
import sqlite3
from types import SimpleNamespace

import pytest

from backend import health, releases, slack_app, slack_gateway, support
from backend.store import H
from backend.tests.helpers.health import health_of
from backend.tests.helpers.onboarding import environment  # noqa: F401

REMOTE = SimpleNamespace(loopback=False)
CLOCK = r"([A-Z][a-z]{2} \d{1,2} )?\d\d:\d\d UTC"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(releases, "CHECKER", releases.Checker())
    monkeypatch.setenv("TICO_UPDATE_CHECK", "off")


def _slack_connected(api, minutes_ago):
    store = api.app.state.store
    slack_app.save(store, "ana", "xoxb-1", "xapp-1")
    slack_app.report(store, "connected", team="T1")
    with store.transaction() as c:
        c.execute(
            "UPDATE service_health SET last_succeeded_at=? WHERE service=?",
            (H.shift(H.now(), minutes=-minutes_ago), slack_app.HEALTH),
        )


def test_a_slack_service_silent_past_its_beats_cant_tell_with_its_last_time(environment):
    api = environment()
    _slack_connected(api, minutes_ago=16)
    slack = health_of(api)[1]["slack"]
    assert slack["status"] == "unknown"
    assert re.fullmatch(f"The Slack service has not reported since {CLOCK}. Is it running\\?", slack["summary"])
    _slack_connected(api, minutes_ago=4)
    assert health_of(api)[1]["slack"]["status"] == "ok"


def test_a_running_slack_service_keeps_its_line_fresh_so_slack_stays_fine(environment, monkeypatch):
    """The gateway's heartbeat rewrites "connected" every BEAT_SECONDS while it is up, so half an hour of a connected
    service still reads Fine; a change of state is written at once, as before."""
    api = environment()
    store = api.app.state.store
    slack_app.save(store, "ana", "xoxb-1", "xapp-1")
    start = H.now()
    clock = [0.0]
    monkeypatch.setattr(H, "now", lambda: H.shift(start, seconds=clock[0]))
    caught_up = []
    gateway = SimpleNamespace(
        slack=SimpleNamespace(connected=lambda: True),
        bridge=SimpleNamespace(catch_up_soon=lambda: caught_up.append(clock[0])),
        stop=SimpleNamespace(set=lambda: None),
    )
    state = {"connected": None, "stamp": None}
    while clock[0] <= 30 * 60:
        slack_gateway.beat(gateway, state, store, "T1", clock=lambda: clock[0])
        clock[0] += 60
    assert caught_up == [0.0]  # only the change to connected catches up
    assert health_of(api)[1]["slack"]["status"] == "ok"


def _remote(**extra):
    return {"mode": "remote", "target_kind": "s3", "last_replicated_at": H.shift(H.now(), minutes=-50), **extra}


def test_a_failed_backup_listing_cant_tell_naming_the_error_and_the_newest_copy(monkeypatch):
    monkeypatch.delenv("TICO_BACKUP_SYNC_INTERVAL", raising=False)
    failed = H.shift(H.now(), minutes=-3)
    backup = _remote(checked_at=H.shift(H.now(), minutes=-5), listing_error="ConnectionError", listing_failed_at=failed)
    check = health._backups({"backup": backup}, REMOTE)
    assert check["status"] == "unknown"
    assert re.fullmatch(
        f"Could not read the backup location at {CLOCK} \\(ConnectionError\\). Newest copy known: {CLOCK}.",
        check["summary"],
    )
    # Past the warning time the age rule turns it to Look, then Broken, as before.
    old = {**backup, "checked_at": H.shift(H.now(), minutes=-15)}
    assert health._backups({"backup": old}, REMOTE)["status"] == "warn"
    older = {**backup, "checked_at": H.shift(H.now(), minutes=-120)}
    assert health._backups({"backup": older}, REMOTE)["status"] == "bad"


def test_a_remote_backup_status_never_checked_cant_tell():
    check = health._backups({"backup": _remote()}, REMOTE)
    assert check["status"] == "unknown" and "not read" in check["summary"]
    assert health._backups({"backup": _remote(checked_at=H.now())}, REMOTE)["status"] == "ok"


def test_the_backup_loop_records_when_and_how_its_listing_failed(tmp_path):
    from backend import replication

    path = tmp_path / "status.json"

    class Down:
        def latest(self, levels):
            raise ConnectionError("no route")

    replication.write_status(Down(), path, now=1_700_000_000)
    saved = json.loads(path.read_text())
    assert saved["listing_error"] == "ConnectionError" and saved["listing_failed_at"].startswith("2023-11-14T22:13:20")
    env = {"TICO_BACKUP_MODE": "remote", "TICO_BACKUP_URL": "s3://acme-backups/db"}
    status = replication.status(env, path)
    assert status["listing_error"] == "ConnectionError" and status["listing_failed_at"] == saved["listing_failed_at"]


def _reading_at(api, at):
    store = api.app.state.store
    health.record_never_stuck(store)
    with store.transaction() as c:
        c.execute(
            "UPDATE registry_metadata SET value=json_set(value,'$.at',?) WHERE key=?", (at, health.NEVER_STUCK_KEY)
        )


def test_a_never_stuck_reading_past_two_missed_runs_cant_tell_with_its_date(environment):
    api = environment()
    at = H.shift(H.now(), hours=-4)
    _reading_at(api, at)
    tasks = health_of(api)[1]["never_stuck"]
    assert tasks["status"] == "unknown"
    when = health._dated(at)
    assert re.fullmatch(r"[A-Z][a-z]{2} \d{1,2}, \d\d:\d\d UTC", when)
    assert tasks["summary"].startswith(f"The hourly reading last ran {when}.")
    _reading_at(api, H.shift(H.now(), minutes=-50))
    tasks = health_of(api)[1]["never_stuck"]
    assert tasks["status"] == "ok" and re.search(r"Read at [A-Z][a-z]{2} \d{1,2}, \d\d:\d\d UTC", tasks["summary"])


def test_an_okd_sends_read_error_other_than_a_missing_table_cant_tell(environment, monkeypatch):
    api = environment()

    def locked(c):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(support, "claimed", locked)
    check = health_of(api)[1]["claimed_oks"]
    assert check["status"] == "unknown" and check["summary"] == "Could not read OK'd sends (OperationalError)."

    def missing(c):
        raise sqlite3.OperationalError("no such table: support_reports")

    monkeypatch.setattr(support, "claimed", missing)
    check = health_of(api)[1]["claimed_oks"]
    assert check["status"] == "ok" and check["summary"] == "Nothing OK'd waits to be sent."


def test_bots_whose_tools_could_not_be_read_are_named_in_a_cant_tell_row(environment, monkeypatch):
    from backend import bot_tools
    from backend.tests.helpers.getting_started import SIGNED_IN, add_bot, enrolled  # noqa: F401

    api = environment()
    runner = enrolled(api)
    deny = "Claude Code settings deny: gh pr create, git push"
    readiness = {}
    for slug in ("scout", "reviewer"):
        add_bot(api, slug)
        readiness[slug] = {"ready": True, "runtime": "claude", "warnings": [deny], "tools": []}
        with api.app.state.store.transaction() as c:
            c.execute(
                "INSERT INTO assignments(bot,runner_id,generation,updated_at,updated_by) VALUES(?,?,1,?,'t')",
                (slug, runner, H.now()),
            )
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE runner_states SET last_seen_at=?, readiness=? WHERE runner_id=?",
            (H.now(), json.dumps({"schema_version": 1, "runtimes": SIGNED_IN, "bots": readiness}), runner),
        )

    def unreadable(c, settings, bot, reads=None):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(bot_tools, "listing", unreadable)
    body, checks = health_of(api)
    row = checks["harness_unread"]
    assert row["status"] == "unknown" and row["label"] == "Harness permissions"
    assert row["summary"] == "Could not read the tools of reviewer and scout."
    assert "harness_denies" not in checks
    assert body["unknown"] == 1


def test_the_api_counts_unknown_apart_from_attention(environment, monkeypatch):
    api = environment()
    before = health_of(api)[0]
    monkeypatch.setattr(support, "claimed", lambda c: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")))
    after, checks = health_of(api)
    assert checks["claimed_oks"]["status"] == "unknown"
    assert after["unknown"] == before["unknown"] + 1
    assert after["attention"] == before["attention"]
