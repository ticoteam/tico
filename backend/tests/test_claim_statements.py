"""The claim's work path reads a runner's bots as sets: its statements do not grow with the number of bots it has queued
work for (execution.candidate, claim_loads.read). Measured on the store's connections, as the claim makes them."""
import contextlib

from backend.store import H
from backend.tests.test_api import api  # noqa: F401  (the fixture)
from backend.tests.test_api import assign, headers, post, ready, runner

CAPABILITIES = ["task_lanes_v2", "session_roots_v1", "task_goals_v1", "capacity_continue_v1", "source_continue_v1",
                "turns_at_once_v1", "private_lanes_v1", "browser_exit_v1", "threads_v1"]


def seed(api, prefix, count):
    """A computer with `count` active bots, each with a queued chat."""
    store = api.app.state.store
    slugs = [f"{prefix}-{n:02d}" for n in range(count)]
    with store.transaction() as c:
        H.sync_registry(c, {s: {"name": s, "runtime": "fake", "status": "active"} for s in slugs},
                        {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben")]})
        for s in slugs:
            c.execute("INSERT INTO bot_config(bot,config,owner_id) VALUES(?,?,'ana')",
                      (s, '{"name": "%s", "runtime": "fake"}' % s))
    machine = runner(api, label=f"Statements {prefix}")
    for s in slugs:
        assign(api, machine, s)
    ready(api, machine, slugs, CAPABILITIES)
    for s in slugs:
        post(api, "chat/" + s, {"text": "Please summarize the current work."})
    return machine


@contextlib.contextmanager
def traced(api):
    """Every statement the claim runs, on any connection the store lends it (reads and the write transaction)."""
    store, seen = api.app.state.store, []
    original = store.read

    @contextlib.contextmanager
    def borrowed():
        with original() as c:
            c.set_trace_callback(seen.append)
            try:
                yield c
            finally:
                c.set_trace_callback(None)
    store.read = borrowed
    try:
        yield seen
    finally:
        del store.read


def claim(api, machine):
    """POST the claim as the computer: (answer, statements)."""
    with traced(api) as seen:
        response = api.post("/api/v2/jobs/claim", json={"bot": None, "start_places": True},
                            headers=headers(machine["token"]))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def handed_run(api, machine, answer):
    """The run the claim handed out completes, and its bot is asked again (so the next claim has work)."""
    attempt = answer.get("attempt") or {}
    assert attempt, answer
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t-" + aid}, machine["token"])
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, machine["token"])
    bot = attempt.get("bot_id") or attempt.get("bot")
    post(api, "chat/" + bot, {"text": "Next, please."})


def work_claim_statements(api, count, prefix):
    machine = seed(api, prefix, count)
    answer, _ = claim(api, machine)            # the first claim may do the company's first-time reads
    handed_run(api, machine, answer)
    answer, statements = claim(api, machine)
    assert answer.get("attempt"), answer     # the measured claim hands out a run: it took the work path
    return statements


def test_claim_work_path_statements_do_not_grow_with_assigned_bots(api):
    few = work_claim_statements(api, 5, "few")
    many = work_claim_statements(api, 20, "many")
    assert few == many, (few, many)
