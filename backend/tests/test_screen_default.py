"""A new computer's Screen setting starts at Control the first time it offers a screen, unless a person already chose
one; a computer from before this default keeps what it had."""

import json

from backend.tests.test_api import api, get, post, runner  # noqa: F401  (the api fixture)


def heartbeat(api, r, offers):
    readiness = {"schema_version": 1, **({"screen": {"available": True, "width": 1440, "height": 900}} if offers else {})}
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capabilities": ["screen_v1"] if offers else [],
                                    "readiness": readiness}, token=r["token"])


def restart(api, r):
    """A runner restarting drops screen_v1 until its display is back up."""
    heartbeat(api, r, False)
    heartbeat(api, r, True)


def mode(api, rid):
    return next(m for m in get(api, "operations")["computers"] if m["id"] == rid)["screen"]


def test_a_new_computer_offering_its_screen_starts_at_control_and_a_persons_choice_stands(api):
    r = runner(api)
    rid = r["runner_id"]
    heartbeat(api, r, False)
    assert mode(api, rid) == "off"                      # no screen offered, nothing to view
    heartbeat(api, r, True)
    assert mode(api, rid) == "control"
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor,detail FROM events WHERE action='runner.screen' AND target=?",
                         (rid,)).fetchall()
    assert [(row["actor"], json.loads(row["detail"])) for row in rows] == [
        ("system", {"mode": "control", "reason": "first offer"})]
    post(api, f"computers/{rid}/screen", {"mode": "view"})
    restart(api, r)
    assert mode(api, rid) == "view"


def test_a_choice_made_before_the_first_offer_stands(api):
    r = runner(api)
    rid = r["runner_id"]
    heartbeat(api, r, False)
    post(api, f"computers/{rid}/screen", {"mode": "off"})     # Off is the one mode a computer with no screen takes
    heartbeat(api, r, True)
    assert mode(api, rid) == "off"


def test_a_computer_from_before_the_default_keeps_its_screen_off_through_restarts(api):
    r = runner(api)
    rid = r["runner_id"]
    with api.app.state.store.transaction() as c:      # what the column's default gives every existing row
        c.execute("UPDATE runners SET screen_default_pending=0 WHERE id=?", (rid,))
    heartbeat(api, r, True)
    restart(api, r)
    assert mode(api, rid) == "off"
