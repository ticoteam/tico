"""Wake presets (backend/wake_presets.py): what a preset holds at enqueue time, what it never holds, and what it never
changes. Times are set on the jobs directly and the planner is run at a chosen moment, so nothing here sleeps."""
import json

import pytest

from backend import github as G
from backend import wake_presets as W
from backend.store import H
from backend.tests.test_api import api, claim, get, headers, post, ready, setup_attempt  # noqa: F401  (api is a fixture)
from backend.tests.test_botops_parity import act
from backend.tests.test_member_bots import botops, turn  # noqa: F401  (botops is a fixture)
from backend.tests.test_task_lanes import new_task, task_bot

PR = "https://github.com/ticoteam/tico/pull/412"


def preset(api, bot, name):
    return post(api, f"bots/{bot}/wake-preset", {"wake_preset": name, "expected_revision": get(api, "bots/" + bot)["revision"]})


def notice(api, task, body, to="ops", **refs):
    with api.app.state.store.transaction() as c:
        return H._wake(c, H.task(c, task["id"]), "bot:" + to, body, refs or None)


def jobs(api, bot="ops", since=""):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT j.id,j.state,j.held,j.wake_class,j.hold_until,m.body FROM jobs j "
                                           "JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? AND m.created_at>? "
                                           "ORDER BY m.rowid", (bot, since))]


def at(api, job_id, when):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE id=?", (when, job_id))


def tick(api, when):
    with api.app.state.store.transaction() as c:
        W.tick(c, when)


def settled(api):
    """Mark the bot's queued jobs (a new task's assignment) handled, so the next claim takes the job under test."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops' AND state='queued'")


def start(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["id"]}, machine["token"])


def finish(api, machine, attempt, started=False, **tokens):
    if not started:
        start(api, machine, attempt)
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done.", **tokens},
         machine["token"])


def employee(api, bot):
    """The row Settings and the branch editor read (ui/app/settings.js loads /employees)."""
    return {e["name"]: e for e in api.get("/api/employees", headers=headers()).json()}[bot]


def test_existing_bots_use_performance_and_new_bots_start_as_doers(api):
    view = get(api, "bots/ops")["wake"]
    assert view["preset"] == "performance" and view["batch_seconds"] == 60
    task = new_task(api, "existing")
    notice(api, task, "Finished: a request", wake_batch="completion")
    assert [(j["state"], j["held"]) for j in jobs(api)] == [("queued", 0), ("queued", 1)]
    made = post(api, "bots", {"slug": "scout", "display_name": "Scout", "description": "A test bot.", "status": "active",
                              "repo": "emp-scout", "thread_mode": "personal", "model": "hermes-profile",
                              "effort": "as-configured", "harness": "hermes", "owner_id": "ana", "owners": ["ana"],
                              "runner_id": None})
    assert made["wake"]["preset"] == "performance" and made["profile"]["name"] == "doer"
    listed = {b["slug"]: b for b in get(api, "bots")}
    assert listed["scout"]["wake"]["batch_seconds"] == 900 and listed["ops"]["wake"]["preset"] == "performance"
    assert employee(api, "scout")["wake"]["batch_seconds"] == 900 and employee(api, "ops")["wake"]["batch_seconds"] == 60
    preset(api, "ops", "performance")
    assert employee(api, "ops")["wake"]["summary"].startswith("Performance")
    preset(api, "ops", "current")                                        # older clients select Performance through the legacy alias
    assert employee(api, "ops")["wake"]["preset"] == "performance"


def test_legacy_wake_migration_is_idempotent_and_preserves_other_settings(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.wake_preset','current','$.compact_at',400000) WHERE bot='ops'")
        before = c.execute("SELECT revision FROM bot_config WHERE bot='ops'").fetchone()[0]
        W.ensure(c)
        row = c.execute("SELECT config,revision FROM bot_config WHERE bot='ops'").fetchone()
        assert json.loads(row[0])["wake_preset"] == "performance"
        assert json.loads(row[0])["compact_at"] == 400000 and row[1] == before + 1
        W.ensure(c)
        assert c.execute("SELECT revision FROM bot_config WHERE bot='ops'").fetchone()[0] == before + 1


@pytest.mark.parametrize("name", ["token_saver", "performance"])
def test_immediate_wakes_are_never_held_and_a_duplicate_wakes_once(api, name):
    preset(api, "ops", name)
    task = new_task(api, "assigned")                                     # an assignment
    post(api, "chat/ops", {"text": "Can you look at this?"})            # a person's question
    post(api, "chat/ops", {"text": "Can you look at this?"})            # the same, sent twice
    # An awaited result, a blocker and a reopened task wake; "Ready for review" is a note now, not an immediate wake.
    for body, refs in (("Unblocked: assigned\nThe migration is finished.", {"blocker_done": task["id"]}),
                       ("Stuck: the deploy key was revoked", {}), ("Open: assigned", {})):
        notice(api, task, body, **refs)
        notice(api, task, body, **refs)                                  # a duplicated event
    rows = jobs(api)
    assert all(not j["held"] for j in rows)
    live = [j["body"] for j in rows if j["state"] == "queued"]
    assert len(live) == len(set(live)) == 5
    assert sum(j["wake_class"] == "duplicate" for j in rows) == 4


@pytest.mark.parametrize("name,window", [("token_saver", 1800), ("performance", 60)])
def test_an_unchanged_webhook_queues_nothing_and_the_preset_is_its_only_timer(api, name, window):
    preset(api, "ops", name)
    task = post(api, "tasks", {"owner_id": "ops", "title": "Repair checks", "body": "x", "links": [PR]})
    failing = {"action": "created", "repository": {"full_name": "ticoteam/tico"}, "pull_request": {"html_url": PR},
               "comment": {"id": 1, "user": {"login": "sam"}}}
    with api.app.state.store.transaction() as c:
        G.pr_signal(c, "pull_request_review_comment", failing)
        G.pr_signal(c, "pull_request_review_comment", failing)
        assert G.flush_wakes(c) == [task["id"]]                         # the next flush, not three minutes on
        G.pr_signal(c, "pull_request_review_comment", failing)                            # the same comment: nothing new
        assert G.flush_wakes(c) == []
        G.queue_wake(c, H.task(c, task["id"]), "The release you armed a wait for is out.")
        assert G.flush_wakes(c) == [task["id"]]                         # an awaited result goes at once too
    [ci] = [j for j in jobs(api) if j["wake_class"] == "review"]
    assert ci["held"] == 1                                               # a person's review comment nobody waits on is batched
    tick(api, H.now())
    [ci] = [j for j in jobs(api) if j["id"] == ci["id"]]
    assert H.parse_ts(ci["hold_until"]) <= H.parse_ts(H.shift(H.now(), seconds=window))
    [armed] = [j for j in jobs(api) if j["body"].startswith("The release you armed")]
    assert (armed["state"], armed["held"]) == ("queued", 0)


@pytest.mark.parametrize("name", ["token_saver", "performance"])
def test_a_subordinates_ordinary_edits_do_not_wake_its_manager(api, name):
    preset(api, "coo", name)
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "bot:coo", "Plan the launch", "Plan it.", "bot:coo")
        task = H.task_create(c, "bot:coo", "Draft the plan", "Please draft it.", "bot:ops", parent_id=parent["id"])
        before = c.execute("SELECT count(*) FROM jobs WHERE bot_id='coo'").fetchone()[0]
        H.task_update(c, "bot:ops", task["id"], status="doing", note="Started on the outline")
        H.task_update(c, "bot:ops", task["id"], note="Half way")
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='coo'").fetchone()[0] == before
        H.task_update(c, "bot:ops", task["id"], status="done", note="Drafted")
        [done] = c.execute("SELECT j.held,m.refs FROM jobs j JOIN messages m ON m.id=j.message_id "
                           "WHERE j.bot_id='coo' AND json_extract(m.refs,'$.subtask')=?", (task["id"],))
        assert done["held"] == 0                       # a delegated result continues the parent's work immediately
        assert json.loads(done["refs"])["task"] == parent["id"]


def test_token_saver_gives_three_notices_in_its_window_one_wake(api):
    preset(api, "ops", "token_saver")
    tasks = [new_task(api, f"report {n}") for n in range(3)]
    since = H.now()
    t0 = H.shift(H.now(), hours=-1)
    for n, task in enumerate(tasks):
        notice(api, task, f"Finished: part {n}", wake_batch="completion")
        at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, minutes=10 * n))
    tick(api, H.shift(t0, minutes=29))
    assert all(j["held"] and j["hold_until"] == H.shift(t0, minutes=30) for j in jobs(api, since=since))
    tick(api, H.shift(t0, minutes=31))
    rows = jobs(api, since=since)
    assert [j["state"] for j in rows] == ["canceled"] * 3 + ["queued"]
    assert rows[-1]["wake_class"] == "digest" and all(f"report {n}" in rows[-1]["body"] for n in range(3))
    # The next batch waits a whole window after this one went out.
    notice(api, tasks[0], "Finished: part 4", wake_batch="completion")
    at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, minutes=40))
    tick(api, H.shift(t0, minutes=41))
    assert jobs(api, since=since)[-1]["hold_until"] == H.shift(t0, minutes=70)


def test_performance_coalesces_one_tasks_notices_within_a_minute(api):
    preset(api, "ops", "performance")
    task = new_task(api, "fix")
    since, t0 = H.now(), H.shift(H.now(), hours=-1)
    for n, body in enumerate(("1 review comments on tico#412", "2 review comments on tico#412")):
        notice(api, task, body, wake_batch="review")
        at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, seconds=20 * n))
    tick(api, H.shift(t0, seconds=59))
    assert all(j["held"] for j in jobs(api, since=since))
    tick(api, H.shift(t0, seconds=61))
    assert [(j["state"], j["held"]) for j in jobs(api, since=since)] == [("completed", 0), ("queued", 0)]


@pytest.mark.parametrize("name,gap", [("token_saver", 2 * 3600), ("performance", 15 * 60)])
def test_an_enabled_sweep_runs_at_most_once_per_cap_from_its_actual_start(api, name, gap):
    machine = task_bot(api, lanes=True)
    preset(api, "ops", name)
    task = new_task(api, "hourly digest")
    settled(api)
    since, t0 = H.now(), H.shift(H.now(), hours=-5)

    def occurrence(n, when):
        notice(api, task, f"Scheduled occurrence {n} absorbed into this open task.", wake="occurrence", sweep="s1")
        at(api, jobs(api, since=since)[-1]["id"], when)
        tick(api, H.shift(when, seconds=1))
        return jobs(api, since=since)[-1]
    first = occurrence(0, t0)
    assert (first["state"], first["held"]) == ("queued", 0)              # the first run starts at once
    assert occurrence(1, H.shift(t0, seconds=60))["state"] == "canceled"  # it has not run yet: nothing to add
    # Its computer takes it just before the cap and finishes; the next run is spaced from that start.
    attempt = claim(api, machine)
    started = H.shift(t0, seconds=gap - 60)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET created_at=? WHERE id=?", (started, attempt["id"]))
    finish(api, machine, attempt)
    late = occurrence(2, H.shift(t0, seconds=gap + 60))
    assert late["held"] and late["hold_until"] == H.shift(started, seconds=gap)


def test_switching_presets_changes_only_the_preset(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.compact_at',400000) WHERE bot='ops'")
        tables = [t for t in ("schedules", "triggers", "credential_grants", "subscriptions")
                  if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()]
        def state():
            row = c.execute("SELECT config,access FROM bot_config WHERE bot='ops'").fetchone()
            config = json.loads(row[0])
            config.pop("wake_preset", None)
            return config, row[1], [c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables]
        before = state()
    for name in ("performance", "token_saver", "current"):
        assert preset(api, "ops", name)["wake"]["preset"] == ("performance" if name == "current" else name)
        with api.app.state.store.read() as c:
            assert state() == before
    assert get(api, "bots/ops")["compact_at"] == 400000
    with api.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT after FROM settings_changes WHERE bot='ops' AND field='wake_preset' "
                                        "ORDER BY created_at")] == ['"token_saver"', '"performance"']


def test_a_usage_limit_with_a_known_reset_waits_for_it_under_a_preset(api, monkeypatch):
    from backend.execution import cooling_off
    from backend.scheduler import Scheduler
    machine, _, attempt = setup_attempt(api)
    preset(api, "ops", "token_saver")
    reset = H.shift(H.now(), minutes=40)[:19] + "+00:00"
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "reset"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": 0, "limited": True,
         "limit_resets_at": reset, "text": ""}, machine["token"])
    scheduler = Scheduler(api.app.state.store, api.app.state.execution)
    for minutes in (-30, -10, -1):                                       # no retry before the reset
        monkeypatch.setattr(H, "now", lambda m=minutes: H.shift(reset, minutes=m))
        ready(api, machine, ["ops"])
        scheduler.tick()
        assert claim(api, machine) is None
    monkeypatch.setattr(H, "now", lambda: H.shift(reset, seconds=2))
    scheduler.tick()
    with api.app.state.store.read() as c:
        assert not cooling_off(c, "ops")
        assert c.execute("SELECT count(*) FROM attempts WHERE job_id=?", (attempt["job_id"],)).fetchone()[0] == 1
        assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == "queued"


def test_a_turn_records_its_wake_and_the_measures_count_it_by_preset(api):
    machine = task_bot(api, lanes=True)
    preset(api, "ops", "token_saver")
    since = H.shift(H.now(), minutes=-1)
    task = new_task(api, "measured")
    assert claim(api, machine)
    [run] = get(api, f"tasks/{task['id']}/run-view")["runs"]
    assert run["wake"]["class"] == "assignment" and run["wake"]["preset_label"] == "Token saver"
    assert run["wake"]["profile"] == "doer" and run["wake"]["profile_label"] == "Doer"   # what the run page shows
    measures = {p["preset"]: p for p in get(api, "wake-presets/measures?since=" + since)["presets"]}
    assert measures["token_saver"]["turns"] == 1 and measures["token_saver"]["pickup_seconds"]["immediate_p50"] is not None
    assert measures["token_saver"]["missed_actions"] == 0 and measures["performance"]["jobs"] == 0


def test_a_duplicate_is_one_event_through_its_turn_and_a_new_event_is_not(api):
    machine = task_bot(api, lanes=True)
    preset(api, "ops", "token_saver")
    task = new_task(api, "awaited")
    settled(api)
    body = "Unblocked: awaited\nThe migration is finished."
    states = lambda: [(j["state"], j["wake_class"]) for j in jobs(api) if j["body"] == body]
    notice(api, task, body, subtask="s1")
    notice(api, task, body, subtask="s1")                                # before the claim
    attempt = claim(api, machine)
    notice(api, task, body, subtask="s1")                                # while its turn runs
    with api.app.state.store.transaction() as c:                         # its turn ran for ten minutes
        c.execute("UPDATE messages SET created_at=? WHERE id=(SELECT message_id FROM jobs WHERE attempt_id=?)",
                  (H.shift(H.now(), minutes=-10), attempt["id"]))
        c.execute("UPDATE attempts SET created_at=? WHERE id=?", (H.shift(H.now(), minutes=-10), attempt["id"]))
    finish(api, machine, attempt)
    notice(api, task, body, subtask="s1")                                # just after it ended
    assert states() == [("completed", None)] + [("canceled", "duplicate")] * 3
    notice(api, task, body, subtask="s2")                                # the same words, another dependency
    with api.app.state.store.transaction() as c:                         # the same event, long after the turn ended
        c.execute("UPDATE attempts SET finished_at=? WHERE id=?",
                  (H.shift(H.now(), seconds=-W.DUPLICATE_SECONDS - 60), attempt["id"]))
    notice(api, task, body, subtask="s1")
    assert states()[-2:] == [("queued", None), ("queued", None)]
    # A person saying the same thing again after the bot answered is a new request.
    said = post(api, "chat/ops", {"text": "Yes"})
    with api.app.state.store.transaction() as c:
        H.say(c, "bot:ops", "human:ana", "Ship the other one as well?", conversation_id=said["conversation_id"])
    post(api, "chat/ops", {"text": "Yes"})
    assert [j["state"] for j in jobs(api) if j["body"] == "Yes"] == ["queued", "queued"]


@pytest.mark.parametrize("name,rooms", [("performance", 1), ("token_saver", 3)])
def test_a_batched_event_redelivered_after_its_batch_went_out_wakes_once_until_its_turn_is_over(api, name, rooms):
    machine = task_bot(api, lanes=True)
    preset(api, "ops", name)
    tasks = [new_task(api, "fix")] * 3 if rooms == 1 else [new_task(api, f"report {n}") for n in range(3)]
    settled(api)
    since, t0 = H.now(), H.shift(H.now(), hours=-1)
    sent = [(task, f"Finished: part {n}") for n, task in enumerate(tasks)]
    for n, (task, body) in enumerate(sent):
        notice(api, task, body, wake_batch="completion")
        at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, seconds=20 * n))
    tick(api, H.shift(t0, minutes=31))                                   # one room: the newest carries; else a digest
    released = len(jobs(api, since=since))
    redeliver = lambda: [notice(api, task, body, wake_batch="completion") for task, body in sent]
    redeliver()                                                          # while the carrier is queued
    attempt = claim(api, machine)
    start(api, machine, attempt)
    assert "running" in [j["state"] for j in jobs(api, since=since)]
    redeliver()                                                          # while it runs
    assert [(j["state"], j["wake_class"]) for j in jobs(api, since=since)[released:]] == [("canceled", "duplicate")] * 6
    finish(api, machine, attempt, started=True)
    redeliver()                                                          # just after it ended
    assert [(j["state"], j["wake_class"]) for j in jobs(api, since=since)[released:]] == [("canceled", "duplicate")] * 9
    with api.app.state.store.transaction() as c:                         # long after the turn ended: a new event
        c.execute("UPDATE attempts SET finished_at=? WHERE id=?",
                  (H.shift(H.now(), seconds=-W.DUPLICATE_SECONDS - 60), attempt["id"]))
    redeliver()
    assert all(j["state"] == "queued" for j in jobs(api, since=since)[-3:])


def test_a_batch_bigger_than_the_room_page_goes_out_as_a_digest_that_names_it(api):
    from backend.execution import HISTORY_PAGE
    preset(api, "ops", "performance")
    task = new_task(api, "noisy")
    since, t0 = H.now(), H.shift(H.now(), hours=-1)
    count = HISTORY_PAGE // 2 + 5
    for n in range(count):
        notice(api, task, f"{n} review comments on tico#412", wake_batch="review")
        at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, seconds=n))
    tick(api, H.shift(t0, minutes=5))
    rows = jobs(api, since=since)
    assert [j["state"] for j in rows] == ["canceled"] * count + ["queued"]
    digest = rows[-1]["body"]
    assert f"{count} updates" in digest and f"and {count - 1} more" in digest and task["conversation_id"] in digest
    with api.app.state.store.read() as c:                                # nothing is marked read on the bot's behalf
        assert c.execute("SELECT count(*) FROM messages m JOIN jobs j ON j.message_id=m.id WHERE j.wake_class='review' "
                         "AND m.delivered_at IS NOT NULL").fetchone()[0] == 0


def test_a_digest_over_many_rooms_names_the_newest_and_counts_the_rest(api):
    from backend import wake_presets as WP
    preset(api, "ops", "token_saver")
    tasks = [new_task(api, f"room {n}") for n in range(WP.DIGEST_ROOMS + 5)]
    since, t0 = H.now(), H.shift(H.now(), hours=-1)
    for n, task in enumerate(tasks):
        notice(api, task, f"Finished: part {n}", wake_batch="completion")
        at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, seconds=n))
    tick(api, H.shift(t0, minutes=31))
    digest = jobs(api, since=since)[-1]["body"]
    assert "and updates in 5 more rooms" in digest
    assert tasks[-1]["conversation_id"] in digest and tasks[0]["conversation_id"] not in digest   # newest first


def test_a_digest_turn_is_measured_from_each_event_and_split_over_its_tickets(api):
    machine = task_bot(api, lanes=True)
    preset(api, "ops", "token_saver")
    tasks = [new_task(api, f"report {n}") for n in range(3)]
    settled(api)
    since, t0 = H.now(), H.shift(H.now(), hours=-1)
    for n, task in enumerate(tasks):
        notice(api, task, f"Finished: part {n}", wake_batch="completion")
        job = jobs(api, since=since)[-1]
        at(api, job["id"], H.shift(t0, minutes=10 * n))
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE messages SET created_at=? WHERE id=(SELECT message_id FROM jobs WHERE id=?)",
                      (H.shift(t0, minutes=10 * n), job["id"]))
    tick(api, H.shift(t0, minutes=31))
    attempt = claim(api, machine)
    finish(api, machine, attempt, usage={"input_tokens": 900, "cached_tokens": 0, "output_tokens": 0, "model": "fake"})
    with api.app.state.store.transaction() as c:
        for task in tasks:
            H.task_update(c, "bot:ops", task["id"], status="done", note="Done")
    [token_saver] = [p for p in get(api, "wake-presets/measures?since=" + H.shift(t0, minutes=-1))["presets"]
                     if p["preset"] == "token_saver"]
    assert token_saver["turns"] == 1 and token_saver["tokens"] == 900 and token_saver["accepted_tickets"] == 3
    assert token_saver["tokens_per_ticket"] == 300 and token_saver["turns_per_ticket"] == 0.33
    assert token_saver["pickup_seconds"]["held_p50"] >= 50 * 60          # from each event, not from the digest



def stale_comments(api, task, n):
    """Tico's reminders left as comments on the task: news no rule wakes for, so a note that starts no job.
    A stale check that flags the owner's task wakes it at once instead (Dev 543, in test_urgent_news.py), so
    these reminders carry no stale-check flag."""
    with api.app.state.store.transaction() as c:
        for i in range(n):
            H.task_comment(c, H.KEEPER, task["id"], f"Still waiting on review {i}.")


def finished_requests(api, task, n):
    """Nonblocking news a preset batches: requests the bot asked for, finished (refs.wake_batch)."""
    with api.app.state.store.transaction() as c:
        for i in range(n):
            H._wake(c, H.task(c, task["id"]), "bot:ops", f"Finished: request {i}", {"wake_batch": "completion"})


def test_comments_start_no_turn_and_two_finished_requests_inside_the_window_give_one(api):
    preset(api, "ops", "performance")
    task = new_task(api, "tidy")
    settled(api)
    since, t0 = H.now(), H.shift(H.now(), hours=-1)
    stale_comments(api, task, 2)
    assert jobs(api, since=since) == []                 # notes for the next run (rules.default_note)
    finished_requests(api, task, 2)
    for n, job in enumerate(jobs(api, since=since)):
        at(api, job["id"], H.shift(t0, seconds=20 * n))
    tick(api, H.shift(t0, seconds=59))
    assert [(j["held"], j["wake_class"]) for j in jobs(api, since=since)] == [(1, "completion")] * 2
    tick(api, H.shift(t0, seconds=61))
    assert [(j["state"], j["held"]) for j in jobs(api, since=since)] == [("completed", 0), ("queued", 0)]


def test_a_persons_message_is_not_delayed_by_a_held_batch(api):
    preset(api, "ops", "token_saver")
    task = new_task(api, "tidy")
    settled(api)
    since = H.now()
    finished_requests(api, task, 1)
    tick(api, H.now())
    [held] = jobs(api, since=since)
    assert held["held"] == 1 and held["hold_until"] > H.now()
    post(api, f"tasks/{task['id']}/comments", {"text": "Please also tidy the README."})  # the person who filed it
    post(api, "chat/ops", {"text": "And send me the link when it is up."})
    tick(api, H.now())
    assert [(j["state"], j["held"], j["hold_until"]) for j in jobs(api, since=since)[1:]] == [("queued", 0, None)] * 2



def test_a_persons_act_through_botops_wakes_the_bot_at_once(api, botops):
    """BotOps marking done, for the person, a task a bot asked them for: Tico's notice to that bot is the person's act,
    as the same act from the app is, and no batch holds it."""
    preset(api, "ops", "token_saver")
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Approve the vendor list", "Please check it.", "human:ana")
    attempt = turn(api, botops, person="ana-test", text="Mark the vendor list done")
    since = H.now()
    version = get(api, "tasks/" + task["id"])["task"]["version"]
    done = act(api, attempt, "POST", "tasks/" + task["id"], {"status": "done", "version": version})
    assert done.status_code == 200, done.text
    [told] = jobs(api, since=since)
    assert (told["state"], told["held"], told["wake_class"]) == ("queued", 0, None)


# A held notice is queued but starts no run: every immediate wake that checks for a run already waiting still goes out.

def held_notice(api, task):
    finished_requests(api, task, 1)
    [held] = [j for j in jobs(api) if j["wake_class"] == "completion"]
    assert held["held"] == 1 and held["state"] == "queued"
    return held


def test_run_now_wakes_the_bot_while_a_notice_on_the_task_is_held(api):
    preset(api, "ops", "token_saver")
    task = new_task(api, "tidy")
    settled(api)
    held_notice(api, task)
    assert post(api, f"tasks/{task['id']}/run-now", {})["queued"] is True
    [run] = [j for j in jobs(api) if j["body"].startswith("Run now:")]
    assert (run["state"], run["held"]) == ("queued", 0)


def test_a_resumed_task_wakes_while_a_notice_on_it_is_held(api):
    from backend import quiet_work
    preset(api, "ops", "token_saver")
    task = new_task(api, "tidy")
    settled(api)
    held_notice(api, task)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET next_run=1, resume=? WHERE id=?", (json.dumps({"until": H.now()}), task["id"]))
        quiet_work.resume(c, H.task(c, task["id"]), "Its date came.", "schedule")
    [resumed] = [j for j in jobs(api) if j["body"].startswith("Resume:")]
    assert (resumed["state"], resumed["held"]) == ("queued", 0)


def test_an_update_request_and_an_automatic_start_go_out_while_a_notice_is_held(api):
    from backend import updates
    from backend.tests.test_auto_tasks import deck, take
    machine = task_bot(api, lanes=True)
    preset(api, "ops", "token_saver")
    [task] = deck(api, 1)
    held_notice(api, task)
    with api.app.state.store.read() as c:
        assert not updates._busy(c, "ops")
    started = take(api, machine)
    assert started and started["task"]["id"] == task["id"] and started["message"]["body"].startswith("Start assigned task")
