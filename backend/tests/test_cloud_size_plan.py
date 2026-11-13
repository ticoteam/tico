"""Cloud machine sizes the plan can start (backend/cloud_machines.py, Engineering #241). Live case, 2026-11-12: the
Builder was set to XLarge, which Settings offered; the provider refused it on the company's plan
(machine_class_plan_required), and for 12+ minutes the machine read "Made, but it never connected to this server"
until a person picked Large and chose Replace machine. Now HQ's size list marks a size the plan cannot start, Settings
and the API refuse it with the reason, and a refusal for the plan falls back by itself to the largest size the plan
allows, telling whoever chose once. HQ is the fake client of backend/tests/helpers/cloud_machines.py."""

import json

from backend import cloud_machines as CM
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.cloud_machines import SIZES, adopt, cloud, employee, events, work  # noqa: F401

PLAN = CM.Refused("plan_required", 403)


def unavailable(*names):
    return [{**s, "available": False, "reason": "Needs a higher plan"} if s["name"] in names else s for s in SIZES]


def notices(cloud):
    with cloud.store.read() as c:
        return [r["body"] for r in c.execute("SELECT body FROM messages WHERE kind='notice' ORDER BY created_at")]


def offering(cloud, *names):
    """HQ marks these sizes unavailable for the company's plan; the next look reads it."""
    cloud.hq.sizes = unavailable(*names)
    cloud.manager.synced = None
    cloud.tick()


# ----------------------------------------------------------------------------- pure
def test_hq_marks_a_size_the_plan_cannot_start_and_an_older_hq_without_the_mark_offers_every_size():
    base = {"name": "xlarge", "cpu": 16, "memory_gb": 32, "usd_per_hour": 0.4, "idle_stop_s": 120}
    assert "available" not in CM.parse_size(base)
    assert "available" not in CM.parse_size({**base, "available": True})
    marked = CM.parse_size({**base, "available": False, "reason": "Needs a higher plan"})
    assert marked["available"] is False and marked["reason"] == "Needs a higher plan"
    assert CM.parse_size({**base, "available": False})["reason"] == "Needs a higher plan"
    assert CM.plan_refusal("plan_required") == "plan_required"
    assert CM.plan_refusal("machine_class_plan_required") == "plan_required"  # the provider's own word
    assert CM.plan_refusal("invalid_size") == "invalid_size"
    assert CM.plan_refusal("cpu_cap") is None and CM.plan_refusal("") is None and CM.plan_refusal(None) is None


def test_the_fallback_is_the_largest_allowed_size_below_or_one_step_down_without_marks():
    assert CM.fallback_size(unavailable("xlarge"), "xlarge") == "large"
    assert CM.fallback_size(unavailable("large", "xlarge"), "xlarge") == "default"
    assert CM.fallback_size(SIZES, "xlarge") == "large"  # no marks: one step per refusal
    assert CM.fallback_size(SIZES, "large") == "default"
    assert CM.fallback_size(SIZES, "small") is None  # the smallest: an ordinary failure
    assert CM.fallback_size(SIZES, "huge") is None
    assert CM.fallback_note(unavailable("xlarge"), "xlarge", "plan_required") == "XLarge needs a higher plan"
    assert CM.fallback_note(SIZES, "xlarge", "invalid_size") == "XLarge isn't available on this plan"


# ----------------------------------------------------------------------------- AC-1..3: offered, refused up front
def test_an_unavailable_size_is_listed_with_its_reason_and_the_api_refuses_it_for_a_bot_and_the_shared_machine(cloud):
    offering(cloud, "xlarge")
    listed = get(cloud.api, "cloud-machines")["sizes"]
    assert [(s["name"], s.get("available", True), s.get("reason")) for s in listed] == [
        ("small", True, None),
        ("default", True, None),
        ("large", True, None),
        ("xlarge", False, "Needs a higher plan"),
    ]
    adopt(cloud)
    refused = cloud.api.post("/api/v2/bots/ops/cloud-machine", json={"size": "xlarge"}, headers=headers())
    assert refused.status_code == 422
    error = refused.json()["error"]
    assert error["code"] == "size_unavailable" and "needs a higher plan" in error["detail"].lower()
    assert error["available"] == ["small", "default", "large"] and error["reason"] == "Needs a higher plan"
    assert cloud.row()["size"] == "default"  # unchanged
    assert post(cloud.api, "bots/ops/cloud-machine", {"size": "large"})["cloud_machine"]["size"] == "large"
    shared = cloud.api.post("/api/v2/cloud-machines/shared", json={"size": "xlarge"}, headers=headers())
    assert shared.status_code == 422 and shared.json()["error"]["code"] == "size_unavailable"
    assert cloud.row(CM.SHARED)["size"] == "default"


# ----------------------------------------------------------------------------- AC-4, AC-7: a create refused for the plan
def test_a_create_refused_for_the_plan_starts_at_once_at_the_largest_allowed_size_and_tells_the_owner_once(cloud):
    offering(cloud, "xlarge")
    adopt(cloud)
    with cloud.store.transaction() as c:  # set before HQ marked it, as the Builder was
        CM.save(c, "ops", size="xlarge")
    cloud.hq.fail["create"] = [PLAN]
    work(cloud)
    cloud.hq.calls.clear()
    cloud.tick()
    assert [call[2] for call in cloud.hq.calls if call[0] == "create"] == ["xlarge"]
    row = cloud.row()
    assert row["size"] == "large" and row["failures"] == 0 and row["next_try_at"] is None and row["problem"] == ""
    cloud.hq.calls.clear()
    cloud.tick(1)  # the next look, no backoff
    assert [call[2] for call in cloud.hq.calls if call[0] == "create"] == ["large"]
    assert cloud.row()["machine_id"]
    # One notice, naming the bot, the refused size, the reason and the size it runs at; the status line says the same.
    said = notices(cloud)
    assert len(said) == 1
    assert "couldn't start at XLarge (needs a higher plan), so it runs at Large" in said[0]
    shown = employee(cloud.api)["cloud_machine"]
    assert shown["grow_note"] == "XLarge needs a higher plan" and shown["problem"] == ""
    fell = events(cloud, "cloud_machine.size_fallback")
    assert [json.loads(e["detail"])["from"] for e in fell] == ["xlarge"]


def test_a_second_fallback_for_the_same_machine_and_size_within_a_day_sends_no_second_notice(cloud):
    adopt(cloud)
    for _ in range(2):
        with cloud.store.transaction() as c:
            CM.save(c, "ops", size="xlarge", machine_id=None, state="none")
        cloud.hq.fail["create"] = [PLAN]
        work(cloud)
        cloud.tick(60)
        assert cloud.row()["size"] == "large"
    assert len(notices(cloud)) == 1
    with cloud.store.transaction() as c:
        CM.save(c, "ops", size="xlarge", machine_id=None, state="none")
    cloud.hq.fail["create"] = [PLAN]
    cloud.tick(CM.FALLBACK_NOTICE_S)
    assert cloud.row()["size"] == "large" and len(notices(cloud)) == 2


# ----------------------------------------------------------------------------- AC-6: no marks, step down
def test_without_marks_each_refusal_steps_down_one_and_the_smallest_is_an_ordinary_failure(cloud):
    adopt(cloud)
    with cloud.store.transaction() as c:
        CM.save(c, "ops", size="large")
    cloud.hq.fail["create"] = [PLAN, PLAN, PLAN]
    work(cloud)
    cloud.tick()
    assert cloud.row()["size"] == "default"
    cloud.tick(1)
    assert cloud.row()["size"] == "small"
    cloud.tick(1)
    row = cloud.row()
    assert row["size"] == "small" and row["failures"] == 1 and row["problem"] == "hq:plan_required"
    shown = employee(cloud.api)["cloud_machine"]
    assert shown["problem"] == "Didn't start (plan_required): trying again"


# ----------------------------------------------------------------------------- AC-10: refused after HQ made the record
def test_a_machine_hq_made_and_then_failed_for_the_plan_never_reads_never_connected_and_falls_back(cloud):
    adopt(cloud)
    with cloud.store.transaction() as c:
        CM.save(c, "ops", size="xlarge")
    work(cloud)
    cloud.tick()
    mid = cloud.row()["machine_id"]
    assert cloud.hq.machines[mid]["size"] == "xlarge"
    # The stress run: HQ made the record, then the provider refused the class.
    cloud.hq.machines[mid].update(state="failed", last_error="machine_class_plan_required")
    cloud.manager.synced = None
    cloud.hq.calls.clear()
    cloud.tick(10)
    row = cloud.row()
    assert row["enroll_fails"] == 0 and row["problem"] != "no_enroll"
    assert row["size"] == "large" and row["grow_note"] == "XLarge needs a higher plan"
    cloud.tick(10)
    assert [call[2] for call in cloud.hq.calls if call[0] == "create"] == ["large"]
    for _ in range(CM.ENROLL_TRIES + 1):  # however many looks: never the stall's words
        cloud.tick(10)
        shown = employee(cloud.api)["cloud_machine"]
        assert "never connected" not in shown["problem"]
    assert len(notices(cloud)) == 1


# ----------------------------------------------------------------------------- AC-5, AC-8: a resize refused, then a change
def test_a_resize_refused_for_the_plan_keeps_its_size_tells_the_owner_and_a_new_choice_clears_the_note(cloud):
    from backend.tests.helpers.cloud_machines import enrolled, stopped_when_idle

    enrolled(cloud)
    cloud.tick(1)
    stopped_when_idle(cloud)
    with cloud.store.transaction() as c:
        CM.save(c, "ops", size="xlarge")
    cloud.hq.fail["start"] = [PLAN]
    work(cloud, text="Another thing")
    cloud.tick(1)
    row = cloud.row()
    assert row["size"] == "default" and row["grow_note"] == "XLarge needs a higher plan"
    said = notices(cloud)
    assert len(said) == 1 and "so it runs at Default" in said[0]
    post(cloud.api, "bots/ops/cloud-machine", {"size": "large"})
    row = cloud.row()
    assert row["size"] == "large" and row["grow_note"] == "" and row["grow_refused_size"] is None
