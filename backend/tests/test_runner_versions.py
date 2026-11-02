"""Runner versions: comparison, the range a server accepts, claim gating and what Health and Computers show."""

import pytest

from backend import releases, runner_versions
from backend.tests.test_api import api, assign, get, post, ready, runner  # noqa: F401


@pytest.fixture(autouse=True)
def server_release(monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.3.0")
    monkeypatch.setattr(runner_versions, "MIN_RUNNER_RELEASE", "0.2.0")
    monkeypatch.setattr(releases, "CHECKER", releases.Checker())


def report(api, r, release, kind="mac", update=None, **extra):
    body = {"version": "test", "platform": "test", "readiness": {"ops": True}, "release": release, "kind": kind, **extra}
    if update:
        body["update"] = update
    return post(api, "runners/heartbeat", body, token=r["token"])


def queue_work(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    post(api, "chat/ops", {"text": "Work for the runner"})
    return r


def test_states_against_the_servers_release():
    state = runner_versions.state
    assert state("0.3.0") == "current" and state("0.4.0") == "current"          # a newer runner still works
    assert state("0.2.5") == "needs_update" and state("0.2.0") == "needs_update"
    assert state("0.1.9") == "incompatible" and state("0.1.9", "updating") == "incompatible"
    assert state("0.2.5", "updating") == "updating"
    assert state("dev") == "unknown" and state("") == "unknown"                  # not on a release: never paused
    assert state("0.3.0-rc.1") == "needs_update"                                 # a release outranks its candidates
    assert state("0.2.5", server="dev") == "unknown"
    assert not runner_versions.incompatible("dev") and not runner_versions.incompatible("")


def test_an_incompatible_runner_does_not_claim(api):
    r = queue_work(api)
    report(api, r, "0.1.0")
    reply = post(api, "jobs/claim", {"bot": None}, token=r["token"])
    assert reply["attempt"] is None and "0.2.0 or later" in reply["paused"] and "paused" in reply["paused"]
    report(api, r, "0.2.1")                                    # it updated
    assert post(api, "jobs/claim", {"bot": None}, token=r["token"])["attempt"]["bot"] == "ops"


def test_outdated_is_behind_the_servers_release_or_unknown():
    outdated = runner_versions.outdated
    assert outdated("0.2.9") and outdated("0.3.0-rc.1")                          # older
    assert not outdated("0.3.0") and not outdated("0.4.0")                       # equal or newer
    assert outdated("dev") and outdated("")                                      # unknown
    assert not outdated("dev", server="dev") and not outdated("0.1.0", server="dev")   # a dev server has nothing to compare


def test_heartbeat_stores_release_and_commit_and_nudges_the_owner_once_per_server_release(api, monkeypatch):
    r = runner(api, operator="ben", label="Ben's Mac")
    commit = "a" * 40

    def notices():
        with api.app.state.store.read() as c:
            return [row["body"] for row in c.execute(
                "SELECT body FROM messages WHERE recipient_id='human:ben' AND kind='notice' ORDER BY created_at")]

    report(api, r, "0.3.0", commit=commit)
    machine = next(m for m in get(api, "operations", token="ben-test")["computers"] if m["id"] == r["runner_id"])
    assert machine["update"]["release"] == "0.3.0" and machine["update"]["commit"] == commit
    assert machine["update"]["outdated"] is False and notices() == []

    report(api, r, "0.2.5", commit=commit)
    assert notices() == []                                      # it gets time to update itself first
    monkeypatch.setattr(runner_versions, "NUDGE_AFTER_S", 0)
    report(api, r, "0.2.5", commit=commit)
    report(api, r, "0.2.5", commit=commit)                     # every heartbeat after: no repeat
    assert len(notices()) == 1 and "Ben's Mac" in notices()[0] and "0.3.0" in notices()[0]
    machine = next(m for m in get(api, "operations", token="ben-test")["computers"] if m["id"] == r["runner_id"])
    assert machine["update"]["outdated"] is True and machine["update"]["server_release"] == "0.3.0"

    monkeypatch.setenv("TICO_VERSION", "0.4.0")                 # a new server release: one more nudge
    report(api, r, "0.2.5")
    report(api, r, "0.2.5")
    assert len(notices()) == 2 and "0.4.0" in notices()[1]



def ben_notices(api):
    with api.app.state.store.read() as c:
        return [row["body"] for row in c.execute(
            "SELECT body FROM messages WHERE recipient_id='human:ben' AND kind='notice' ORDER BY created_at")]


def behind_for_two_hours(api, r):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_versions SET outdated_since=? WHERE runner_id=?",
                  (runner_versions.H.shift(runner_versions.H.now(), seconds=-7200), r["runner_id"]))


def test_a_new_server_release_restarts_the_wait_before_a_nudge(api, monkeypatch):
    r = runner(api, operator="ben", label="Ben's Mac")
    report(api, r, "0.2.5")
    behind_for_two_hours(api, r)
    report(api, r, "0.2.5")
    assert len(ben_notices(api)) == 1
    monkeypatch.setenv("TICO_VERSION", "0.4.0")                 # it gets its hour to take this one by itself too
    report(api, r, "0.2.5")
    assert len(ben_notices(api)) == 1
    behind_for_two_hours(api, r)
    report(api, r, "0.2.5")
    assert len(ben_notices(api)) == 2 and "0.4.0" in ben_notices(api)[1]


def test_a_pinned_computer_shows_outdated_but_its_owner_is_not_nudged(api):
    r = runner(api, operator="ben", label="Ben's Mac")
    pinned = {"state": "pinned", "target": "0.3.0", "error": "pinned to this release; the server runs 0.3.0"}
    report(api, r, "0.2.5", update=pinned)
    behind_for_two_hours(api, r)
    report(api, r, "0.2.5", update=pinned)
    assert ben_notices(api) == []
    machine = next(m for m in get(api, "operations", token="ben-test")["computers"] if m["id"] == r["runner_id"])
    assert machine["update"]["outdated"] is True and machine["update"]["update_state"] == "pinned"



def test_queued_work_says_it_waits_for_the_computer_to_update_only_once_the_computer_stops_taking_work(api):
    """A computer due an update keeps claiming for 20 minutes, then takes no new work until its running turns end.
    Chat and task said only "Queued" for that hold; now they say why, and only while it lasts."""
    from backend import health, health_monitor
    from backend.store import H
    r = runner(api, label="Team box")
    assign(api, r, "ops")
    chat = post(api, "chat/ops", {"text": "Are you there?"})
    task = post(api, "tasks", {"owner": "ops", "title": "Check the invoices", "body": "Today."})
    store = api.app.state.store
    queued, held = ("Saved — queued", "Queued"), ("Saved — waiting for the computer to update", "Waiting for the computer to update")

    def shown():
        label = get(api, f"conversations/{chat['conversation_id']}/snapshot")["execution"]["label"]
        return label, get(api, f"tasks/{task['id']}")["waiting_for"]

    def update(state, target="0.3.0", release="0.2.5"):
        report(api, r, release, update={"state": state, "target": target, "error": ""})

    def began(minutes):             # the reported state began this long ago
        with store.transaction() as c:
            c.execute("UPDATE runner_versions SET update_since=? WHERE runner_id=?",
                      (H.shift(H.now(), minutes=-minutes), r["runner_id"]))

    report(api, r, "0.3.0")
    assert shown() == queued                                            # claiming as usual
    update("waiting")
    began(19)
    update("waiting")
    assert shown() == queued                                            # due an update, still claiming
    began(21)
    update("waiting", target="0.3.1")                                   # a newer target keeps the drain's start
    assert shown() == held
    # Health names the hold and how long it has run; it is still not a queue_idle Tico bug (PR 623).
    with store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE bot_id='ops'", (H.shift(H.now(), minutes=-30),))
        online = {x["id"] for x in health._online_runners(c)}
        assert {row["reason"] for row in health._waiting(c, online)[2]} == {"updating"}
        found = {f["check_id"]: f for f in health_monitor.findings(c, store.settings)}
    assert "queue_idle" not in found
    assert "UTC (1 min), waiting to update." in found["runner_outdated"]["detail"]
    update("updating")                                                  # the hold goes on into the update itself
    with store.read() as c:
        assert runner_versions.holding_since(c, r["runner_id"]) < H.shift(H.now(), seconds=-50)
    assert shown() == held
    # Its update failed: it claims again, and a retry waits a fresh drain time before it holds anything.
    update("failed")
    began(25)
    update("failed")
    assert shown() == queued
    update("waiting")
    assert shown() == queued
    report(api, r, "0.2.5", update={"state": "pinned", "target": "0.3.0", "error": "pinned"})
    assert shown() == queued
    # A Docker update still settling reports `updating` on the server's own release: it claims nothing.
    update("updating", release="0.3.0")
    assert shown() == held
    report(api, r, "0.3.0")                                             # settled: the label goes on its own
    assert shown() == queued
