"""A computer's and a bot's "Turns at once": the claim holds new turns at the limit, and says so."""

from backend import start_places
from backend.store import H
from backend.tests.helpers.api import as_member, claim, get, headers, post, put, runner  # noqa: F401  (api is a fixture)
from backend.tests.helpers.assistant import assistant_turn
from backend.tests.helpers.settings_assistant import mcp
from backend.tests.helpers.task_lanes import new_task, task_bot
from backend.tests.helpers.turns_at_once import set_computer

NEW_RUNNER = ["task_lanes_v2", "turns_at_once_v1"]


def set_bot(api, limit, expected=200, token="ana-test"):
    revision = get(api, "bots/ops")["revision"]
    return post(
        api,
        "bots/ops/turns-at-once",
        {"turns_at_once": limit, "expected_revision": revision},
        token=token,
        expected=expected,
    )


def heartbeat(api, machine, capacity, capabilities):
    return post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capacity": capacity,
            "readiness": {"ops": True},
            "capabilities": capabilities,
        },
        token=machine["token"],
    )


def test_a_lowered_computer_limit_holds_new_turns_at_once_and_clearing_it_restores_the_capacity(api):
    machine = task_bot(api, lanes=True, capacity=4)
    for title in ("first", "second", "third"):
        new_task(api, title)
    assert claim(api, machine) and claim(api, machine)
    assert set_computer(api, machine, 1)["turn_limit"] == 1
    # The server holds the third turn at once: twice the lowered limit (2) is already claimed, and the runner
    # has not heard of the lower limit yet.
    assert claim(api, machine) is None
    assert set_computer(api, machine, None)["turn_limit"] == 4
    assert claim(api, machine)["bot"] == "ops"


def test_a_raised_computer_limit_admits_more_turns_on_a_runner_that_takes_it(api):
    machine = task_bot(api, lanes=True, capacity=2)
    heartbeat(api, machine, 2, NEW_RUNNER)
    for title in ("first", "second", "third", "fourth", "fifth", "sixth"):
        new_task(api, title)
    assert set_computer(api, machine, 3)["turn_limit"] == 3
    # The runner learns the limit in its heartbeat reply, which is what it gates its own starts on.
    assert heartbeat(api, machine, 2, NEW_RUNNER)["turns_at_once"] == 3
    # Twice the raised limit (6) may be claimed; the seventh waits.
    assert [claim(api, machine) is not None for _ in range(6)] == [True] * 6
    assert claim(api, machine) is None


def test_a_runner_that_cannot_take_a_raised_limit_runs_at_the_capacity_it_reports(api):
    machine = task_bot(api, lanes=True, capacity=2)
    for title in ("first", "second", "third", "fourth", "fifth"):
        new_task(api, title)
    assert set_computer(api, machine, 5)["turn_limit"] == 2
    # Twice the reported capacity (2) may be claimed; the fifth waits.
    assert all(claim(api, machine) for _ in range(4))
    assert claim(api, machine) is None


def test_a_bot_at_its_limit_holds_its_work_while_another_bot_on_the_computer_runs(api):
    machine = task_bot(api, lanes=True, capacity=4, others=("finance",))
    ops_tasks = [new_task(api, "first"), new_task(api, "second")]
    new_task(api, "review", owner="finance")
    assert set_bot(api, 1)["turns_at_once"] == 1
    assert claim(api, machine)["bot"] == "ops"
    # ops holds its one turn: its next work waits and says so, and finance's work still starts.
    assert claim(api, machine)["bot"] == "finance"
    assert claim(api, machine) is None
    assert get(api, "tasks/" + ops_tasks[1]["id"])["waiting_for"] == "At its turn limit"
    assert get(api, "bots/ops")["turns_at_once"] == 1


def test_clearing_the_bot_limit_lets_its_queued_work_start_again(api):
    machine = task_bot(api, lanes=True, capacity=4)
    new_task(api, "first")
    new_task(api, "second")
    set_bot(api, 1)
    assert claim(api, machine) is not None
    assert claim(api, machine) is None
    set_bot(api, None)
    assert claim(api, machine) is not None


def test_only_a_bot_manager_or_an_admin_sets_the_limits_and_the_bounds_hold(api):
    machine = task_bot(api, lanes=True)
    as_member(api, "ben@acme.example")
    set_bot(api, 3, expected=403, token="ben-test")
    post(api, f"computers/{machine['runner_id']}/turns-at-once", {"turns_at_once": 3}, token="ben-test", expected=403)
    for bad in (0, 257, -1, "3", True, 1.5):
        set_bot(api, bad, expected=422)
        post(api, f"computers/{machine['runner_id']}/turns-at-once", {"turns_at_once": bad}, expected=422)


def test_the_bot_limit_is_in_the_settings_history_and_undo_restores_the_previous_value(api):
    set_bot(api, 2)
    change = next(row for row in get(api, "settings/history")["changes"] if row["field"] == "turns_at_once")
    post(api, "settings/history/" + change["id"] + "/undo", {"expected_revision": get(api, "bots/ops")["revision"]})
    assert get(api, "bots/ops")["turns_at_once"] is None


def test_health_names_a_bot_at_its_limit_by_its_reason_and_says_so(api):
    machine = task_bot(api, lanes=True, capacity=4)
    new_task(api, "first")
    new_task(api, "second")
    set_bot(api, 1)
    assert claim(api, machine) is not None
    # Health lists work only once it has waited a while (health.QUEUE_MINUTES), like any held work.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE state='queued'", (H.shift(H.now(), minutes=-30),))
    stuck = [row for row in get(api, "health")["stuck"] if row.get("bot") == "ops"]
    assert [row["reason"] for row in stuck] == ["bot_limit"], stuck
    assert "At its turn limit" in stuck[0]["why"]


def test_a_parked_turn_still_holds_its_bots_turn(api):
    """A parked turn is one of its bot's turns: the claim does not start the bot's next task on another lane beside
    it, and the parked turn's own start, once its wait clears, takes the bot's one place, not a second one."""
    machine = task_bot(api, lanes=True, capacity=4)
    new_task(api, "first")
    new_task(api, "second")
    assert set_bot(api, 1)["turns_at_once"] == 1
    parked = claim(api, machine)
    assert parked is not None and parked["bot"] == "ops"
    post(api, f"attempts/{parked['id']}/start-place", {"waiting": "pre-start requirements"}, machine["token"])
    assert claim(api, machine) is None
    assert post(api, f"attempts/{parked['id']}/start-place", {}, machine["token"])["reserved"] is True
    assert claim(api, machine) is None


def test_a_parked_turn_waits_for_its_bot_when_the_limit_drops_under_its_other_turn(api):
    """A limit lowered while two of the bot's turns are held: the parked one is refused its place and says why, the way
    a computer's capacity refuses one, and gets it once the other turn ends."""
    machine = task_bot(api, lanes=True, capacity=4)
    new_task(api, "first")
    new_task(api, "second")
    assert set_bot(api, 2)["turns_at_once"] == 2
    other, parked = claim(api, machine), claim(api, machine)
    assert other is not None and parked is not None
    post(api, f"attempts/{parked['id']}/start-place", {"waiting": "pre-start requirements"}, machine["token"])
    assert set_bot(api, 1)["turns_at_once"] == 1
    assert post(api, f"attempts/{parked['id']}/start-place", {}, machine["token"]) == {
        "reserved": False,
        "waiting": "at its turn limit",
    }
    post(
        api, f"attempts/{other['id']}/complete", {"outcome": "interrupted", "text": "", "last_seq": 0}, machine["token"]
    )
    assert post(api, f"attempts/{parked['id']}/start-place", {}, machine["token"])["reserved"] is True


def test_omitting_the_turns_at_once_field_is_refused_and_an_explicit_null_clears_it(api):
    machine = task_bot(api, lanes=True, capacity=4)
    assert set_computer(api, machine, 2)["turn_limit"] == 2
    assert set_bot(api, 1)["turns_at_once"] == 1
    post(api, f"computers/{machine['runner_id']}/turns-at-once", {}, expected=422)
    post(
        api,
        "bots/ops/turns-at-once",
        {"expected_revision": get(api, "bots/ops")["revision"]},
        token="ana-test",
        expected=422,
    )
    with api.app.state.store.read() as c:
        assert c.execute("SELECT turns_at_once FROM runners WHERE id=?", (machine["runner_id"],)).fetchone()[0] == 2
    assert get(api, "bots/ops")["turns_at_once"] == 1
    assert set_computer(api, machine, None)["turn_limit"] == 4
    assert set_bot(api, None)["turns_at_once"] is None


def test_with_assistant_acts_without_asking_off_a_computer_limit_is_a_confirm_card_the_person_taps(api):
    rid = runner(api, label="Test Mac")["runner_id"]
    put(api, "access/rules", {"assistant_direct": False})
    r, attempt = assistant_turn(api, "ana-test", "Let Test Mac run 3 turns at once")
    err, card = mcp(
        api,
        "tico_settings_set",
        {"key": "computer.turns_at_once", "value": {"turns_at_once": 3}, "computer": rid, "on_behalf_of": "turn"},
        attempt["token"],
    )
    assert not err and card["needs_confirm"] is True, card
    with api.app.state.store.read() as c:
        assert c.execute("SELECT turns_at_once FROM runners WHERE id=?", (rid,)).fetchone()[0] is None
    done = post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token="ana-test")["action"]
    assert done["status"] == "done", done
    with api.app.state.store.read() as c:
        assert c.execute("SELECT turns_at_once FROM runners WHERE id=?", (rid,)).fetchone()[0] == 3


# Each way a bot's turn can start is held to its "Turns at once". Reviewer 1 ran 8 turns at once on Nov 13 at 00:48 UTC,
# with a limit of 3; its turns before ~01:40 UTC ran with no limit set, and none ran more than 3 at once after it.


def test_a_runner_that_negotiates_no_start_places_is_held_to_the_bot_limit_at_its_claim(api):
    """An older runner starts every lease it is given: the claim alone holds the bot to its limit."""
    machine = task_bot(api, lanes=True, capacity=4)
    for title in ("first", "second", "third"):
        new_task(api, title)
    set_bot(api, 2)

    def old_claim():
        return post(api, "jobs/claim", {"bot": None}, token=machine["token"])["attempt"]

    assert old_claim() is not None and old_claim() is not None
    assert old_claim() is None


def test_a_persons_message_waits_for_its_bots_limit_like_any_other_turn(api):
    """A person's message passes a busy computer, never the bot's own limit."""
    machine = task_bot(api, lanes=True, capacity=4)
    new_task(api, "first")
    set_bot(api, 1)
    running = claim(api, machine)
    post(api, f"attempts/{running['id']}/started", {"thread_id": "t"}, machine["token"])
    post(api, "chat/ops", {"text": "How is it going?"})
    assert claim(api, machine) is None
    post(
        api, f"attempts/{running['id']}/complete", {"outcome": "completed", "text": "", "last_seq": 0}, machine["token"]
    )
    assert claim(api, machine) is not None


def test_a_stopped_turn_holds_its_place_until_it_ends_and_the_turn_that_replaces_it_waits(api):
    """Stop and redirect puts the person's message back in line at once, while the stopped turn still runs on its
    computer: the redirect starts only when the stopped turn has ended."""
    machine = task_bot(api, lanes=True, capacity=4)
    set_bot(api, 1)
    post(api, "chat/ops", {"text": "Count the March numbers"})
    running = claim(api, machine)
    post(api, f"attempts/{running['id']}/started", {"thread_id": "t"}, machine["token"])
    redirect = post(api, "chat/ops", {"text": "Stop that; do April instead"})
    assert post(api, "bots/ops/stop", {"run_id": running["id"], "redirect": redirect["id"]})["stopping"] is True
    assert claim(api, machine) is None
    post(
        api,
        f"attempts/{running['id']}/complete",
        {"outcome": "interrupted", "text": "", "last_seq": 0},
        machine["token"],
    )
    nxt = claim(api, machine)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT message_id FROM jobs WHERE id=?", (nxt["job_id"],)).fetchone()[0] == redirect["id"]


def test_a_lapsed_turn_put_back_in_line_runs_once_and_never_beside_its_replacement(api):
    """A lease that lapsed before its turn started goes back in line and no longer holds the bot's place; the computer's
    late report that it started is refused, so the old start cannot run beside the new one."""
    machine = task_bot(api, lanes=True, capacity=4)
    new_task(api, "first")
    new_task(api, "second")
    set_bot(api, 1)
    lapsed = claim(api, machine)
    assert claim(api, machine) is None
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.shift(H.now(), seconds=-1), lapsed["id"]))
        api.app.state.execution.expire(c)
    again = claim(api, machine)
    assert again is not None and claim(api, machine) is None
    late = api.post(
        f"/api/v2/attempts/{lapsed['id']}/started",
        json={"thread_id": "late"},
        headers=headers(machine["token"]),
    )
    assert late.status_code == 409
    with api.app.state.store.read() as c:
        assert start_places.bot_held(c, "ops") == 1


def test_health_warns_when_a_bot_has_more_turns_than_its_limit_and_clears_when_it_is_back_under(api):
    """Lowered from 2 to 1 while two turns run: Health's Turn limit row names the bot and both numbers, until one ends."""
    machine = task_bot(api, lanes=True, capacity=4)
    new_task(api, "first")
    new_task(api, "second")
    set_bot(api, 2)
    first, second = claim(api, machine), claim(api, machine)
    for turn in (first, second):
        post(api, f"attempts/{turn['id']}/started", {"thread_id": turn["id"]}, machine["token"])

    def warning():
        return next((x for x in get(api, "health")["checks"] if x["id"] == "turn_limit:ops"), None)

    assert warning() is None  # at its limit is not over it
    set_bot(api, 1)
    row = warning()
    assert row["label"] == "Turn limit" and row["status"] == "warn"
    assert "has 2 turns at once, over its limit of 1." in row["summary"]
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "text": "", "last_seq": 0}, machine["token"])
    assert warning() is None
