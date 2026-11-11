"""A person or a bot acts while the company's shared cloud machine is asleep: the action starts the machine, or says
what it waits for. Each test is one journey on a Tico Cloud company whose bots share one machine that has stopped when
idle, through the real routes and the machine manager's own look; only HQ is a fake (helpers/cloud_machines.py)."""

from backend import cloud_machines as CM
from backend.store import H
from backend.tests.helpers.api import headers, post, ready, settled
from backend.tests.helpers.cloud_machines import adopt, claim_on, cloud, idle, work  # noqa: F401


def asleep(cloud, *bots):
    """These bots on the shared machine: made for the first one's work, its runner enrolled and reporting them ready,
    then stopped when idle with its runner silent for an hour. Returns its computer (runner_id, token)."""
    for bot in bots:
        adopt(cloud, bot, "shared")
    work(cloud, bots[0])
    assert cloud.tick()[-1] == "create" and cloud.hq.calls[-1][1] == CM.SHARED
    computer = post(
        cloud.api, "runners/enroll", {"code": cloud.hq.calls[-1][3], "label": "boat-vm", "platform": "linux"}
    )
    cloud.tick()  # every member is assigned to it
    ready(cloud.api, computer, bots)
    idle(cloud)
    rest(cloud)
    with cloud.store.transaction() as c:
        c.execute(
            "UPDATE runner_states SET last_seen_at=? WHERE runner_id=?",
            (H.shift(H.now(), hours=-1), computer["runner_id"]),
        )
    return computer


def rest(cloud):
    """Its pause passes with nothing run: the machine stops."""
    cloud.hq.calls.clear()
    cloud.at[0] = cloud.row(CM.SHARED)["active_at"] + 400
    cloud.tick()
    assert cloud.hq.actions() == ["stop"] and cloud.row(CM.SHARED)["state"] == "stopped"
    cloud.hq.calls.clear()


def shown(cloud, bot):
    """What the bot's row says of its machine, as Team and the bot page read it."""
    found = next(e for e in cloud.api.get("/api/employees", headers=headers()).json() if e["name"] == bot)
    return found["cloud_machine"]["problem"]


def hold(cloud, bot, held):
    """The bot's queued work held, as a plan's limit holds it until the reset, or let go again."""
    with cloud.store.transaction() as c:
        c.execute("UPDATE jobs SET held=? WHERE bot_id=? AND state='queued'", (int(held), bot))


def test_one_bots_work_that_never_ran_does_not_keep_the_shared_machine_asleep_for_another_bots(cloud):
    asleep(cloud, "ops", "finance")
    work(cloud, "finance")
    hold(cloud, "finance", True)  # finance's work waits for its plan's reset
    cloud.at[0] += 5
    work(cloud, "ops")
    assert cloud.tick(10) == ["start"]
    rest(cloud)  # started for ops' work, and nothing ran there
    assert cloud.tick(60) == [] and cloud.row(CM.SHARED)["problem"] == "stuck"
    hold(cloud, "finance", False)  # the reset: finance's older work can run now
    assert cloud.tick(60) == ["start"], (
        "finance's work can run, but the shared machine stays asleep because ops' newer work was already woken for "
        f"once; finance's row says: {shown(cloud, 'finance')!r}"
    )
    assert cloud.row(CM.SHARED)["problem"] == "" and shown(cloud, "finance") == ""


def test_work_that_waited_at_a_wake_is_not_woken_for_again_one_by_one(cloud):
    asleep(cloud, "ops", "finance")
    work(cloud, "finance")
    cloud.at[0] += 5
    work(cloud, "ops")
    assert cloud.tick(10) == ["start"]
    rest(cloud)  # both waited at that wake and neither ran: one start is spent on both, not one each
    assert cloud.tick(60) == [] and cloud.tick(600) == [] and cloud.row(CM.SHARED)["problem"] == "stuck"


def test_two_bots_whose_wakes_were_spent_get_new_work_at_once_one_start_and_both_run(cloud):
    computer = asleep(cloud, "ops", "finance")
    work(cloud, "finance")
    cloud.at[0] += 5
    work(cloud, "ops")
    assert cloud.tick(10) == ["start"]
    rest(cloud)
    assert cloud.tick(60) == [] and cloud.row(CM.SHARED)["problem"] == "stuck"
    idle(cloud)
    cloud.at[0] += 5
    work(cloud, "finance", "A new question for finance.")
    work(cloud, "ops", "A new question for ops.")
    assert cloud.tick(10) == ["start"] and cloud.tick(10) == ["start"]  # one start for the two of them, in all
    ready(cloud.api, computer, ["ops", "finance"])  # its runner is back
    settled(cloud.api, computer)  # and has reported in since
    ran = {bot: claim_on(cloud, computer, bot)["attempt"] for bot in ("ops", "finance")}
    assert all(ran.values()), f"after one start both bots' new work runs; got {ran}"
    assert cloud.row(CM.SHARED)["problem"] == ""


def test_run_now_on_a_task_whose_run_already_waits_starts_the_machine_that_was_woken_for_it_once(cloud):
    asleep(cloud, "ops", "finance")
    task = post(cloud.api, "tasks", {"owner": "ops", "title": "Summarize", "body": "Please summarize the work."})
    assert cloud.tick(10) == ["start"]
    rest(cloud)  # started for the task, and it did not run there
    assert cloud.tick(60) == [] and shown(cloud, "ops") == "Started, but its work did not run there"
    answer = post(cloud.api, f"tasks/{task['id']}/run-now", {})
    assert answer["queued"] is False  # its run was already waiting: no second one
    assert cloud.tick(10) == ["start"], (
        "Run now answered yes and nothing started: the machine stays asleep and the task waits; "
        f"the bot's row says: {shown(cloud, 'ops')!r}"
    )
    assert shown(cloud, "ops") == ""
    # Run now on a machine that is held for another reason changes nothing there.
    rest(cloud)
    with cloud.store.transaction() as c:
        CM.save(c, CM.SHARED, problem="no_enroll")
    post(cloud.api, f"tasks/{task['id']}/run-now", {})
    assert cloud.row(CM.SHARED)["problem"] == "no_enroll" and cloud.row(CM.SHARED)["wake_key"]
