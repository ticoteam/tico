"""A card's one status line (task_reads.status_line): Starting, Up next, Working, Needs you or Paused, on every task
read, and sent again over the live stream when a run on the task is leased, starts or ends."""

import json
import time

from backend import cloud_machines as CM
from backend import events as E
from backend import task_calls, task_list_routes
from backend import task_engine as TE
from backend import task_reads as R
from backend import waiting_on as W
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import assign, claim, get, post, ready, runner  # noqa: F401


def test_each_state_comes_from_what_the_task_holds_now():
    bot = W.Wait(W.BOT, "bot:ops", "Count the words")
    assert R.status_line("open", bot) == "up_next"
    assert R.status_line("open", bot, "leased") == "starting"
    assert R.status_line("open", bot, "running") == "working"
    assert R.status_line("open", W.Wait(W.PERSON, "human:ana", "OK the page")) == "needs_you"
    paused = W.Wait(W.PAUSED, "bot:ops", "Ops has no computer", href="#/settings/computers")
    assert R.status_line("open", paused) == "paused"
    # Its cloud computer is on its way: it starts by itself.
    assert R.status_line("open", paused, machine=R.WAKING) == "starting"
    # Its enrolled cloud computer failed to start before any run was claimed.
    assert R.status_line("open", bot, machine=R.FAILED) == "paused"
    assert R.status_line("open", bot, "running", machine=R.FAILED) == "working"
    # None of the five: finished, nobody's turn, or a wait on something else.
    assert R.status_line("done", bot, "running") is None
    assert R.status_line("open", None) is None
    assert R.status_line("open", W.Wait(W.PERSON, None, "Nobody has it")) is None
    assert R.status_line("open", W.Wait(W.TASKS, "t1", "A sub-task is not finished")) is None


def test_paused_links_to_its_fix_a_company_fix_first():
    # The turn carries where Paused's Fix goes: a company's fix (Connect GitHub) before the page that shows the cause.
    github = W.Wait(W.PAUSED, "bot:ops", "Connect GitHub", fix="#/tools/github", href="#/settings/computers")
    assert W.view(github, lambda _t: True)["href"] == "#/tools/github"
    assert W.view(W.Wait(W.PAUSED, "bot:ops", "x", href="#/settings/computers"), lambda _t: True)["href"] == (
        "#/settings/computers"
    )
    assert "href" not in W.view(W.Wait(W.PAUSED, "bot:ops", "Ops is paused"), lambda _t: True)


def lines(api, task_id):
    row = next(t for t in get(api, "tasks?owner=ops&status=open")["tasks"] if t["id"] == task_id)
    return row["status_line"], get(api, f"tasks/{task_id}")["task"]["status_line"]


def run_changes(api, task_id):
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT count(*) FROM changes WHERE topic='tasks' AND kind='run' AND subject_id=?", (task_id,)
        ).fetchone()[0]


def test_a_run_moving_on_a_task_changes_its_line_on_the_list_and_its_read_and_tells_the_stream(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Count the words", "body": "Count them."})
    assert lines(api, task["id"]) == ("up_next", "up_next")
    attempt = claim(api, machine)
    assert lines(api, task["id"]) == ("starting", "starting")
    assert run_changes(api, task["id"]) == 1
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "thread"}, machine["token"])
    assert lines(api, task["id"]) == ("working", "working")
    assert run_changes(api, task["id"]) == 2
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "12 words."},
        machine["token"],
    )
    assert "working" not in lines(api, task["id"])
    assert run_changes(api, task["id"]) == 3


def two_tasks(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    first = post(api, "tasks", {"owner": "ops", "title": "Count the words", "body": "Count them."})
    second = post(api, "tasks", {"owner": "ops", "title": "Count the lines", "body": "Count them."})
    return machine, first, second


def test_a_run_is_on_the_task_it_names_not_the_chat_it_came_from_on_the_reads_and_the_stream(api):
    machine, first, second = two_tasks(api)
    attempt = claim(api, machine)
    before = {first["id"]: run_changes(api, first["id"]), second["id"]: run_changes(api, second["id"])}
    with api.app.state.store.transaction() as c:
        named = c.execute("SELECT task_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
        other = second["id"] if named == first["id"] else first["id"]
        # A carried message's run in this task's chat is the other task's (execution: task_wakes.message_task).
        c.execute("UPDATE attempts SET task_id=? WHERE id=?", (other, attempt["id"]))
    assert lines(api, named) == ("up_next", "up_next")
    assert lines(api, other) == ("starting", "starting")
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "thread"}, machine["token"])
    assert lines(api, other) == ("working", "working") and lines(api, named) == ("up_next", "up_next")
    assert (run_changes(api, other) - before[other], run_changes(api, named) - before[named]) == (1, 0)
    # A run that names no task is on the one open task of its chat, on both.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET task_id=NULL WHERE id=?", (attempt["id"],))
        R.run_moved(c, attempt["id"])
    assert lines(api, named) == ("working", "working") and lines(api, other) == ("up_next", "up_next")
    assert run_changes(api, named) - before[named] == 1


def test_an_enrolled_cloud_computer_waking_says_starting_and_tells_the_stream(api):
    machine, first, _ = two_tasks(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO cloud_machines(bot, placement, runner_id, state) VALUES('ops', 'own', ?, 'stopped')",
            (machine["runner_id"],),
        )
    assert lines(api, first["id"]) == ("up_next", "up_next")
    with api.app.state.store.transaction() as c:
        CM.save(c, "ops", state="starting")
    assert lines(api, first["id"]) == ("starting", "starting")
    with api.app.state.store.read() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM changes WHERE topic='tasks' AND kind='status' AND subject_id=?", (first["id"],)
            ).fetchone()[0]
            == 1
        )


def test_a_cloud_start_that_fails_before_any_lease_reaches_the_card_on_the_stream(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Count the words", "body": "Count them."})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO cloud_machines(bot, machine_id, state) VALUES('_shared', 'm1', 'starting')")
        c.execute("INSERT INTO cloud_machines(bot) VALUES('ops')")
    assert lines(api, task["id"]) == ("starting", "starting")
    store, auth = api.app.state.store, api.app.state.auth
    who = Identity("human:ana", "human", "ana@acme.example")
    task_views = task_list_routes.readers(auth)[2]
    with store.read() as c:
        cursor = E.latest(c)
    with store.transaction() as c:
        CM.save(c, "_shared", problem="no_enroll")
    with store.read() as c:
        events = E.read(c, auth, who, cursor, topics=("tasks",), task_views=task_views)[0]
    sent = [e[2]["task"] for e in events if e[2].get("id") == task["id"]]
    assert sent and sent[-1]["status_line"] == "paused" and sent[-1]["turn"]["href"] == "#/settings/computers"


def test_a_company_fix_heads_the_paused_waits_on_the_list_and_the_tasks_own_read(company):
    with company.transaction() as c:
        X.bot(c, "builder")
        # Two Paused waits: its paused bot's own work, and Connect GitHub (a company fix) for the PR.
        ship = (
            {"name": "Ship", "owner": "bot:builder", "requirements": [{"kind": "owner_done"}, X.github("pr_open")]},
        )
        X.pipeline(c, "ship", "Shipping", ship)
        task = X.new_task(c, X.ANA, "Open the PR", "ship", assignee="bot:builder")
        c.execute("UPDATE bots SET state='paused' WHERE id='builder'")
        paused = [w for w in W.of(c, TE.task(c, task["id"])) if w.state == W.PAUSED]
        assert len(paused) == 2 and paused[0].fix and not paused[1].fix
        row = R.brief(c, H.tasks(c, ids=[task["id"]], limit=None, order="finished", visible="1"))[0]
        detail = task_calls.task_view(c, task["id"], X.ANA)
        assert row["turn"]["href"] == detail["turn"]["href"] == paused[0].fix
        assert row["status_line"] == detail["status_line"] == "paused"


def test_a_stopped_visit_does_not_say_up_next(api):
    _, task, _ = two_tasks(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE visits SET level='person' WHERE task_id=? AND left_at IS NULL", (task["id"],))
    assert lines(api, task["id"]) == (None, None)
    row = next(t for t in get(api, "tasks?owner=ops&status=open")["tasks"] if t["id"] == task["id"])
    assert row["stuck"] is True and row["turn"]["state"] == "bot"


def changed(api, task_id):
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT count(*) FROM changes WHERE topic='tasks' AND kind IN ('status','run') AND subject_id=?",
            (task_id,),
        ).fetchone()[0]


def test_a_computer_assigned_or_a_hold_on_its_agent_sends_the_task_again(api):
    from backend import holds

    task = post(api, "tasks", {"owner": "ops", "title": "Count the words", "body": "Count them."})
    assert lines(api, task["id"]) == ("paused", "paused")  # no computer
    machine = runner(api)
    seen = changed(api, task["id"])
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    assert lines(api, task["id"]) == ("up_next", "up_next") and changed(api, task["id"]) == seen + 1
    soon = "2099-01-01T00:00:00.000000Z"
    with api.app.state.store.transaction() as c:
        # A failed-run hold (stored as `failing`) waiting out its back-off: the agent still says Up next.
        holds.put(c, holds.FAILING, "bot", "ops", actor=H.TICO_ACTOR, reason="Runs fail", until_at=soon)
    assert lines(api, task["id"]) == ("up_next", "up_next") and changed(api, task["id"]) == seen + 2
    with api.app.state.store.transaction() as c:
        # The back-off becomes a stop (no until_at): Paused, sent again.
        holds.put(c, holds.FAILING, "bot", "ops", actor=H.TICO_ACTOR, reason="Runs fail")
    assert lines(api, task["id"]) == ("paused", "paused") and changed(api, task["id"]) == seen + 3
    with api.app.state.store.transaction() as c:
        holds.release(c, holds.FAILING, "bot", "ops", actor=H.TICO_ACTOR, how="person")
    assert lines(api, task["id"]) == ("up_next", "up_next") and changed(api, task["id"]) == seen + 4


def test_one_change_per_task_per_transaction_however_many_places_send_it(api):
    machine, first, _ = two_tasks(api)
    attempt = claim(api, machine)
    with api.app.state.store.read() as c:
        named = c.execute("SELECT task_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
    seen = changed(api, named)
    with api.app.state.store.transaction() as c:
        R.status_moved(c, ["ops"])
        R.status_moved(c, ["bot:ops"], [named])
        R.run_moved(c, attempt["id"])
    assert changed(api, named) == seen + 1
    with api.app.state.store.transaction() as c:
        R.status_moved(c, ["ops"])
    assert changed(api, named) == seen + 2


def test_a_run_in_a_shared_chat_naming_no_task_is_on_none_and_a_closed_task_gets_nothing(api):
    machine, first, second = two_tasks(api)
    attempt = claim(api, machine)
    with api.app.state.store.transaction() as c:
        named = c.execute("SELECT task_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
        other = second["id"] if named == first["id"] else first["id"]
        chat = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (named,)).fetchone()[0]
        # Two open tasks in one chat, and a run that names neither, by its attempt, its message or its wake.
        c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (chat, other))
        c.execute("UPDATE attempts SET task_id=NULL, message_id=NULL WHERE id=?", (attempt["id"],))
        c.execute(
            "UPDATE messages SET wake_key=NULL, refs='{}' WHERE id=(SELECT j.message_id FROM attempts a "
            "JOIN jobs j ON j.id=a.job_id WHERE a.id=?)",
            (attempt["id"],),
        )
    assert lines(api, named) == ("up_next", "up_next") and lines(api, other) == ("up_next", "up_next")
    before = run_changes(api, named) + run_changes(api, other)
    with api.app.state.store.transaction() as c:
        R.run_moved(c, attempt["id"])
    assert run_changes(api, named) + run_changes(api, other) == before
    # Named again, on a task now closed: no change for it.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET task_id=? WHERE id=?", (named, attempt["id"]))
        H.task_close(c, "human:ana", named, note="Not needed.")
    seen = run_changes(api, named)
    with api.app.state.store.transaction() as c:
        R.run_moved(c, attempt["id"])
    assert run_changes(api, named) == seen


def test_an_enrolled_cloud_computer_that_failed_to_start_says_paused_with_its_fix(api):
    machine, first, _ = two_tasks(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO cloud_machines(bot, placement, runner_id, state) VALUES('ops', 'own', ?, 'stopped')",
            (machine["runner_id"],),
        )
        CM.save(c, "ops", problem="hq:capacity")
    assert lines(api, first["id"]) == ("paused", "paused")
    row = next(t for t in get(api, "tasks?owner=ops&status=open")["tasks"] if t["id"] == first["id"])
    detail = get(api, f"tasks/{first['id']}")["task"]
    for turn in (row["turn"], detail["turn"]):
        assert turn["href"] == "#/settings/computers" and "didn't start" in turn["line"]


def test_a_run_naming_its_task_by_task_id_in_its_refs_is_on_that_task_before_its_wake(api):
    machine, first, second = two_tasks(api)
    attempt = claim(api, machine)
    with api.app.state.store.transaction() as c:
        named = c.execute("SELECT task_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
        other = second["id"] if named == first["id"] else first["id"]
        message = c.execute("SELECT message_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
        c.execute("UPDATE attempts SET task_id=NULL WHERE id=?", (attempt["id"],))
        c.execute("UPDATE messages SET refs=json_object('task_id', ?) WHERE id=?", (other, message))
    assert lines(api, other) == ("starting", "starting") and lines(api, named) == ("up_next", "up_next")


def test_the_cloud_starts_own_state_says_paused_or_starting(api):
    machine, first, _ = two_tasks(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO cloud_machines(bot, placement, runner_id, state) VALUES('ops', 'own', ?, 'failed')",
            (machine["runner_id"],),
        )
    # HQ says the start failed, with no problem code: Paused, fixed on Settings > Computers.
    assert lines(api, first["id"]) == ("paused", "paused")
    row = next(t for t in get(api, "tasks?owner=ops&status=open")["tasks"] if t["id"] == first["id"])
    assert row["turn"]["href"] == "#/settings/computers"
    with api.app.state.store.transaction() as c:
        CM.save(c, "ops", state="running", problem=CM.OFFLINE_RESTARTING)
    # Restarting after it went offline: it starts by itself.
    assert lines(api, first["id"]) == ("starting", "starting")


def test_a_runs_refs_are_read_as_the_message_ref_parser_reads_them(api):
    machine, first, second = two_tasks(api)
    attempt = claim(api, machine)
    with api.app.state.store.transaction() as c:
        named = c.execute("SELECT task_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
        other = second["id"] if named == first["id"] else first["id"]
        message = c.execute("SELECT message_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
        c.execute("UPDATE attempts SET task_id=NULL WHERE id=?", (attempt["id"],))
    for refs in ({"task": ["", f" {other} "]}, {"task": None, "task_id": [other]}):
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps(refs), message))
            assert H.refs_task_id(refs).strip() == other
        assert lines(api, other) == ("starting", "starting") and lines(api, named) == ("up_next", "up_next")


def test_a_cloud_machine_hq_says_failed_before_its_computer_enrolled_says_paused_with_its_fix(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Count the words", "body": "Count them."})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO cloud_machines(bot, machine_id, state) VALUES('_shared', 'm1', 'failed')")
        c.execute("INSERT INTO cloud_machines(bot) VALUES('ops')")
    assert lines(api, task["id"]) == ("paused", "paused")
    row = next(t for t in get(api, "tasks?owner=ops&status=open")["tasks"] if t["id"] == task["id"])
    assert row["turn"]["href"] == "#/settings/computers"


def test_a_failed_restart_says_paused_until_its_computer_checks_in_even_while_hq_says_starting(api):
    machine, first, _ = two_tasks(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO cloud_machines(bot, placement, runner_id, state) VALUES('ops', 'own', ?, 'starting')",
            (machine["runner_id"],),
        )
        c.execute(
            "UPDATE runner_states SET last_seen_at='2000-01-01T00:00:00.000000Z' WHERE runner_id=?",
            (machine["runner_id"],),
        )
        H.event(c, H.TICO_ACTOR, "cloud_machine.restart_failed", "ops", {"machine": "m1"})
    assert lines(api, first["id"]) == ("paused", "paused")
    ready(api, machine, ["ops"])  # its computer checks in: up, and no longer Paused
    assert lines(api, first["id"]) == ("up_next", "up_next")


def test_a_live_lease_says_starting_or_working_whatever_its_machine_says(api):
    machine, first, second = two_tasks(api)
    attempt = claim(api, machine)
    with api.app.state.store.transaction() as c:
        named = c.execute("SELECT task_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
        c.execute(
            "INSERT INTO cloud_machines(bot, placement, runner_id, state) VALUES('ops', 'own', ?, 'failed')",
            (machine["runner_id"],),
        )
    other = second["id"] if named == first["id"] else first["id"]
    assert lines(api, named) == ("starting", "starting") and lines(api, other) == ("paused", "paused")
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "thread"}, machine["token"])
    assert lines(api, named) == ("working", "working")


def test_a_runs_refs_are_the_parsers_own_answer_whitespace_and_all(api):
    machine, first, second = two_tasks(api)
    attempt = claim(api, machine)
    with api.app.state.store.transaction() as c:
        named = c.execute("SELECT task_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
        other = second["id"] if named == first["id"] else first["id"]
        message = c.execute("SELECT message_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
        c.execute("UPDATE attempts SET task_id=NULL WHERE id=?", (attempt["id"],))
        refs = {"task": [7, "\t", f"\t{other}\n"]}
        c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps(refs), message))
    assert H.refs_task_id(refs) == other
    assert lines(api, other) == ("starting", "starting") and lines(api, named) == ("up_next", "up_next")


def test_a_check_in_newer_than_a_recorded_failed_start_clears_it(api):
    machine, first, _ = two_tasks(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO cloud_machines(bot, placement, runner_id, state, updated_at) "
            "VALUES('ops', 'own', ?, 'failed', '2001-01-01T00:00:00.000000Z')",
            (machine["runner_id"],),
        )
        c.execute(
            "UPDATE runner_states SET last_seen_at='2000-01-01T00:00:00.000000Z' WHERE runner_id=?",
            (machine["runner_id"],),
        )
    assert lines(api, first["id"]) == ("paused", "paused")
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE runner_states SET last_seen_at='2002-01-01T00:00:00.000000Z' WHERE runner_id=?",
            (machine["runner_id"],),
        )
    assert lines(api, first["id"]) == ("up_next", "up_next")


def test_a_start_after_a_failed_restart_says_starting_until_its_computer_checks_in(api):
    machine, first, _ = two_tasks(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO cloud_machines(bot, placement, runner_id, state) VALUES('ops', 'own', ?, 'running')",
            (machine["runner_id"],),
        )
        c.execute(
            "UPDATE runner_states SET last_seen_at='2000-01-01T00:00:00.000000Z' WHERE runner_id=?",
            (machine["runner_id"],),
        )
        H.event(c, H.TICO_ACTOR, "cloud_machine.restart_failed", "ops", {"machine": "m1"})
    assert lines(api, first["id"]) == ("paused", "paused")
    with api.app.state.store.transaction() as c:
        # Started again after the timeout; HQ has already let go of start_asked_at and says running.
        c.execute("UPDATE cloud_machines SET woke_at=?, start_asked_at=NULL WHERE bot='ops'", (time.time() + 5,))
    assert lines(api, first["id"]) == ("starting", "starting")
    ready(api, machine, ["ops"])  # its computer checks in
    assert lines(api, first["id"]) == ("up_next", "up_next")
