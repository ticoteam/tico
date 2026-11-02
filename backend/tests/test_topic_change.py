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
            last = c.execute("SELECT t.id FROM turns t JOIN bot_sessions s ON s.bot=t.bot AND s.thread_id=t.thread_id "
                             "WHERE t.bot='ops'").fetchone()[0]
            c.execute("UPDATE turns SET input_tokens=?,cached_tokens=?,prompt_tokens=?,finished_at=? WHERE id=?",
                      (summed - cached, cached, context, H.shift(H.now(), seconds=-age), last))
    usage()
    return api, machine, conversation, model, usage


def checks(api):
    T.settle()                                # each row is written once the send has its answer
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor,detail FROM events WHERE action='judge.call' AND target='topic-change@1' "
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
    recent = {"finished_at": H.shift(now, seconds=-60), "input_tokens": 5_000, "cached_tokens": 12_000,
              "prompt_tokens": 17_000}
    assert T.warmth(recent, now) == (True, 17_000), "a recent turn with cached tokens: warm; context is its last prompt"
    stale = {**recent, "finished_at": H.shift(now, seconds=-301)}
    assert T.warmth(stale, now) == (False, 17_000), "older than the 5-minute cache lifetime: cold"
    no_usage = {"finished_at": H.shift(now, seconds=-10), "cached_tokens": None, "prompt_tokens": 5_000}
    assert T.warmth(no_usage, now) == (False, 5_000), "a harness that reports no cache use (Grok) is always cold"
    assert T.warmth({**no_usage, "cached_tokens": 0}, now)[0] is False
    assert T.warmth({**recent, "input_tokens": None}, now)[0] is False, "cached but no fresh count: unknown share, cold"
    assert T.warmth({**recent, "prompt_tokens": None}, now) == (True, None), "no request counts: size unknown"
    assert T.warmth(None, now) == (False, None), "no turn on the session yet: cold"


@pytest.mark.parametrize("cached,fresh,warm", [
    (50_000, 50_000, True),            # exactly half the input from the cache: warm
    (49_000, 51_000, False),           # 49%: cold
    (1_000, 59_000, False),            # a tiny cache hit on a 60k turn: cold, so it gets checked
    (12_000, 0, True),                 # everything cached
])
def test_warm_needs_at_least_half_the_input_read_from_the_cache(cached, fresh, warm):
    now = H.now()
    turn = {"finished_at": H.shift(now, seconds=-60), "input_tokens": fresh, "cached_tokens": cached,
            "prompt_tokens": cached + fresh, "runtime": "claude"}
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
    unknown = {"finished_at": H.shift(now, seconds=-(T.WARM_SECONDS + 1)), "input_tokens": 0, "cached_tokens": 9_000,
               "runtime": "something-new"}
    assert T.warmth(unknown, now)[0] is False and T.warmth({**unknown, "finished_at": H.shift(now, seconds=-60)}, now)[0], \
        "a harness not in the table takes the 5-minute default"


def test_the_lifetimes_are_the_documented_ones_and_are_read_per_harness(chat, monkeypatch):
    # Pinned to the provider docs cited in CACHE_LIFETIME: change one only with its doc.
    assert T.CACHE_LIFETIME == {"claude": 300, "codex": 300, "gemini": 300, "grok": 300, "pi": 300, "cursor": 300,
                                "musecode": 300}
    api, _, _, _, usage = chat
    usage(15_000, 12_000, 600)
    with api.app.state.store.read() as c:
        turn = T.last_turn(c, api.app.state.store.settings, "ops")
    assert turn["runtime"] == "fake", "the test runner's harness, read from the bot's session"
    assert T.warmth(turn)[0] is False, "10 minutes old: cold under the 5-minute default"
    monkeypatch.setitem(T.CACHE_LIFETIME, turn["runtime"], 900)
    assert T.warmth(turn)[0] is True, "the bot's own harness lifetime decides, not the default"


@pytest.mark.parametrize("context,cached,age,checked", [
    (15_000, 8_000, 30, False),        # under 20k, warm: no check (acceptance 2)
    (60_000, 45_000, 30, False),       # 20k to 100k, warm (75% cached): no check (the lead's reading)
    (60_000, 20_000, 30, True),        # 20k to 100k with a small cache hit (33%): cold, so checked (Dev 190)
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


@pytest.mark.parametrize("context,age,checked", [
    (15_000, 30, False),               # six warm requests of about 18k: summed over 100k, the last prompt under 20k
    (None, 30, False),                 # a runner that sent no request counts: the size never triggers a check
    (None, 600, True),                 # ... a cold cache still does
])
def test_the_size_is_the_newest_prompt_never_the_turns_summed_usage(chat, context, age, checked):
    api, _, _, model, usage = chat
    model.choice = "same_topic"
    usage(context, 90_000, age, summed=6 * 18_000)
    post(api, "chat/ops", {"text": "Next one"})
    assert bool(model.calls) is checked and len(checks(api)) == int(checked)
    if checked:
        assert checks(api)[0]["context_tokens"] is None and checks(api)[0]["gate"] == "cold"


def run_with(api, machine, requests):
    """A turn whose runner reports these per-request counts; its stored (input, cached, prompt) tokens."""
    with api.app.state.store.transaction() as c:
        team_rules.save(c, "human:ana", {"topic_fresh": False})
    post(api, "chat/ops", {"text": "Next one"})
    turn = claim(api, machine)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-" + turn["id"][:8]}, machine["token"])
    totals = [sum(r[i] for r in requests) for i in range(3)]
    part = {"model": "claude-haiku-5-5", "runtime": "claude", "billing": "api", "input_tokens": totals[0],
            "cached_tokens": totals[1], "output_tokens": totals[2], "requests": requests}
    r = api.post(f"/api/v2/attempts/{turn['id']}/complete", headers=headers(machine["token"]),
                 json={"outcome": "completed", "last_seq": 0, "text": "Done.", "usage": {**part, "segments": [part]}})
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        return tuple(c.execute("SELECT input_tokens,cached_tokens,prompt_tokens FROM turns WHERE id=?",
                               (turn["id"],)).fetchone())


@pytest.mark.parametrize("requests,prompt", [
    # The runner's own fixture (runner-rs claude.rs): a 123,002-token prompt of the bot's, then a subagent's 900.
    ([[3002, 120000, 78, 1], [900, 0, 5, 0]], 123_002),
    # A small prompt of the bot's, then a large subagent's: the session is still small.
    ([[500, 15000, 100, 1], [2000, 118000, 50, 0]], 15_500),
    ([[500, 50000, 100, 1], [1000, 54000, 200, 1], [300, 0, 9, 0]], 55_000),   # the newest of the bot's own
    ([[500, 50000, 100], [1000, 54000, 200]], None),     # an older runner: no marks, size unknown
    ([[900, 0, 5, 0]], None),                            # only a subagent's: unknown
])
def test_a_turn_keeps_the_newest_of_the_bots_own_prompts_as_the_context_size(chat, requests, prompt):
    api, machine, _, _, _ = chat
    stored = run_with(api, machine, requests)
    assert stored == (sum(r[0] for r in requests), sum(r[1] for r in requests), prompt), \
        "the summed totals stay; the context is the bot's own newest prompt"


def test_the_server_tells_runners_it_takes_the_marks(chat):
    """The runner sends each request's fourth count only when the server names this feature (runner-rs execute.rs
    `served`); an older server refuses it."""
    api, machine, _, _, _ = chat
    features = api.get("/api/v2/runners/desired", headers=headers(machine["token"])).json()["features"]
    assert features["usage_requests"] is True and features["usage_request_main"] is True
    assert features["run_pushes"] is True


@pytest.mark.parametrize("requests,checked", [
    ([[3002, 120000, 78, 1], [900, 0, 5, 0]], True),     # a warm session over 100k: checked, whatever the subagent
    ([[500, 15000, 100, 1], [2000, 118000, 50, 0]], False),   # a warm small session: a large subagent never checks it
    ([[3002, 120000, 78]], False),                       # an older runner, warm: size unknown, no check
])
def test_only_the_bots_own_prompt_size_triggers_a_check(chat, requests, checked):
    api, machine, _, model, _ = chat
    run_with(api, machine, requests)
    with api.app.state.store.transaction() as c:
        team_rules.save(c, "human:ana", {"topic_fresh": True})
    model.choice = "same_topic"
    post(api, "chat/ops", {"text": "And another"})
    assert bool(model.calls) is checked and len(checks(api)) == int(checked)
    if checked:
        assert checks(api)[0]["gate"] == "big"


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
            "SELECT target,detail FROM events WHERE action='session.clear'")]
        measures = [json.loads(r[0]) for r in c.execute(
            "SELECT detail FROM events WHERE action='chat.topic_changed' AND target=?", (conversation,))]
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
    assert checks(api)[0]["error"] == "timeout" and checks(api)[0]["clear"] is False, "a late new topic is dropped"
    assert 1400 <= checks(api)[0]["delay_ms"] <= 1500 + 100, "the delay is what the send waited"
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


@pytest.mark.parametrize("slow", ["gate", "audit"])
def test_slow_reads_or_a_slow_audit_write_do_not_hold_the_send_past_the_budget(chat, monkeypatch, slow):
    api, _, conversation, model, _ = chat
    release = threading.Event()
    real = getattr(T, "_" + slow if slow == "audit" else slow)

    def held(*args, **kwargs):
        release.wait(5)
        return real(*args, **kwargs)
    monkeypatch.setattr(T, "_audit" if slow == "audit" else "gate", held)
    try:
        started = time.monotonic()
        message = post(api, "chat/ops", {"text": UNRELATED})
        took = time.monotonic() - started
    finally:
        release.set()
    assert took < T.BUDGET + 0.5, f"the send waited {took:.2f}s"
    if slow == "gate":                        # the reads ran past the budget: no call is made or applied
        assert model.calls == [] and checks(api) == []
        assert "topic_changed" not in (message.get("refs") or {}) and session_state(api, conversation)["epoch"] == 0
    else:                                     # the answer came in time; its audit row follows on its own
        assert message["refs"].get("topic_changed") is True and session_state(api, conversation)["epoch"] == 1
        assert [r["clear"] for r in checks(api)] == [True], "one audit row, still counted against the day's calls"


# -------------------------------------------------------------------------------- the chat changed meanwhile

def bot_says(c, conversation, text):
    """A bot message lands in the chat (a queued reply, another tab's send) while Decide is asked."""
    c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at) "
              "VALUES(?,?,'bot:ops','human:ana','say',?,?)", ("late-" + text[:4], conversation, text, H.now()))


CHANGES = {
    "cleared": lambda api, c, conv: views.clear_chat_session(c, api.app.state.store.settings, "human:ana", "ops", conv),
    "message": lambda api, c, conv: bot_says(c, conv, "One more thing"),
    "goal": lambda api, c, conv: c.execute(
        "INSERT INTO chat_goals(id,conversation_id,bot,objective,status,set_by,set_at,updated_at) "
        "VALUES('g1',?,'ops','Ship the fix','active','human:ana',?,?)", (conv, H.now(), H.now())),
    "off": lambda api, c, conv: team_rules.save(c, "human:ana", {"topic_fresh": False}),
}


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_decision_about_a_chat_that_changed_meanwhile_clears_nothing(chat, change):
    api, _, conversation, model, _ = chat
    before = session_state(api, conversation)
    model.hold = threading.Event()
    sent = {}
    sender = threading.Thread(target=lambda: sent.update(post(api, "chat/ops", {"text": UNRELATED})))
    sender.start()
    deadline = time.monotonic() + 1.0
    while not model.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert model.calls, "the check is asking Decide"
    with api.app.state.store.transaction() as c:
        CHANGES[change](api, c, conversation)
    model.hold.set()                          # a confident new topic, about the chat as it was
    sender.join(5)
    assert "topic_changed" not in (sent.get("refs") or {}), "no divider"
    expected = {**before, "epoch": 1, "bot_epoch": 1, "thread": False} if change == "cleared" else before
    assert session_state(api, conversation) == expected, "no clear beyond what happened meanwhile"
    with api.app.state.store.read() as c:
        kept = [json.loads(r[0]) for r in c.execute("SELECT detail FROM events WHERE action='chat.topic_kept'")]
        changed = c.execute("SELECT count(*) FROM events WHERE action='chat.topic_changed'").fetchone()[0]
    assert kept == [{"why": {"cleared": "changed", "message": "changed"}.get(change, change)}] and changed == 0
    assert [r["clear"] for r in checks(api)] == [True], "the audit keeps the decision; topic_kept says it was refused"


def test_overlapping_new_topic_sends_clear_once_and_keep_the_first_message(chat):
    api, machine, conversation, model, _ = chat
    model.hold = threading.Event()
    sent = []
    senders = [threading.Thread(target=lambda t=t: sent.append(post(api, "chat/ops", {"text": t})))
               for t in (UNRELATED, "Plan the offsite agenda")]
    for sender in senders:
        sender.start()
        deadline = time.monotonic() + 1.0
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
    assert sorted(m["body"] for m in turn["history"]) == sorted(m["body"] for m in sent), \
        "both new messages are after the clear"


def test_a_clear_between_admission_and_history_is_not_cleared_again(chat, monkeypatch):
    """A Clear that commits after the gate admitted the old three-turn session but before history and the marker are
    read: the check reads the chat as admitted, so its decision no longer matches and nothing is cleared twice."""
    api, machine, conversation, _, _ = chat
    store = api.app.state.store
    real = T.gate

    def gate_then_clear(*args, **kwargs):
        verdict = real(*args, **kwargs)
        assert verdict[0], "the old session was admitted"
        with store.transaction() as c:          # another tab's Clear commits now
            views.clear_chat_session(c, store.settings, "human:ana", "ops", conversation)
        return verdict
    monkeypatch.setattr(T, "gate", gate_then_clear)
    message = post(api, "chat/ops", {"text": UNRELATED})
    assert "topic_changed" not in (message.get("refs") or {}), "no divider"
    assert session_state(api, conversation) == {"epoch": 1, "bot_epoch": 1, "thread": False}, "cleared once"
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
            skew[0] = T.BUDGET + 0.1             # the answer arrives past the deadline; the send has not woken yet
            return answer
    api.app.state.judge = Late()
    message = post(api, "chat/ops", {"text": UNRELATED})
    assert "topic_changed" not in (message.get("refs") or {}), "a late new topic is never applied"
    assert session_state(api, conversation)["epoch"] == 0
    row = checks(api)[0]
    assert row["error"] == "timeout" and row["clear"] is False and row["answers"]["topic"]["choice"] == "new_topic"


def test_sends_at_the_same_moment_cannot_pass_the_daily_call_limit_together(chat):
    """One call left today: the first send reserves it while Decide is asked; a second send at that moment finds none
    free and asks nothing. The reservation is handed back once the first call's audit row counts it."""
    api, _, _, model, _ = chat
    settings = api.app.state.store.settings
    settings.decisions_daily_calls_owner = 1
    model.hold = threading.Event()
    first = threading.Thread(target=lambda: post(api, "chat/ops", {"text": UNRELATED}))
    first.start()
    deadline = time.monotonic() + 5.0
    while not model.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(model.calls) == 1, "the first send is asking Decide with the last call"
    try:
        post(api, "chat/ops", {"text": "Plan the offsite agenda"})
    finally:
        model.hold.set()
        first.join(5)
    assert len(model.calls) == 1, "the second send found no call free and asked nothing"
    errors = [r.get("error") for r in checks(api)]   # each row lands once its send returns, in either order
    assert len(errors) == 2 and errors.count("budget") == 1, "the second asked nothing; the first asked with the call"
    assert T._SLOTS == {}, "every reservation is handed back"
    settings.decisions_daily_calls_owner = 2      # the next day's room: one more call, counted from the audit rows
    post(api, "chat/ops", {"text": "Order the team lunch"})
    assert len(model.calls) == 1, "two rows today already use both calls"


# -------------------------------------------------------------------------------- never checked

def test_never_checked_in_a_shared_bots_room_even_with_one_person(chat):
    api, _, conversation, model, _ = chat
    with api.app.state.store.transaction() as c:   # what rooms.sync_shared_room makes when its owner is its only member
        c.execute("UPDATE conversations SET scope='shared' WHERE id=?", (conversation,))
    before = session_state(api, conversation)
    post(api, "chat/ops", {"text": UNRELATED})
    assert model.calls == [] and checks(api) == [] and session_state(api, conversation) == before

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


def test_never_checked_with_files_uploaded_alongside(chat):
    """The multipart route (files uploaded with the message): no check, the session kept."""
    api, machine, conversation, model, usage = chat
    before = session_state(api, conversation)
    r = api.post("/api/v2/uploads/chat/ops", data={"text": UNRELATED},
                 files={"files": ("note.txt", b"Agenda for the offsite", "text/plain")}, headers=headers())
    assert r.status_code == 200, r.text
    assert model.calls == [] and checks(api) == [] and session_state(api, conversation) == before
    assert not r.json()["message"]["refs"].get("topic_changed")
    finish(api, machine, claim(api, machine))
    usage()
    post(api, "chat/ops", {"text": UNRELATED})
    assert len(model.calls) == 1, "the same chat, without files, is checked"


def test_never_checked_with_files_attached_on_the_json_route(chat):
    """A file already in the chat, attached by reference on the JSON route (`attachments`): no check, the session
    kept, as for files uploaded alongside. QA on the v0.3.64 candidate saw one Decide call and a clear here."""
    api, machine, conversation, model, usage = chat
    r = api.post("/api/v2/uploads/chat/ops", data={"text": "Here is the note"},
                 files={"files": ("note.txt", b"Agenda for the offsite", "text/plain")}, headers=headers())
    fid = r.json()["message"]["refs"]["attachments"][0]["file_id"]
    finish(api, machine, claim(api, machine))
    usage()
    before = session_state(api, conversation)
    message = post(api, "chat/ops", {"text": UNRELATED, "attachments": [f"{fid}@1"]})
    assert [a["file_id"] for a in message["refs"]["attachments"]] == [fid], "the file still goes with the message"
    assert model.calls == [] and checks(api) == [] and session_state(api, conversation) == before
    assert not message["refs"].get("topic_changed")
    finish(api, machine, claim(api, machine))
    usage()
    post(api, "chat/ops", {"text": UNRELATED})
    assert len(model.calls) == 1, "the same chat, without files, is checked"


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
