"""Runner versions: comparison, the range a server accepts, claim gating and what Health and Computers show."""

import pytest

from backend import releases, runner_online, runner_versions
from backend.tests.helpers.api import assign, get, post, ready, runner  # noqa: F401


@pytest.fixture(autouse=True)
def server_release(monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.3.0")
    monkeypatch.setattr(runner_versions, "MIN_RUNNER_RELEASE", "0.2.0")
    monkeypatch.setattr(releases, "CHECKER", releases.Checker())


def report(api, r, release, kind="mac", update=None, **extra):
    body = {
        "version": "test",
        "platform": "test",
        "readiness": {"ops": True},
        "release": release,
        "kind": kind,
        **extra,
    }
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
    assert state("0.3.0") == "current" and state("0.4.0") == "current"  # a newer runner still works
    assert state("0.2.5") == "needs_update" and state("0.2.0") == "needs_update"
    assert state("0.1.9") == "incompatible" and state("0.1.9", "updating") == "incompatible"
    assert state("0.2.5", "updating") == "updating"
    assert state("dev") == "unknown" and state("") == "unknown"  # not on a release: never paused
    assert state("0.3.0-rc.1") == "needs_update"  # a release outranks its candidates
    assert state("0.2.5", server="dev") == "unknown"
    assert not runner_versions.incompatible("dev") and not runner_versions.incompatible("")


def test_an_incompatible_runner_does_not_claim(api):
    r = queue_work(api)
    report(api, r, "0.1.0")
    reply = post(api, "jobs/claim", {"bot": None}, token=r["token"])
    assert reply["attempt"] is None and "0.2.0 or later" in reply["paused"] and "paused" in reply["paused"]
    report(api, r, "0.2.1")  # it updated
    assert post(api, "jobs/claim", {"bot": None}, token=r["token"])["attempt"]["bot"] == "ops"


def test_outdated_is_behind_the_servers_release_or_unknown():
    outdated = runner_versions.outdated
    assert outdated("0.2.9") and outdated("0.3.0-rc.1")  # older
    assert not outdated("0.3.0") and not outdated("0.4.0")  # equal or newer
    assert outdated("dev") and outdated("")  # unknown
    assert not outdated("dev", server="dev") and not outdated(
        "0.1.0", server="dev"
    )  # a dev server has nothing to compare


def test_heartbeat_stores_release_and_commit_and_nudges_the_owner_once_per_server_release(api, monkeypatch):
    r = runner(api, operator="ben", label="Ben's Mac")
    commit = "a" * 40

    def notices():
        with api.app.state.store.read() as c:
            return [
                row["body"]
                for row in c.execute(
                    "SELECT body FROM messages WHERE recipient_id='human:ben' AND kind='notice' ORDER BY created_at"
                )
            ]

    report(api, r, "0.3.0", commit=commit)
    machine = next(m for m in get(api, "operations", token="ben-test")["computers"] if m["id"] == r["runner_id"])
    assert machine["update"]["release"] == "0.3.0" and machine["update"]["commit"] == commit
    assert machine["update"]["outdated"] is False and notices() == []

    report(api, r, "0.2.5", commit=commit)
    assert notices() == []  # it gets time to update itself first
    monkeypatch.setattr(runner_versions, "NUDGE_AFTER_S", 0)
    report(api, r, "0.2.5", commit=commit)
    report(api, r, "0.2.5", commit=commit)  # every heartbeat after: no repeat
    assert len(notices()) == 1 and "Ben's Mac" in notices()[0] and "0.3.0" in notices()[0]
    machine = next(m for m in get(api, "operations", token="ben-test")["computers"] if m["id"] == r["runner_id"])
    assert machine["update"]["outdated"] is True and machine["update"]["server_release"] == "0.3.0"

    monkeypatch.setenv("TICO_VERSION", "0.4.0")  # a new server release: one more nudge
    report(api, r, "0.2.5")
    report(api, r, "0.2.5")
    assert len(notices()) == 2 and "0.4.0" in notices()[1]


def ben_notices(api):
    with api.app.state.store.read() as c:
        return [
            row["body"]
            for row in c.execute(
                "SELECT body FROM messages WHERE recipient_id='human:ben' AND kind='notice' ORDER BY created_at"
            )
        ]


def behind_for_two_hours(api, r):
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE runner_versions SET outdated_since=? WHERE runner_id=?",
            (runner_versions.H.shift(runner_versions.H.now(), seconds=-7200), r["runner_id"]),
        )


def test_a_new_server_release_restarts_the_wait_before_a_nudge(api, monkeypatch):
    r = runner(api, operator="ben", label="Ben's Mac")
    report(api, r, "0.2.5")
    behind_for_two_hours(api, r)
    report(api, r, "0.2.5")
    assert len(ben_notices(api)) == 1
    monkeypatch.setenv("TICO_VERSION", "0.4.0")  # it gets its hour to take this one by itself too
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


def test_repeated_update_deferrals_show_the_blocked_cause_in_health_and_release_the_hold(api, tmp_path, monkeypatch):
    from runner_support import release_update as ru

    r = queue_work(api)
    report(api, r, "0.2.5", update={"state": "updating", "target": "0.3.0", "error": ""})
    monkeypatch.setenv("TICO_UPDATE_RETRY", "short")
    reason = "the runner binary is not published yet"
    for _ in range(ru.MAX_DEFERRALS):
        outcome = ru.defer_update(tmp_path, "0.3.0", reason)
        report(api, r, "0.2.5", update=outcome)
        with api.app.state.store.read() as c:
            assert not runner_versions.holding_since(c, r["runner_id"])
    body = get(api, "health")
    machine = next(m for m in body["computers"] if m["id"] == r["runner_id"])
    assert machine["update"]["update_state"] == "blocked"
    assert machine["update"]["error"] == outcome["error"] and reason in outcome["error"]
    check = next(c for c in body["checks"] if c["id"] == "runners")
    assert check["status"] == "warn" and outcome["error"] in check["summary"]
    assert post(api, "jobs/claim", {"bot": None}, token=r["token"])["attempt"]["bot"] == "ops"


def test_transient_update_retries_never_report_a_hold_or_blocked_in_health(api, tmp_path, monkeypatch):
    from runner_support import release_update as ru

    monkeypatch.setenv("TICO_UPDATE_RETRY", "short")
    r = queue_work(api)
    for reason in ("active work", "checkout lock busy", "Git gate busy") * 3:
        outcome = ru.defer_update(tmp_path, "0.3.0", reason, transient=True)
        report(api, r, "0.2.5", update=outcome)
        with api.app.state.store.read() as c:
            assert not runner_versions.holding_since(c, r["runner_id"])
        machine = next(m for m in get(api, "health")["computers"] if m["id"] == r["runner_id"])
        assert machine["update"]["update_state"] == "idle"
    assert post(api, "jobs/claim", {"bot": None}, token=r["token"])["attempt"]["bot"] == "ops"


def test_queued_work_says_it_waits_for_the_computer_to_update_only_once_the_computer_stops_taking_work(api):
    """A computer due an update keeps claiming for 20 minutes, then takes no new work until its running turns end.
    Chat and task said only "Queued" for that hold; now they say why, and only while it lasts."""
    from backend import health, maintenance_health
    from backend.store import H

    r = runner(api, label="Team box")
    assign(api, r, "ops")
    chat = post(api, "chat/ops", {"text": "Are you there?"})
    task = post(api, "tasks", {"owner": "ops", "title": "Check the invoices", "body": "Today."})
    store = api.app.state.store
    queued, held = (
        ("Saved — queued", "Queued"),
        ("Saved — waiting for the computer to update", "Waiting for the computer to update"),
    )

    def shown():
        label = get(api, f"conversations/{chat['conversation_id']}/snapshot")["execution"]["label"]
        return label, get(api, f"tasks/{task['id']}")["waiting_for"]

    def update(state, target="0.3.0", release="0.2.5"):
        report(api, r, release, update={"state": state, "target": target, "error": ""})

    def began(minutes):  # the reported state began this long ago
        with store.transaction() as c:
            c.execute(
                "UPDATE runner_versions SET update_since=? WHERE runner_id=?",
                (H.shift(H.now(), minutes=-minutes), r["runner_id"]),
            )

    report(api, r, "0.3.0")
    assert shown() == queued  # claiming as usual
    update("waiting")
    began(19)
    update("waiting")
    assert shown() == queued  # due an update, still claiming
    began(21)
    update("waiting", target="0.3.1")  # a newer target keeps the drain's start
    assert shown() == held
    # Health names the hold and how long it has run; it is still not a queue_idle Tico bug (PR 623).
    with store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE bot_id='ops'", (H.shift(H.now(), minutes=-30),))
        online = {x["id"] for x in runner_online.online_rows(c)}
        assert {row["reason"] for row in health._waiting(c, online)[2]} == {"updating"}
        found = {f["check_id"]: f for f in maintenance_health.findings(c, store.settings)}
    assert "queue_idle" not in found
    assert "UTC (1 min), waiting to update." in found["runner_outdated"]["detail"]
    update("updating")  # the hold goes on into the update itself
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
    report(api, r, "0.3.0")  # settled: the label goes on its own
    assert shown() == queued


def check_health(api):
    """One Check health pass (check_health.run_once): its result and the findings it left stored, present or not."""
    from backend.maintenance_steps import check_health as step

    store = api.app.state.store
    result = step.run_once(store, api.app.state.auth, store.settings)
    with store.read() as c:
        stored = {r["key"]: dict(r) for r in c.execute("SELECT * FROM health_findings")}
    return result, stored


def present(stored, runner_id=""):
    return sorted(k for k, f in stored.items() if f["present"] and runner_id in k)


def ago(api, table, column, minutes, runner_id):
    from backend.store import H

    with api.app.state.store.transaction() as c:
        c.execute(f"UPDATE {table} SET {column}=? WHERE runner_id=?", (H.shift(H.now(), minutes=-minutes), runner_id))


def test_a_healthy_update_moving_from_its_drain_to_the_switch_files_nothing(api):
    """A drain behind a turn that worked during the hold, then the switch: the switch keeps the hold's start for
    Health's duration, but it is a move, and an event from a turn that has since finished is one too."""
    from backend.store import H

    r = queue_work(api)
    aid = post(api, "jobs/claim", {"bot": None}, token=r["token"])["attempt"]["id"]
    report(api, r, "0.2.5", update={"state": "waiting", "target": "0.3.0", "error": "", "running": 1})
    for column in ("update_since", "update_changed_at"):
        ago(api, "runner_versions", column, 20, r["runner_id"])
    with api.app.state.store.transaction() as c:  # the turn wrote a minute ago, then finished
        c.execute(
            "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,1,'tool','{}',?)",
            (aid, H.shift(H.now(), minutes=-1)),
        )
        c.execute("UPDATE attempts SET state='completed',finished_at=? WHERE id=?", (H.now(), aid))
        assert runner_versions.stuck(c) == {}
    report(api, r, "0.2.5", update={"state": "updating", "target": "0.3.0", "error": ""})
    with api.app.state.store.read_transaction() as c:
        row = c.execute("SELECT update_since FROM runner_versions WHERE runner_id=?", (r["runner_id"],)).fetchone()
        assert row["update_since"] < H.shift(H.now(), minutes=-19)  # the hold's start, kept
    result, stored = check_health(api)
    assert not [k for k in result["details"]["filed"] if k.startswith("update_stuck")]
    assert not [k for k in stored if k.startswith("update_stuck")]


def test_a_hung_docker_updater_that_keeps_heartbeating_is_one_finding(api):
    """The sidecar stopped moving while the runner kept its heartbeat: one finding, filed on its first pass, with the
    queued work assigned to the computer and the Docker hold recovery; no other finding says it again. A row from
    before update_changed_at counts an unchanged report from its state's start; a revoked computer is never stuck."""
    r = queue_work(api)
    report(api, r, "0.2.5", kind="docker", update={"state": "updating", "target": "0.3.0", "error": ""})
    ago(api, "runner_versions", "update_since", 16, r["runner_id"])  # it switched 16 minutes ago
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_versions SET update_changed_at=NULL WHERE runner_id=?", (r["runner_id"],))
    report(api, r, "0.2.5", kind="docker", update={"state": "updating", "target": "0.3.0", "error": ""})
    with api.app.state.store.transaction() as c:  # its queued work is old enough for the queue check
        c.execute(
            "UPDATE jobs SET created_at=? WHERE bot_id='ops'",
            (runner_versions.H.shift(runner_versions.H.now(), minutes=-30),),
        )
    result, stored = check_health(api)
    key = "update_stuck:" + r["runner_id"]
    assert key in result["details"]["filed"] and present(stored, r["runner_id"]) == [key]
    # The whole fleet is stuck: no "No computer is set up", no queue, no version line; its own finding speaks for it.
    fleet = ("page:computers", "page:waiting", "page:queue", "page:runners", "page:cloud")
    assert not [k for k in present(stored) if k.split(":", 2)[0] + ":" + k.split(":", 2)[1] in fleet]
    finding = stored[key]
    assert (
        finding["severity"] == "bad"
        and finding["title"] == "Test Mac's update has made no progress for at least 15 min"
    )
    assert "1 queued job(s) are assigned to this computer (ops)." in finding["detail"]
    assert "`docker compose -f runner.compose.yaml logs updater`" in finding["fix"]
    assert "end the hold by hand as docs/updates.md says (A Docker runner)" in finding["fix"]
    report(api, r, "0.2.6", kind="docker", update={"state": "updating", "target": "0.3.0", "error": ""})
    with api.app.state.store.read_transaction() as c:  # a new release reported is progress: the hold goes on
        assert (
            runner_versions.stuck(c) == {}
            and runner_versions.holding_since(c, r["runner_id"]) < finding["first_seen_at"]
        )
    ago(api, "runner_versions", "update_changed_at", 16, r["runner_id"])
    with api.app.state.store.transaction() as c:
        assert set(runner_versions.stuck(c)) == {r["runner_id"]}
        c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (finding["last_seen_at"], r["runner_id"]))
        assert runner_versions.stuck(c) == {}


def test_a_stuck_computer_is_its_own_finding_while_the_fleet_aggregates_go_on_without_it(api):
    """A's update is stuck and B is offline: A's update_stuck, and an aggregate about B alone (A is in neither its count
    nor its total). B comes back: the aggregate clears and A's finding stays. A moves on: its finding clears."""
    a, b = runner(api, label="Mac A"), runner(api, label="Mac B")
    report(api, b, "0.3.0")
    ago(api, "runner_states", "last_seen_at", 30, b["runner_id"])
    report(api, a, "0.2.5", update={"state": "updating", "target": "0.3.0", "error": ""})
    for column in ("update_since", "update_changed_at"):
        ago(api, "runner_versions", column, 16, a["runner_id"])
    check_health(api)
    result, stored = check_health(api)  # the aggregate files on its second pass
    assert present(stored, a["runner_id"]) == ["update_stuck:" + a["runner_id"]]
    assert stored["page:computers"]["present"] and stored["page:computers"]["detail"] == "Every computer is offline."
    report(api, b, "0.3.0")  # B recovers
    result, stored = check_health(api)
    assert "page:computers" in result["details"]["cleared"] and not stored["page:computers"]["present"]
    assert present(stored, a["runner_id"]) == ["update_stuck:" + a["runner_id"]]
    report(api, a, "0.3.0")  # A updated
    result, stored = check_health(api)
    assert "update_stuck:" + a["runner_id"] in result["details"]["cleared"] and not present(stored, a["runner_id"])


def test_a_drain_behind_a_turn_that_stopped_moving_points_at_the_turn(api):
    """A turn still runs but nothing moved for 15 minutes: the finding asks for a look at the stalled run, not patience."""
    r = queue_work(api)
    post(api, "jobs/claim", {"bot": None}, token=r["token"])
    report(api, r, "0.2.5", update={"state": "waiting", "target": "0.3.0", "error": "", "running": 1})
    for column in ("update_since", "update_changed_at"):
        ago(api, "runner_versions", column, 16, r["runner_id"])
    result, stored = check_health(api)
    fix = stored["update_stuck:" + r["runner_id"]]["fix"]
    assert "turns still run on Test Mac but nothing has moved" in fix and "Nothing to do" not in fix
    report(api, r, "0.2.5", update={"state": "waiting", "target": "0.3.0", "error": "", "running": 0})
    ago(api, "runner_versions", "update_since", 16, r["runner_id"])
    ago(api, "runner_versions", "update_changed_at", 16, r["runner_id"])
    _, stored = check_health(api)  # the runner says none runs: its 0 stands over the server's live count
    assert "turn(s) still run" not in stored["update_stuck:" + r["runner_id"]]["fix"]


def test_a_stuck_updates_way_out_changing_wakes_its_task_once(api):
    """Its drain waits on a turn that still works (wait), then the turn stops moving (look at it): the same Agent Ops
    task hears the new way out, once, and is woken through the batched wake."""
    from backend import bot_settings as BS
    from backend.store import H

    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES('botops','botops','Agent Ops','active')")
        c.execute("INSERT INTO bot_config(bot,owner_id) VALUES('botops','ana')")
        BS.write(c, "botops", {"name": "botops", "runtime": "fake", "status": "active"})
    r = queue_work(api)
    aid = post(api, "jobs/claim", {"bot": None}, token=r["token"])["attempt"]["id"]
    report(api, r, "0.2.5", update={"state": "waiting", "target": "0.3.0", "error": "", "running": 1})
    for column in ("update_since", "update_changed_at"):
        ago(api, "runner_versions", column, 46, r["runner_id"])

    def wrote(minutes):  # the turn's event, and the change every event writes, that long ago
        at = H.shift(H.now(), minutes=-minutes)
        with api.app.state.store.transaction() as c:
            c.execute(
                "INSERT OR REPLACE INTO attempt_events(attempt_id,seq,kind,payload,created_at) "
                "VALUES(?,1,'tool','{}',?)",
                (aid, at),
            )
            c.execute("UPDATE changes SET created_at=? WHERE topic='runs' AND subject_id=?", (at, aid))

    def said(task_id):
        with api.app.state.store.read() as c:
            return [
                row[0]
                for row in c.execute(
                    "SELECT m.body FROM messages m JOIN tasks t ON t.conversation_id=m.conversation_id "
                    "WHERE t.id=? AND m.body LIKE 'Now:%'",
                    (task_id,),
                )
            ]

    wrote(1)
    first, stored = check_health(api)
    key = "update_stuck:" + r["runner_id"]
    assert stored[key]["fix"].startswith("Nothing to do by hand") and first["woke"] == 1
    (task,) = first["details"]["tasks"].values()
    wrote(16)
    worse, stored = check_health(api)
    assert stored[key]["fix"].startswith("Agent Ops: turns still run")
    # Its wake goes through the batched wake: out now, or waiting for the wake cap to allow it.
    assert task in worse["details"].get("woke_tasks", []) + worse["details"].get("wake_waiting", [])
    (note,) = said(task)
    assert "Fix:\nAgent Ops: turns still run on Test Mac but nothing has moved" in note
    check_health(api)
    assert len(said(task)) == 1  # said once
    report(api, r, "0.3.0")  # it updated: the finding clears
    assert key in check_health(api)[0]["details"]["cleared"]
    report(api, r, "0.3.0", update={"state": "updating", "target": "0.3.1", "error": ""})
    for column in ("update_since", "update_changed_at"):
        ago(api, "runner_versions", column, 16, r["runner_id"])
    check_health(api)  # stuck again: the same task hears it is back, with the fix it has now
    with api.app.state.store.read() as c:
        (back,) = [
            row[0]
            for row in c.execute(
                "SELECT m.body FROM messages m JOIN tasks t ON t.conversation_id=m.conversation_id "
                "WHERE t.id=? AND m.body LIKE 'Back:%'",
                (task,),
            )
        ]
    assert "\n\nFix:\n" in back


def test_an_offline_aggregate_that_named_a_now_stuck_computer_clears(api):
    """The Computers aggregate named A and B. B is silent with work queued, so the aggregate would be held for it, but A's
    update is now stuck: the aggregate's text names a computer it no longer speaks for, so it clears."""
    a, b = runner(api, label="Mac A"), runner(api, label="Mac B")
    assign(api, b, "ops")
    for r in (a, b):
        report(api, r, "0.3.0")
        ago(api, "runner_states", "last_seen_at", 30, r["runner_id"])
    check_health(api)
    result, stored = check_health(api)
    assert stored["page:computers"]["present"]
    post(api, "chat/ops", {"text": "Work for B"})  # B is silent now: computer_silent speaks for it
    report(api, a, "0.2.5", update={"state": "updating", "target": "0.3.0", "error": ""})
    for column in ("update_since", "update_changed_at"):
        ago(api, "runner_versions", column, 16, a["runner_id"])
    result, stored = check_health(api)
    assert "page:computers" in result["details"]["cleared"] and "page:computers" not in result["details"]["held"]
    assert present(stored, a["runner_id"]) == ["update_stuck:" + a["runner_id"]]


def test_a_stuck_computers_full_disk_still_shows(api):
    """Only the checks update_stuck speaks for leave a stuck computer out: its full disk is still a Disk space line."""
    import json

    r = runner(api, label="Team box")
    report(api, r, "0.2.5", update={"state": "updating", "target": "0.3.0", "error": ""})
    for column in ("update_since", "update_changed_at"):
        ago(api, "runner_versions", column, 16, r["runner_id"])
    gb = 1024**3
    readiness = {
        "schema_version": 1,
        "runtimes": {},
        "bots": {},
        "disk": {"total_bytes": 100 * gb, "free_bytes": 2 * gb},
    }
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET readiness=? WHERE runner_id=?", (json.dumps(readiness), r["runner_id"]))
    _, stored = check_health(api)
    assert "update_stuck:" + r["runner_id"] in present(stored)
    assert "page:disk:" + r["runner_id"] in present(stored)
