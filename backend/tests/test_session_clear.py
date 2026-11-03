"""A person's Clear on a direct chat (POST /api/employees/{bot}/session/clear): the conversation's session epoch moves,
every later turn carries it as `session_epoch`, and its history starts after the clear. With native threads each chat
is its own thread tree with its own provider session (clients/sessions.py root keys), so the bot's epoch
(`bot_session_epoch`, for the one thread `session: bot` keeps) stays where it was and no other chat is touched."""
from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401  (api is a fixture)


def clear(api, bot="ops", token="ana-test"):
    return api.post(f"/api/employees/{bot}/session/clear", json={}, headers=headers(token))


def finish(api, machine, turn):
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-" + turn["id"][:8]}, machine["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, machine["token"])


def test_a_clear_moves_the_epoch_every_later_turn_carries_and_starts_history_after_it(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    conversation = post(api, "chat/ops", {"text": "Remember the Acme code word"})["conversation_id"]
    first = claim(api, machine)
    assert first["session_epoch"] == first["bot_session_epoch"] == 0, "a conversation never cleared is at epoch 0"
    finish(api, machine, first)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM bot_sessions WHERE bot='ops'").fetchone()

    post(api, "chat/ops", {"text": "What was it?"})
    second = claim(api, machine)
    assert second["session_epoch"] == 0, "no clear: the same epoch, so the runner resumes its thread"
    finish(api, machine, second)

    cleared = clear(api)
    assert cleared.status_code == 200 and cleared.json() == {"cleared": True, "conversation_id": conversation}
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM bot_sessions WHERE bot='ops'").fetchone()
        assert c.execute("SELECT epoch FROM session_epochs WHERE conversation_id=?", (conversation,)).fetchone()[0] == 1
    post(api, "chat/ops", {"text": "Start over: a new question"})
    third = claim(api, machine)
    assert third["conversation"]["id"] == conversation and third["session_epoch"] == 1
    assert third["bot_session_epoch"] == 0, "the chat has its own session: the bot's shared thread is not moved"
    assert [m["body"] for m in third["history"]] == ["Start over: a new question"]
    finish(api, machine, third)

    assert clear(api).status_code == 200
    post(api, "chat/ops", {"text": "And again"})
    assert claim(api, machine)["session_epoch"] == 2, "each clear moves it again"


def test_a_clear_waits_for_the_running_turn_and_touches_only_the_persons_own_chat(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    ana = post(api, "chat/ops", {"text": "A long job"})["conversation_id"]
    turn = claim(api, machine)
    busy = clear(api)
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "busy", busy.text
    finish(api, machine, turn)

    ben = post(api, "chat/ops", {"text": "Ben's question"}, token="ben-test")["conversation_id"]
    assert ben != ana
    finish(api, machine, claim(api, machine))
    assert clear(api, token="ben-test").json()["conversation_id"] == ben
    post(api, "chat/ops", {"text": "Ana again"})
    again = claim(api, machine)
    assert again["conversation"]["id"] == ana and again["session_epoch"] == 0, "Ben's clear leaves Ana's history alone"
    assert "A long job" in [m["body"] for m in again["history"]]
    # Ana's chat has its own session, apart from Ben's: his clear does not start hers anew.
    assert again["bot_session_epoch"] == 0


def test_a_clear_with_no_chat_yet_is_a_no_op(api):
    response = clear(api, bot="finance")
    assert response.status_code == 200 and response.json() == {"cleared": True}
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM session_epochs").fetchone()


def test_a_runner_without_threads_gets_each_chat_its_own_session_not_the_bots_shared_thread(api):
    """A runner that lacks threads_v1 keys a `session: bot` turn on the bot's one provider thread, which every chat
    shares. Its chat turns are sent as `session: task` instead, so each conversation keeps its own session."""
    old = runner(api)
    assign(api, old, "ops")
    ready(api, old, ["ops"])
    post(api, "chat/ops", {"text": "Old runner question"})
    turn = claim(api, old)
    assert turn["config"]["session"] == "task", "an older runner: one session per chat"
    finish(api, old, turn)
