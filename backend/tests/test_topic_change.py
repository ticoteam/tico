"""Topic changed (backend/topic_change.py): before a person's message in their direct chat goes to the bot, a plain-code
gate decides whether a Decide check runs (a latest prompt of 100k tokens or more, or a cold cache; never with the switch
off, in a shared bot's room, with an active goal, a running turn or under 3 turns), a faked Decide answers, and only a
confident new topic clears the session exactly as the person's own Clear does, marking the message for the chat's quiet
"Topic changed" line. A timeout, an error, the cap or `not_sure` sends the message in the same session with no line, as
does a decision about a chat that changed while Decide was asked. Every check is a `judge.call` row."""

import json
import threading
import time

import pytest

from backend import team_rules, views
from backend import topic_change as T
from backend.harnesses import HARNESS_CATALOG
from backend.store import H
from backend.tests.helpers.api import assign, claim, headers, post, ready, runner  # noqa: F401  (api is a fixture)
from backend.tests.helpers.session_clear import clear, finish
from clients import judge as J

UNRELATED = "Draft a blog post about our new pricing"


class FakeDecide:
    """Answers the `topic` question as told; `hold` makes it slow, `fail` makes it raise."""

    def __init__(self, choice="new_topic", confidence=0.95):
        self.choice, self.confidence = choice, confidence
        self.calls, self.fail, self.hold = [], None, None
        self.asked = threading.Event()  # set when a call arrives, so a test waits for the call itself

    def __call__(self, state, questions, label=None):
        self.calls.append({"state": state, "questions": questions, "label": label})
        self.asked.set()
        if self.hold:
            self.hold.wait(5)
        if self.fail:
            raise self.fail
        return {
            "model": "tico-decide-low",
            "usage": {"input_tokens": 900, "output_tokens": 0},
            "ms": 140,
            "answers": {"topic": {"type": "choice", "choice": self.choice, "confidence": self.confidence}},
        }


@pytest.fixture
def chat(api):
    """Ana's direct chat with ops: three finished turns, the switch on, a fake Decide. `usage(context, cached, age)`
    sets what the last turn reported and how long ago it finished: `context` is its newest request's prompt
    (`prompt_tokens`, None for a runner that sent no request counts), `summed` its totals over every request."""
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    conversation = None
    for text in ("Fix the login redirect bug", "Did the tests pass?", "Push the fix"):
        conversation = post(api, "chat/ops", {"text": text})["conversation_id"]
        finish(api, machine, claim(api, machine))
    store = api.app.state.store
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"topic_fresh": True})
    model = FakeDecide()
    api.app.state.judge = model

    def usage(context=150_000, cached=40_000, age=30, summed=None):
        summed = summed or context or 0
        with store.transaction() as c:
            last = c.execute(
                "SELECT t.id FROM attempts t JOIN bot_sessions s ON s.bot=t.bot_id AND s.thread_id=t.thread_id "
                "WHERE t.bot_id='ops'"
            ).fetchone()[0]
            c.execute(
                "UPDATE attempts SET input_tokens=?,cached_tokens=?,prompt_tokens=?,settled_at=? WHERE id=?",
                (summed - cached, cached, context, H.shift(H.now(), seconds=-age), last),
            )

    usage()
    return api, machine, conversation, model, usage


def checks(api):
    T.settle()  # each row is written once the send has its answer
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT actor,detail FROM events WHERE action='judge.call' AND target='topic-change@1' ORDER BY rowid"
        ).fetchall()
    assert all(r[0] == "human:ana" for r in rows), "a check counts against the sender's own daily calls"
    return [json.loads(r[1]) for r in rows]


def epoch(api, conversation):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT epoch FROM session_epochs WHERE conversation_id=?", (conversation,)).fetchone()
        return row[0] if row else 0


def session_state(api, conversation):
    """What a clear changes: the chat's epoch, the bot's epoch and whether the bot's thread pointer is kept."""
    with api.app.state.store.read() as c:
        bot = c.execute("SELECT epoch FROM bot_session_epochs WHERE bot='ops'").fetchone()
        return {
            "epoch": epoch(api, conversation),
            "bot_epoch": bot[0] if bot else 0,
            "thread": bool(c.execute("SELECT 1 FROM bot_sessions WHERE bot='ops'").fetchone()),
        }


# -------------------------------------------------------------------------------- the warm / cold rule and size bands


def test_warm_needs_a_turn_under_five_minutes_old_that_read_the_cache():
    now = H.now()
    recent = {
        "finished_at": H.shift(now, seconds=-60),
        "input_tokens": 5_000,
        "cached_tokens": 12_000,
        "prompt_tokens": 17_000,
    }
    assert T.warmth(recent, now) == (True, 17_000), "a recent turn with cached tokens: warm; context is its last prompt"
    stale = {**recent, "finished_at": H.shift(now, seconds=-301)}
    assert T.warmth(stale, now) == (False, 17_000), "older than the 5-minute cache lifetime: cold"
    no_usage = {"finished_at": H.shift(now, seconds=-10), "cached_tokens": None, "prompt_tokens": 5_000}
    assert T.warmth(no_usage, now) == (False, 5_000), "a harness that reports no cache use (Grok) is always cold"
    assert T.warmth({**no_usage, "cached_tokens": 0}, now)[0] is False
    assert T.warmth({**recent, "input_tokens": None}, now)[0] is False, "cached but no fresh count: unknown share, cold"
    assert T.warmth({**recent, "prompt_tokens": None}, now) == (True, None), "no request counts: size unknown"
    rewrote = {**recent, "input_tokens": 500, "cache_write_tokens": 20_000}
    assert T.warmth(rewrote, now)[0] is False, "a cache write is not a cache hit: mostly rewritten is cold"
    assert T.warmth(None, now) == (False, None), "no turn on the session yet: cold"


@pytest.mark.parametrize(
    "cached,fresh,warm",
    [
        (50_000, 50_000, True),  # exactly half the input from the cache: warm
        (49_000, 51_000, False),  # 49%: cold
        (12_000, 0, True),  # everything cached
    ],
)
def test_warm_needs_at_least_half_the_input_read_from_the_cache(cached, fresh, warm):
    now = H.now()
    turn = {
        "finished_at": H.shift(now, seconds=-60),
        "input_tokens": fresh,
        "cached_tokens": cached,
        "prompt_tokens": cached + fresh,
        "runtime": "claude",
    }
    assert T.warmth(turn, now)[0] is warm


def test_every_harness_has_a_cache_lifetime_and_reads_warm_inside_it_and_cold_after():
    assert set(T.CACHE_LIFETIME) == {row["runtime"] for row in HARNESS_CATALOG if not row.get("external")}
    now = H.now()
    for runtime, lifetime in T.CACHE_LIFETIME.items():
        turn = {"input_tokens": 2_000, "cached_tokens": 18_000, "prompt_tokens": 20_000, "runtime": runtime}
        inside = {**turn, "finished_at": H.shift(now, seconds=-(lifetime - 5))}
        after = {**turn, "finished_at": H.shift(now, seconds=-(lifetime + 1))}
        assert T.warmth(inside, now)[0] is True, runtime
        assert T.warmth(after, now)[0] is False, runtime
        if runtime == "grok":
            reported_none = {**inside, "cached_tokens": None, "input_tokens": 20_000}
            assert T.warmth(reported_none, now)[0] is False, "Grok reports no cached tokens: always cold"
    unknown = {
        "finished_at": H.shift(now, seconds=-(T.WARM_SECONDS + 1)),
        "input_tokens": 0,
        "cached_tokens": 9_000,
        "runtime": "something-new",
    }
    assert (
        T.warmth(unknown, now)[0] is False and T.warmth({**unknown, "finished_at": H.shift(now, seconds=-60)}, now)[0]
    ), "a harness not in the table takes the 5-minute default"


def test_the_lifetimes_are_the_documented_ones_and_are_read_per_harness(chat, monkeypatch):
    # Pinned to the provider docs cited in CACHE_LIFETIME: change one only with its doc.
    assert T.CACHE_LIFETIME == {
        "claude": 300,
        "codex": 300,
        "gemini": 300,
        "grok": 300,
        "pi": 300,
        "cursor": 300,
        "musecode": 300,
    }
    api, _, _, _, usage = chat
    usage(15_000, 12_000, 600)
    with api.app.state.store.read() as c:
        turn = T.last_turn(c, api.app.state.store.settings, "ops")
    assert turn["runtime"] == "fake", "the test runner's harness, read from the bot's session"
    assert T.warmth(turn)[0] is False, "10 minutes old: cold under the 5-minute default"
    monkeypatch.setitem(T.CACHE_LIFETIME, turn["runtime"], 900)
    assert T.warmth(turn)[0] is True, "the bot's own harness lifetime decides, not the default"


@pytest.mark.parametrize(
    "context,cached,age,checked",
    [
        (60_000, 45_000, 30, False),  # 20k to 100k, warm (75% cached): no check (the lead's reading)
        (60_000, 20_000, 30, True),  # 20k to 100k with a small cache hit (33%): cold, so checked (Dev 190)
        (15_000, 8_000, 600, True),  # cold (stale turn): checked at any size
    ],
)
def test_the_size_bands_and_warmth_decide_whether_decide_is_asked(chat, context, cached, age, checked):
    api, _, conversation, model, usage = chat
    model.choice = "same_topic"
    usage(context, cached, age)
    post(api, "chat/ops", {"text": "Next one"})
    assert bool(model.calls) is checked and len(checks(api)) == int(checked)
    if checked:
        row = checks(api)[0]
        assert row["context_tokens"] == context and row["gate"] == ("big" if context >= 100_000 else "cold")


def run_with(api, machine, requests):
    """A turn whose runner reports these per-request counts; its stored (input, cached, prompt) tokens."""
    with api.app.state.store.transaction() as c:
        team_rules.save(c, "human:ana", {"topic_fresh": False})
    post(api, "chat/ops", {"text": "Next one"})
    turn = claim(api, machine)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-" + turn["id"][:8]}, machine["token"])
    totals = [sum(r[i] for r in requests) for i in range(3)]
    part = {
        "model": "claude-haiku-5-5",
        "runtime": "claude",
        "billing": "api",
        "input_tokens": totals[0],
        "cached_tokens": totals[1],
        "output_tokens": totals[2],
        "requests": requests,
    }
    r = api.post(
        f"/api/v2/attempts/{turn['id']}/complete",
        headers=headers(machine["token"]),
        json={"outcome": "completed", "last_seq": 0, "text": "Done.", "usage": {**part, "segments": [part]}},
    )
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        return tuple(
            c.execute(
                "SELECT input_tokens,cached_tokens,prompt_tokens FROM attempts WHERE id=?", (turn["id"],)
            ).fetchone()
        )


def test_the_server_tells_runners_it_takes_the_marks(chat):
    """The runner sends each request's fourth count only when the server names this feature (runner-rs execute.rs
    `served`); an older server refuses it."""
    api, machine, _, _, _ = chat
    features = api.get("/api/v2/runners/desired", headers=headers(machine["token"])).json()["features"]
    assert features["usage_requests"] is True and features["usage_request_main"] is True
    assert features["run_pushes"] is True


def test_the_context_size_is_the_bots_own_newest_prompt_never_a_subagents_or_the_summed_usage(chat):
    """One chat, the request shapes in turn: what the last turn reports decides the size (never its summed usage),
    a turn keeps the newest of the bot's own prompts, and only that prompt's size triggers a check."""
    api, machine, _, model, usage = chat
    model.choice = "same_topic"

    def drain():
        for _ in range(4):
            turn = claim(api, machine)
            if not turn:
                break
            finish(api, machine, turn)

    for context, age, checked in [
        (15_000, 30, False),  # six warm requests of about 18k: summed over 100k, the last prompt under 20k
        (None, 30, False),  # a runner that sent no request counts: the size never triggers a check
        (None, 600, True),  # ... a cold cache still does
    ]:
        usage(context, 90_000, age, summed=6 * 18_000)
        seen = len(checks(api))
        model.calls.clear()
        post(api, "chat/ops", {"text": "Next one"})
        assert bool(model.calls) is checked and len(checks(api)) - seen == int(checked), (context, age)
        if checked:
            assert checks(api)[-1]["context_tokens"] is None and checks(api)[-1]["gate"] == "cold"
        drain()

    for requests, prompt in [
        # The runner's own fixture (runner-rs claude.rs): a 123,002-token prompt of the bot's, then a subagent's 900.
        ([[3002, 120000, 78, 1], [900, 0, 5, 0]], 123_002),
        # A small prompt of the bot's, then a large subagent's: the session is still small.
        ([[500, 15000, 100, 1], [2000, 118000, 50, 0]], 15_500),
        ([[500, 50000, 100, 1], [1000, 54000, 200, 1], [300, 0, 9, 0]], 55_000),  # the newest of the bot's own
        ([[500, 50000, 100], [1000, 54000, 200]], None),  # an older runner: no marks, size unknown
        ([[900, 0, 5, 0]], None),  # only a subagent's: unknown
    ]:
        stored = run_with(api, machine, requests)
        assert stored == (sum(r[0] for r in requests), sum(r[1] for r in requests), prompt), (
            "the summed totals stay; the context is the bot's own newest prompt"
        )

    for requests, checked in [
        ([[3002, 120000, 78, 1], [900, 0, 5, 0]], True),  # a warm session over 100k: checked, whatever the subagent
        (
            [[500, 15000, 100, 1], [2000, 118000, 50, 0]],
            False,
        ),  # a warm small session: a large subagent never checks it
        ([[3002, 120000, 78]], False),  # an older runner, warm: size unknown, no check
    ]:
        run_with(api, machine, requests)
        with api.app.state.store.transaction() as c:
            team_rules.save(c, "human:ana", {"topic_fresh": True})
        seen = len(checks(api))
        model.calls.clear()
        post(api, "chat/ops", {"text": "And another"})
        assert bool(model.calls) is checked and len(checks(api)) - seen == int(checked), requests
        if checked:
            assert checks(api)[-1]["gate"] == "big"
        drain()


# -------------------------------------------------------------------------------- a new topic clears like Clear


def test_the_topic_clear_leaves_the_same_state_as_the_manual_clear(chat):
    api, machine, conversation, model, _ = chat
    post(api, "chat/ops", {"text": UNRELATED})
    after_topic = session_state(api, conversation)
    finish(api, machine, claim(api, machine))
    # The person's own Clear, from the same kind of state: each moves the same three things by one step.
    assert clear(api).status_code == 200
    after_manual = session_state(api, conversation)
    assert after_topic == {"epoch": 1, "bot_epoch": 0, "thread": False}
    assert after_manual == {"epoch": 2, "bot_epoch": 0, "thread": False}


# -------------------------------------------------------------------------------- no clear


def test_a_refer_back_an_unsure_answer_an_error_the_cap_or_the_budget_sends_in_the_same_session(chat):
    api, machine, conversation, model, usage = chat
    unchanged = {"epoch": 0, "bot_epoch": 0, "thread": True}

    def send(text):
        message = post(api, "chat/ops", {"text": text})
        assert "topic_changed" not in (message.get("refs") or {})
        assert session_state(api, conversation)["epoch"] == 0
        finish(api, machine, claim(api, machine))
        usage()

    for choice, confidence in [("same_topic", 0.97), ("not_sure", 0.9), ("new_topic", 0.55)]:
        model.choice, model.confidence = choice, confidence
        model.calls.clear()
        send("Do that again")
        assert len(model.calls) == 1, "the check ran"
        assert session_state(api, conversation) == unchanged
        assert checks(api)[-1]["clear"] is False

    model.choice, model.confidence = "new_topic", 0.95
    for fail in (
        J.JudgeError("unavailable", "down", retryable=True),
        RuntimeError("bug"),
        J.JudgeError("spend_cap", "cap", 429),
    ):
        model.fail = fail
        send(UNRELATED)
        assert checks(api)[-1]["clear"] is False and checks(api)[-1]["error"]
    model.fail = None

    api.app.state.store.settings.decisions_daily_calls_owner = 0
    model.calls.clear()
    send(UNRELATED)
    assert model.calls == [] and checks(api)[-1]["error"] == "budget"


# -------------------------------------------------------------------------------- the chat changed meanwhile


def bot_says(c, conversation, text):
    """A bot message lands in the chat (a queued reply, another tab's send) while Decide is asked."""
    c.execute(
        "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at) "
        "VALUES(?,?,'bot:ops','human:ana','say',?,?)",
        ("late-" + text[:4], conversation, text, H.now()),
    )


CHANGES = {
    "cleared": lambda api, c, conv: views.clear_chat_session(c, api.app.state.store.settings, "human:ana", "ops", conv),
    "message": lambda api, c, conv: bot_says(c, conv, "One more thing"),
    "goal": lambda api, c, conv: c.execute(
        "INSERT INTO chat_goals(id,conversation_id,bot,objective,status,set_by,set_at,updated_at) "
        "VALUES('g1',?,'ops','Ship the fix','active','human:ana',?,?)",
        (conv, H.now(), H.now()),
    ),
    "off": lambda api, c, conv: team_rules.save(c, "human:ana", {"topic_fresh": False}),
}


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_decision_about_a_chat_that_changed_meanwhile_clears_nothing(chat, change, monkeypatch):
    api, _, conversation, model, _ = chat
    # The budget is how long a send may wait, which this test does not measure. On a loaded runner the reads before
    # Decide can run past BUDGET, and a check cut short there never asks, so the budget must not decide the ordering.
    monkeypatch.setattr(T, "BUDGET", 30)
    before = session_state(api, conversation)
    model.hold = threading.Event()
    sent = {}
    sender = threading.Thread(target=lambda: sent.update(post(api, "chat/ops", {"text": UNRELATED})))
    sender.start()
    try:
        # Decide is asked only after the chat was read, so a change made once it is asked is one it did not see.
        assert model.asked.wait(10), "the check is asking Decide"
        with api.app.state.store.transaction() as c:
            CHANGES[change](api, c, conversation)
    finally:
        model.hold.set()  # a confident new topic, about the chat as it was
    sender.join(5)
    assert "topic_changed" not in (sent.get("refs") or {}), "no divider"
    expected = {**before, "epoch": 1, "thread": False} if change == "cleared" else before
    assert session_state(api, conversation) == expected, "no clear beyond what happened meanwhile"
    with api.app.state.store.read() as c:
        kept = [json.loads(r[0]) for r in c.execute("SELECT detail FROM events WHERE action='chat.topic_kept'")]
        changed = c.execute("SELECT count(*) FROM events WHERE action='chat.topic_changed'").fetchone()[0]
    assert kept == [{"why": {"cleared": "changed", "message": "changed"}.get(change, change)}] and changed == 0
    assert [r["clear"] for r in checks(api)] == [True], "the audit keeps the decision; topic_kept says it was refused"


def test_overlapping_new_topic_sends_clear_once_and_keep_the_first_message(chat, monkeypatch):
    api, machine, conversation, model, _ = chat
    # Both sends must be asking Decide before either is answered; the budget is not what this test measures, so on a
    # loaded runner it must not cut the first check short while the second is still starting.
    monkeypatch.setattr(T, "BUDGET", 30)
    model.hold = threading.Event()
    sent = []
    senders = [
        threading.Thread(target=lambda t=t: sent.append(post(api, "chat/ops", {"text": t})))
        for t in (UNRELATED, "Plan the offsite agenda")
    ]
    for sender in senders:
        sender.start()
        deadline = time.monotonic() + 10.0
        while len(model.calls) < senders.index(sender) + 1 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert len(model.calls) == 2, "both sends are asking Decide about the same chat"
    model.hold.set()
    for sender in senders:
        sender.join(5)
    assert len(sent) == 2
    assert sum(bool((m.get("refs") or {}).get("topic_changed")) for m in sent) == 1, "one divider"
    assert session_state(api, conversation)["epoch"] == 1, "cleared once"
    turn = claim(api, machine)
    assert sorted(m["body"] for m in turn["history"]) == sorted(m["body"] for m in sent), (
        "both new messages are after the clear"
    )


def test_a_clear_between_admission_and_history_is_not_cleared_again(chat, monkeypatch):
    """A Clear that commits after the gate admitted the old three-turn session but before history and the marker are
    read: the check reads the chat as admitted, so its decision no longer matches and nothing is cleared twice."""
    api, machine, conversation, _, _ = chat
    store = api.app.state.store
    real = T.gate

    def gate_then_clear(*args, **kwargs):
        verdict = real(*args, **kwargs)
        assert verdict[0], "the old session was admitted"
        with store.transaction() as c:  # another tab's Clear commits now
            views.clear_chat_session(c, store.settings, "human:ana", "ops", conversation)
        return verdict

    monkeypatch.setattr(T, "gate", gate_then_clear)
    message = post(api, "chat/ops", {"text": UNRELATED})
    assert "topic_changed" not in (message.get("refs") or {}), "no divider"
    assert session_state(api, conversation) == {"epoch": 1, "bot_epoch": 0, "thread": False}, "cleared once"
    with store.read() as c:
        kept = [json.loads(r[0]) for r in c.execute("SELECT detail FROM events WHERE action='chat.topic_kept'")]
    assert kept == [{"why": "changed"}]
    turn = claim(api, machine)
    assert [m["body"] for m in turn["history"]] == [UNRELATED], "the message is after the first clear"


def test_an_answer_after_the_deadline_is_dropped_even_when_the_worker_runs_first(chat, monkeypatch):
    """The answer is ready only after the deadline, and the worker hands it over before the waiting send wakes."""
    api, _, conversation, _, _ = chat
    skew = [0.0]
    monkeypatch.setattr(T, "_clock", lambda: time.monotonic() + skew[0])

    class Late(FakeDecide):
        def __call__(self, *args, **kwargs):
            answer = super().__call__(*args, **kwargs)
            skew[0] = T.BUDGET + 0.1  # the answer arrives past the deadline; the send has not woken yet
            return answer

    api.app.state.judge = Late()
    message = post(api, "chat/ops", {"text": UNRELATED})
    assert "topic_changed" not in (message.get("refs") or {}), "a late new topic is never applied"
    assert session_state(api, conversation)["epoch"] == 0
    row = checks(api)[0]
    assert row["error"] == "timeout" and row["clear"] is False and row["answers"]["topic"]["choice"] == "new_topic"


# -------------------------------------------------------------------------------- never checked


def test_never_checked_when_the_chat_is_shared_off_goaled_busy_a_task_chat_has_files_or_is_under_three_turns(chat):
    """Every gate condition that keeps a send from being checked, one after another in one chat. Each one is undone and
    its leftover turns finished so the next starts from the same checked-if-asked chat."""
    api, machine, conversation, model, usage = chat

    def quiet(why):
        assert model.calls == [] and checks(api) == [], why

    def drain():
        for _ in range(6):
            turn = claim(api, machine)
            if not turn:
                break
            finish(api, machine, turn)
        usage()

    def set_rule(on):
        with api.app.state.store.transaction() as c:
            team_rules.save(c, "human:ana", {"topic_fresh": on})

    before = session_state(api, conversation)
    with api.app.state.store.transaction() as c:  # what rooms.sync_shared_room makes when its owner is its only member
        c.execute("UPDATE conversations SET scope='shared' WHERE id=?", (conversation,))
    post(api, "chat/ops", {"text": UNRELATED})
    quiet("a shared bot's room, even with one person")
    assert session_state(api, conversation) == before
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET scope='direct' WHERE id=?", (conversation,))
    drain()

    set_rule(False)
    post(api, "chat/ops", {"text": UNRELATED})
    quiet("the switch off")
    set_rule(True)
    drain()

    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO chat_goals(id,conversation_id,bot,objective,status,set_by,set_at,updated_at) "
            "VALUES('g1',?,'ops','Ship the fix','active','human:ana',?,?)",
            (conversation, H.now(), H.now()),
        )
    post(api, "chat/ops", {"text": UNRELATED})
    quiet("an active chat goal")
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM chat_goals WHERE id='g1'")
    drain()

    task = post(api, "tasks", {"owner": "ops", "title": "Look into it", "body": "The login bug"})
    post(api, f"tasks/{task['id']}/chat", {"text": UNRELATED})
    post(api, "chat/ops", {"text": UNRELATED, "refs": {"task": task["id"]}})
    quiet("a task chat")
    drain()

    r = api.post(
        "/api/v2/uploads/chat/ops",
        data={"text": UNRELATED},
        files={"files": ("note.txt", b"Agenda for the offsite", "text/plain")},
        headers=headers(),
    )
    assert r.status_code == 200, r.text
    quiet("files uploaded alongside")
    assert session_state(api, conversation) == before
    assert not r.json()["message"]["refs"].get("topic_changed")
    fid = r.json()["message"]["refs"]["attachments"][0]["file_id"]
    drain()

    # A file already in the chat, attached by reference on the JSON route: QA on the v0.3.64 candidate saw one
    # Decide call and a clear here.
    message = post(api, "chat/ops", {"text": UNRELATED, "attachments": [f"{fid}@1"]})
    assert [a["file_id"] for a in message["refs"]["attachments"]] == [fid], "the file still goes with the message"
    quiet("files attached on the JSON route")
    assert session_state(api, conversation) == before
    assert not message["refs"].get("topic_changed")
    drain()

    model.choice = "same_topic"
    post(api, "chat/ops", {"text": "Run the long job"})  # checked (big), same session
    assert len(model.calls) == 1, "the same chat, without files, is checked"
    model.calls.clear()
    running = claim(api, machine)  # now running
    post(api, "chat/ops", {"text": UNRELATED})
    assert model.calls == [], "a running turn"
    finish(api, machine, running)
    drain()
    model.calls.clear()

    assert clear(api).status_code == 200
    for text in ("One", "Two"):
        post(api, "chat/ops", {"text": text})
        finish(api, machine, claim(api, machine))
    usage()
    model.calls.clear()
    post(api, "chat/ops", {"text": UNRELATED})
    assert model.calls == [], "two turns since the clear"


def test_a_send_with_files_is_never_considered_whatever_the_route():
    """The one rule both routes answer to: referenced files, uploaded files, a command or a task chat's message."""
    from backend import models as M

    plain = M.ChatCreate(text="Hello")
    assert T.considered(plain, uploads=())
    assert not T.considered(plain, uploads=[{"name": "note.txt"}])
    assert not T.considered(M.ChatCreate(text="Hello", attachments=["f1@1"]), uploads=())
    assert not T.considered(M.ChatCreate(text="/clear", command=True), uploads=())
    assert not T.considered(M.ChatCreate(text="Hello", refs={"task": "t1"}), uploads=())


def test_a_retried_send_does_not_check_again(chat):
    api, _, _, model, _ = chat
    key = "retry-1"
    first = api.post("/api/v2/chat/ops", json={"text": UNRELATED}, headers=headers(key=key))
    again = api.post("/api/v2/chat/ops", json={"text": UNRELATED}, headers=headers(key=key))
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert len(model.calls) == 1 and len(checks(api)) == 1


# -------------------------------------------------------------------------------- the switch


def test_the_switch_is_off_by_default_on_by_the_environment_and_an_admins_to_turn(api, monkeypatch):
    store = api.app.state.store
    with store.read() as c:
        assert team_rules.load(c)["topic_fresh"] is False, "off by default for customers"
    monkeypatch.setenv("TICO_TOPIC_FRESH", "1")
    with store.read() as c:
        assert team_rules.load(c)["topic_fresh"] is True, "on for Tico Team's own server"
    r = api.put("/api/v2/access/rules", json={"topic_fresh": False}, headers=headers("cara-test"))
    assert r.status_code == 403 and "Only an owner or an admin" in r.text, "a member may not turn it"
    r = api.put("/api/v2/access/rules", json={"topic_fresh": False}, headers=headers("ben-test"))
    assert r.status_code == 200 and r.json()["topic_fresh"] is False, r.text
    with store.read() as c:
        assert team_rules.load(c)["topic_fresh"] is False, "a saved choice wins over the environment"
