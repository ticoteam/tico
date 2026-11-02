"""A private task run writes to native threads only where its private work stays: its own private task thread.

A company-visible message the run wrote earlier cannot be edited into private words, and a post to the run's own task
thread is admitted (the native message route), while a post to any other thread is refused. Fictional people only:
ben (requester), ana (company), the bot ops. The run's task is private, so its readers are the requester and the owner.
"""

import pytest

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
    return row, claim(api, machine, bot)


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
    _, _, sub_id = hidden_sub_thread(api)
    assert api.get(f"/api/v2/threads/{sub_id}", headers=headers("cara-test")).json()["title"] == ""
    # A new sub-thread's title is neutral for everyone, the parent's author included: the stored title is the rule.
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
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (keeper,)).fetchone()[0] == "Plain topic line"
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
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0] == "The launch is on Friday."
        threads._neutral_titles(c)
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0] == "The launch is on Friday."
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
