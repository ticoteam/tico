"""Helpers that other code shares, moved out of test_task_detail_queries.py so they import no test file (the bench
bench/perf-task-reads.py builds the same task)."""

from backend.store import H
from backend.tests.helpers.api import assign, claim, post, ready, runner
from backend.tests.helpers.bot_list_reads import add_bots

# Tool calls each run streams. Every step names its own call id, so a run of N steps used to cost about N queries.
STEPS = 10


def build(api, comments=0, people_comments=0, events=0, children=0, bots=0):
    """A task owned by bot `ops` with `comments` bot replies, each from its own run that the runner claimed, streamed
    STEPS tool calls and completed; `people_comments` notes from Ben that each name a child task; `events` history
    rows; `children` child tasks; and `bots` more bots in the company. Ana is the requester."""
    add_bots(api, {f"x{n:02d}": {} for n in range(bots)})
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid = post(api, "tasks", {"owner": "ops", "title": "Grow the task", "body": "Do it."})["id"]
    for k in range(max(comments, 1)):
        if k:
            post(api, f"tasks/{tid}/comments", {"text": f"@ops step {k}"})
        aid = claim(api, machine)["id"]
        post(api, f"attempts/{aid}/started", {"thread_id": f"run {k}"}, machine["token"])
        rows = [{"seq": 1, "kind": "status", "payload": {"state": "active", "tool_events": True}}]
        for j in range(STEPS):
            call = {"tool": "Read", "item_id": f"r{k}-{j}"}
            rows += [
                {"seq": 2 + 2 * j, "kind": "tool", "payload": {**call, "status": "started"}},
                {"seq": 3 + 2 * j, "kind": "tool", "payload": {**call, "status": "completed"}},
            ]
        post(api, f"attempts/{aid}/events", {"events": rows}, machine["token"])
        post(
            api,
            f"attempts/{aid}/complete",
            {"outcome": "completed", "text": f"Reply {k}.", "last_seq": len(rows)},
            machine["token"],
        )
    # The child tasks come after the runs: a parent waits on its sub-tasks, so its own wakes would have no turn left.
    with api.app.state.store.transaction() as c:
        kids = [
            H.task_create(c, "human:ana", f"Child {k}", "Part of it.", "bot:ops", parent_id=tid)["id"]
            for k in range(max(children, comments, people_comments))
        ]
        for k in range(people_comments):
            H.task_comment(c, "human:ben", tid, f"Ben note {k}", wake=False, extra_refs={"task": [kids[k]]})
        for k in range(events):
            c.execute(
                "INSERT INTO history_events(id,subject_type,subject_id,created_at,actor_id,kind,old,new,note) "
                "VALUES(?,'task',?,?,?,?,?,?,?)",
                (f"ev{k}", tid, H.now(), "human:ana", "status", "open", "doing", ""),
            )
    return tid
