"""Renaming a computer (POST /api/v2/computers/{rid}/label): owners and admins, 1 to 100 characters once trimmed, and
nothing at enrollment puts the old name back: neither the same machine retrying its code nor a cloud machine made again.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture).
"""
from backend import cloud_machines as CM
from backend.tests.test_api import api, get, post, runner  # noqa: F401  (api is a fixture)
from backend.tests.test_cloud_machines import cloud, enrolled, work  # noqa: F401  (cloud is a fixture)


def label_of(api, rid):  # noqa: F811
    with api.app.state.store.read() as c:
        return c.execute("SELECT label FROM runners WHERE id=?", (rid,)).fetchone()["label"]


def rename(api, rid, label, token="ana-test", expected=200):  # noqa: F811
    return post(api, f"computers/{rid}/label", {"label": label}, token=token, expected=expected)


def test_an_owner_or_admin_renames_a_computer_and_a_member_cannot(api):  # noqa: F811
    machine = runner(api, label="Test Mac")
    rid = machine["runner_id"]
    assert rename(api, rid, "  Studio Mac  ") == {"label": "Studio Mac"}
    assert label_of(api, rid) == "Studio Mac"
    assert {m["id"]: m["label"] for m in get(api, "operations")["computers"]}[rid] == "Studio Mac"
    with api.app.state.store.read() as c:
        event = c.execute("SELECT actor,detail FROM events WHERE action='runner.renamed' AND target=?", (rid,)).fetchone()
    assert event["actor"] == "human:ana" and '"before": "Test Mac"' in event["detail"]
    # The old spelling still answers; an admin may rename too.
    post(api, f"runners/{rid}/label", {"label": "Build box"}, token="ben-test")
    assert label_of(api, rid) == "Build box"
    refused = rename(api, rid, "Cara's now", token="cara-test", expected=403)
    assert refused["error"]["code"] == "forbidden" and label_of(api, rid) == "Build box"
    # Its next heartbeat keeps the name.
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capacity": 1, "readiness": {},
                                         "capabilities": []},
         token=machine["token"])
    assert label_of(api, rid) == "Build box"
    rename(api, "no-such-computer", "x", expected=404)
    post(api, f"computers/{rid}/revoke", {})
    rename(api, rid, "Gone", expected=404)


def test_a_name_must_be_1_to_100_characters_on_one_line(api):  # noqa: F811
    rid = runner(api)["runner_id"]
    for bad in ("", "   ", "x" * 101, "two\nlines", 7, None):
        rename(api, rid, bad, expected=422)
    post(api, f"computers/{rid}/label", {}, expected=422)
    assert label_of(api, rid) == "Test Mac"
    assert rename(api, rid, "y" * 100) == {"label": "y" * 100}


def test_the_same_machine_retrying_its_enrollment_after_a_rename_keeps_the_new_name(api):  # noqa: F811
    code = post(api, "enrollments", {"operator": "ana"})["code"]
    first = post(api, "runners/enroll", {"code": code, "label": "Test Mac", "platform": "test"})
    rename(api, first["runner_id"], "Studio Mac")
    again = post(api, "runners/enroll", {"code": code, "label": "Test Mac", "platform": "test"})
    assert again == first and label_of(api, first["runner_id"]) == "Studio Mac"
    rename(api, first["runner_id"], "Studio Mac 2")         # a second rename still knows the name it enrolled with
    assert post(api, "runners/enroll", {"code": code, "label": "Test Mac", "platform": "test"}) == first
    # Another machine with the same code is still refused, under either name.
    post(api, "runners/enroll", {"code": code, "label": "Other Mac", "platform": "test"}, expected=409)
    assert label_of(api, first["runner_id"]) == "Studio Mac 2"


def test_a_renamed_cloud_machine_keeps_its_name_when_it_enrolls_again_or_is_made_again(cloud):  # noqa: F811
    first = enrolled(cloud)
    assert label_of(cloud.api, first["runner_id"]) == "ops (cloud)"
    rename(cloud.api, first["runner_id"], "Builder box")
    with cloud.store.read() as c:
        assert CM.label_for(c, "ops") == "Builder box"
    # A retried enroll with the same code (lost answer) returns the computer as it is.
    code = cloud.hq.calls[[call[0] for call in cloud.hq.calls].index("create")][3]
    again = post(cloud.api, "runners/enroll", {"code": code, "label": "boat-vm", "platform": "linux"})
    assert again["runner_id"] == first["runner_id"] and label_of(cloud.api, first["runner_id"]) == "Builder box"
    # HQ lost the machine: it is made again, and the new computer enrolls under the name a person gave it.
    cloud.tick(400)
    work(cloud, text="More")
    cloud.hq.fail["start"] = [CM.Refused("not_found", 404)]
    cloud.hq.machines["m1"]["state"] = "deleted"
    cloud.hq.calls.clear()
    assert cloud.tick(10) == ["start"]
    assert cloud.tick(10) == ["start", "create"]
    second = post(cloud.api, "runners/enroll", {"code": cloud.hq.calls[-1][3], "label": "boat-vm", "platform": "linux"})
    assert second["runner_id"] != first["runner_id"]
    assert label_of(cloud.api, second["runner_id"]) == "Builder box"
    # Other machines keep their own names.
    with cloud.store.read() as c:
        assert CM.label_for(c, "cpo").endswith(" (cloud)") and CM.label_for(c, CM.SHARED) == "Shared cloud machine"
