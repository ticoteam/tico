"""A run its bot may not read fails once as it is handed out, and the claim takes the next job (#240). On 12 Nov a wake
filed under the key `room:<stage>:<freed>` was read as a run about a task called "room": every call on it was refused
(409 stale_lease), its lease lapsed, and it went back to the head of the bot's queue, 167 times. The access check stays
exactly as strict; what changes is that a refusal ends the job. A wake whose key is not filed under its own task is
refused before anything is queued."""

from backend import execution, task_wakes
from backend.store import H
from backend.tests.helpers.api import claim, post  # noqa: F401  (api is a fixture)
from backend.tests.helpers.task_lanes import new_task, task_bot


def misfiled_wake(api, text="Spec has room."):
    """A queued run for `ops` whose wake key names no task: what never_stuck.room_key wrote before #240."""
    message = post(api, "chat/ops", {"text": text})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET wake_key='room:product-spec:start' WHERE id=?", (message["id"],))
        job = c.execute("SELECT id FROM jobs WHERE message_id=?", (message["id"],)).fetchone()
    return job["id"]


def test_a_refused_job_fails_once_and_the_claim_takes_the_next_job(api):
    machine = task_bot(api, lanes=True)
    refused = misfiled_wake(api)
    task = new_task(api, "the next job")
    taken = claim(api, machine)
    assert taken is not None and taken["lane"] == "task:" + task["id"], taken
    with api.app.state.store.read() as c:
        job = c.execute("SELECT state,attempt_id FROM jobs WHERE id=?", (refused,)).fetchone()
        run = c.execute("SELECT * FROM attempts WHERE id=?", (job["attempt_id"],)).fetchone()
        assert job["state"] == "failed"
        assert (run["state"], run["exit"], run["summary"]) == ("failed", "failed", execution.REFUSED_RUN)
        assert run["final_text"] == execution.REFUSED_RUN
        assert c.execute("SELECT 1 FROM events WHERE target=? AND action='attempt.refused'", (run["id"],)).fetchone()
    # It is never taken again: later claims and the lapse of every lease leave it failed, with its one run.
    for _ in range(3):
        claim(api, machine)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE state IN ('leased','running')", (H.now(),))
        api.app.state.execution.expire(c)
    claim(api, machine)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (refused,)).fetchone()[0] == "failed"
        assert c.execute("SELECT COUNT(*) FROM attempts WHERE job_id=?", (refused,)).fetchone()[0] == 1


def test_a_refused_job_alone_in_the_queue_hands_out_nothing_and_stays_failed(api):
    machine = task_bot(api, lanes=True)
    refused = misfiled_wake(api)
    assert claim(api, machine) is None
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (refused,)).fetchone()[0] == "failed"
        assert c.execute("SELECT COUNT(*) FROM attempts WHERE job_id=?", (refused,)).fetchone()[0] == 1


def test_the_refusal_names_no_task_and_carries_none_of_its_words(api):
    machine = task_bot(api, lanes=True)
    refused = misfiled_wake(api, "Secret plan for the private idea.")
    claim(api, machine)
    with api.app.state.store.read() as c:
        run = c.execute("SELECT a.* FROM attempts a JOIN jobs j ON j.attempt_id=a.id WHERE j.id=?", (refused,))
        run = run.fetchone()
        for said in (run["summary"], run["final_text"]):
            assert "Secret" not in said and "room" not in said


def test_a_bot_that_owns_and_asked_for_a_private_task_takes_its_turn(api):
    machine = task_bot(api, lanes=True)
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Its own private idea", "Private.", "bot:ops", private=True, lint=False)
    taken = claim(api, machine)
    assert taken is not None and taken["lane"] == "task:" + task["id"], taken
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM attempts WHERE bot_id='ops' AND state='failed'").fetchone()


def test_a_wake_not_filed_under_its_own_task_is_refused_and_queues_nothing(api):
    task_bot(api, lanes=True)
    task = new_task(api, "a task")
    with api.app.state.store.transaction() as c:
        row = H.task(c, task["id"])
        before = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        for key in ("room:product-spec:start", "other-task:visit:owner:0", task["id"] + "x:visit:owner:0"):
            assert task_wakes.add(c, row, "bot:ops", {"id": "visit"}, "Spec has room.", wake_key=key) is None, key
        assert c.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == before
        assert not c.execute("SELECT 1 FROM messages WHERE wake_key LIKE 'room:%'").fetchone()


def test_a_wake_key_is_filed_under_the_task_its_first_part_names():
    assert task_wakes.filed_under("t1:v1:owner:0", "t1")
    assert task_wakes.filed_under(task_wakes.key("t1", "v1"), "t1")
    assert not task_wakes.filed_under("room:product-spec:t1@now", "t1")
    assert not task_wakes.filed_under("t10:v1:owner:0", "t1")


def test_every_written_out_wake_key_in_the_backend_starts_with_a_task_id():
    """Each `wake_key=f"..."` passed to `task_wakes.add` starts with an expression (the task id) and then ":". The
    guard in `add` only logs when it refuses a key, so a new caller with a fixed prefix (as "room:" and the Stage rules
    sweeps' "stage-rules-sweep:" were) would wake nobody; this finds it before it merges."""
    import ast
    from pathlib import Path

    root = Path(execution.__file__).parent
    bad = []
    for path in root.rglob("*.py"):
        if "tests" in path.relative_to(root).parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add"):
                continue
            if not (isinstance(node.func.value, ast.Name) and node.func.value.id in ("task_wakes", "TW")):
                continue
            for kw in node.keywords:
                if kw.arg != "wake_key":
                    continue
                key = kw.value
                if isinstance(key, ast.Constant):
                    bad.append(f"{path.relative_to(root)}:{key.lineno}")
                elif isinstance(key, ast.JoinedStr):
                    head, *rest = key.values
                    after = rest[0] if rest else None
                    if not (
                        isinstance(head, ast.FormattedValue)
                        and isinstance(after, ast.Constant)
                        and str(after.value).startswith(":")
                    ):
                        bad.append(f"{path.relative_to(root)}:{key.lineno}")
    assert not bad, bad
