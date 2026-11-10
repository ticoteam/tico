"""A subtask continues its parent task's session (hubdb.session_root), and a person finishing one under a
bot's task wakes the bot on the parent."""

import json

from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, runner  # noqa: F401  (api is a fixture)
from runner_support import state
from backend import bot_settings as BS


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


def roots_runner(api, capabilities):
    with api.app.state.store.transaction() as c:
        config = BS.read(c, 'ops')
        BS.write(c, 'ops', {**config, 'session': 'task'})
    machine = runner(api)
    assign(api, machine, "ops")
    post(
        api,
        "runners/heartbeat",
        {"version": "test", "platform": "test", "readiness": {"ops": True}, "capabilities": capabilities},
        token=machine["token"],
    )
    return machine


def test_a_subtask_turn_runs_in_its_parents_lane_and_session_and_links_and_checkpoints_the_parent(api):
    machine = roots_runner(api, ["task_lanes_v2", "session_roots_v1"])
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
    assert state.session_key({"session": "task"}, turn) == "task:" + parent
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


def test_subtasks_a_bot_files_for_itself_from_one_turn_start_in_their_own_lanes_side_by_side(api):
    machine = roots_runner(api, ["task_lanes_v2", "session_roots_v1"])
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


def test_an_older_runner_and_subtask_session_own_keep_each_subtask_its_own_session(api):
    machine = roots_runner(api, ["task_lanes_v2"])
    (parent,) = tasks(api, ("bot:ops", None))
    assert claim(api, machine)["lane"] == "task:" + parent
    with api.app.state.store.transaction() as c:
        sub = H.task_create(c, "human:ana", "Step 1 of the launch", "Do this step.", "bot:ops", parent_id=parent)["id"]
    assert claim(api, machine)["lane"] == "task:" + sub, "beside its parent's running turn, in a lane of its own"
    with api.app.state.store.read() as c:
        assert H.session_root(c, "ops", sub, {"subtask_session": "own"}) == sub
    turn = {"bot": "ops", "task": {"id": sub}}  # no session_task_id: keyed on the subtask
    assert state.session_key({}, turn) == "task:" + sub


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
