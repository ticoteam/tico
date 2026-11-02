"""A claim's failure stays with its cause: one job, one bot or the storage. Every held run comes back on its own.

The claim checks each candidate job and prepares the one it hands out (backend/execution.py). An error in a job's own
checks or start belongs to that job: its writes are rolled back, its traceback is logged once, and the job is held
(backend/claim_holds.py) for a wait that doubles with each failed try, up to an hour. An error in a bot's own check (its
budget or back-off) passes that bot over for the rest of the claim and holds none of its runs; Health names the check
until one of its runs starts. A claim holds at most CLAIM_HOLDS jobs. A team's default and a storage error fail it.
"""
import json
import sqlite3

import pytest

from backend import (auto_tasks, batch_work, claim_holds, execution, gateway_connection, health, health_monitor,
                     providers, read_cache, read_deps, run_backoff, task_privacy as privacy, turn_budgets, wake_presets)
from backend.store import H
from backend.tests.test_api import (  # noqa: F401 (api is a fixture)
    api, assign, claim, get, headers, post, ready, runner, setup_attempt)
from backend.tests.test_held_runs import task_held
from backend.tests.test_run_backoff import at, complete, setup as budget_setup


@pytest.fixture(autouse=True)
def fresh_logs():
    """A row's failure is logged once an hour (batch_work.report), and job ids start again in each test's database."""
    batch_work._logged.clear()


def jobs_of(api, bot):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT id,state,held,hold_reason,hold_task_id,attempt_id FROM jobs "
                                           "WHERE bot_id=? ORDER BY created_at,id", (bot,))]


def task_jobs(api, task_id):
    """ops's jobs for one task, oldest first, with the Needs-you item a claim hold filed."""
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(
            "SELECT j.id,j.state,j.held,j.hold_reason,j.hold_task_id FROM jobs j JOIN messages m ON m.id=j.message_id "
            "JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE j.bot_id='ops' AND {H.MESSAGE_TASK_SQL}=? ORDER BY j.created_at,j.id", (task_id,))]


def hold_of(api, job_id):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT state,held,hold_until,hold_reason,hold_task_id,hold_streak FROM jobs WHERE id=?",
                              (job_id,)).fetchone())


def wait_ends(api, monkeypatch, job_id):
    """The clock moves to just after the job's wait."""
    at(monkeypatch, api, H.shift(hold_of(api, job_id)["hold_until"], seconds=1))


def raising_for_ops(monkeypatch):
    """Every job of ops raises as its checks run: an error that belongs to that work."""
    checked = execution.job_refusal

    def raising(c, row, *args, **kwargs):
        if row["bot"] == "ops":
            raise RuntimeError("the export's state cannot be read")
        return checked(c, row, *args, **kwargs)
    monkeypatch.setattr(execution, "job_refusal", raising)


def bot_check_raising(monkeypatch, module, name, bot):
    """`module.name`, a check of one bot's state (its budget or back-off), raises for `bot` alone. Returns the calls."""
    real = getattr(module, name)
    calls = []

    def raising(c, checked_bot, *args, **kwargs):
        if checked_bot == bot:
            calls.append(checked_bot)
            raise RuntimeError(f"{bot}'s {name} cannot be read")
        return real(c, checked_bot, *args, **kwargs)
    monkeypatch.setattr(module, name, raising)
    return calls


def held_refresh(api, monkeypatch):
    """ops has one task whose job raises in its checks: one claim holds it and files the bot's Needs-you item."""
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    with api.app.state.store.transaction() as c:
        task_id = H.task_create(c, H.KEEPER, "Refresh the export", "Refresh it.", H.bot_actor("ops"), lint=False)["id"]
    raising_for_ops(monkeypatch)
    assert claim(api, machine) is None
    return machine, task_id


def item_status(api, task_id):
    with api.app.state.store.read() as c:
        return dict(H.task(c, task_id))["status"]


def two_bots_with_chats(api, machine):
    """ops's chat is created first and dated earlier, so its job is the one a claim considers first."""
    assign(api, machine, "ops")
    assign(api, machine, "cpo")
    ready(api, machine, ["ops", "cpo"])
    post(api, "chat/ops", {"text": "Look at the export."})
    post(api, "chat/cpo", {"text": "Look at the pricing."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE bot_id='ops'", (H.shift(H.now(), minutes=-5),))


def test_a_job_whose_checks_raise_is_held_and_the_claim_hands_out_the_next_bots_job(api, monkeypatch, caplog):
    machine = runner(api, "ana")
    two_bots_with_chats(api, machine)
    raised = []
    checked = execution.job_refusal

    def refusing(c, row, *args, **kwargs):
        if row["bot_id"] == "ops":
            raised.append(row["id"])
            raise RuntimeError("the export's state cannot be read")
        return checked(c, row, *args, **kwargs)
    monkeypatch.setattr(execution, "job_refusal", refusing)

    turn = claim(api, machine)
    assert turn["bot"] == "cpo"
    [bad] = jobs_of(api, "ops")
    assert (bad["held"], bad["hold_reason"], bad["state"]) == (1, claim_holds.REASON, "queued")
    assert raised == [bad["id"]]
    tracebacks = [r for r in caplog.records if r.exc_info and f"claim failed for {bad['id']}" in r.getMessage()]
    assert len(tracebacks) == 1

    # Held, the job is not evaluated again by the next claim, and Health names it.
    assert claim(api, machine) is None
    assert raised == [bad["id"]]
    store = api.app.state.store
    with store.transaction() as c:
        assert "claim_failed" in {f["check_id"] for f in health_monitor.findings(c, store.settings)}

    # The owner's release queues it again. It raises again, so it is held again, and its traceback is not logged twice.
    post(api, "bots/ops/release", {})
    assert jobs_of(api, "ops")[0]["held"] == 0
    assert claim(api, machine) is None
    assert raised == [bad["id"], bad["id"]]
    assert jobs_of(api, "ops")[0]["held"] == 1
    assert len([r for r in caplog.records if r.exc_info and f"claim failed for {bad['id']}" in r.getMessage()]) == 1


def test_the_writes_a_failing_job_made_before_its_error_are_rolled_back(api, monkeypatch):
    machine = runner(api, "ana")
    two_bots_with_chats(api, machine)
    handed_out = gateway_connection.hand_out

    def failing(c, aid, config):
        if c.execute("SELECT bot_id FROM attempts WHERE id=?", (aid,)).fetchone()[0] == "ops":
            raise RuntimeError("the runtime configuration cannot be built")
        return handed_out(c, aid, config)
    monkeypatch.setattr(gateway_connection, "hand_out", failing)

    turn = claim(api, machine)
    assert turn["bot"] == "cpo"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE bot_id='ops'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM turns WHERE bot='ops'").fetchone()[0] == 0
    [bad] = jobs_of(api, "ops")
    assert (bad["state"], bad["attempt_id"], bad["held"]) == ("queued", None, 1)
    [good] = jobs_of(api, "cpo")
    assert (good["state"], good["attempt_id"]) == ("leased", turn["id"])


def test_a_moved_input_that_failed_on_the_old_upsert_is_held_and_other_bots_still_claim(api, monkeypatch):
    """A moved input whose upsert fails once the history sweep has indexed the earlier run: fold_behind's upsert on it
    raises. The upsert is put back here at the point fold_behind writes; the job it fails for is held, and a second
    bot's job is handed out in the same claim."""
    machine, attempt, task_id, _ = task_held(api)
    store = api.app.state.store
    with store.transaction() as c:
        verdict = c.execute("SELECT m.id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='ops' "
                            "AND j.state='queued' AND m.body LIKE 'VERDICT:%'").fetchone()[0]
        c.execute("INSERT INTO attempt_inputs VALUES(?,?,?)", (attempt["id"], verdict, H.now()))
        cpo_task = H.task_create(c, H.KEEPER, "Price list", "Check the list.", H.bot_actor("cpo"), lint=False)["id"]
        H.task_comment(c, H.KEEPER, cpo_task, "The pricing numbers are in.")
    with store.read() as conn:
        read_deps.sweep(conn)
    post(api, f"jobs/{attempt['job_id']}/reconcile", {"run_id": attempt["id"], "decision": "resume",
         "note": "Nothing was pushed yet; carry on from the build.", "acknowledge_uncertain_effects": True})
    assign(api, machine, "cpo")
    ready(api, machine, ["ops", "cpo"])

    recorded = privacy.record_moved_input

    def old_upsert(c, prior_attempt, aid, queued):
        recorded(c, prior_attempt, aid, queued)
        c.execute("INSERT INTO attempt_inputs VALUES(?,?,?) ON CONFLICT(message_id) DO UPDATE SET "
                  "attempt_id=excluded.attempt_id,acked_at=excluded.acked_at", (aid, queued["id"], H.now()))
    monkeypatch.setattr(privacy, "record_moved_input", old_upsert)

    # The claim passes over the held run within the same call, handing out the bot's next job, and the claim after
    # it reaches cpo, whose work the held run never blocked.
    turn = claim(api, machine)
    assert turn is not None and turn["job_id"] != attempt["job_id"]
    assert (claim(api, machine) or {}).get("bot_id") == "cpo"
    [held] = [j for j in jobs_of(api, "ops") if j["id"] == attempt["job_id"]]
    assert (held["held"], held["hold_reason"], held["state"]) == (1, claim_holds.REASON, "queued")
    assert held["attempt_id"] == attempt["id"]
    with store.read() as c:
        error = c.execute("SELECT detail FROM events WHERE action='job.claim_failed' AND target=?",
                          (attempt["job_id"],)).fetchone()
        assert error and json.loads(error[0])["error"] == "IntegrityError"
        # The held job's preparation was rolled back: it made no attempt of its own.
        assert c.execute("SELECT count(*) FROM attempts WHERE job_id=?", (attempt["job_id"],)).fetchone()[0] == 1
        assert any("could not start this run" in note["body"] for note in H.task_comments(c, task_id))


def test_an_error_outside_the_candidate_still_fails_the_claim(api, monkeypatch):
    machine = runner(api, "ana")
    two_bots_with_chats(api, machine)

    def broken(*args, **kwargs):
        raise RuntimeError("the company's turn state cannot be read")
    monkeypatch.setattr(execution.org_turns, "observe", broken)

    with pytest.raises(RuntimeError):
        claim(api, machine)
    assert all(not job["held"] for job in jobs_of(api, "ops") + jobs_of(api, "cpo"))


def test_a_storage_error_in_a_job_check_stops_the_claim_instead_of_holding_the_job(api, monkeypatch):
    machine = runner(api, "ana")
    two_bots_with_chats(api, machine)

    def full_disk(c, row, *args, **kwargs):
        if row["bot_id"] == "ops":
            error = sqlite3.OperationalError("database or disk is full")
            error.sqlite_errorcode = sqlite3.SQLITE_FULL
            raise error
        return checked(c, row, *args, **kwargs)
    checked = execution.job_refusal
    monkeypatch.setattr(execution, "job_refusal", full_disk)

    # A full disk answers the claim with a retryable 503 (the app's storage handler), and holds nothing.
    answer = api.post("/api/v2/jobs/claim", json={"bot": None, "start_places": True},
                      headers=headers(machine["token"]))
    assert answer.status_code == 503, answer.text
    assert answer.json()["error"]["code"] == "storage_unavailable" and answer.json()["error"]["retryable"] is True
    assert [job["held"] for job in jobs_of(api, "ops")] == [0]


def test_a_task_gets_no_second_job_while_its_held_job_waits(api, monkeypatch):
    machine, task_id = held_refresh(api, monkeypatch)
    assert [(job["state"], job["held"]) for job in task_jobs(api, task_id)] == [("queued", 1)]
    assert claim(api, machine) is None
    assert [job["held"] for job in task_jobs(api, task_id)] == [1]


def test_a_held_job_is_cancelled_by_the_obsolete_pass_only_after_its_wait_ends(api, monkeypatch):
    """The obsolete-notice pass leaves a held job alone while its wait runs. Once the wait has ended the job is free,
    and the next claim cancels it, its task being finished, and closes the Needs-you item its hold filed."""
    machine, task_id = held_refresh(api, monkeypatch)
    held = task_jobs(api, task_id)[0]
    with api.app.state.store.transaction() as c:
        H.task_update(c, H.KEEPER, task_id, status="done")
    at(monkeypatch, api, H.shift(hold_of(api, held["id"])["hold_until"], seconds=-30))
    assert claim(api, machine) is None
    assert (hold_of(api, held["id"])["state"], hold_of(api, held["id"])["held"]) == ("queued", 1)
    assert item_status(api, held["hold_task_id"]) in H.ACTIVE_STATUSES

    monkeypatch.undo()      # the job's checks work again, and its wait has ended
    wait_ends(api, monkeypatch, held["id"])
    assert claim(api, machine) is None
    after = hold_of(api, held["id"])
    assert (after["state"], after["held"], after["hold_reason"]) == ("canceled", 0, None)
    assert item_status(api, held["hold_task_id"]) not in H.ACTIVE_STATUSES


def test_release_frees_a_held_job_another_path_cancelled_and_closes_its_item(api, monkeypatch):
    """Archiving a bot cancels its queued jobs, held ones too. Release still frees them and closes the Needs-you item
    they filed, so no item waits on a job that can no longer run."""
    machine, task_id = held_refresh(api, monkeypatch)
    held = task_jobs(api, task_id)[0]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops' AND state='queued'")
    assert post(api, "bots/ops/release", {})["released"]["claim"] == 1
    assert item_status(api, held["hold_task_id"]) not in H.ACTIVE_STATUSES
    assert [(job["state"], job["held"]) for job in task_jobs(api, task_id)] == [("canceled", 0)]


def test_a_start_retry_never_hands_out_an_obsolete_notice(api, monkeypatch):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    store = api.app.state.store
    post(api, "chat/ops", {"text": "Look at the export."})
    with store.read() as c:
        person_job = c.execute("SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id "
                               "WHERE j.bot_id='ops' AND m.sender_id LIKE 'human:%'").fetchone()[0]
    with store.transaction() as c:
        done = H.task_create(c, H.KEEPER, "Finished export", "Done.", H.bot_actor("ops"), lint=False)["id"]
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (done,))
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops' AND state='queued' AND id<>?", (person_job,))
        conversation = H.task(c, done)["conversation_id"]
        # A claim's obsolete pass cancels a page of CLAIM_MAINTENANCE notices, so one is left queued for the retry.
        for n in range(execution.CLAIM_MAINTENANCE + 1):
            H.say(c, H.KEEPER, "bot:ops", f"PR #{n} checks passed", kind="notice", conversation_id=conversation,
                  refs={"task": done, "trigger": "other", "wake": "update"})
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='ops' AND state='queued' AND held=0").fetchone()[0] == (
            execution.CLAIM_MAINTENANCE + 2)
    handed_out = gateway_connection.hand_out

    def failing(c, aid, config):
        if c.execute("SELECT job_id FROM attempts WHERE id=?", (aid,)).fetchone()[0] == person_job:
            raise RuntimeError("the runtime configuration cannot be built")
        return handed_out(c, aid, config)
    monkeypatch.setattr(gateway_connection, "hand_out", failing)

    # The person's job fails to start and is held. The retry then finds the notice; the claim passes it over, so no
    # attempt starts for the bot.
    assert claim(api, machine) is None
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE bot_id='ops'").fetchone()[0] == 0
        held, reason = c.execute("SELECT held,hold_reason FROM jobs WHERE id=?", (person_job,)).fetchone()
    assert (held, reason) == (1, claim_holds.REASON)


def test_a_bot_budget_refusal_holds_no_job_over_several_claims(api, monkeypatch):
    machine = budget_setup(api, monkeypatch, tasks=6)
    budget = get(api, "bots/ops/budget")
    post(api, "bots/ops/budget", {"turns_per_hour": 2, "expected_revision": budget["revision"]})
    for _ in range(2):
        complete(api, machine, claim(api, machine))
    for _ in range(3):
        assert claim(api, machine) is None
    assert get(api, "bots/ops/budget")["held"]
    assert [job for job in jobs_of(api, "ops") if job["held"]] == []


@pytest.mark.parametrize("check", ["budget", "back-off"])
def test_a_bot_check_that_raises_passes_its_bot_over_and_the_claim_hands_out_the_others(api, monkeypatch, check):
    """A bot's own budget or back-off check that cannot be read fails no claim: the claim passes that bot over and hands
    out another bot's run, and none of the bot's runs is held for it."""
    machine = runner(api, "ana")
    two_bots_with_chats(api, machine)
    if check == "budget":
        calls = bot_check_raising(monkeypatch, turn_budgets, "refusal", "ops")
    else:
        calls = bot_check_raising(monkeypatch, run_backoff, "refusal", "ops")
    turn = claim(api, machine)
    assert turn["bot"] == "cpo"
    assert claim(api, machine) is None
    assert calls
    assert [(job["state"], job["held"]) for job in jobs_of(api, "ops")] == [("queued", 0)]


def test_a_bot_check_that_raises_is_logged_once_per_claim_and_named_in_health_until_its_run_starts(api, monkeypatch,
                                                                                                    caplog):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    for n in range(3):
        post(api, "chat/ops", {"text": f"Look at item {n}."})
    calls = bot_check_raising(monkeypatch, turn_budgets, "refusal", "ops")
    assert claim(api, machine) is None
    # The first of the bot's jobs raised; the claim passed the bot over for the rest of its scan.
    assert len(calls) == 1
    assert [job["held"] for job in jobs_of(api, "ops")] == [0, 0, 0]
    logged = [r for r in caplog.records if r.exc_info and "claim check failed for bot ops" in r.getMessage()]
    assert len(logged) == 1
    store = api.app.state.store
    with store.transaction() as c:
        [finding] = [f for f in health_monitor.findings(c, store.settings) if f["check_id"] == "claim_check"]
    assert "RuntimeError" in finding["title"]

    # Once the back-off has passed, the check reads again: its next run starts, and the finding clears.
    monkeypatch.undo()
    at(monkeypatch, api, H.shift(H.now(), seconds=claim_holds.CHECK_RETRY_FIRST + 1))
    assert claim(api, machine)["bot"] == "ops"
    with store.transaction() as c:
        assert "claim_check" not in {f["check_id"] for f in health_monitor.findings(c, store.settings)}


def test_a_team_budget_default_that_raises_fails_the_claim_and_holds_no_job(api, monkeypatch):
    machine = budget_setup(api, monkeypatch, tasks=6)

    def unreadable(*args, **kwargs):
        raise RuntimeError("the team defaults cannot be read")
    monkeypatch.setattr(turn_budgets, "team", unreadable)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            claim(api, machine)
    assert [job for job in jobs_of(api, "ops") if job["held"]] == []


def test_a_claim_holds_at_most_claim_holds_jobs_that_fail_to_start(api, monkeypatch):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    total = execution.CLAIM_HOLDS + 2
    for n in range(total):
        post(api, "chat/ops", {"text": f"Look at item {n}."})
    starts = []

    def failing(c, aid, config):
        starts.append(aid)
        raise RuntimeError("the runtime configuration cannot be built")
    monkeypatch.setattr(gateway_connection, "hand_out", failing)

    assert claim(api, machine) is None
    assert len(starts) == execution.CLAIM_HOLDS
    assert sum(job["held"] for job in jobs_of(api, "ops")) == execution.CLAIM_HOLDS
    assert claim(api, machine) is None
    assert sum(job["held"] for job in jobs_of(api, "ops")) == total


def test_a_claim_holds_at_most_claim_holds_jobs_whose_checks_raise(api, monkeypatch, caplog):
    """A bot whose every job raises holds CLAIM_HOLDS of them in one claim, not the whole backlog; the next claim holds
    the next ones."""
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    total = 2 * execution.CLAIM_HOLDS + 1
    for n in range(total):
        post(api, "chat/ops", {"text": f"Look at item {n}."})
    raising_for_ops(monkeypatch)

    assert claim(api, machine) is None
    assert sum(job["held"] for job in jobs_of(api, "ops")) == execution.CLAIM_HOLDS
    logged = [r for r in caplog.records if r.exc_info and "claim failed for" in r.getMessage()]
    assert len(logged) == execution.CLAIM_HOLDS
    assert claim(api, machine) is None
    assert sum(job["held"] for job in jobs_of(api, "ops")) == 2 * execution.CLAIM_HOLDS


def test_a_provider_wide_start_error_holds_each_run_and_they_come_back_after_their_wait(api, monkeypatch):
    """Every run fails to start while the provider's runtime settings cannot be built: each is held. Once the settings
    work and the waits have ended, both bots' runs are handed out."""
    machine = runner(api, "ana")
    two_bots_with_chats(api, machine)
    broken = {"on": True}
    handed_out = gateway_connection.hand_out

    def hand_out(c, aid, config):
        if broken["on"]:
            raise RuntimeError("the provider's runtime settings cannot be built")
        return handed_out(c, aid, config)
    monkeypatch.setattr(gateway_connection, "hand_out", hand_out)
    assert claim(api, machine) is None
    held = {bot: [job for job in jobs_of(api, bot) if job["held"]] for bot in ("ops", "cpo")}
    assert len(held["ops"]) == len(held["cpo"]) == 1

    broken["on"] = False
    later = max(hold_of(api, job["id"])["hold_until"] for bot in held for job in held[bot])
    at(monkeypatch, api, H.shift(later, seconds=1))
    turns = [claim(api, machine), claim(api, machine)]
    assert {turn["bot"] for turn in turns} == {"ops", "cpo"}
    assert all(not job["held"] for bot in ("ops", "cpo") for job in jobs_of(api, bot))


def test_a_held_run_is_retried_after_each_wait_and_a_failure_doubles_the_next_one_up_to_an_hour(api, monkeypatch):
    """A held run is retried when its wait ends. Each failed try renews its hold with a wait twice as long as the last,
    from a minute to an hour, under the one Needs-you item its first hold filed."""
    machine, task_id = held_refresh(api, monkeypatch)
    [job] = task_jobs(api, task_id)
    item = job["hold_task_id"]
    assert hold_of(api, job["id"])["hold_streak"] == 1
    for streak, wait in zip(range(2, 9), (120, 240, 480, 960, 1920, 3600, 3600)):
        wait_ends(api, monkeypatch, job["id"])
        when = H.now()
        assert claim(api, machine) is None
        held = hold_of(api, job["id"])
        assert (held["held"], held["hold_streak"]) == (1, streak)
        assert held["hold_until"] == H.shift(when, seconds=wait)
        assert held["hold_task_id"] == item
    assert item_status(api, item) in H.ACTIVE_STATUSES
    with api.app.state.store.read() as c:
        assert len([note for note in H.task_comments(c, task_id) if "could not start this run" in note["body"]]) == 1


def test_a_held_run_that_starts_clears_its_hold_its_needs_you_item_and_its_health_finding(api, monkeypatch):
    machine, task_id = held_refresh(api, monkeypatch)
    [job] = task_jobs(api, task_id)
    monkeypatch.undo()      # the job's checks work again
    wait_ends(api, monkeypatch, job["id"])
    assert claim(api, machine)["job_id"] == job["id"]
    held = hold_of(api, job["id"])
    assert (held["held"], held["hold_reason"], held["hold_task_id"], held["hold_streak"]) == (0, None, None, 0)
    assert item_status(api, job["hold_task_id"]) not in H.ACTIVE_STATUSES
    store = api.app.state.store
    with store.transaction() as c:
        assert "claim_failed" not in {f["check_id"] for f in health_monitor.findings(c, store.settings)}


def test_a_wake_preset_release_and_replan_leave_a_claim_hold_to_its_own_wait(api, monkeypatch):
    """The scheduler's batch release and a preset change plan and release only the wake presets' holds: a run a claim
    holds keeps its wait, its reason and its count until its claim lets it go."""
    machine, task_id = held_refresh(api, monkeypatch)
    [job] = task_jobs(api, task_id)
    before = hold_of(api, job["id"])
    store = api.app.state.store
    with store.transaction() as c:
        wake_presets.tick(c, at=H.shift(before["hold_until"], hours=1))
        wake_presets.replan(c, "ops")
    assert hold_of(api, job["id"]) == before


def test_a_database_from_before_the_hold_count_keeps_its_held_runs_and_counts_their_tries_from_zero(api, monkeypatch):
    machine, task_id = held_refresh(api, monkeypatch)
    [job] = task_jobs(api, task_id)
    store = api.app.state.store
    with store.transaction() as c:          # a database from before the column
        c.execute("ALTER TABLE jobs DROP COLUMN hold_streak")
    store.initialize()
    held = hold_of(api, job["id"])
    assert (held["held"], held["hold_reason"], held["hold_streak"]) == (1, claim_holds.REASON, 0)
    wait_ends(api, monkeypatch, job["id"])
    assert claim(api, machine) is None
    assert hold_of(api, job["id"])["hold_streak"] == 1


@pytest.mark.parametrize("obsolete_ahead", [execution.CLAIM_PICKS - 2, execution.CLAIM_PICKS - 1])
def test_a_claim_passes_over_at_most_claim_picks_obsolete_notices(api, monkeypatch, obsolete_ahead):
    """A claim cancels a page of obsolete notices, then drops the candidates it still reaches under its lock: at most
    CLAIM_PICKS in all, the snapshot's stale first pick among them. Past that it answers none, and the next claim
    cancels the rest and hands the open job out."""
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    store = api.app.state.store
    with store.transaction() as c:
        done = H.task_create(c, H.KEEPER, "Finished export", "Done.", H.bot_actor("ops"), lint=False)["id"]
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (done,))
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops' AND state='queued'")
        conversation = H.task(c, done)["conversation_id"]
        for n in range(execution.CLAIM_MAINTENANCE + obsolete_ahead):
            H.say(c, H.KEEPER, "bot:ops", f"PR #{n} checks passed", kind="notice", conversation_id=conversation,
                  refs={"task": done, "trigger": "other", "wake": "update"})
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='ops' AND state='queued' AND held=0").fetchone()[0] == (
            execution.CLAIM_MAINTENANCE + obsolete_ahead)
    with store.transaction() as c:
        open_task = H.task_create(c, H.KEEPER, "Open export", "Do it.", H.bot_actor("ops"), lint=False)["id"]
    [open_job] = task_jobs(api, open_task)

    if obsolete_ahead < execution.CLAIM_PICKS - 1:
        assert claim(api, machine)["job_id"] == open_job["id"]
    else:
        assert claim(api, machine) is None
        with store.read() as c:
            assert c.execute("SELECT state FROM jobs WHERE id=?", (open_job["id"],)).fetchone()[0] == "queued"
        assert claim(api, machine)["job_id"] == open_job["id"]


def test_a_login_check_that_raises_for_one_job_holds_that_job_and_the_bots_other_job_starts(api, monkeypatch):
    """The login check of one job (its continuation's login) is that job's error: the job is held, and the bot's other
    queued job is handed out in the same claim, rather than the whole bot being passed over."""
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "first"})
    post(api, "chat/ops", {"text": "second"})
    bad, other = [job["id"] for job in jobs_of(api, "ops")]
    real = execution.login_refusal

    def raising(c, settings, runner_id, bot, config, job_id=None, ctx=None):
        if job_id == bad:
            raise RuntimeError("the job's continuation cannot be read")
        return real(c, settings, runner_id, bot, config, job_id, ctx)
    monkeypatch.setattr(execution, "login_refusal", raising)

    turn = claim(api, machine)
    assert turn is not None and turn["job_id"] == other
    held, started = jobs_of(api, "ops")
    assert (held["held"], held["hold_reason"]) == (1, claim_holds.REASON)
    assert (started["state"], started["held"]) == ("leased", 0)


def test_a_team_wide_provider_error_fails_the_claim_and_passes_no_bot_over(api, monkeypatch):
    """The company's provider choice is every bot's: when it cannot be read the claim fails. Passing each bot over with
    its own claim_check finding would hide a team-wide fault behind one finding per bot."""
    machine = runner(api, "ana")
    two_bots_with_chats(api, machine)

    # The read beneath providers.load: its team-wide error fails the claim whichever bot's claim reads it.
    real = read_cache.metadata

    def unreadable(c, key):
        if key == providers.KEY:
            raise RuntimeError("the provider choice cannot be read")
        return real(c, key)
    monkeypatch.setattr(read_cache, "metadata", unreadable)

    with pytest.raises(RuntimeError):
        claim(api, machine)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM service_health WHERE service GLOB 'claim_check:*'").fetchone()[0] == 0
    assert all(not job["held"] for job in jobs_of(api, "ops") + jobs_of(api, "cpo"))


def test_a_claim_hold_on_a_running_bots_run_is_not_delivered_as_input_until_its_wait_ends(api, monkeypatch):
    """A run the claim holds is not folded into a running turn of its bot while its wait runs: its state stays queued
    and its hold stays. Once the wait has ended it is delivered, and its hold and Needs-you item clear."""
    r, _, first = setup_attempt(api)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "thread"}, r["token"])
    follow = post(api, "chat/ops", {"text": "Also include the weekly numbers"})
    store = api.app.state.store
    with store.transaction() as c:
        job = c.execute("SELECT id FROM jobs WHERE message_id=?", (follow["id"],)).fetchone()[0]
        claim_holds.hold(c, store.settings, job, "RuntimeError")
    item = hold_of(api, job)["hold_task_id"]

    def delivered():
        return [m["id"] for m in post(api, f"attempts/{first['id']}/inputs", {}, r["token"])["messages"]]
    assert delivered() == []
    assert (hold_of(api, job)["state"], hold_of(api, job)["held"]) == ("queued", 1)

    wait_ends(api, monkeypatch, job)
    assert delivered() == [follow["id"]]
    after = hold_of(api, job)
    assert (after["state"], after["held"], after["hold_reason"]) == ("input", 0, None)
    assert item_status(api, item) not in H.ACTIVE_STATUSES


def test_a_failing_bot_check_is_not_run_again_by_polls_inside_its_back_off(api, monkeypatch, caplog):
    """After a bot's check raises, its polls pass the bot over without running the check again and without the writer
    lock until the back-off passes. The traceback is logged once per back-off, and the next back-off is longer."""
    machine = runner(api, "ana")
    two_bots_with_chats(api, machine)
    calls = bot_check_raising(monkeypatch, turn_budgets, "refusal", "ops")
    writes = []
    claimed = execution.Execution.claim

    def counted(self, *args, **kwargs):
        writes.append(1)
        return claimed(self, *args, **kwargs)
    monkeypatch.setattr(execution.Execution, "claim", counted)

    def logged():
        return len([r for r in caplog.records if r.exc_info and "claim check failed for bot ops" in r.getMessage()])
    assert claim(api, machine)["bot"] == "cpo"
    assert (len(calls), logged()) == (1, 1)
    writes_before = len(writes)
    for _ in range(10):
        assert claim(api, machine) is None
    assert (len(calls), len(writes), logged()) == (1, writes_before, 1)

    # The back-off passes: the check runs once more, fails again, and the next wait is twice as long.
    at(monkeypatch, api, H.shift(H.now(), seconds=claim_holds.CHECK_RETRY_FIRST + 1))
    assert claim(api, machine) is None
    assert (len(calls), logged()) == (2, 2)
    for _ in range(10):
        assert claim(api, machine) is None
    assert len(calls) == 2
    at(monkeypatch, api, H.shift(H.now(), seconds=2 * claim_holds.CHECK_RETRY_FIRST - 1))
    # The other bot's run may be handed out again once its lease lapses: only the check's count is asserted.
    claim(api, machine)
    assert len(calls) == 2
    at(monkeypatch, api, H.shift(H.now(), seconds=2))
    assert claim(api, machine) is None
    assert (len(calls), logged()) == (3, 3)
    # Health reads the check too, after the claim's count: its finding names the bot all the while.
    store = api.app.state.store
    with store.transaction() as c:
        assert "claim_check" in {f["check_id"] for f in health_monitor.findings(c, store.settings)}


def wake_notice(api, task_id, body):
    """A notice on one task for ops, batched by its wake preset (the completion kind)."""
    with api.app.state.store.transaction() as c:
        H._wake(c, H.task(c, task_id), "bot:ops", body, {"wake_batch": "completion"})
    with api.app.state.store.read() as c:
        return c.execute("SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='ops' AND m.body=?",
                         (body,)).fetchone()[0]


def batch_task(api, title):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, H.KEEPER, title, "Done.", H.bot_actor("ops"), lint=False)["id"]


def batch_hold(api, job_id):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT hold_until,held,hold_reason FROM jobs WHERE id=?", (job_id,)).fetchone())


def test_a_batch_planned_while_a_claim_hold_still_waits_keeps_its_own_window(api, monkeypatch):
    """A batch's release is set by its own window, not by a run the claim holds and is still waiting for: the held run
    has a wait of its own (wake_presets._batch_slot's waiting query)."""
    t0 = "2026-10-24T12:00:00.000000Z"
    store = api.app.state.store
    at(monkeypatch, api, t0)
    post(api, "bots/ops/wake-preset", {"wake_preset": "token_saver",
         "expected_revision": get(api, "bots/ops")["revision"]})
    first = wake_notice(api, batch_task(api, "first"), "Finished: first")
    wake_tick(api, t0)
    wake_tick(api, H.shift(t0, minutes=31))
    at(monkeypatch, api, H.shift(t0, minutes=31))
    with store.transaction() as c:
        claim_holds.hold(c, store.settings, first, "RuntimeError")          # held until t0 + 32 min
    at(monkeypatch, api, H.shift(t0, minutes=40))
    second = wake_notice(api, batch_task(api, "second"), "Finished: second")
    wake_tick(api, H.shift(t0, minutes=41))
    assert batch_hold(api, second)["hold_until"] == H.shift(t0, minutes=70)
    assert batch_hold(api, second)["held"] == 1


def test_a_batch_planned_after_a_claim_hold_was_let_go_is_not_spaced_by_that_holds_wait(api, monkeypatch):
    """The next batch of a bot waits a window after its last batch went out, never after a run the claim held: a held
    run let go at its wait's end does not move that window (wake_presets._batch_slot's last query)."""
    t0 = "2026-10-24T12:00:00.000000Z"
    store = api.app.state.store
    at(monkeypatch, api, t0)
    post(api, "bots/ops/wake-preset", {"wake_preset": "token_saver",
         "expected_revision": get(api, "bots/ops")["revision"]})
    first = wake_notice(api, batch_task(api, "first"), "Finished: first")
    wake_tick(api, t0)
    wake_tick(api, H.shift(t0, minutes=31))
    at(monkeypatch, api, H.shift(t0, minutes=40))
    second = wake_notice(api, batch_task(api, "second"), "Finished: second")
    at(monkeypatch, api, H.shift(t0, minutes=44))
    with store.transaction() as c:
        claim_holds.hold(c, store.settings, first, "RuntimeError")          # held until t0 + 45 min
    at(monkeypatch, api, H.shift(t0, minutes=46))
    with store.transaction() as c:
        claim_holds.release_due(c)
    wake_tick(api, H.shift(t0, minutes=46))
    assert batch_hold(api, first)["held"] == 0
    assert batch_hold(api, second)["hold_until"] == H.shift(t0, minutes=70)


def wake_tick(api, when):
    with api.app.state.store.transaction() as c:
        wake_presets.tick(c, when)


def test_a_bot_check_that_raises_does_not_stop_another_bots_open_task_from_starting(api, monkeypatch):
    """The idle claim's backfill asks each bot with an open task the same bot-wide checks: one that raises passes that
    bot over there too, and is recorded and backed off as in the queue, so the other bot's task still starts."""
    machine = runner(api, "ana")
    for bot in ("ops", "cpo"):
        assign(api, machine, bot)
    ready(api, machine, ["ops", "cpo"])
    tasks = {bot: post(api, "tasks", {"owner": bot, "title": f"Implement the {bot} report",
                                      "body": "Build the requested feature."}) for bot in ("ops", "cpo")}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled'")        # no notice queued: only the backfill starts them
    calls = bot_check_raising(monkeypatch, run_backoff, "refusal", "ops")
    turn = claim(api, machine)
    assert (turn["bot"], turn["task"]["id"]) == ("cpo", tasks["cpo"]["id"])
    assert calls
    # Inside the back-off the next polls do not run ops's check again, and they answer.
    seen = len(calls)
    for _ in range(3):
        assert claim(api, machine) is None
    assert len(calls) == seen
    with api.app.state.store.transaction() as c:
        assert "claim_check" in {f["check_id"] for f in health_monitor.findings(c, api.app.state.store.settings)}


def open_task_only(api, machine, bots):
    """Each bot has one open task and nothing queued: only the claim's backfill starts them."""
    for bot in bots:
        assign(api, machine, bot)
    ready(api, machine, list(bots))
    tasks = {bot: post(api, "tasks", {"owner": bot, "title": f"Implement the {bot} report",
                                      "body": "Build the requested feature."}) for bot in bots}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled'")
    return tasks


def claim_check_bots(api):
    with api.app.state.store.transaction() as c:
        return [r[0] for r in c.execute("SELECT service FROM service_health WHERE service GLOB 'claim_check:*'")]


def test_a_task_whose_backfill_check_raises_does_not_stop_another_bots_task(api, monkeypatch):
    """An error in one open task's own checks belongs to that task: the backfill passes it over and the next bot's task
    still starts."""
    machine = runner(api, "ana")
    tasks = open_task_only(api, machine, ("ops", "cpo"))
    real, raised = auto_tasks.refusal, []

    def raising(c, row, *args, **kwargs):
        if row["owner_id"] == "bot:ops":
            raised.append(row["id"])
            raise RuntimeError("ops's open task cannot be read")
        return real(c, row, *args, **kwargs)
    monkeypatch.setattr(auto_tasks, "refusal", raising)
    # ops's task is looked at first, so its error is met before cpo's task could answer the claim.
    monkeypatch.setattr(execution, "turns_here", lambda c, runner_id: {"cpo": 0, "ops": -1})
    turn = claim(api, machine)
    assert raised and set(raised) == {tasks["ops"]["id"]}
    assert (turn["bot"], turn["task"]["id"]) == ("cpo", tasks["cpo"]["id"])


def test_a_bot_check_that_raises_in_the_backfill_alone_is_recorded_and_backed_off(api, monkeypatch):
    """A bot whose only work is an open task: its raising check is recorded and backed off even though the claim has
    nothing to hand out, so later polls neither run the check again nor lose the Health finding. The claims skip the
    idle read, as one does whenever there is something to write first, so the writer's backfill meets the error."""
    machine = runner(api, "ana")
    open_task_only(api, machine, ("ops",))
    monkeypatch.setattr(execution.Execution, "idle_claim", lambda *args, **kwargs: None)
    calls = bot_check_raising(monkeypatch, run_backoff, "refusal", "ops")
    assert claim(api, machine) is None
    seen = len(calls)
    assert seen and claim_check_bots(api) == ["claim_check:ops"]
    for _ in range(3):
        assert claim(api, machine) is None
    assert len(calls) == seen


def test_a_bot_check_that_passes_again_in_the_backfill_ends_its_back_off_and_finding(api, monkeypatch):
    """Once the bot's check reads cleanly in the backfill, Health stops naming it and a later failure backs off from
    the first wait again, though no run of the bot started."""
    machine = runner(api, "ana")
    open_task_only(api, machine, ("ops",))
    monkeypatch.setattr(auto_tasks, "refusal", lambda *a, **k: "auto_start_off")
    real = run_backoff.refusal
    bot_check_raising(monkeypatch, run_backoff, "refusal", "ops")
    assert claim(api, machine) is None
    assert claim_check_bots(api) == ["claim_check:ops"]
    monkeypatch.setattr(run_backoff, "refusal", real)
    at(monkeypatch, api, H.shift(H.now(), seconds=claim_holds.CHECK_RETRY_MAX + 1))
    assert claim(api, machine) is None
    assert claim_check_bots(api) == []
    bot_check_raising(monkeypatch, run_backoff, "refusal", "ops")
    assert claim(api, machine) is None
    assert api.app.state.execution.check_backoff["ops"][1] == 1


def test_a_check_that_passes_after_a_restart_still_ends_its_finding(api, monkeypatch):
    """The back-off lives in the server's memory and a restart forgets it; the Health finding is in the database, and
    the first clean check after the restart ends it."""
    machine = runner(api, "ana")
    open_task_only(api, machine, ("ops",))
    monkeypatch.setattr(auto_tasks, "refusal", lambda *a, **k: "auto_start_off")
    real = run_backoff.refusal
    bot_check_raising(monkeypatch, run_backoff, "refusal", "ops")
    assert claim(api, machine) is None
    assert claim_check_bots(api) == ["claim_check:ops"]
    monkeypatch.setattr(run_backoff, "refusal", real)
    api.app.state.execution.check_backoff.clear()
    assert claim(api, machine) is None
    assert claim_check_bots(api) == []


def test_a_draining_bot_keeps_its_check_back_off(api, monkeypatch):
    """Draining refuses the bot before its checks run, so it says nothing of whether they pass: the back-off and the
    finding stay."""
    machine = runner(api, "ana")
    open_task_only(api, machine, ("ops",))
    bot_check_raising(monkeypatch, run_backoff, "refusal", "ops")
    assert claim(api, machine) is None
    monkeypatch.setattr(execution, "_claim_refusal", lambda c, settings, runner, bot, *a, **k: ("draining", None))
    at(monkeypatch, api, H.shift(H.now(), seconds=claim_holds.CHECK_RETRY_MAX + 1))
    assert claim(api, machine) is None
    assert claim_check_bots(api) == ["claim_check:ops"]
    assert "ops" in api.app.state.execution.check_backoff


@pytest.mark.parametrize("check", ["bot", "job"])
def test_health_lists_waiting_bots_while_a_claim_check_raises(api, monkeypatch, check):
    """Health's waiting list asks the claim's own checks; one that raises leaves that bot out of the list (Health names
    the failing check or the held run instead) and the page still renders."""
    machine = runner(api, "ana")
    two_bots_with_chats(api, machine)
    if check == "bot":
        bot_check_raising(monkeypatch, run_backoff, "refusal", "ops")
    else:
        monkeypatch.setattr(execution, "job_refusal", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unreadable")))
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE state='queued'", (H.shift(H.now(), minutes=-30),))
        online = {r["id"] for r in health._online_runners(c)}
        waiting, slow, stuck = health._waiting(c, online, store.settings)
    assert "ops" not in {r["bot_id"] for r in stuck}
