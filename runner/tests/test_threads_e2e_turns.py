"""Native threads, on the real Rust runner: a bot's turns in a thread, from the wake to the reply (docs/dev/threads-contract.md
sections 4.3 to 4.5 and 8).

The Computer, its fake harnesses on PATH and the release binary are the conformance suite's own
(runner/tests/test_conformance.py): every test starts a real `tico-runner run` against the live API, and the fake
`claude` or `codex` records each provider turn it is given. The tests here answer the threads questions: who a
wake starts, which sub-thread the reply lands in, when the loop guard holds a wake, and which provider session a
turn continues. A peer bot whose turns the test finishes by hand (cpo) makes bot-caused wakes without a second
computer.

Slow: it builds the release binary once per session and starts runner processes.
"""

import json
import sqlite3
import time

import pytest

from backend.tests.live import live  # noqa: F401  (the live fixture the Computer listens through)
from backend.tests.test_api import api, assign, claim, post, ready, runner  # noqa: F401  (the api fixture)
from backend.tests.threads_e2e import call, create_thread, home_id, ok, require_threads_api, say, timeline
from runner.tests.test_conformance import Computer, computer, harness, rust_binary, setup, until  # noqa: F401

pytestmark = pytest.mark.slow

HARNESSES = ["claude", "codex"]


def finish(api, r, attempt, text):
    """The peer bot's answer, as runner-rs sends it: started, one final event, then completion."""
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "local-thread"}, token=r["token"])
    event = {"events": [{"seq": 1, "kind": "message", "payload": {"text": text, "final": True}}]}
    post(api, f"attempts/{aid}/events", event, token=r["token"])
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": text, "last_seq": 1}, token=r["token"])


def bodies(api, thread_id, token="ana-test"):
    return [row.get("body") for row in timeline(api, token, thread_id)]


def provider_turns(computer):
    """Each provider turn the fake harness saw, in order, as (started, thread): `new` with the thread it made, or
    `resume` with the thread it continued. Claude names its session on the command line; Codex's thread is in the
    thread/start or thread/resume call."""
    turns = []
    for call_ in computer.calls():
        if computer.harness == "claude" and "prompt" in call_:
            argv = call_["argv"]
            if "--resume" in argv:
                turns.append(("resume", argv[argv.index("--resume") + 1]))
            else:
                turns.append(("new", argv[argv.index("--session-id") + 1]))
        elif computer.harness == "codex" and call_.get("harness") == "codex" \
                and call_.get("method") in ("thread/start", "thread/resume"):
            turns.append(("new" if call_["method"] == "thread/start" else "resume", call_["thread"]))
    return turns


def finished(computer):
    return len([a for a in computer.attempts() if a["result"]])


@pytest.mark.parametrize("harness", HARNESSES)
def test_a_bot_woken_in_a_sub_thread_answers_there_from_that_subtree_and_no_sibling(api, computer):
    require_threads_api(api)
    computer.start()
    t = create_thread(api, title="Launch", members=["bot:ops"])
    kickoff = say(api, "ana-test", t["id"], "Kickoff notes")
    pricing = say(api, "ana-test", t["id"], "Pricing notes")
    # A sibling sub-thread under another message: its text is never in the asked sub-thread's turn.
    say(api, "ana-test", t["id"], "SIDE-SECRET: an unrelated pricing question", parent_message_id=pricing["id"])
    asked = say(api, "ana-test", t["id"], "@ops list the risks", parent_message_id=kickoff["id"])
    sub = asked["thread_id"]
    until(lambda: "Acme reply." in bodies(api, sub), "the bot's answer in the sub-thread that asked")
    attempt = computer.attempts()[-1]
    assert attempt["state"] == "completed" and attempt["result"]["outcome"] == "completed"
    prompt = computer.prompts()[-1]["prompt"]
    assert "list the risks" in prompt, "the turn carries the message it was woken for"
    assert "SIDE-SECRET" not in prompt, "a turn in one sub-thread never carries a sibling sub-thread"
    in_tree = [row for row in timeline(api, "ana-test", t["id"]) if row.get("body") == "Acme reply."]
    assert [row["thread_id"] for row in in_tree] == [sub], "the root's timeline shows the answer under its sub-thread"
    reply = next(row for row in timeline(api, "ana-test", sub) if row.get("body") == "Acme reply.")
    assert reply["thread_id"] == sub, "the answer lands in the sub-thread that asked"


@pytest.mark.parametrize("harness", ["claude"])
def test_a_mention_wakes_the_bot_and_plain_chatter_in_its_thread_does_not(api, computer):
    require_threads_api(api)
    computer.start()
    t = create_thread(api, title="Standup", members=["bot:ops", "human:cara"])
    chatter = say(api, "cara-test", t["id"], "lunch at noon?")
    time.sleep(2)
    assert computer.attempts() == [], "plain chatter in a thread the bot follows starts no run"
    with api.app.state.store.read() as c:
        modes = c.execute("SELECT mode FROM message_deliveries WHERE message_id=? AND recipient='bot:ops'",
                          (chatter["id"],)).fetchall()
    assert all(mode[0] in ("digest", "none") for mode in modes), f"the bot gets no run for chatter: {modes}"
    asked = say(api, "ana-test", t["id"], "@ops what is on the agenda?")
    until(lambda: finished(computer) == 1, "the reply to the mention")
    assert len(computer.attempts()) == 1, "one mention starts one run"
    with api.app.state.store.read() as c:
        mode = c.execute("SELECT mode, reason FROM message_deliveries WHERE message_id=? AND recipient='bot:ops'",
                         (asked["id"],)).fetchone()
    assert tuple(mode) == ("queue", "mention"), f"an addressed mention is a queued run: {tuple(mode)}"


@pytest.mark.parametrize("harness", ["claude"])
def test_the_loop_guard_holds_a_bots_wakes_and_a_persons_message_resumes_them(api, computer, monkeypatch):
    require_threads_api(api)
    from backend import threads
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 2)
    computer.start()
    peer = runner(api)
    assign(api, peer, "cpo")
    ready(api, peer, ["cpo"])
    t = create_thread(api, title="Ping pong", members=["bot:ops", "bot:cpo"])

    def started():
        return len(computer.attempts())

    def held():
        with api.app.state.store.read() as c:
            return c.execute("SELECT COUNT(*) FROM message_deliveries WHERE root_id=? AND recipient='bot:ops' "
                             "AND state='capped'", (t["id"],)).fetchone()[0]

    def deliveries():
        with api.app.state.store.read() as c:
            return [tuple(r) for r in c.execute("SELECT mode, reason, state, cause FROM message_deliveries "
                                                "WHERE root_id=? AND recipient='bot:ops' ORDER BY created_at",
                                                (t["id"],)).fetchall()]

    for n in range(1, 7):
        say(api, "ana-test", t["id"], f"@cpo round {n}")
        turn = claim(api, peer, "cpo")
        assert turn is not None, f"round {n}: a person's mention wakes cpo"
        before = started()
        # A plain bot-to-bot message waits for its recipient's next run (backend/bot_to_bot.py), so cpo's ping is flagged
        # needs-action: that is the bot-caused wake the loop guard counts. The flag is written with cpo's own turn token.
        post(api, f"threads/{t['id']}/messages", {"text": f"@ops ping {n}", "kind": "say",
                                                  "refs": {"needs_action": True}}, token=turn["token"])
        finish(api, peer, turn, "")
        try:
            until(lambda: started() > before or held() > 0, f"round {n}: ops runs, or its wake is held")
        except AssertionError as error:
            raise AssertionError(f"{error}; ops deliveries {deliveries()}, runs {started()}") from error
        if held():
            break
        # A wake that lands while ops is still in its last turn is steered into that turn, not a new run: let it finish.
        until(lambda: finished(computer) == started(), f"round {n}: ops ends its turn before the next wake")
    assert held() >= 1, "past the guard the bot's wake is held as capped, never dropped"
    stopped = started()
    time.sleep(2)
    assert started() == stopped, "a held wake starts no run"
    attention = ok(call(api, "GET", "threads", "ana-test"))["attention"]
    assert t["id"] in [row.get("root_id") for row in attention], "the root is Needs you while its wakes are held"
    say(api, "ana-test", t["id"], "@ops carry on")
    until(lambda: started() > stopped, "a person's message resumes the held wakes on the real runner")
    until(lambda: held() == 0, "the resumed wakes are no longer capped")


@pytest.mark.parametrize("harness", HARNESSES)
def test_a_thread_keeps_its_provider_session_and_no_other_thread_shares_it(api, computer):
    require_threads_api(api)
    computer.start()
    plan = create_thread(api, title="Plan", members=["bot:ops"])
    say(api, "ana-test", plan["id"], "@ops first question")
    until(lambda: len(provider_turns(computer)) == 1 and finished(computer) == 1, "the first reply")
    say(api, "ana-test", plan["id"], "@ops second question")
    until(lambda: finished(computer) == 2, "the second reply")
    other = create_thread(api, title="Other plan", members=["bot:ops"])
    say(api, "ana-test", other["id"], "@ops a question in another private thread")
    until(lambda: finished(computer) == 3, "the reply in the other thread")
    dm = ok(call(api, "POST", "threads", "ana-test", {"audience": "personal", "members": ["bot:ops"]}))
    say(api, "ana-test", dm["id"], "@ops a direct message")
    until(lambda: finished(computer) == 4, "the reply in the direct message")
    home = home_id(api, "ops")
    say(api, "ana-test", home, "a note for the Home")
    until(lambda: finished(computer) == 5, "the reply in the Home")
    first, second, third, fourth, fifth = provider_turns(computer)
    assert first[0] == "new" and second == ("resume", first[1]), "a second turn in one thread resumes its session"
    assert third[0] == "new" and third[1] != first[1], "another private thread gets a session of its own"
    assert fourth[0] == "new" and fourth[1] not in (first[1], third[1]), "a DM never shares a thread's session"
    assert fifth[0] == "new" and fifth[1] not in (first[1], third[1], fourth[1]), "the Home never shares the DM's session"


@pytest.mark.parametrize("harness", ["claude"])
def test_the_first_turn_after_an_update_starts_fresh_and_recovers_only_its_roots_history(api, computer):
    require_threads_api(api)
    computer.start()
    elsewhere = create_thread(api, title="Pricing", members=["bot:ops"])
    say(api, "ana-test", elsewhere["id"], "OTHER-ROOM note: the price list is final")
    room = create_thread(api, title="Launch", members=["bot:ops"])
    say(api, "ana-test", room["id"], "ROOM-B note: launch is Tuesday")
    # The state an older runner left: the bot's one shared thread, under the key every chat used before threads.
    db = computer.config.parent / f"state-{computer.registration['runner_id']}" / "runner.sqlite"
    with sqlite3.connect(db, timeout=10) as conn:
        conn.execute("INSERT OR REPLACE INTO sessions VALUES('ops','bot','claude','old-bot-thread-0001')")
    say(api, "ana-test", room["id"], "@ops summarise this room")
    until(lambda: finished(computer) == 1, "the reply after the update")
    kind, thread = provider_turns(computer)[-1]
    assert kind == "new" and thread != "old-bot-thread-0001", "the old shared thread is never resumed by a root"
    prompt = computer.prompts()[-1]["prompt"]
    # The fresh thread is replayed this root's earlier messages inline, oldest first (runner-rs/src/fallback.rs
    # first_turn_pack), and nothing from another root.
    assert "ROOM-B note: launch is Tuesday" in prompt, "the fresh thread is replayed this root's earlier messages"
    assert elsewhere["id"] not in prompt and "OTHER-ROOM" not in prompt, \
        "nothing from another root reaches the fresh thread's prompt"


@pytest.mark.parametrize("harness", ["claude"])
def test_a_public_turn_is_replayed_its_threads_public_history_and_no_direct_message_or_legacy_text(api, computer):
    require_threads_api(api)
    computer.start()
    dm = ok(call(api, "POST", "threads", "ana-test", {"audience": "personal", "members": ["bot:ops"]}))
    say(api, "ana-test", dm["id"], "@ops DM-SECRET: the board deck is late")
    until(lambda: finished(computer) == 1, "the reply in the direct message")
    seen = len(computer.calls())
    company = create_thread(api, title="Launch notes", audience="company", members=["bot:ops"])
    say(api, "ana-test", company["id"], "PUBLIC-ROOM note: launch is Tuesday")
    legacy = say(api, "ana-test", company["id"], "LEGACY-SECRET: the price is internal")
    # A message its old readers alone may read: kept in the thread, never replayed to a public session.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET legacy_audience='shared:nobody', restricted=1 WHERE id=?", (legacy["id"],))
    say(api, "ana-test", company["id"], "@ops summarise the launch notes")
    until(lambda: finished(computer) == 2, "the reply in the public thread")
    kind, _thread = provider_turns(computer)[-1]
    assert kind == "new", "a public thread's first turn starts its own session"
    prompt = computer.prompts()[-1]["prompt"]
    assert "PUBLIC-ROOM note: launch is Tuesday" in prompt, "a public turn is replayed its thread's public history"
    assert "DM-SECRET" not in prompt, "a direct message never reaches a public turn"
    assert "LEGACY-SECRET" not in prompt, "a legacy-audience message never reaches a public turn"
    # Nothing the provider saw during the public turn, not only its prompt, carries the direct message's text.
    leaked = [call_ for call_ in computer.calls()[seen:] if "DM-SECRET" in json.dumps(call_)]
    assert not leaked, f"the direct message reached the provider in a public turn: {leaked}"
