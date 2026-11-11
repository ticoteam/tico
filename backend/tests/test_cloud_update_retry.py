"""A company is updated while its cloud machine sleeps, and the machine's runner fails to start its update.

The runner tries a failed update again six hours later, but only while it runs. The server woke a stopped machine once
per release to update, so a machine whose one try failed stayed on the old runner for as long as it had no work, with
nothing on Health: a runner fix in the new release never reached it. HQ is a fake and the clock is the manager's."""

from backend import cloud_machines as CM
from backend.store import H
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.cloud_machines import cloud, enrolled, stopped_when_idle  # noqa: F401

ERROR = "could not start the update: systemd-run exited 1"


def health(cloud):
    checks = cloud.api.get("/api/v2/health", headers=headers("ana-test")).json()["checks"]
    return " ".join(check["summary"] for check in checks if check["status"] != "ok")


def test_a_stopped_machine_whose_update_failed_is_on_health_and_starts_once_more_to_try_again(cloud, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.6.0-rc.8")
    computer = enrolled(cloud)
    body = {"version": "test", "platform": "test", "release": "0.6.0-rc.7", "kind": "linux"}
    body["update"] = {"state": "failed", "target": "0.6.0-rc.8", "error": ERROR}
    post(cloud.api, "runners/heartbeat", body, token=computer["token"])  # its last word before it went quiet
    stopped_when_idle(cloud)
    with cloud.store.transaction() as c:
        c.execute(
            "UPDATE runner_states SET last_seen_at=? WHERE runner_id=?",
            (H.shift(H.now(), hours=-1), computer["runner_id"]),
        )
    said = health(cloud)
    assert "still on Tico 0.6.0-rc.7" in said and "0.6.0-rc.8 failed" in said and ERROR in said, (
        f"a cloud machine left on the old runner by a failed update is not on Health: {said!r}"
    )
    assert cloud.tick(600) == []  # its runner would not try again yet: a start now is spent on nothing
    assert cloud.tick(CM.UPDATE_RETRY) == ["start"], (
        "six hours after its update failed, a stopped cloud machine with no work is never started to try again: "
        "it stays on the old runner"
    )
    assert cloud.row()["wake_key"] == "update:0.6.0-rc.8:again"
    cloud.hq.calls.clear()
    cloud.at[0] = cloud.row()["active_at"] + 300
    assert cloud.tick() == ["stop"]  # it failed again, or said nothing
    cloud.hq.calls.clear()
    assert cloud.tick(2 * CM.UPDATE_RETRY) == [] and cloud.row()["problem"] == ""  # once more, not every six hours
    body["release"], body["update"] = "0.6.0-rc.8", {"state": "idle", "target": ""}
    post(cloud.api, "runners/heartbeat", body, token=computer["token"])
    assert "still on Tico" not in health(cloud)
