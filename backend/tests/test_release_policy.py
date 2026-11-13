"""One release policy for runs that can wait (backend/wake_presets.py, Product #248 spec v4, items 8-11): Due, Can
wait up to N hours, Prefers warm, At the deadline Run cold or Skip, on the job hold, released by the one check in the
scheduler tick. Times are set on rows directly and the check is run at a chosen moment, so nothing here sleeps."""

import json

import pytest

from backend import automation_actions as AA
from backend import automation_flow as AF
from backend import updates as U
from backend import wake_presets as W
from backend import bot_settings as BS
from backend.store import H
from backend.tests.helpers.api import claim, get, headers, post, ready  # noqa: F401  (api is a fixture)
from backend.tests.helpers.task_lanes import new_task, task_bot

DAY = 24 * 3600


def held(api, task=None, body="A scheduled routine run.", bot="ops", automation=None, **policy):
    """A scheduled routine's run as automation_actions.start_turn files it: a routine task for the bot whose wake
    carries the release policy (refs.release) and names the routine. Returns its job, with `task_id`. `task` is
    unused (kept so a call reads as the run of that work)."""
    refs = {"release": W.policy(**policy), "automation": automation or "auto-" + body[:8]}
    with api.app.state.store.transaction() as c:
        made = H.task_create(
            c,
            "bot:assistant",
            body[:60],
            body,
            "bot:" + bot,
            lint=False,
            deduplicate=False,
            routine=True,
            wake_refs=refs,
        )
        row = c.execute(
            "SELECT j.* FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? "
            "AND json_extract(m.refs,'$.task')=? ORDER BY j.rowid DESC LIMIT 1",
            (bot, made["id"]),
        ).fetchone()
        return {**dict(row), "task_id": made["id"]}


def job(api, job_id):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def queued_at(api, job_id, when):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE id=?", (when, job_id))


def tick(api, when):
    with api.app.state.store.transaction() as c:
        W.tick(c, when)


def settle_all(api, bot="ops"):
    """The bot's other queued jobs (a new task's assignment) handled, so only the job under test is left."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id=? AND state='queued' AND held=0", (bot,))


def last_turn(api, ended, bot="ops", running=False):
    """The bot's last turn: running now, or ended at `ended` (its last model request)."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO attempts(id,bot_id,state,created_at,finished_at,lane,last_seq,thread_id,runner_id,"
            "input_tokens) VALUES(?,?,?,?,?,?,0,?,?,100)",
            (
                H.new_id(),
                bot,
                "running" if running else "completed",
                H.shift(ended, minutes=-5),
                None if running else ended,
                "default",
                "thread-" + ended[:19],
                None,
            ),
        )


def end_running(api, bot, ended):
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE attempts SET state='completed',finished_at=? WHERE bot_id=? AND state='running'", (ended, bot)
        )


def released(row):
    return json.loads(row["released"]) if row["released"] else None


# ---------------------------------------------------------------------------------------------------- the warm window
def test_a_warm_job_is_not_released_while_the_bot_is_busy_and_goes_once_it_has_idled_45_minutes(api):
    task = new_task(api, "daily digest")
    settle_all(api)
    t0 = H.shift(H.now(), hours=-6)
    routine = held(api, task, wait=DAY, warm=True)
    queued_at(api, routine["id"], t0)
    last_turn(api, t0, running=True)
    tick(api, t0)  # Due, and the bot is busy
    tick(api, H.shift(t0, minutes=50))
    assert job(api, routine["id"])["held"] == 1
    end_running(api, "ops", H.shift(t0, hours=1))  # its turn ends: idle from here
    tick(api, H.shift(t0, minutes=60 + 30))  # idle 30 minutes: not yet
    assert job(api, routine["id"])["held"] == 1
    tick(api, H.shift(t0, minutes=60 + 46))  # idle 46 minutes: inside 45-55
    row = job(api, routine["id"])
    assert row["held"] == 0 and row["state"] == "queued"
    assert released(row)["warm"] is True and released(row)["waited"] == 106 * 60


def test_the_window_closes_at_minute_55_and_the_job_waits_for_the_next_one(api):
    task = new_task(api, "weekly")
    settle_all(api)
    t0 = H.shift(H.now(), hours=-6)
    routine = held(api, task, wait=DAY, warm=True)
    queued_at(api, routine["id"], t0)
    last_turn(api, H.shift(t0, minutes=-56))  # idle 56 minutes at Due: the cache is cold, the window gone
    tick(api, t0)
    tick(api, H.shift(t0, minutes=1))
    assert job(api, routine["id"])["held"] == 1
    last_turn(api, H.shift(t0, minutes=10))  # it works again; 45 minutes after that is its next window
    tick(api, H.shift(t0, minutes=10 + 44))
    assert job(api, routine["id"])["held"] == 1
    tick(api, H.shift(t0, minutes=10 + 45))
    assert job(api, routine["id"])["held"] == 0 and released(job(api, routine["id"]))["warm"] is True


def test_two_jobs_released_by_one_window_run_one_after_another_oldest_due_first(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "two routines")
    settle_all(api)
    t0 = H.shift(H.now(), hours=-6)
    newer = held(api, task, "Routine B.", wait=DAY, warm=True, automation="b")
    older = held(api, task, "Routine A.", wait=DAY, warm=True, automation="a")
    queued_at(api, newer["id"], H.shift(t0, minutes=5))
    queued_at(api, older["id"], t0)
    last_turn(api, H.shift(t0, minutes=10))
    tick(api, H.shift(t0, minutes=6))
    tick(api, H.shift(t0, minutes=10 + 46))
    first, second = job(api, older["id"]), job(api, newer["id"])
    assert (first["held"], second["held"]) == (0, 1)  # the oldest Due goes first...
    assert second["release_after"] == older["id"]  # ...and the other runs after it
    attempt = claim(api, machine)
    assert attempt["job_id"] == older["id"]
    tick(api, H.shift(t0, minutes=10 + 47))
    assert job(api, newer["id"])["held"] == 1  # not while the first runs
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE id=?", (older["id"],))
        c.execute("UPDATE attempts SET state='completed',finished_at=? WHERE id=?", (H.now(), attempt["id"]))
    tick(api, H.shift(t0, minutes=10 + 70))  # the first is over: the next goes, on the cache it just used
    row = job(api, newer["id"])
    assert row["held"] == 0 and released(row)["warm"] is True


# ---------------------------------------------------------------------------------------------------- the deadline
def test_a_bot_that_never_idles_runs_a_two_hour_job_cold_at_the_deadline(api):
    task = new_task(api, "cold")
    settle_all(api)
    t0 = H.shift(H.now(), hours=-6)
    routine = held(api, task, wait=2 * 3600, warm=True, deadline=W.RUN)
    queued_at(api, routine["id"], t0)
    for minutes in range(0, 120, 20):  # a turn every 20 minutes: never 45 idle
        last_turn(api, H.shift(t0, minutes=minutes))
        tick(api, H.shift(t0, minutes=minutes + 1))
    tick(api, H.shift(t0, seconds=2 * 3600 - 1))
    assert job(api, routine["id"])["held"] == 1
    tick(api, H.shift(t0, hours=2))
    row = job(api, routine["id"])
    assert (row["held"], row["state"]) == (0, "queued")
    assert released(row) == {"warm": False, "waited": 2 * 3600, "at": H.shift(t0, hours=2)}


def test_with_skip_the_job_does_not_run_and_is_recorded_as_skipped(api):
    task = new_task(api, "skipped")
    settle_all(api)
    t0 = H.shift(H.now(), hours=-6)
    routine = held(api, task, wait=2 * 3600, warm=True, deadline=W.SKIP)
    queued_at(api, routine["id"], t0)
    tick(api, t0)
    tick(api, H.shift(t0, hours=2, seconds=30))
    row = job(api, routine["id"])
    assert (row["held"], row["state"]) == (0, "canceled")
    assert released(row)["skipped"] is True and released(row)["waited"] == 2 * 3600 + 30
    with api.app.state.store.read() as c:
        event = c.execute("SELECT detail FROM events WHERE action='job.release_skipped' AND target=?", (routine["id"],))
        assert json.loads(event.fetchone()[0])["waited"] == 2 * 3600 + 30
        # Its routine task is closed with it, so the routine's next run is not absorbed into it.
        assert H.task(c, routine["task_id"])["status"] != "open"


def test_a_job_that_does_not_prefer_warm_starts_at_due(api):
    task = new_task(api, "team routine")
    settle_all(api)
    t0 = H.shift(H.now(), hours=-1)
    routine = held(api, task)  # a team's own routine: wait 0, warm off
    queued_at(api, routine["id"], t0)
    tick(api, H.shift(t0, seconds=1))
    assert job(api, routine["id"])["held"] == 0


# ---------------------------------------------------------------------------------------------------- never held
def test_a_task_turn_and_a_persons_message_start_at_once_whatever_is_held(api):
    task = new_task(api, "held beside")
    routine = held(api, task, wait=DAY, warm=True)
    assert job(api, routine["id"])["held"] == 1
    assigned = new_task(api, "a pipeline task")  # its turn
    said = post(api, "chat/ops", {"text": "Can you look at this now?"})  # a person's message
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT j.held FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='ops' "
            "AND (json_extract(m.refs,'$.task')=? OR m.id=?) AND j.state='queued'",
            (assigned["id"], said["id"]),
        ).fetchall()
    assert rows and all(r["held"] == 0 for r in rows)


def test_only_tico_can_make_a_wake_wait(api):
    """A bot's message that carries a release policy is never held: the hold reads only Tico's own notices."""
    with api.app.state.store.transaction() as c:
        said = H.say(c, "bot:assistant", "bot:ops", "Run this later", refs={"release": W.policy(wait=DAY, warm=True)})
        row = c.execute("SELECT held FROM jobs WHERE message_id=?", (said["id"],)).fetchone()
    assert row is None or row["held"] == 0


# ---------------------------------------------------------------------------------------------------- the presets go
def test_former_token_saver_and_performance_bots_both_release_notices_after_five_minutes(api):
    with api.app.state.store.transaction() as c:
        BS.update(
            c, "ops", {"wake_preset": "token_saver", "profile": "doer", "profile_options": {"batch_seconds": 1800}}
        )
        BS.update(c, "assistant", {"wake_preset": "performance", "profile": "doer"})
        assert W.retire_presets(c) == 2
    for bot in ("ops", "assistant"):
        task = new_task(api, "notice for " + bot, owner=bot)
        settle_all(api, bot)
        t0 = H.shift(H.now(), hours=-1)
        with api.app.state.store.transaction() as c:
            H._wake(c, H.task(c, task["id"]), "bot:" + bot, "Finished: a request", {"wake_batch": "completion"})
            notice = c.execute("SELECT id FROM jobs WHERE bot_id=? ORDER BY rowid DESC LIMIT 1", (bot,)).fetchone()[0]
        queued_at(api, notice, t0)
        tick(api, H.shift(t0, seconds=W.NOTICE_SECONDS - 1))
        assert job(api, notice)["hold_until"] == H.shift(t0, seconds=W.NOTICE_SECONDS)
        assert job(api, notice)["held"] == 1
        tick(api, H.shift(t0, seconds=W.NOTICE_SECONDS))
        assert job(api, notice)["held"] == 0, bot


def test_the_migration_strips_presets_and_profile_wake_numbers_and_replans_held_jobs(api):
    task = new_task(api, "planned")
    with api.app.state.store.transaction() as c:
        BS.update(
            c,
            "ops",
            {
                "wake_preset": "token_saver",
                "profile": "lead",
                "profile_options": {
                    "batch_seconds": 1800,
                    "batch_scope": "bot",
                    "sweep_seconds": 7200,
                    "digest_hour": 8,
                },
                "compact_at": 400000,
            },
        )
        H._wake(c, H.task(c, task["id"]), "bot:ops", "Finished: x", {"wake_batch": "completion"})
        c.execute("UPDATE jobs SET hold_until='2001-01-01T00:00:00.000000Z' WHERE bot_id='ops' AND held=1")
        before = c.execute("SELECT revision FROM bot_config WHERE bot='ops'").fetchone()[0]
        W.ensure(c)
        config = BS.read(c, "ops")
        assert "wake_preset" not in config and config["profile_options"] == {"digest_hour": 8}
        assert config["compact_at"] == 400000 and config["profile"] == "lead"  # nothing else moves
        assert c.execute("SELECT revision FROM bot_config WHERE bot='ops'").fetchone()[0] == before + 1
        assert (
            c.execute("SELECT count(*) FROM jobs WHERE bot_id='ops' AND held=1 AND hold_until IS NOT NULL").fetchone()[
                0
            ]
            == 0
        )
        W.ensure(c)  # once per bot
        assert c.execute("SELECT revision FROM bot_config WHERE bot='ops'").fetchone()[0] == before + 1
        assert c.execute("SELECT count(*) FROM bot_settings WHERE wake_preset IS NOT NULL").fetchone()[0] == 0


def test_there_is_no_preset_to_pick_and_no_profile_carries_wake_numbers(api):
    revision = get(api, "bots/ops")["revision"]
    response = api.post(
        "/api/v2/bots/ops/wake-preset",
        json={"wake_preset": "token_saver", "expected_revision": revision},
        headers=headers(),
    )
    assert response.status_code in (404, 405)
    view = get(api, "bots/ops")
    assert "preset" not in view["wake"] and view["wake"]["notice_seconds"] == W.NOTICE_SECONDS
    assert not {"batch_seconds", "batch_scope", "sweep_seconds"} & set(view["profile"])
    for choice in get(api, "profiles")["profiles"]:
        assert not {"batch_seconds", "batch_scope", "sweep_seconds"} & set(choice)


# ---------------------------------------------------------------------------------------------------- the repeat guard
def test_a_routine_is_not_queued_again_while_its_last_run_is_running_and_waits_15_minutes_after_it_started(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "hourly")
    settle_all(api)
    first = held(api, task, "Hourly run 1.", automation="hourly")
    tick(api, H.now())
    attempt = claim(api, machine)
    assert attempt["job_id"] == first["id"]
    second = held(api, task, "Hourly run 2.", automation="hourly")
    tick(api, H.now())
    assert job(api, second["id"])["state"] == "canceled"  # its last run is still going: nothing to add
    with api.app.state.store.read() as c:  # and its routine task is closed with it, so nothing works it later
        assert H.task(c, second["task_id"])["status"] != "open"
        assert H.task(c, first["task_id"])["status"] == "open"
    started = H.shift(H.now(), minutes=-5)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE id=?", (first["id"],))
        c.execute(
            "UPDATE attempts SET state='completed',created_at=?,finished_at=? WHERE id=?",
            (started, H.now(), attempt["id"]),
        )
    third = held(api, task, "Hourly run 3.", automation="hourly")
    tick(api, H.now())
    row = job(api, third["id"])
    assert row["held"] == 1 and row["hold_until"] == H.shift(started, seconds=W.REPEAT_SECONDS)
    other = held(api, task, "Another routine.", automation="other")  # another routine is not spaced by this one
    tick(api, H.now())
    assert job(api, other["id"])["held"] == 0


# ---------------------------------------------------------------------------------------------------- who gets which values
def test_shipped_update_routines_wait_one_interval_cold_and_fresh(api):
    assert U.shipped_release("daily") == W.policy(wait=DAY, warm=True, deadline=W.RUN, session=W.FRESH)
    assert U.shipped_release("weekly") == W.policy(wait=7 * DAY, warm=True, deadline=W.RUN, session=W.FRESH)


def test_a_team_routine_starts_with_wait_0_and_its_settings_set_the_policy():
    def auto(release=None, trigger="schedule"):
        flow = {"trigger": {"type": trigger, "cron": "0 9 * * *"}, **({"release": release} if release else {})}
        return {"id": "a1", "flow": flow}

    assert AA.release_of(None, auto(), None) == W.policy(session=W.FRESH)  # a team's own routine: wait 0
    set_ = {"warm": True, "wait_hours": 6, "deadline": "skip", "session": "continue"}
    assert AA.release_of(None, auto(set_), None) == W.policy(wait=6 * 3600, warm=True, deadline=W.SKIP, session=W.FORK)
    assert AA.release_of(None, auto(trigger="webhook"), None) is None  # only a schedule's runs wait


@pytest.mark.parametrize(
    "release",
    [
        {"warm": "yes"},
        {"wait_hours": -1},
        {"wait_hours": "6"},
        {"wait_hours": 24 * 15},
        {"deadline": "later"},
        {"session": "latest"},
        {"other": 1},
    ],
)
def test_routine_settings_refuse_what_the_policy_would_not_keep(release):
    flow = {
        "name": "Daily",
        "owner": "bot:ops",
        "trigger": {"type": "schedule", "cron": "0 9 * * *"},
        "steps": [],
        "release": release,
    }
    with pytest.raises(AF.Invalid):
        AF.check(flow)


def test_routine_settings_are_for_a_schedule_only():
    base = {"name": "Daily", "owner": "bot:ops", "steps": [], "release": {"warm": True, "wait_hours": 24}}
    assert AF.check({**base, "trigger": {"type": "schedule", "cron": "0 9 * * *"}})["release"]["wait_hours"] == 24
    with pytest.raises(AF.Invalid):
        AF.check({**base, "trigger": {"type": "manual"}})


def test_policy_refuses_values_it_would_not_keep():
    for bad in ({"wait": -1}, {"wait": 1.5}, {"wait": True}, {"deadline": "later"}, {"session": "x"}, {"due": "soon"}):
        with pytest.raises(ValueError):
            W.policy(**bad)


# ---------------------------------------------------------------------------------------------------- the run record
def test_a_released_run_records_warm_fork_and_its_wait_and_a_forking_runner_is_handed_the_session(api):
    machine = task_bot(api, lanes=True)
    ready(api, machine, ["ops"], capabilities=["task_lanes_v2", "fork_session_v1"])
    task = new_task(api, "continue latest")
    settle_all(api)
    t0 = H.shift(H.now(), hours=-3)
    routine = held(api, task, wait=DAY, warm=True, session=W.FORK)
    queued_at(api, routine["id"], t0)
    with api.app.state.store.transaction() as c:  # the bot's latest session, on this computer
        c.execute(
            "INSERT INTO attempts(id,bot_id,state,created_at,finished_at,lane,last_seq,thread_id,runner_id,task_id,"
            "input_tokens) VALUES(?,?,?,?,?,?,0,?,?,?,100)",
            (
                H.new_id(),
                "ops",
                "completed",
                t0,
                H.shift(t0, minutes=30),
                "default",
                "latest-thread",
                machine["runner_id"],
                task["id"],
            ),
        )
    tick(api, t0)
    tick(api, H.shift(t0, minutes=30 + 50))
    attempt = claim(api, machine)
    assert attempt["job_id"] == routine["id"] and attempt["fork_thread"] == "latest-thread"
    with api.app.state.store.transaction() as c:  # its usage, as the run reports it (#251)
        c.execute("UPDATE attempts SET cached_tokens=182000,cache_write_tokens=4000 WHERE id=?", (attempt["id"],))
    [run] = [r for r in get(api, f"tasks/{routine['task_id']}/run-view")["runs"] if r["id"] == attempt["id"]]
    record = run["wake"]["release"]
    assert record["warm"] is True and record["session"] == "fork" and record["waited_seconds"] == 80 * 60
    assert "preset" not in run["wake"]  # nothing new records a preset
    assert (run["cache_read_tokens"], run["cache_write_tokens"]) == (182000, 4000)


def test_a_runner_that_cannot_fork_starts_fresh_and_the_record_says_so(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "fresh")
    settle_all(api)
    routine = held(api, task, session=W.FORK)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO attempts(id,bot_id,state,created_at,finished_at,lane,last_seq,thread_id,runner_id) "
            "VALUES(?,?,?,?,?,?,0,?,?)",
            (H.new_id(), "ops", "completed", H.now(), H.now(), "default", "latest-thread", machine["runner_id"]),
        )
    tick(api, H.now())
    attempt = claim(api, machine)
    assert attempt["job_id"] == routine["id"] and "fork_thread" not in attempt
    [run] = [r for r in get(api, f"tasks/{routine['task_id']}/run-view")["runs"] if r["id"] == attempt["id"]]
    assert run["wake"]["release"]["session"] == "fresh" and run["wake"]["release"]["warm"] is False


def test_a_job_chained_behind_one_that_never_ends_still_goes_at_its_own_deadline(api):
    task = new_task(api, "stuck first")
    settle_all(api)
    t0 = H.shift(H.now(), hours=-30)
    first = held(api, task, "Routine A.", wait=DAY, warm=True, automation="a")
    second = held(api, task, "Routine B.", wait=2 * 3600, warm=True, automation="b")
    queued_at(api, first["id"], t0)
    queued_at(api, second["id"], H.shift(t0, minutes=1))
    last_turn(api, t0)
    tick(api, H.shift(t0, minutes=2))
    tick(api, H.shift(t0, minutes=46))
    assert job(api, second["id"])["release_after"] == first["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (first["id"],))  # it never ends
    tick(api, H.shift(t0, minutes=1 + 119))
    assert job(api, second["id"])["held"] == 1
    tick(api, H.shift(t0, minutes=1 + 120))
    row = job(api, second["id"])
    assert row["held"] == 0 and released(row)["warm"] is False


def test_routine_settings_save_read_back_and_clear_through_the_api(api):
    from backend.tests.helpers.automation_api import make, patch

    settings = {"warm": True, "wait_hours": 6, "deadline": "skip", "session": "continue"}
    made = make(api)
    assert "release" not in made or made["release"] is None  # a team's own routine starts with the wait off
    saved = patch(api, made["id"], {"release": settings})
    assert saved["release"] == settings and get(api, f"automations/{made['id']}")["release"] == settings
    patch(api, made["id"], {"release": {**settings, "wait_hours": 400}}, expected=422)
    cleared = patch(api, made["id"], {"release": None})
    assert cleared.get("release") is None
    # Only a schedule's runs wait: an event trigger with settings is refused, and moving to one clears them.
    patch(api, made["id"], {"release": settings})
    event = {"type": "event", "name": "task.created"}
    patch(api, made["id"], {"trigger": event}, expected=422)
    moved = patch(api, made["id"], {"trigger": event, "release": None})
    assert moved["trigger"]["type"] == "event" and moved.get("release") is None


def test_a_task_turn_that_folds_a_held_routine_wake_is_not_held(api):
    """A pipeline task turn is never held: when the engine folds a waiting routine wake into a new turn on the same
    task (a person moves it, a stage hands it on), the new turn does not carry the routine's release policy."""
    from backend import task_wakes

    routine = held(api, None, "A held routine run.", wait=DAY, warm=True)
    with api.app.state.store.transaction() as c:
        task = H.task(c, routine["task_id"])
        visit = c.execute("SELECT id FROM visits WHERE task_id=? ORDER BY rowid DESC LIMIT 1", (task["id"],)).fetchone()
        task_wakes.add(c, task, "bot:ops", {"id": visit[0]}, "Your turn: a person moved this task.", try_n=1)
        rows = c.execute(
            "SELECT j.held,j.state,j.release FROM jobs j JOIN messages m ON m.id=j.message_id "
            "WHERE j.bot_id='ops' AND json_extract(m.refs,'$.task')=? ORDER BY j.rowid",
            (task["id"],),
        ).fetchall()
    live = [r for r in rows if r["state"] == "queued"]
    assert live and all(r["held"] == 0 and r["release"] is None for r in live)


def test_a_fresh_release_tells_a_runner_that_can_to_start_a_session_of_its_own(api):
    machine = task_bot(api, lanes=True)
    ready(api, machine, ["ops"], capabilities=["task_lanes_v2", "fresh_session_v1"])
    task = new_task(api, "fresh routine")
    settle_all(api)
    routine = held(api, task, session=W.FRESH)
    tick(api, H.now())
    attempt = claim(api, machine)
    assert attempt["job_id"] == routine["id"] and attempt["fresh_session"] is True
    # The record says what the runner did, once it reports it.
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["id"]}, machine["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Done.", "session": {"kind": "new", "why": "fresh"}},
        machine["token"],
    )
    [run] = [r for r in get(api, f"tasks/{routine['task_id']}/run-view")["runs"] if r["id"] == attempt["id"]]
    assert run["wake"]["release"]["session"] == "fresh"
    with api.app.state.store.read() as c:  # a fresh session on purpose is not a lost one in the task's history
        assert not c.execute(
            "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='session'",
            (routine["task_id"],),
        ).fetchone()


def test_a_fresh_run_that_resumed_on_an_older_runner_is_recorded_as_resumed(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "old runner")
    settle_all(api)
    routine = held(api, task, session=W.FRESH)
    tick(api, H.now())
    attempt = claim(api, machine)
    assert attempt["job_id"] == routine["id"] and "fresh_session" not in attempt
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["id"]}, machine["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Done.", "session": {"kind": "resumed"}},
        machine["token"],
    )
    [run] = [r for r in get(api, f"tasks/{routine['task_id']}/run-view")["runs"] if r["id"] == attempt["id"]]
    assert run["wake"]["release"]["session"] == "resumed"


def test_a_continue_latest_run_never_forks_a_private_task_or_a_chat(api):
    machine = task_bot(api, lanes=True)
    ready(api, machine, ["ops"], capabilities=["task_lanes_v2", "fork_session_v1"])
    open_task = new_task(api, "shared work")
    settle_all(api)
    t0 = H.shift(H.now(), hours=-2)
    with api.app.state.store.transaction() as c:
        secret = H.task_create(c, "bot:assistant", "Private matter", "x", "bot:ops", private=True)
        for n, (task_id, thread) in enumerate(
            ((open_task["id"], "shared-thread"), (secret["id"], "private-thread"), (None, "chat-thread"))
        ):
            c.execute(
                "INSERT INTO attempts(id,bot_id,state,created_at,finished_at,lane,last_seq,thread_id,runner_id,task_id) "
                "VALUES(?,?,?,?,?,?,0,?,?,?)",
                (
                    H.new_id(),
                    "ops",
                    "completed",
                    t0,
                    H.shift(t0, minutes=n),
                    "default",
                    thread,
                    machine["runner_id"],
                    task_id,
                ),
            )
    settle_all(api)
    routine = held(api, open_task, session=W.FORK)
    tick(api, H.now())
    attempt = claim(api, machine)
    assert attempt["job_id"] == routine["id"] and attempt["fork_thread"] == "shared-thread"


def test_a_dropped_run_never_closes_a_task_it_did_not_make(api):
    """Skip and the repeat guard close a run's own routine task; a run that names an existing task leaves it open."""
    task = new_task(api, "a person's task")
    settle_all(api)
    t0 = H.shift(H.now(), hours=-6)
    with api.app.state.store.transaction() as c:
        H._wake(
            c,
            H.task(c, task["id"]),
            "bot:ops",
            "A routine looks at this task.",
            {
                "release": W.policy(wait=3600, warm=True, deadline=W.SKIP),
                "automation": "auto-look",
                "wake": "task_turn",
            },
        )
        row = c.execute(
            "SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='ops' AND j.held=1 "
            "AND json_extract(m.refs,'$.task')=? ORDER BY j.rowid DESC LIMIT 1",
            (task["id"],),
        ).fetchone()
    assert row, "the routine's wake on the existing task is held"
    queued_at(api, row[0], t0)
    tick(api, t0)
    tick(api, H.shift(t0, hours=2))
    assert job(api, row[0])["state"] == "canceled"  # skipped at its deadline
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "open"


def test_a_turn_that_never_reached_the_model_does_not_restart_the_idle_count(api):
    task = new_task(api, "idle")
    settle_all(api)
    t0 = H.shift(H.now(), hours=-6)
    routine = held(api, task, wait=DAY, warm=True)
    queued_at(api, routine["id"], t0)
    last_turn(api, H.shift(t0, minutes=-50))  # its last model request, 50 minutes before
    with api.app.state.store.transaction() as c:  # a turn that failed before any request, 5 minutes before
        c.execute(
            "INSERT INTO attempts(id,bot_id,state,created_at,finished_at,lane,last_seq) VALUES(?,?,?,?,?,?,0)",
            (H.new_id(), "ops", "failed", H.shift(t0, minutes=-6), H.shift(t0, minutes=-5), "default"),
        )
    tick(api, t0)
    row = job(api, routine["id"])
    assert row["held"] == 0 and released(row)["warm"] is True
