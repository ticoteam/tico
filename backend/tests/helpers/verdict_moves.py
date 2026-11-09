"""Helpers that other tests share, moved out of test_verdict_moves.py so they import no test file."""

from backend import hubdb as H, verdict_moves as V
from backend.tests.usage_rows import segment


HEAD, NEW, BASE = "a" * 40, "d" * 40, "c" * 40


STEPS = [
    ("On Deck", "open"),
    ("In Progress", "doing"),
    ("Review 1", "review"),
    ("Review 1 Fixes", "doing"),
    ("Review 2", "review"),
    ("Review 2 Fixes", "doing"),
    ("In QA", "review"),
    ("QA Fixes", "doing"),
    ("Pending Release", "review"),
    ("Post Release QA", "review"),
    ("Done", "done"),
]


def usage(c, task_id, bot, model="claude-sonnet-5-5", provider="anthropic", runtime=None):
    turn = H.new_id()
    c.execute(
        "INSERT INTO attempts(id,bot_id,created_at,settled_at,task_id) VALUES(?,?,?,?,?)",
        (turn, bot, H.now(), H.now(), task_id),
    )
    runtime = runtime or ("claude" if provider == "anthropic" else "codex")
    segment(
        c,
        turn,
        input_tokens=20,
        output_tokens=20,
        model=model,
        provider=provider,
        runtime=runtime,
        harness=runtime,
        effort="high",
    )
    return turn


def check(api, task, title="Review 1: PR #412", owner="bot:cmo", **ran):
    """A check `owner` ran on the ticket; `ran` names the model it ran on (usage's model, provider, runtime)."""
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, "bot:cpo", title, f"Check PR #412 head {HEAD}", owner, parent_id=task["id"], lint=False)
        turn = usage(c, row["id"], H.actor_id(owner), **ran)
        from backend.check_independence import capture_attempt

        capture_attempt(c, task["id"], row["id"], HEAD, turn)
        c.execute("DELETE FROM jobs")
    return row


def verdict(api, row, value, head=HEAD):
    with api.app.state.store.transaction() as c:
        H.task_update(c, row["owner_id"], row["id"], status="done", note="Reviewed.", verdict=value, verdict_head=head)
        turn = c.execute(
            "SELECT id FROM attempts WHERE task_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (row["id"],)
        ).fetchone()
        if turn:
            V.after_capture(c, row["id"], head, turn["id"])


def step(api, task):
    with api.app.state.store.read() as c:
        return V.step_of(c, H.task(c, task["id"]))


def builder_jobs(api):
    with api.app.state.store.read() as c:
        return [
            H.message(c, r[0])
            for r in c.execute("SELECT message_id FROM jobs WHERE bot_id='cpo' AND state='queued' ORDER BY rowid")
        ]
