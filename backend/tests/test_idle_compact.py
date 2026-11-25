"""Idle compaction (backend/idle_compact.py): a big Claude-plan session idle 45-55 minutes gets one hidden `/compact` in
the same session on the same computer, and nothing about it shows: no seq, no reply, no Needs you, no retry. Anything
newer on the thread (a person's message, a dream, another run) stops it before it is sent, at its claim and at its
start; a 5-minute-cache sign-in, a small context or a cold cache never gets one. Synthetic, the fictional company only."""

import pytest

from backend import idle_compact as I
from backend import run_failures
from backend.store import H
from backend.tests.helpers.api import assign, claim, post, ready, runner  # noqa: F401  (api is a fixture)
from backend.tests.helpers.session_clear import finish


@pytest.fixture
def idle(api):
    """Ops answered Ana once in her chat. `age(...)` makes that turn a Claude-plan one of `tokens` context that ended
    `minutes` ago, as the runner and its usage would have recorded it."""
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    conversation = post(api, "chat/ops", {"text": "Fix the login redirect bug"})["conversation_id"]
    turn = claim(api, machine)
    finish(api, machine, turn)
    store = api.app.state.store

    def age(minutes=46, tokens=250_000, billing="subscription", runtime="claude"):
        with store.transaction() as c:
            c.execute(
                "UPDATE attempts SET billing=?,handed_runtime=?,prompt_tokens=?,finished_at=? WHERE id=?",
                (billing, runtime, tokens, H.shift(H.now(), seconds=-60 * minutes), turn["id"]),
            )

    age()
    return api, machine, store, conversation, turn, age


def sweep(store):
    with store.transaction() as c:
        return I.sweep(c, every_minute=False)


def why(store, turn):
    with store.read() as c:
        return I.why_not(c, turn["id"], H.now())


def shown(store, conversation):
    """What the chat shows: messages with a seq, and the bot's own posts."""
    with store.read() as c:
        return c.execute(
            "SELECT count(*) FROM messages WHERE conversation_id=? AND (seq IS NOT NULL OR sender_id='bot:ops')",
            (conversation,),
        ).fetchone()[0]


def test_one_hidden_compact_in_the_same_session_and_nothing_shows(idle):
    api, machine, store, conversation, turn, _ = idle
    before = shown(store, conversation)
    assert sweep(store) == 1
    assert sweep(store) == 0, "one per thread: the queued compaction is newer work"
    with store.read() as c:
        notice = dict(
            c.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND sender_id=? AND body='/compact'",
                (conversation, H.TICO_ACTOR),
            ).fetchone()
        )
        source_key = c.execute("SELECT computer_session_key FROM attempts WHERE id=?", (turn["id"],)).fetchone()[0]
    refs = H._json(notice["refs"], {})
    assert notice["seq"] is None and notice["recipient_id"] == "bot:ops"
    assert refs["command"] is True and refs["after"] == turn["id"] and refs["purpose"] == I.PURPOSE
    assert "from" not in refs, "never read as a person's words"
    assert source_key, "the turn ran in a saved session"

    compaction = claim(api, machine)
    assert compaction["message"]["body"] == "/compact"
    with store.read() as c:
        row = c.execute(
            "SELECT computer_session_key,runner_id,purpose FROM attempts WHERE id=?", (compaction["id"],)
        ).fetchone()
    assert tuple(row) == (source_key, machine["runner_id"], I.PURPOSE), "the same session on the same computer"
    assert post(api, f"attempts/{compaction['id']}/inputs", {}, machine["token"]).get("messages") == []
    post(api, f"attempts/{compaction['id']}/started", {"thread_id": "t-" + turn["id"][:8]}, machine["token"])
    post(
        api,
        f"attempts/{compaction['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Compacted."},
        machine["token"],
    )
    assert shown(store, conversation) == before, "no reply posted and no line in the chat"
    with store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (notice["id"],)).fetchone()[0] == "completed"
        assert not c.execute("SELECT 1 FROM question_marks WHERE bot_id='ops'").fetchone()
    assert sweep(store) == 0, "once per idle spell: the compaction is now the session's newest run"


def test_a_failed_compaction_is_quiet_and_not_retried(idle):
    api, machine, store, conversation, turn, _ = idle
    sweep(store)
    compaction = claim(api, machine)
    post(api, f"attempts/{compaction['id']}/started", {"thread_id": "t-" + turn["id"][:8]}, machine["token"])
    post(
        api,
        f"attempts/{compaction['id']}/complete",
        {"outcome": "failed", "last_seq": 0, "text": "compact failed"},
        machine["token"],
    )
    with store.read() as c:
        assert (
            c.execute(
                "SELECT j.state FROM jobs j JOIN attempts a ON a.job_id=j.id WHERE a.id=?", (compaction["id"],)
            ).fetchone()[0]
            == "canceled"
        )
        assert c.execute(
            "SELECT 1 FROM events WHERE action='attempt.skipped' AND target=?", (compaction["id"],)
        ).fetchone()
        assert c.execute("SELECT state FROM bot_status WHERE bot='ops'").fetchone()[0] == "idle"
        assert run_failures.get(c, "ops") == {}, "no failure counted against the bot: no wait, no hold"
    assert sweep(store) == 0 and claim(api, machine) is None, "nothing queued again"


def test_newer_work_after_the_send_stops_it_at_start(idle):
    api, machine, store, conversation, turn, _ = idle
    sweep(store)
    compaction = claim(api, machine)
    post(api, "chat/ops", {"text": "Also check the logout page"})
    post(
        api,
        f"attempts/{compaction['id']}/started",
        {"thread_id": "t-" + turn["id"][:8]},
        machine["token"],
        expected=409,
    )
    post(api, f"attempts/{compaction['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, machine["token"])
    with store.read() as c:
        assert (
            c.execute(
                "SELECT j.state FROM jobs j JOIN attempts a ON a.job_id=j.id WHERE a.id=?", (compaction["id"],)
            ).fetchone()[0]
            == "canceled"
        )
    person = claim(api, machine)
    assert person["message"]["body"] == "Also check the logout page", "the person's turn runs as it would have"


def test_what_is_never_compacted(idle):
    api, machine, store, conversation, turn, age = idle
    age(billing="api")
    assert why(store, turn) == "not a Claude plan sign-in", "an API key's cache lives 5 minutes"
    age(runtime="codex")
    assert why(store, turn) == "not a Claude plan sign-in"
    age(tokens=150_000)
    assert why(store, turn) == "a small context"
    age(minutes=59)
    assert why(store, turn) == "its cache is cold"
    age(minutes=30)
    assert sweep(store) == 0, "before the warm window"
    age()
    assert why(store, turn) is None

    task = post(api, "tasks", {"owner": "ops", "title": "Tidy the logs", "body": "Rotate them weekly."})
    with store.transaction() as c:
        assert I._task_why(c, task["id"]) is None and not I._task_goal(c, "ops", task["id"])
        c.execute("UPDATE jobs SET wake_class='dream' WHERE bot_id='ops' AND state='queued'")
    assert why(store, turn) == "a dream is queued", "the dream forks the session first, or it is not compacted"
    with store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops' AND state='queued'")
    post(api, "chat/ops", {"text": "Thanks, one more thing"})
    assert why(store, turn) == "newer work queued or going"
    assert sweep(store) == 0

    with store.transaction() as c:
        assert I.may_start(c, "nothing-here", {"after": "no-such-run"}) is False
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (task["id"],))
        assert I._task_why(c, task["id"]) == "the task ended"


def test_the_sweep_and_its_checks_read_by_index(company):
    """The once-a-minute sweep reads only the runs that ended in the window, and each check one index range."""
    now = H.now()
    plans = {}
    with company.read() as c:
        for name, sql, args in (
            ("due", I._DUE, (now, now, I.PURPOSE, I.CONTEXT_MIN)),
            ("newer", I._NEWER, ("ops", now, "task:x", "a", "b")),
            ("busy", I._BUSY, I._busy("ops", "m", "conv", "t")),
            ("dream", I._DREAM, ("ops", *I._LIVE)),
            ("asked", I._ASKED, ("ops", "t", "conv")),
        ):
            plans[name] = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql, args)]
    print(plans)  # the report quotes these plans
    assert any("attempts_finished_state" in line for line in plans["due"]), plans["due"]
    assert any("attempts_bot_created" in line for line in plans["newer"]), plans["newer"]
    assert not [line for plan in plans.values() for line in plan if line.startswith("SCAN")], plans
