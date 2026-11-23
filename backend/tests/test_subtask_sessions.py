"""A subtask continues its parent task's session (hubdb.session_root), and a person finishing one under a
bot's task wakes the bot on the parent."""

import json

import pytest

from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, ready, runner  # noqa: F401  (api is a fixture)
from backend.tests.helpers.task_lanes import RUNNER_CAPABILITIES
from backend import bot_settings as BS, delivery, execution, migrations


def tasks(api, *specs):
    """Each spec is (owner, parent index or None), filed by ana; returns their ids."""
    ids = []
    with api.app.state.store.transaction() as c:
        for n, (owner, parent) in enumerate(specs):
            row = H.task_create(
                c,
                "human:ana",
                f"Step {n} of the launch",
                "Do this step.",
                owner,
                parent_id=ids[parent] if parent is not None else None,
                lint=False,
            )
            ids.append(row["id"])
    return ids


def test_the_root_walks_up_same_owner_parents_and_stops_at_another_owner_or_a_loop(api):
    top, mid, person, under_person, own = tasks(
        api, ("bot:ops", None), ("bot:ops", 0), ("human:ana", 1), ("bot:ops", 2), ("bot:ops", 1)
    )
    with api.app.state.store.transaction() as c:
        assert H.session_root(c, "ops", own) == top
        assert H.session_root(c, "ops", person) == top  # a person's subtask under the bot's task
        assert H.session_root(c, "ops", under_person) == under_person
        assert H.session_root(c, "ops", own, {"subtask_session": "own"}) == own
        # A loop in the parent links ends the walk instead of spinning.
        c.execute(
            "INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'parent',?)", (top, own, H.now())
        )
        assert H.session_root(c, "ops", own) == top


def test_a_subtask_the_bot_filed_for_itself_is_its_own_root(api):
    (parent,) = tasks(api, ("bot:ops", None))
    with api.app.state.store.transaction() as c:
        own = H.task_create(c, "bot:ops", "Build the export", "Its own work.", "bot:ops", parent_id=parent, lint=False)[
            "id"
        ]
        asked = H.task_create(c, "human:ana", "Add the CSV column", "x", "bot:ops", parent_id=own, lint=False)["id"]
        assert H.session_root(c, "ops", own) == own
        assert H.session_root(c, "ops", asked) == own, "a person's subtask under it continues its session"


def filed_wakes(c, since):
    """The tasks the engine woke ops on since `since` because what they filed ended (task_engine._ended)."""
    return [
        r[0]
        for r in c.execute(
            "SELECT json_extract(m.refs,'$.task') FROM jobs j JOIN messages m ON m.id=j.message_id "
            "WHERE j.bot_id='ops' AND j.state='queued' AND m.wake_key LIKE '%:filed:%' AND m.rowid>? ORDER BY m.rowid",
            (since,),
        )
    ]


def test_finishing_the_last_subtask_wakes_its_parent_once_whatever_session_it_ran_in(api):
    # v3: a parent waits on what it filed and is woken once when that ends. A subtask that continued the parent's
    # session wakes it too: the parent's turn is what moves the parent on.
    first, second = tasks(api, ("bot:ops", None), ("bot:ops", None))
    with api.app.state.store.transaction() as c:
        asked = H.task_create(c, "human:ana", "Add the CSV column", "x", "bot:ops", parent_id=first, lint=False)["id"]
        own = H.task_create(c, "bot:ops", "Build the export", "Its own work.", "bot:ops", parent_id=second, lint=False)[
            "id"
        ]
        assert H.session_root(c, "ops", asked) == first and H.session_root(c, "ops", own) == own
        since = c.execute("SELECT max(rowid) FROM messages").fetchone()[0]
        H.task_done(c, "bot:ops", asked, "Column added.", H.task(c, asked)["version"])
        H.task_done(c, "bot:ops", own, "Export built.", H.task(c, own)["version"])
        assert filed_wakes(c, since) == [first, second]


def test_a_private_subtask_shares_a_private_parents_session_only_when_both_have_the_same_readers(api):
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "human:ana", "Plan the private launch", "x", "bot:ops", private=True, lint=False)[
            "id"
        ]
        shared = H.task_create(
            c, "human:ana", "Draft the private copy", "x", "bot:ops", parent_id=parent, private=True, lint=False
        )["id"]
        # The bot's own private subtask: ana, on the parent, cannot read it.
        apart = H.task_create(
            c, "human:ana", "Check the private figures", "x", "bot:ops", parent_id=parent, private=True, lint=False
        )["id"]
        c.execute("UPDATE tasks SET requester_id='bot:ops' WHERE id=?", (apart,))
        public = H.task_create(c, "human:ana", "Draft the public copy", "x", "bot:ops", parent_id=parent, lint=False)[
            "id"
        ]
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (public,))  # made public later
        assert H.session_root(c, "ops", shared) == parent
        assert H.session_root(c, "ops", apart) == apart
        assert H.session_root(c, "ops", public) == public


def roots_runner(api, mode="task"):
    with api.app.state.store.transaction() as c:
        config = BS.read(c, 'ops')
        BS.write(c, 'ops', {**config, 'session': mode})
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"], RUNNER_CAPABILITIES)
    return machine


def test_a_subtask_turn_runs_in_its_parents_lane_and_session_and_links_and_checkpoints_the_parent(api):
    machine = roots_runner(api)
    (parent,) = tasks(api, ("bot:ops", None))
    first = claim(api, machine)
    assert first["lane"] == "task:" + parent and first["task"]["session_task_id"] == parent
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t-parent"}, machine["token"])
    with api.app.state.store.transaction() as c:  # filed for the bot under its task, mid-turn
        sub = H.task_create(c, "human:ana", "Step 1 of the launch", "Do this step.", "bot:ops", parent_id=parent)["id"]
    assert claim(api, machine) is None, "the subtask waits behind its parent's turn in the same lane"
    post(
        api,
        f"attempts/{first['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Planned."},
        machine["token"],
    )
    turn = claim(api, machine)
    assert turn["task"]["id"] == sub
    assert (turn["lane"], turn["task"]["session_task_id"]) == ("task:" + parent, parent)
    assert turn["task"]["checkpoint"] == "Planned.", "a fresh session starts from the root's checkpoint"
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-parent"}, machine["token"])
    with api.app.state.store.transaction() as c:  # moved out from under the parent mid-turn
        c.execute("DELETE FROM task_relations WHERE from_task=? AND kind='parent'", (sub,))
    post(
        api,
        f"attempts/{turn['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Step 1 drafted."},
        machine["token"],
    )
    with api.app.state.store.read() as c:
        assert [link["url"] for link in H.task_links(c, parent) if link["kind"] == "session"] == [
            "session:ops/t-parent"
        ]
        assert [link for link in H.task_links(c, sub) if link["kind"] == "session"] == []
        # The thread lives in the url; the API's detail still names it, and the stored detail does not repeat it.
        [parent_link] = [link for link in H.task_links(c, parent) if link["kind"] == "session"]
        assert json.loads(parent_link["detail"])["thread_id"] == "t-parent"
        stored = c.execute("SELECT detail FROM task_links WHERE id=?", (parent_link["id"],)).fetchone()[0]
        assert "thread_id" not in json.loads(stored) and json.loads(stored)["bot"] == "ops"
    assert get(api, "tasks/" + parent)["task"]["checkpoint"] == "Step 1 drafted."
    assert not get(api, "tasks/" + sub)["task"].get("checkpoint")


@pytest.mark.parametrize("mode", ["task", "bot"])
def test_only_started_moves_advance_the_fence_of_the_shared_session(api, mode):
    first_machine = roots_runner(api, mode)
    second_machine = runner(api, label="Second computer")
    parent, generation = None, 1
    for index, (machine, fence) in enumerate(
        [(first_machine, 0), (second_machine, 1), (first_machine, 0), (second_machine, 1), (first_machine, 2)]
    ):
        if index:
            generation = assign(api, machine, "ops", generation=generation)["generation"]
            ready(api, machine, ["ops"], RUNNER_CAPABILITIES)
        if index != 2:  # The previous machine handed that same job back before start.
            with api.app.state.store.transaction() as c:
                task = H.task_create(
                    c, "human:ana", f"Launch step {index}", "Do this step.", "bot:ops", parent_id=parent, private=False
                )
            if mode == "task" and parent is None:
                parent = task["id"]
        turn = claim(api, machine)
        assert turn["task"]["id"] == task["id"] and turn["session_fence"] == fence
        if mode == "task":
            assert turn["task"]["session_task_id"] == parent
        if index != 1:
            post(api, f"attempts/{turn['id']}/started", {"thread_id": f"thread-{index}"}, machine["token"])
        result = {"outcome": "interrupted", "yielded": True} if index == 1 else {"outcome": "completed"}
        post(api, f"attempts/{turn['id']}/complete", {**result, "last_seq": 0}, machine["token"])


@pytest.mark.parametrize("mode", ["task", "bot"])
def test_the_first_claim_after_upgrade_fences_an_older_thread_on_the_target_computer(api, mode):
    target = roots_runner(api, mode)
    latest = runner(api, label="Second computer")
    (parent,) = tasks(api, ("bot:ops", None))

    def finish(machine, name):
        turn = claim(api, machine)
        post(api, f"attempts/{turn['id']}/started", {"thread_id": name}, machine["token"])
        post(api, f"attempts/{turn['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])

    finish(target, "thread-older")
    generation = assign(api, latest, "ops", generation=1)["generation"]
    ready(api, latest, ["ops"], RUNNER_CAPABILITIES)
    with api.app.state.store.transaction() as c:
        H.task_create(
            c,
            "human:ana",
            "Latest step",
            "Continue.",
            "bot:ops",
            parent_id=parent if mode == "task" else None,
            private=False,
        )
    finish(latest, "thread-latest")

    with api.app.state.store.transaction() as c:
        c.execute("DROP TABLE session_computers")
        c.execute("DROP INDEX attempts_saved_session")
        c.execute("ALTER TABLE attempts DROP COLUMN computer_session_key")
        migration = next(m for m in migrations.files() if m.version.endswith("_session_computers"))
        migrations._module(migration).up(c)
        key = "task:" + parent if mode == "task" else "bot"
        plan = [r["detail"] for r in c.execute("EXPLAIN QUERY PLAN " + execution.LATEST_SESSION_RUN_SQL, ("ops", key))]
        assert len(plan) == 1 and "attempts_saved_session (bot_id=? AND computer_session_key=?)" in plan[0]
        H.task_create(c, "human:ana", "Next step", "Continue.", "bot:ops", parent_id=parent, private=False)
    assign(api, target, "ops", generation=generation)
    ready(api, target, ["ops"], RUNNER_CAPABILITIES)
    assert claim(api, target)["session_fence"] == 1


@pytest.mark.parametrize("retired", [False, True])
def test_an_exact_continuation_waits_for_its_computer_and_preserves_the_thread(api, retired):
    home = roots_runner(api)
    other = runner(api, label="Second computer")
    tasks(api, ("bot:ops", None))
    first = claim(api, home)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "saved-here"}, home["token"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": 0}, home["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET result=json_set(result,'$.lease_continue',1) WHERE id=?", (first["id"],))
        c.execute(
            "UPDATE jobs SET state='queued',created_at=? WHERE id=?",
            (H.shift(H.now(), minutes=-60), first["job_id"]),
        )

    generation = assign(api, other, "ops", generation=first["generation"])["generation"]
    ready(api, other, ["ops"], RUNNER_CAPABILITIES)
    assert claim(api, other) is None
    [held] = [row for row in get(api, "health")["stuck"] if row["reason"] == "continuation_home"]
    assert "computer that saved its session" in held["why"]

    if retired:
        post(api, f"computers/{home['runner_id']}/revoke", {})
        machine = other
    else:
        assign(api, home, "ops", generation=generation)
        ready(api, home, ["ops"], RUNNER_CAPABILITIES)
        machine = home
    following = claim(api, machine)
    assert following["job_id"] == first["job_id"] and following["continue_thread"] == "saved-here"
    post(api, f"attempts/{following['id']}/started", {"thread_id": "saved-here"}, machine["token"])
    if retired:
        post(
            api,
            f"attempts/{following['id']}/complete",
            {"outcome": "failed", "last_seq": 0, "text": "The saved thread could not be resumed on this computer."},
            machine["token"],
        )
    with api.app.state.store.read() as c:
        assert tuple(c.execute("SELECT runner_id,fence FROM session_computers WHERE bot_id='ops'").fetchone()) == (
            home["runner_id"],
            0,
        )
        if retired:
            assert c.execute("SELECT state FROM jobs WHERE id=?", (first["job_id"],)).fetchone()[0] == "uncertain"


@pytest.mark.parametrize("homes", ["root", "child", "split"])
def test_subtask_prefers_its_own_worktrees_and_reports_split_homes(api, homes):
    old = roots_runner(api)
    (parent,) = tasks(api, ("bot:ops", None))
    first = claim(api, old)
    assert first["task"]["id"] == parent
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at,repo,branch,computer_id,path) "
            "VALUES('fixture-tree',?,'worktree','worktree:fixture','present','bot:ops',?,'Acme/product',"
            "'tico/fixture',?,'tasks/fixture/product')",
            (parent, H.now(), old["runner_id"]),
        )
    post(api, f"attempts/{first['id']}/started", {"thread_id": "old-thread"}, old["token"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": 0}, old["token"])
    with api.app.state.store.transaction() as c:
        child = H.task_create(c, "human:ana", "Launch follow-up", "Do this step.", "bot:ops", parent_id=parent)["id"]

    new = runner(api, label="Second computer")
    assign(api, new, "ops", generation=first["generation"])
    ready(api, new, ["ops"], RUNNER_CAPABILITIES)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET last_seen_at=NULL WHERE runner_id=?", (old["runner_id"],))
        c.execute("UPDATE jobs SET created_at=? WHERE state='queued'", (H.shift(H.now(), minutes=-60),))
        if homes != "root":
            c.execute(
                "INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at,computer_id,repo,path) "
                "VALUES('child-tree',?,'worktree','worktree:child','present','bot:ops',?,?,'Acme/child','tasks/child')",
                (child, H.now(), new["runner_id"]),
            )
        if homes == "split":
            c.execute(
                "INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at,computer_id) "
                "VALUES('child-other-tree',?,'worktree','worktree:child-other','present','bot:ops',?,?)",
                (child, H.now(), old["runner_id"]),
            )
    if homes == "child":
        turn = claim(api, new)
        assert turn["task"]["id"] == child
        assert [tree["repo"] for tree in turn["worktrees"]] == ["Acme/child"]
        return
    assert claim(api, new) is None
    health = get(api, "health")
    reason = "worktree_conflict" if homes == "split" else "worktree_home"
    [stuck] = [item for item in health["stuck"] if item["reason"] == reason]
    assert "Test Mac" in stuck["why"]
    if homes == "split":
        assert "Second computer" in stuck["why"]
    assert stuck["fix"]["tab"] == "devices"
    if homes == "root":
        # A run already on the same task still holds it when its saved lane differs from the queued job's lane.
        store = api.app.state.store
        with store.transaction() as c:
            c.execute("UPDATE attempts SET state='running',task_id=?,lane='main' WHERE id=?", (child, first["id"]))
        with store.read() as c:
            assert execution.bot_claimable(c, store.settings, new["runner_id"], "ops")[0] == "lane_held"
        assert all(item["reason"] != "worktree_home" for item in get(api, "health")["stuck"])
        with store.transaction() as c:
            c.execute(
                "UPDATE attempts SET state='completed',task_id=?,lane=? WHERE id=?",
                (parent, first["lane"], first["id"]),
            )
        assign(api, old, "ops", generation=first["generation"] + 1)
        post(
            api,
            "runners/heartbeat",
            {
                "version": "test",
                "platform": "test",
                "readiness": {"schema_version": 1, "bots": {"ops": {"ready": True}}, "worktrees": True},
                "capabilities": RUNNER_CAPABILITIES,
            },
            old["token"],
        )
        resumed = claim(api, old)
        assert resumed["task"]["id"] == child
        assert [tree["repo"] for tree in resumed["worktrees"]] == ["Acme/product"]
        return

    post(api, f"computers/{old['runner_id']}/revoke", {})
    moved = claim(api, new)
    assert moved["task"]["id"] == child
    assert [tree["repo"] for tree in moved["worktrees"]] == ["Acme/child"]


def test_a_moved_subtask_replays_messages_posted_while_the_parent_was_running(api):
    home = roots_runner(api)
    (parent,) = tasks(api, ("bot:ops", None))
    first = claim(api, home)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "parent-thread"}, home["token"])
    store = api.app.state.store
    with store.transaction() as c:
        in_flight = H.task_comment(c, "human:ana", parent, "Include the revised figures.", wake=False)
    post(
        api,
        f"attempts/{first['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "The draft is ready."},
        home["token"],
    )
    with store.transaction() as c:
        child = H.task_create(c, "human:ana", "Finish the draft", "Finish it.", "bot:ops", parent_id=parent)
        deferred = H.task_comment(c, "human:ana", child["id"], "Use the approved template.", wake=False)
        delivery.defer(
            c, "ops", "comment", deferred["id"], conversation_id=child["conversation_id"], task_id=child["id"]
        )
    new = runner(api, label="Second computer")
    assign(api, new, "ops", generation=first["generation"])
    ready(api, new, ["ops"], RUNNER_CAPABILITIES)
    turn = claim(api, new)
    assert turn["task"]["id"] == child["id"] and turn["session_fence"] == 1
    assert in_flight["created_at"] < turn["task"]["checkpoint_at"]
    replayed = {m["id"] for m in turn["moved_history"]}
    assert in_flight["id"] in replayed and deferred["id"] not in replayed
    assert f"[{deferred['id']}]" in turn["deferred"]


def test_subtasks_a_bot_files_for_itself_from_one_turn_start_in_their_own_lanes_side_by_side(api):
    machine = roots_runner(api)
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "readiness": {"ops": True},
            "capacity": 8,
            "capabilities": ["task_lanes_v2", "session_roots_v1"],
        },
        token=machine["token"],
    )
    (parent,) = tasks(api, ("bot:ops", None))
    plan = claim(api, machine)
    assert plan["lane"] == "task:" + parent
    with api.app.state.store.transaction() as c:
        subs = [
            H.task_create(
                c, "bot:ops", f"Build part {n} of the plan", "Its own work.", "bot:ops", parent_id=parent, lint=False
            )["id"]
            for n in range(3)
        ]
    turns = [claim(api, machine) for _ in subs]
    assert {(t["lane"], t["task"]["session_task_id"]) for t in turns} == {("task:" + s, s) for s in subs}
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM attempt_inputs WHERE attempt_id=?", (plan["id"],)).fetchone(), (
            "the filing turn is handed none of their notices"
        )


def test_a_subtask_keeps_its_own_session_and_lane_beside_its_parent(api):
    machine = roots_runner(api)
    (parent,) = tasks(api, ("bot:ops", None))
    plan = claim(api, machine)
    assert plan["lane"] == "task:" + parent
    post(api, f"attempts/{plan['id']}/started", {"thread_id": "t-parent"}, machine["token"])
    # Two turns at once on this computer, and this bot's subtasks keep sessions of their own (subtask_session: own).
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "readiness": {"ops": True},
            "capacity": 2,
            "capabilities": RUNNER_CAPABILITIES,
        },
        token=machine["token"],
    )
    with api.app.state.store.transaction() as c:
        BS.write(c, "ops", {**BS.read(c, "ops"), "subtask_session": "own"})
        sub = H.task_create(c, "human:ana", "Step 1 of the launch", "Do this step.", "bot:ops", parent_id=parent)["id"]
    turn = claim(api, machine)
    assert turn["lane"] == "task:" + sub, "beside its parent's running turn, in a lane of its own"
    with api.app.state.store.read() as c:
        assert H.session_root(c, "ops", sub, {"subtask_session": "own"}) == sub
    assert (turn["task"].get("session_task_id") or turn["task"]["id"]) == sub


def test_a_person_finishing_a_subtask_wakes_the_bot_once_on_the_parent_with_its_title_never_its_note(api):
    (parent,) = tasks(api, ("bot:ops", None))
    with api.app.state.store.transaction() as c:
        sub = H.task_create(
            c, "bot:ops", "Approve the launch copy", "Yes or no.", "human:ana", parent_id=parent, lint=False
        )["id"]
        budget = H.task_create(
            c, "bot:ops", "Approve the budget", "Yes or no.", "human:ana", parent_id=parent, lint=False
        )["id"]
        since = c.execute("SELECT max(rowid) FROM messages").fetchone()[0]
        H.task_done(c, "human:ana", sub, "Approved.", H.task(c, sub)["version"])
        assert filed_wakes(c, since) == [], "the parent still waits on the budget"
        H.task_done(c, "human:ana", budget, "Salary detail: 140k.", H.task(c, budget)["version"])
        assert filed_wakes(c, since) == [parent]
        [body] = [
            m["body"]
            for m in H.messages(c, H.task(c, parent)["conversation_id"])
            if ":filed:" in str(m.get("wake_key") or "")
        ]
    assert "Approve the launch copy: Done" in body and "Approve the budget: Done" in body
    assert "Approved." not in body and "140k" not in body


def test_a_turn_reads_only_its_own_lane_of_the_room_and_the_main_chat_keeps_the_task_lines(api):
    from backend import execution

    (tid,) = tasks(api, ("bot:finance", None))
    chat = {"id": "room", "kind": "chat"}
    page = [
        {"id": "hi", "refs": {}},
        {"id": "work", "refs": {"task": tid}},
        {"id": "report", "refs": {"task": tid, "task_report": "done"}},
    ]
    with api.app.state.store.read() as c:
        main = execution.lane_history(c, "finance", {}, page, chat, "main")
        own = execution.lane_history(c, "finance", {}, page, chat, "task:" + tid)
    assert [m["id"] for m in main] == ["hi", "report"] and [m["id"] for m in own] == ["work"]
