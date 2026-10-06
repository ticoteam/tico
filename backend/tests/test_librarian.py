"""The Librarian (backend/librarian.py, docs/librarian.md): a person's question goes to a private docs
conversation with the built-in bot, a bot asks with an ask message, and the Librarian acts as itself."""

import pytest

from backend import librarian
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401

RESULTS = [{"type": "internal", "id": "d1", "path": "finance/refunds.md", "title": "Refund policy",
            "excerpt": "Refunds within 14 days", "score": 1.0},
           {"type": "linked", "id": "l1", "title": "Help centre", "url": "https://help.example.com",
            "kind": "website", "description": "", "score": 0.5}]


@pytest.fixture
def desk(api, monkeypatch):
    """A company with an active Librarian on a computer, and the docs search stubbed (stream A's route)."""
    seen = []

    async def search(request, question):
        seen.append(question)
        return RESULTS
    monkeypatch.setattr(librarian, "_search", search)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) "
                  "VALUES('librarian','Librarian','fake','','','','keeper','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES('librarian','{}',NULL,'ana')")
    r = runner(api)
    assign(api, r, "librarian")
    ready(api, r, ["librarian"])
    api.seen, api.runner = seen, r
    return api


def ask(api, question, token="ana-test", expected=200, **more):
    return post(api, "docs/ask", {"question": question, **more}, token=token, expected=expected)


def test_a_question_goes_to_the_persons_private_docs_conversation_and_is_answered_there(desk):
    sent = ask(desk, "How long do refunds take?")
    assert sent["results"] == RESULTS and desk.seen == ["How long do refunds take?"]
    again = ask(desk, "And for gift cards?", conversation_id=sent["conversation_id"])
    assert again["conversation_id"] == sent["conversation_id"]                      # one conversation, follow-ups too
    # The Librarian takes the question as itself: its token is the bot's, never the person's.
    attempt = claim(desk, desk.runner)
    assert get(desk, "me", attempt["token"])["actor"] == "bot:librarian"
    post(desk, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, token=desk.runner["token"])
    post(desk, f"attempts/{attempt['id']}/complete",
         {"outcome": "completed", "text": r"14 days.\n\nRead Hub docs with your coworker. "
          "[Internal doc · Refund policy](doc:d1) `printf '\\n'`", "last_seq": 0},
         token=desk.runner["token"])
    snapshot = get(desk, f"conversations/{sent['conversation_id']}/snapshot")
    reply = next(m for m in snapshot["messages"] if m["from_actor"] == "bot:librarian")
    assert reply["in_reply_to"] in (sent["message_id"], again["message_id"])
    assert "Refund policy" in reply["body"]
    assert "14 days.\n\nRead Tico docs with your coworker." in reply["body"]
    assert "`printf '\\n'`" in reply["body"]


def test_nobody_else_reads_or_posts_in_a_persons_docs_conversation_the_owner_included(desk):
    ana, ben = ask(desk, "What is our refund policy?"), ask(desk, "What is Ben's private question?", token="ben-test")
    assert ana["conversation_id"] != ben["conversation_id"]
    get(desk, f"conversations/{ben['conversation_id']}/messages", "ana-test", expected=403)      # the owner too
    get(desk, f"conversations/{ana['conversation_id']}/messages", "ben-test", expected=403)
    get(desk, f"conversations/{ana['conversation_id']}/snapshot", "cara-test", expected=403)
    ask(desk, "Sneaking in", token="ben-test", expected=403, conversation_id=ana["conversation_id"])
    post(desk, "messages", {"to": "librarian", "text": "Injected", "conversation_id": ana["conversation_id"]},
         token="ben-test", expected=403)
    # A conversation that is not a docs conversation is not a place to ask.
    other = post(desk, "chat/ops", {"text": "hi"})["conversation_id"]
    ask(desk, "Wrong room", expected=422, conversation_id=other)


def test_docs_history_is_private_and_only_docs_rooms_can_be_reopened(desk):
    mine = ask(desk, "My private docs question")
    theirs = ask(desk, "Another private docs question", token="ben-test")
    ask(desk, "Another docs chat", token="ben-test", new_conversation=True)
    assert [row["id"] for row in get(desk, "librarian/conversations")["conversations"]] == [mine["conversation_id"]]
    other_history = get(desk, "librarian/conversations", token="ben-test")["conversations"]
    assert mine["conversation_id"] not in {row["id"] for row in other_history}
    post(desk, f"librarian/conversations/{theirs['conversation_id']}/reopen", {}, expected=403)
    post(desk, f"librarian/conversations/{mine['conversation_id']}/reopen", {}, token="ben-test", expected=403)
    post(desk, "librarian/conversations/missing/reopen", {}, expected=404)
    with desk.app.state.store.transaction() as c:
        other = H.open_conversation(c, "human:ana", ["human:ana", "bot:coo"], kind="chat",
                                    scope="personal", owner_actor="human:ana", room_key="coo")
        direct = H.open_conversation(c, "human:ana", ["human:ana", "bot:librarian"], kind="chat",
                                     room_key="docs", owner_actor="human:ana")
    for cid in (other["id"], direct["id"]):
        post(desk, f"librarian/conversations/{cid}/reopen", {}, expected=422)
    assert len(get(desk, "librarian/conversations")["conversations"]) == 1
    attempt = claim(desk, desk.runner)
    get(desk, "librarian/conversations", token=attempt["token"], expected=403)
    post(desk, f"librarian/conversations/{mine['conversation_id']}/reopen", {}, token=attempt["token"], expected=403)


def test_a_bot_asks_the_librarian_with_an_ask_message_and_the_final_text_is_the_answer(desk):
    ops = runner(desk, label="Ops Mac")
    assign(desk, ops, "ops")
    ready(desk, ops, ["ops"])
    post(desk, "chat/ops", {"text": "Please look at the refund docs"})
    turn = claim(desk, ops)
    question = post(desk, "messages", {"to": "librarian", "text": "How long do refunds take?", "kind": "ask",
                                       "wait_s": 60}, token=turn["token"])
    attempt = claim(desk, desk.runner)
    assert attempt["bot"] == "librarian"
    post(desk, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, token=desk.runner["token"])
    post(desk, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Not in the docs.", "last_seq": 0},
         token=desk.runner["token"])
    with desk.app.state.store.read() as c:
        assert H.answers_to(c, [question["id"]])[question["id"]]["body"] == "Not in the docs."
    ask(desk, "A bot uses hub docs ask, not this route", token=turn["token"], expected=403)



def test_mcp_docs_ask_status_collects_the_same_private_answer(desk):
    from backend.mcp import InProcessApi
    from backend.tests.test_mcp import call
    assert InProcessApi.docs_wait_max == 20
    err, pending = call(desk, "hub_doc_ask", {"question": "Refund window?", "wait_s": 0})
    assert not err and pending["timeout"] and pending["message_id"] and pending["conversation_id"]
    ids = {k: pending[k] for k in ("message_id", "conversation_id")}
    err, waiting = call(desk, "hub_doc_ask_status", ids)
    assert not err and waiting == pending
    err, refused = call(desk, "hub_doc_ask_status", ids, token="ben-test")
    assert err and refused["error"] == "forbidden"
    attempt = claim(desk, desk.runner)
    post(desk, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, token=desk.runner["token"])
    post(desk, f"attempts/{attempt['id']}/complete",
         {"outcome": "completed", "text": "14 days. [Internal doc · Refund policy](doc:d1)", "last_seq": 0},
         token=desk.runner["token"])
    err, final = call(desk, "hub_doc_ask_status", ids)
    assert not err and final["covered"] and final["answer"].startswith("14 days")
    snapshot = get(desk, f"conversations/{pending['conversation_id']}/snapshot")
    assert len([m for m in snapshot["messages"] if m["from_actor"] == "human:ana"]) == 1


