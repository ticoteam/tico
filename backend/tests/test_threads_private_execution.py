"""A private task run writes to native threads only where its private work stays: its own private task thread.

A company-visible message the run wrote earlier cannot be edited into private words, and a post to the run's own task
thread is admitted (the native message route), while a post to any other thread is refused. Fictional people only:
ben (requester), ana (company), the bot ops. The run's task is private, so its readers are the requester and the owner.
"""

import pytest

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import threads
from backend.store import H
from backend.tests.test_api import api, assign, claim, headers, ready, runner  # noqa: F401
from backend.tests.test_thread_api import new_thread, say  # noqa: F401


def task(api, *, requester="human:ben", owner="human:ana", private=True, title="Sensitive packet"):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, requester, title, "Confidential attachment and comment.", owner,
                             private=private, lint=False)


def private_run(api, bot="ops"):
    """A private task owned by the bot, and the bot's run on it: (task row, its attempt)."""
    row = task(api, requester="human:ben", owner="bot:" + bot)
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    attempt = claim(api, machine, bot)
    with api.app.state.store.transaction() as c:      # the run carries its private task (its work)
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (attempt["id"], row["id"]))
    return row, attempt


def company_message_by_bot(api, bot="ops", text="Company status"):
    """A company thread, and a message the bot wrote into it before its private run."""
    thread = new_thread(api, title="Company", members=(), body=None)
    with api.app.state.store.transaction() as c:
        posted = threads.post(c, "bot:" + bot, thread["id"], text)
    return thread, posted


@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def test_a_private_run_cannot_edit_its_company_message_into_private_words(api):
    thread = api.post("/api/v2/threads", json={"title": "Company", "audience": "company"},
                      headers=headers("ana-test")).json()
    with api.app.state.store.transaction() as c:
        posted = threads.post(c, "bot:ops", thread["id"], "Company status")
    row, attempt = private_run(api)
    edit = api.patch(f"/api/v2/messages/{posted['id']}", json={"text": "Private plan from the requester"},
                     headers=headers(attempt["token"]))
    assert edit.status_code == 403, edit.text
    with api.app.state.store.transaction() as c:
        assert H.message(c, posted["id"])["body"] == "Company status"


def test_a_private_run_posts_to_its_own_private_task_thread_and_no_other_thread(api):
    row, attempt = private_run(api)
    own = api.post(f"/api/v2/threads/{row['conversation_id']}/messages", json={"text": "Progress"},
                   headers=headers(attempt["token"]))
    assert own.status_code == 200, own.text
    company = api.post("/api/v2/threads", json={"title": "Company", "audience": "company"},
                       headers=headers("ana-test")).json()
    other = api.post(f"/api/v2/threads/{company['id']}/messages", json={"text": "Private progress"},
                     headers=headers(attempt["token"]))
    assert other.status_code in (403, 404), other.text


def test_a_sub_thread_title_does_not_repeat_a_hidden_parent_line(api):
    """A company thread's message that names a private task is restricted: a reader who cannot read that task reads
    its sub-thread's title through every read path, and it must not carry the message's first line."""
    secret = task(api, requester="human:cara", owner="human:cara")
    thread = api.post("/api/v2/threads", json={"title": "Company", "audience": "company"},
                      headers=headers("ana-test")).json()
    with api.app.state.store.transaction() as c:
        parent = threads.post(c, "human:cara", thread["id"], "Secret launch plan for the board",
                              refs={"task": secret["id"]})
    sub = api.post("/api/v2/threads", json={"title": "ignored", "audience": "company",
                                            "parent_message_id": parent["id"]}, headers=headers("cara-test"))
    assert sub.status_code == 200, sub.text
    sub_id = sub.json()["id"]
    seen = api.get(f"/api/v2/threads/{sub_id}", headers=headers("ben-test"))
    assert seen.status_code == 200, seen.text
    leaks = [seen.text]
    listing = api.get("/api/v2/threads", headers=headers("ben-test")).json()
    leaks.append(str(listing))
    leaks.append(str(api.get(f"/api/v2/threads/{thread['id']}/timeline", headers=headers("ben-test")).json()))
    leaks.append(str(api.get("/api/v2/threads/search", params={"q": "secret"}, headers=headers("ben-test")).json()))
    with api.app.state.store.read() as c:
        leaks.append(str(c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0]))
    for text in leaks:
        assert "Secret launch plan" not in text, text[:300]


def hidden_sub_thread(api, text="Secret launch plan for the board"):
    """A company thread, a message from the requester that names a private task (restricted), and its sub-thread."""
    secret = task(api, requester="human:cara", owner="human:cara")
    thread = api.post("/api/v2/threads", json={"title": "Company", "audience": "company"},
                      headers=headers("ana-test")).json()
    with api.app.state.store.transaction() as c:
        parent = threads.post(c, "human:cara", thread["id"], text, refs={"task": secret["id"]})
    sub = api.post("/api/v2/threads", json={"title": "", "audience": "company", "parent_message_id": parent["id"]},
                   headers=headers("cara-test"))
    assert sub.status_code == 200, sub.text
    return thread, parent, sub.json()["id"]


def test_the_reader_of_a_hidden_parent_still_sees_the_sub_thread_title(api):
    """The title is derived on read: the parent's reader sees its first line, and nothing is stored for anyone to find."""
    _, _, sub_id = hidden_sub_thread(api)
    assert api.get(f"/api/v2/threads/{sub_id}", headers=headers("cara-test")).json()["title"] == \
        "Secret launch plan for the board"
    assert api.get(f"/api/v2/threads/{sub_id}", headers=headers("ben-test")).json()["title"] == ""
    with api.app.state.store.read() as c:
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0] == ""


def test_a_title_stored_before_the_fix_is_hidden_from_a_reader_of_a_hidden_parent(api):
    """A sub-thread made before the title rule holds its parent's first line. Reads hide it from the reader who cannot
    read the parent, and the stored copy is cleared by the start-up cleanup."""
    _, _, sub_id = hidden_sub_thread(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET subject=? WHERE id=?", ("Secret launch plan for the board", sub_id))
    seen = api.get(f"/api/v2/threads/{sub_id}", headers=headers("ben-test"))
    assert seen.status_code == 200, seen.text
    assert "Secret launch plan" not in seen.text
    assert api.get(f"/api/v2/threads/{sub_id}", headers=headers("cara-test")).json()["title"] == \
        "Secret launch plan for the board"
    with api.app.state.store.transaction() as c:
        threads._neutral_titles(c)
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0] == ""


def test_the_cleanup_keeps_a_title_a_person_gave_and_a_visible_parent_keeps_its_derived_title(api):
    thread, _, sub_id = hidden_sub_thread(api)
    with api.app.state.store.transaction() as c:
        plain = threads.post(c, "human:cara", thread["id"], "Plain topic line")
        c.execute("UPDATE conversations SET subject=? WHERE id=?", ("Budget notes", sub_id))
        keeper = threads.sub_thread(c, "human:cara", plain["id"])["id"]
        c.execute("UPDATE conversations SET subject=? WHERE id=?", ("Plain topic line", keeper))
        threads._neutral_titles(c)
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0] == "Budget notes"
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (keeper,)).fetchone()[0] == ""
    assert api.get(f"/api/v2/threads/{keeper}", headers=headers("ben-test")).json()["title"] == "Plain topic line"


def runner_reply(c, thread_id, question, text, refs):
    """A runner's final reply: it carries its turn and answers a message, which makes it restricted by flag alone."""
    return threads.post(c, "bot:ops", thread_id, text, kind="answer", refs=refs, in_reply_to=question)


def test_a_sub_thread_on_an_ordinary_bot_reply_keeps_its_title(api):
    thread = api.post("/api/v2/threads", json={"title": "Company", "audience": "company"},
                      headers=headers("ana-test")).json()
    with api.app.state.store.transaction() as c:
        question = threads.post(c, "human:ana", thread["id"], "When is the launch?")
        reply = runner_reply(c, thread["id"], question["id"], "The launch is on Friday.", {"turn_id": "turn_1"})
        assert H.message(c, reply["id"])["restricted"] == 1
        sub_id = threads.sub_thread(c, "human:ana", reply["id"])["id"]
        # Nothing derived is stored: the title is the parent's first line, shown on read to readers of the parent.
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0] == ""
        threads._neutral_titles(c)
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0] == ""
    for reader in ("ana-test", "ben-test"):
        seen = api.get(f"/api/v2/threads/{sub_id}", headers=headers(reader)).json()
        assert seen["title"] == "The launch is on Friday."
        assert seen["breadcrumb"][-1]["title"] == "The launch is on Friday."


def test_a_sub_thread_on_a_private_task_reply_stores_no_title(api):
    secret = task(api, requester="human:cara", owner="human:cara")
    thread = api.post("/api/v2/threads", json={"title": "Company", "audience": "company"},
                      headers=headers("ana-test")).json()
    with api.app.state.store.transaction() as c:
        question = threads.post(c, "human:ana", thread["id"], "What are the terms?")
        named = runner_reply(c, thread["id"], question["id"], "Terms are on the private task.",
                             {"turn_id": "turn_2", "task": secret["id"]})
        sub_id = threads.sub_thread(c, "human:cara", named["id"])["id"]
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0] == ""
    assert api.get(f"/api/v2/threads/{sub_id}", headers=headers("ben-test")).json()["title"] == ""
    assert api.get(f"/api/v2/threads/{sub_id}", headers=headers("cara-test")).json()["title"] == "Terms are on the private task."
    # And in the run's own private task thread.
    row, attempt = private_run(api)
    own = api.post(f"/api/v2/threads/{row['conversation_id']}/messages", json={"text": "Private progress"},
                   headers=headers(attempt["token"]))
    assert own.status_code == 200, own.text
    with api.app.state.store.transaction() as c:
        own_sub = threads.sub_thread(c, "human:ben", own.json()["id"])["id"]
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (own_sub,)).fetchone()[0] == ""


def test_the_title_cleanup_runs_once(api):
    _, parent, sub_id = hidden_sub_thread(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET subject=? WHERE id=?", ("Secret launch plan for the board", sub_id))
        c.execute("DELETE FROM registry_metadata WHERE key=?", (threads.NEUTRAL_TITLES_KEY,))
        threads._neutral_titles_once(c)
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0] == ""
        c.execute("UPDATE conversations SET subject=? WHERE id=?", ("Secret launch plan for the board", sub_id))
        threads._neutral_titles_once(c)
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0] != ""


def test_a_private_run_posts_to_no_other_private_task_thread_and_edits_its_own_message(api):
    row, attempt = private_run(api)
    other = task(api, requester="human:ben", owner="human:ana")
    other_post = api.post(f"/api/v2/threads/{other['conversation_id']}/messages", json={"text": "Not mine"},
                          headers=headers(attempt["token"]))
    assert other_post.status_code in (403, 404), other_post.text
    own = api.post(f"/api/v2/threads/{row['conversation_id']}/messages", json={"text": "Progress"},
                   headers=headers(attempt["token"]))
    assert own.status_code == 200, own.text
    edit = api.patch(f"/api/v2/messages/{own.json()['id']}", json={"text": "Progress, revised"},
                     headers=headers(attempt["token"]))
    assert edit.status_code == 200, edit.text


def company_thread_with_bot_and_ben(api):
    """A company thread Ben made, with the bot ops in it, as a native thread (audience company)."""
    thread = api.post("/api/v2/threads", json={"title": "Company", "audience": "company"},
                      headers=headers("ben-test")).json()
    with api.app.state.store.transaction() as c:
        threads.post(c, "bot:ops", thread["id"], "Hello from ops")
    return thread


def test_a_private_run_cannot_send_legacy_messages_into_a_company_thread(api):
    """`/messages` and `/conversations/{cid}/messages` with a private run's token: the company thread's readers would
    read the run's confidential text, whatever its participants list says."""
    row, attempt = private_run(api)
    thread = company_thread_with_bot_and_ben(api)
    sent = api.post("/api/v2/messages", json={"to": "human:ben", "text": "Confidential from ops",
                                              "conversation_id": thread["id"]}, headers=headers(attempt["token"]))
    assert sent.status_code == 403, sent.text
    reply = api.post(f"/api/v2/conversations/{thread['id']}/messages", json={"text": "Confidential reply"},
                     headers=headers(attempt["token"]))
    assert reply.status_code in (403, 404), reply.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM messages WHERE body LIKE 'Confidential%'").fetchone()[0] == 0


def test_a_private_run_can_still_reply_in_its_own_private_task_thread_by_legacy_send(api):
    row, attempt = private_run(api)
    reply = api.post(f"/api/v2/conversations/{row['conversation_id']}/messages", json={"text": "Progress reply"},
                     headers=headers(attempt["token"]))
    assert reply.status_code == 200, reply.text


def test_a_stored_derived_title_is_cleared_when_its_parent_task_turns_private(api):
    """A company message that cites a public task gets a sub-thread titled with its first line. When the task turns
    private, the stored title must not still carry that line for every reader of the company thread."""
    public = task(api, private=False, requester="human:cara", owner="human:cara", title="Public item")
    thread = api.post("/api/v2/threads", json={"title": "Company", "audience": "company"},
                      headers=headers("cara-test")).json()
    with api.app.state.store.transaction() as c:
        parent = threads.post(c, "human:cara", thread["id"], "Quarterly launch figures for the board",
                              refs={"task": public["id"]})
    sub = api.post("/api/v2/threads", json={"title": "", "audience": "company", "parent_message_id": parent["id"]},
                   headers=headers("cara-test")).json()["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1, visibility='private' WHERE id=?", (public["id"],))
    with api.app.state.store.read() as c:
        stored = c.execute("SELECT subject FROM conversations WHERE id=?", (sub,)).fetchone()[0]
    assert "Quarterly launch" not in stored, stored
    # A search hit's title is its thread's stored title: none of them carries the parent's first line.
    for token in ("ben-test", "cara-test"):
        found = api.get("/api/v2/threads/search", params={"q": "quarterly"}, headers=headers(token)).json()
        assert all("Quarterly launch" not in (hit.get("title") or "") for hit in found["hits"]), token


def test_a_removed_bot_cannot_fetch_a_message_addressed_to_it(api):
    """A bot removed from a members thread keeps its addressed message only while it reads the thread: its
    sender/addressee shortcut must not answer before the thread's readers are checked."""
    from backend.tests.test_api import setup_attempt
    from backend.tests.test_thread_api import new_thread
    _, _, attempt = setup_attempt(api, "ops")
    thread = new_thread(api, members=("bot:ops",))
    sent = api.post(f"/api/v2/threads/{thread['id']}/messages", json={"text": "Note for ops", "to": "bot:ops"},
                    headers=headers("ana-test"))
    assert sent.status_code == 200, sent.text
    message_id = sent.json()["id"]
    assert api.get(f"/api/v2/messages/{message_id}", headers=headers(attempt["token"])).status_code == 200
    removed = api.delete(f"/api/v2/threads/{thread['id']}/members/bot:ops", headers=headers("ana-test"))
    assert removed.status_code == 200, removed.text
    seen = api.get(f"/api/v2/messages/{message_id}", headers=headers(attempt["token"]))
    assert seen.status_code == 404, seen.text
    assert "Note for ops" not in seen.text


def test_a_private_run_cannot_answer_a_company_question_with_private_words(api):
    """Ben asks the bot ops in a company thread; the bot's private run answers through the answer route. The answer is
    a message the company thread's readers read, so the run's confidential words are refused there."""
    thread = company_thread_with_bot_and_ben(api)
    added = api.post(f"/api/v2/threads/{thread['id']}/members", json={"actor": "bot:ops"}, headers=headers("ana-test"))
    assert added.status_code == 200, added.text
    asked = api.post("/api/v2/messages", json={"to": "bot:ops", "text": "Which launch date?", "kind": "ask",
                                               "conversation_id": thread["id"]}, headers=headers("ben-test"))
    assert asked.status_code == 200, asked.text
    row, attempt = private_run(api)
    answer = api.post(f"/api/v2/messages/{asked.json()['id']}/answer", json={"text": "Confidential launch plan"},
                      headers=headers(attempt["token"]))
    assert answer.status_code == 403, answer.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM messages WHERE body LIKE 'Confidential%'").fetchone()[0] == 0


def _dm_question(api, **refs):
    asked = api.post("/api/v2/messages", json={"to": "bot:ops", "text": "Which launch date?", "kind": "ask", **refs},
                     headers=headers("ben-test"))
    assert asked.status_code == 200, asked.text
    return asked.json()["id"]


def _lose_the_task(api, row):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET requester_id='human:ana' WHERE id=?", (row["id"],))


def test_a_private_runs_answer_in_a_dm_keeps_its_task_after_the_asker_loses_it(api):
    """Everyone in Ben's DM reads the run's private task, so the run may answer his untagged question. The answer stays
    tied to that task: once Ben loses the task, he no longer reads what the run told him from it."""
    asked = _dm_question(api)
    row, attempt = private_run(api)
    answer = api.post(f"/api/v2/messages/{asked}/answer", json={"text": "Confidential launch plan"},
                      headers=headers(attempt["token"]))
    assert answer.status_code == 200, answer.text
    aid = answer.json()["id"]
    assert api.get(f"/api/v2/messages/{aid}", headers=headers("ben-test")).status_code == 200
    _lose_the_task(api, row)
    assert api.get(f"/api/v2/messages/{aid}", headers=headers("ben-test")).status_code == 404


def test_a_private_runs_note_on_an_options_question_keeps_its_task_too(api):
    with api.app.state.store.transaction() as c:
        asked = H.say(c, "human:ben", "bot:ops", "Which launch date?", kind="ask",
                      refs={"questions": [{"id": "q", "text": "Which?", "options": [{"label": "May"}, {"label": "June"}]}]})
    row, attempt = private_run(api)
    answer = api.post(f"/api/v2/messages/{asked['id']}/answer", json={"text": "Confidential launch plan"},
                      headers=headers(attempt["token"]))
    assert answer.status_code == 200, answer.text
    aid = answer.json()["id"]
    _lose_the_task(api, row)
    assert api.get(f"/api/v2/messages/{aid}", headers=headers("ben-test")).status_code == 404


def _route_context(api, attempt):
    """A company thread with the bot ops in it and a message the bot wrote there, for the route table below."""
    thread = company_thread_with_bot_and_ben(api)
    added = api.post(f"/api/v2/threads/{thread['id']}/members", json={"actor": "bot:ops"}, headers=headers("ana-test"))
    assert added.status_code == 200, added.text
    with api.app.state.store.transaction() as c:
        posted = threads.post(c, "bot:ops", thread["id"], "Company status")
    return {"thread": thread["id"], "message": posted["id"], "token": attempt["token"], "ben": "human:ben"}


# Every route a private run can use to put message text or a title into a company thread, with the body that would
# do it. A private run is refused on each: its text is one the company thread's readers would read.
ROUTE_WRITES = [
    ("POST", "/api/v2/messages", lambda x: {"to": x["ben"], "text": "Confidential", "conversation_id": x["thread"]}),
    ("POST", "/api/v2/conversations/{thread}/messages", lambda x: {"text": "Confidential"}),
    ("POST", "/api/v2/threads/{thread}/messages", lambda x: {"text": "Confidential"}),
    ("PATCH", "/api/v2/messages/{message}", lambda x: {"text": "Confidential"}),
    ("PATCH", "/api/v2/threads/{thread}", lambda x: {"title": "Confidential"}),
    ("POST", "/api/v2/threads", lambda x: {"title": "Confidential", "audience": "company"}),
    ("POST", "/api/v2/threads/dm", lambda x: {"to": x["ben"], "text": "Confidential"}),
    ("POST", "/api/v2/threads/{thread}/members", lambda x: {"actor": x["ben"]}),
    ("PUT", "/api/v2/threads/{thread}/follow", lambda x: None),
    ("POST", "/api/v2/conversations/{thread}/goal", lambda x: {"action": "set", "objective": "Confidential"}),
    ("POST", "/api/v2/notes", lambda x: {"title": "Confidential", "body": "Confidential"}),
    ("POST", "/api/v2/updates", lambda x: {"title": "Confidential", "body": "Confidential"}),
]


@pytest.mark.parametrize("method,path,body", ROUTE_WRITES, ids=[f"{m} {p}" for m, p, _ in ROUTE_WRITES])
def test_a_private_run_is_refused_on_every_route_that_writes_text_into_a_company_thread(api, method, path, body):
    _, attempt = private_run(api)
    ctx = _route_context(api, attempt)
    url = path.format(thread=ctx["thread"], message=ctx["message"])
    payload = body(ctx)
    kwargs = {"headers": headers(ctx["token"])}
    if payload is not None:
        kwargs["json"] = payload
    response = api.request(method, url, **kwargs)
    assert response.status_code == 403, f"{method} {path}: {response.status_code} {response.text}"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM messages WHERE body LIKE 'Confidential%'").fetchone()[0] == 0
