"""Topic changed (backend/topic_change.py): before a person's message in their direct chat goes to the bot, a plain-code
gate decides whether a Decide check runs (100k tokens or more, or a cold cache; never with the switch off, an active
goal, a running turn or under 3 turns), a faked Decide answers, and only a confident new topic clears the session exactly
as the person's own Clear does, marking the message for the chat's quiet "Topic changed" line. A timeout, an error,
the cap or `not_sure` sends the message in the same session with no line. Every check is a `judge.call` row."""

import json
import threading
import time

import pytest

from backend import team_rules
from backend import topic_change as T
from backend.store import H
from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401  (api is a fixture)
from backend.tests.test_session_clear import clear, finish
from clients import judge as J

UNRELATED = "Draft a blog post about our new pricing"


class FakeDecide:
    """Answers the `topic` question as told; `hold` makes it slow, `fail` makes it raise."""

    def __init__(self, choice="new_topic", confidence=0.95):
        self.choice, self.confidence = choice, confidence
        self.calls, self.fail, self.hold = [], None, None

    def __call__(self, state, questions, label=None):
        self.calls.append({"state": state, "questions": questions, "label": label})
        if self.hold:
            self.hold.wait(5)
        if self.fail:
            raise self.fail
        return {"model": "tico-decide-1.0", "usage": {"input_tokens": 900, "output_tokens": 0}, "ms": 140,
                "answers": {"topic": {"type": "choice", "choice": self.choice, "confidence": self.confidence}}}


@pytest.fixture
def chat(api):
    """Ana's direct chat with ops: three finished turns, the switch on, a fake Decide. `usage(context, cached, age)`
    sets what the last turn reported and how long ago it finished."""
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

    def usage(context=150_000, cached=40_000, age=30):
        with store.transaction() as c:
            last = c.execute("SELECT t.id FROM turns t JOIN bot_sessions s ON s.bot=t.bot AND s.thread_id=t.thread_id "
                             "WHERE t.bot='ops'").fetchone()[0]
            c.execute("UPDATE turns SET input_tokens=?,cached_tokens=?,finished=? WHERE id=?",
                      (context - cached, cached, H.shift(H.now(), seconds=-age), last))
    usage()
    return api, machine, conversation, model, usage


def checks(api):
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor,detail_json FROM events WHERE action='judge.call' AND target='topic-change@1' "
                         "ORDER BY rowid").fetchall()
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
        return {"epoch": epoch(api, conversation), "bot_epoch": bot[0] if bot else 0,
                "thread": bool(c.execute("SELECT 1 FROM bot_sessions WHERE bot='ops'").fetchone())}


# -------------------------------------------------------------------------------- the warm / cold rule and size bands

def test_warm_needs_a_turn_under_five_minutes_old_that_read_the_cache():
    now = H.now()
    recent = {"finished": H.shift(now, seconds=-60), "input_tokens": 5_000, "cached_tokens": 12_000}
    assert T.warmth(recent, now) == (True, 17_000), "a recent turn with cached tokens: warm; context is input + cached"
    stale = {**recent, "finished": H.shift(now, seconds=-301)}
    assert T.warmth(stale, now) == (False, 17_000), "older than the 5-minute cache lifetime: cold"
    no_usage = {"finished": H.shift(now, seconds=-10), "input_tokens": 5_000, "cached_tokens": None}
    assert T.warmth(no_usage, now) == (False, 5_000), "a harness that reports no cache use (Grok) is always cold"
    assert T.warmth({**no_usage, "cached_tokens": 0}, now)[0] is False
    assert T.warmth(None, now) == (False, 0), "no turn on the session yet: cold"


@pytest.mark.parametrize("context,cached,age,checked", [
    (15_000, 8_000, 30, False),        # under 20k, warm: no check (acceptance 2)
    (60_000, 20_000, 30, False),       # 20k to 100k, warm: no check (the lead's reading)
    (100_000, 40_000, 30, True),       # 100k or more: checked, warm or not
    (15_000, 8_000, 600, True),        # cold (stale turn): checked at any size
    (15_000, 0, 30, True),             # cold (no cache use reported): checked
])
def test_the_size_bands_and_warmth_decide_whether_decide_is_asked(chat, context, cached, age, checked):
    api, _, conversation, model, usage = chat
    model.choice = "same_topic"
    usage(context, cached, age)
    post(api, "chat/ops", {"text": "Next one"})
    assert bool(model.calls) is checked and len(checks(api)) == int(checked)
    if checked:
        row = checks(api)[0]
        assert row["context_tokens"] == context and row["gate"] == ("big" if context >= 100_000 else "cold")


# -------------------------------------------------------------------------------- a new topic clears like Clear

def test_a_new_topic_clears_the_session_like_the_manual_clear_and_marks_the_message(chat):
    api, machine, conversation, model, _ = chat
    assert session_state(api, conversation) == {"epoch": 0, "bot_epoch": 0, "thread": True}
    response = api.post("/api/v2/chat/ops", json={"text": UNRELATED}, headers=headers())
    assert response.status_code == 200, response.text
    message = response.json()["message"]
    assert message["refs"].get("topic_changed") is True, "the chat shows the quiet line above this message"
    assert session_state(api, conversation) == {"epoch": 1, "bot_epoch": 1, "thread": False}

    # Decide saw the last 3 turns, each the person's words and the bot's answer, and the new message.
    state = model.calls[0]["state"]
    assert [t["person"] for t in state["recent"]] == ["Fix the login redirect bug", "Did the tests pass?", "Push the fix"]
    assert all(t["bot"] == "Done." for t in state["recent"]) and state["message"] == UNRELATED
    assert model.calls[0]["label"] == "topic-change@1"

    # The next turn starts fresh: a new epoch, and history from the new message only.
    turn = claim(api, machine)
    assert turn["session_epoch"] == turn["bot_session_epoch"] == 1
    assert [m["body"] for m in turn["history"]] == [UNRELATED]
    finish(api, machine, turn)

    with api.app.state.store.read() as c:
        clears = [(r[0], json.loads(r[1] or "null")) for r in c.execute(
            "SELECT target,detail_json FROM events WHERE action='session.clear'")]
        measures = [json.loads(r[0]) for r in c.execute(
            "SELECT detail_json FROM events WHERE action='chat.topic_changed' AND target=?", (conversation,))]
    assert clears == [(conversation, {"reason": "topic_change"})]
    assert measures == [{"message_id": message["id"], "tokens_before": 150_000, "delay_ms": measures[0]["delay_ms"]}]
    assert 0 <= measures[0]["delay_ms"] < 1500
    row = checks(api)[0]
    assert row["clear"] is True and row["answers"] == {"topic": {"choice": "new_topic", "confidence": 0.95}}
    assert UNRELATED not in json.dumps(row) and "login" not in json.dumps(row), "the audit never keeps the chat's text"


def test_the_topic_clear_leaves_the_same_state_as_the_manual_clear(chat):
    api, machine, conversation, model, _ = chat
    post(api, "chat/ops", {"text": UNRELATED})
    after_topic = session_state(api, conversation)
    finish(api, machine, claim(api, machine))
    # The person's own Clear, from the same kind of state: each moves the same three things by one step.
    assert clear(api).status_code == 200
    after_manual = session_state(api, conversation)
    assert after_topic == {"epoch": 1, "bot_epoch": 1, "thread": False}
    assert after_manual == {"epoch": 2, "bot_epoch": 2, "thread": False}


# -------------------------------------------------------------------------------- no clear

@pytest.mark.parametrize("choice,confidence", [("same_topic", 0.97), ("not_sure", 0.9), ("new_topic", 0.55)])
def test_a_message_that_refers_back_or_an_unsure_answer_sends_in_the_same_session(chat, choice, confidence):
    api, _, conversation, model, _ = chat
    model.choice, model.confidence = choice, confidence
    message = post(api, "chat/ops", {"text": "Do that again"})
    assert len(model.calls) == 1, "the check ran"
    assert "topic_changed" not in (message.get("refs") or {})
    assert session_state(api, conversation) == {"epoch": 0, "bot_epoch": 0, "thread": True}
    assert checks(api)[0]["clear"] is False


def test_a_slow_decide_does_not_hold_the_send_past_the_budget(chat):
    api, machine, conversation, model, _ = chat
    model.hold = threading.Event()
    try:
        started = time.monotonic()
        message = post(api, "chat/ops", {"text": UNRELATED})
        took = time.monotonic() - started
    finally:
        model.hold.set()
    assert took < T.BUDGET + 0.5, f"the send waited {took:.2f}s"
    assert "topic_changed" not in (message.get("refs") or {})
    assert session_state(api, conversation)["epoch"] == 0
    assert checks(api)[0]["error"] == "timeout" and checks(api)[0]["delay_ms"] <= 1500 + 100
    turn = claim(api, machine)
    assert turn["session_epoch"] == 0 and UNRELATED in [m["body"] for m in turn["history"]]


@pytest.mark.parametrize("fail", [J.JudgeError("unavailable", "down", retryable=True), RuntimeError("bug"),
                                  J.JudgeError("spend_cap", "cap", 429)])
def test_a_decide_error_or_the_cap_sends_in_the_same_session(chat, fail):
    api, _, conversation, model, _ = chat
    model.fail = fail
    message = post(api, "chat/ops", {"text": UNRELATED})
    assert "topic_changed" not in (message.get("refs") or {})
    assert session_state(api, conversation)["epoch"] == 0
    assert checks(api)[0]["clear"] is False and checks(api)[0]["error"]


def test_the_daily_budget_reached_asks_nothing_and_sends_in_the_same_session(chat):
    api, _, conversation, model, _ = chat
    api.app.state.store.settings.decisions_daily_calls_owner = 0
    post(api, "chat/ops", {"text": UNRELATED})
    assert model.calls == [] and checks(api)[0]["error"] == "budget"
    assert session_state(api, conversation)["epoch"] == 0


# -------------------------------------------------------------------------------- never checked

def test_never_checked_with_the_switch_off(chat):
    api, _, _, model, _ = chat
    with api.app.state.store.transaction() as c:
        team_rules.save(c, "human:ana", {"topic_fresh": False})
    post(api, "chat/ops", {"text": UNRELATED})
    assert model.calls == [] and checks(api) == []


def test_never_checked_with_an_active_chat_goal(chat):
    api, _, conversation, model, _ = chat
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO chat_goals(id,conversation_id,bot,objective,status,set_by,set_at,updated_at) "
                  "VALUES('g1',?,'ops','Ship the fix','active','human:ana',?,?)", (conversation, H.now(), H.now()))
    post(api, "chat/ops", {"text": UNRELATED})
    assert model.calls == [] and checks(api) == []


def test_never_checked_while_a_turn_is_running(chat):
    api, machine, _, model, _ = chat
    model.choice = "same_topic"
    post(api, "chat/ops", {"text": "Run the long job"})       # checked (big), same session
    assert len(model.calls) == 1
    model.calls.clear()
    claim(api, machine)                                     # now running
    post(api, "chat/ops", {"text": UNRELATED})
    assert model.calls == []


def test_never_checked_under_three_turns_since_the_last_clear(chat):
    api, machine, conversation, model, _ = chat
    assert clear(api).status_code == 200
    for text in ("One", "Two"):
        post(api, "chat/ops", {"text": text})
        finish(api, machine, claim(api, machine))
    post(api, "chat/ops", {"text": UNRELATED})
    assert model.calls == [], "two turns since the clear"


def test_never_checked_in_a_task_chat(chat):
    api, _, _, model, _ = chat
    task = post(api, "tasks", {"owner": "ops", "title": "Look into it", "body": "The login bug"})
    post(api, f"tasks/{task['id']}/chat", {"text": UNRELATED})
    post(api, "chat/ops", {"text": UNRELATED, "refs": {"task": task["id"]}})
    assert model.calls == [] and checks(api) == []


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
