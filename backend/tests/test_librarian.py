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
        c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created_at) "
                  "VALUES('librarian','Librarian','fake','','','','keeper','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config,team,operator) VALUES('librarian','{}',NULL,'ana')")
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




def test_a_research_request_is_one_task_to_the_librarian_for_that_page_and_section(desk):
    doc = post(desk, "docs", {"title": "Competitors", "body": "# Competitors\n\n## Pricing\n\nTBD.", "path": "sales/competitors.md"})["doc"]
    asked = post(desk, "librarian/research", {"target": doc["id"], "section": "Pricing",
                                              "instructions": "Add Acme's and Northwind's public list prices."})
    assert asked["kind"] == "doc" and asked["target"] == doc["id"]
    task = get(desk, f"tasks/{asked['task_id']}")
    task = task.get("task", task)
    assert task["owner"] == "bot:librarian" and task["title"] == "Research: Competitors · Pricing"
    assert "Add Acme's and Northwind's public list prices." in task["body"]
    assert "Target: " + doc["id"] in task["body"] and "playbooks/research-a-section.md" in task["body"]
    again = post(desk, "librarian/research", {"target": "sales/competitors.md", "section": "Pricing", "instructions": "And Initech."})
    assert again["task_id"] != asked["task_id"]                                     # asking again is a second task
    page = post(desk, "librarian/research", {"target": "market/overview", "instructions": "Add the EU market."})
    assert page["kind"] == "market page"
    post(desk, "librarian/research", {"target": "market/overview", "instructions": "x"}, token="ben-test", expected=403)
    assert "research" in str(task["labels"])                                        # the playbook checks it
    # Nothing to add to, a locked doc, and a bot asking are refused before anything is filed.
    post(desk, "librarian/research", {"target": "market/weekly-delta", "instructions": "x"}, expected=404)
    post(desk, "librarian/research", {"target": "nope", "instructions": "x"}, expected=404)
    with desk.app.state.store.transaction() as c:
        c.execute("UPDATE docs SET locked=1 WHERE id=?", (doc["id"],))
    post(desk, "librarian/research", {"target": doc["id"], "instructions": "x"}, expected=409)
    post(desk, "librarian/research", {"target": "market/overview", "instructions": "x"}, token=desk.runner["token"], expected=403)


def test_docs_first_visit_takes_the_sources_and_files_one_setup_task(desk):
    # A first visit asks while Docs holds nothing a person wrote or linked; the Librarian's own map does not count.
    post(desk, "docs", {"title": "Docs index", "body": "x", "path": "_librarian/index.md"})
    assert get(desk, "librarian/setup")["docs"]["show"] is True
    # Skipping is each person's own: Ben's skip leaves it showing for Ana.
    assert post(desk, "librarian/setup/docs", {"skip": True}, token="ben-test")["docs"]["show"] is False
    assert get(desk, "librarian/setup")["docs"]["show"] is True
    post(desk, "librarian/setup/docs", {}, expected=422)                                # nothing to set up from
    post(desk, "librarian/setup/docs", {"sources": ["not a link"]}, expected=422)
    assert get(desk, "doc-sources")["sources"] == []                                   # a refusal adds nothing
    done = post(desk, "librarian/setup/docs", {
        "sources": ["https://github.com/acme/handbook", "https://help.acme.example", "https://help.acme.example"],
        "note": "Policies are in Google Drive."})
    assert [s["kind"] for s in done["sources"]] == ["github", "website"]
    assert {s["url"] for s in get(desk, "doc-sources")["sources"]} == {"https://github.com/acme/handbook",
                                                                       "https://help.acme.example"}
    task = get(desk, f"tasks/{done['task_id']}")
    task = task.get("task", task)
    assert task["owner"] == "bot:librarian" and task["title"] == "Set up Docs"
    assert "https://help.acme.example" in task["body"] and "Policies are in Google Drive." in task["body"]
    assert "playbooks/docs-setup.md" in task["body"]
    # Docs is past its first visit for everyone now, and Ben can still set up more from the menu.
    after = get(desk, "librarian/setup")["docs"]
    assert after["show"] is False and after["task"]["id"] == done["task_id"]
    assert post(desk, "librarian/setup/docs", {"sources": ["https://help.acme.example"]}, token="ben-test")["task_id"]
    # Bots and a Librarian that is off are refused: the Librarian's own turn credential, not a runner's.
    ask(desk, "Where do refunds live?")
    bot = claim(desk, desk.runner)["token"]
    get(desk, "librarian/setup", token=bot, expected=403)
    post(desk, "librarian/setup/docs", {"note": "x"}, token=bot, expected=403)
    with desk.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE slug='librarian'")
    post(desk, "librarian/setup/docs", {"note": "x"}, expected=409)


def test_market_questions_are_kept_as_answered_so_the_owner_can_skip_and_resume(desk):
    first = get(desk, "librarian/setup")["market"]
    assert first["can_answer"] is True and first["step"] == 0 and first["task"] is None
    answers = {"sell": "https://northwind.example", "buyers": "Property managers in Ohio"}
    post(desk, "librarian/setup/market", {"answers": answers, "step": 2, "action": "skip"})
    later = get(desk, "librarian/setup")["market"]                                      # another browser, a day later
    assert later["skipped"] is True and later["step"] == 2
    assert later["answers"] == {**answers, "competitors": "", "watch": ""}
    # The first answer is the one that must be there; the Librarian finds the rest.
    post(desk, "librarian/setup/market", {"answers": {"buyers": "x"}, "step": 3, "action": "finish"}, expected=422)
    done = post(desk, "librarian/setup/market", {"answers": {**answers, "competitors": "CleanCo\nSparkle",
                                                             "watch": "r/PropertyManagement"},
                                                 "step": 3, "action": "finish"})
    task = get(desk, f"tasks/{done['task']['id']}")
    task = task.get("task", task)
    assert task["owner"] == "bot:librarian" and task["title"] == "Set up the market map"
    for text in ("## Who we compete with\n\nCleanCo\nSparkle", "## What to watch\n\nr/PropertyManagement",
                 "What Market tracks", "playbooks/market-setup.md"):
        assert text in task["body"]
    assert done["skipped"] is False
    # Only the owner answers: the market is the owner's and the Librarian's to change.
    assert get(desk, "librarian/setup", token="ben-test")["market"] == {
        "can_answer": False, "answers": {"sell": "", "buyers": "", "competitors": "", "watch": ""}, "step": 0,
        "skipped": False, "task": None}
    post(desk, "librarian/setup/market", {"answers": answers, "action": "save"}, token="ben-test", expected=403)


def test_a_market_setup_task_is_only_ever_the_owners_own_librarian_task(desk):
    # The owner can write any preferences row, so a stored task id is checked: it must be the Librarian's task and
    # the owner's own, or the setup shows no task.
    elsewhere = post(desk, "tasks", {"owner": "ops", "title": "Not the Librarian's", "body": "x"})
    elsewhere = elsewhere.get("task", elsewhere)
    bens = post(desk, "librarian/setup/docs", {"note": "On the wiki"}, token="ben-test")["task_id"]
    for forged in (elsewhere["id"], bens):
        post(desk, "preferences/librarian.market_setup", {"value": {"task_id": forged}})
        assert get(desk, "librarian/setup")["market"]["task"] is None
    done = post(desk, "librarian/setup/market", {"answers": {"sell": "https://northwind.example"}, "step": 3,
                                                 "action": "finish"})
    assert get(desk, "librarian/setup")["market"]["task"]["id"] == done["task"]["id"]
