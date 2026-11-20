"""A run on a private task writes wherever a run on a public task may: company threads, its own messages there, and the
tasks it files. Privacy is on the reading side: the private task and its thread stay its readers' alone, and what the
run files is private unless it asks for company-visible. Fictional people only: ben (requester), ana and cara
(company), the bot ops. The run's task is private, so its readers are the requester and the owner.
"""

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import threads
from backend.store import H
from backend.tests.helpers.api import headers, runner
from backend.tests.helpers.thread_api import new_thread, say  # noqa: F401
from backend.tests.helpers.threads_private_execution import private_run, task, private_tasks_have_an_unrelated_manager


def company_message_by_bot(api, bot="ops", text="Company status"):
    """A company thread, and a message the bot wrote into it before its private run."""
    thread = new_thread(api, title="Company", members=(), body=None)
    with api.app.state.store.transaction() as c:
        posted = threads.post(c, "bot:" + bot, thread["id"], text)
    return thread, posted


def test_a_private_run_edits_its_company_message_and_the_company_reads_the_edit(api):
    thread = api.post(
        "/api/v2/threads", json={"title": "Company", "audience": "company"}, headers=headers("ana-test")
    ).json()
    with api.app.state.store.transaction() as c:
        posted = threads.post(c, "bot:ops", thread["id"], "Company status")
    row, attempt = private_run(api)
    edit = api.patch(
        f"/api/v2/messages/{posted['id']}", json={"text": "Company status, revised"}, headers=headers(attempt["token"])
    )
    assert edit.status_code == 200, edit.text
    seen = api.get(f"/api/v2/messages/{posted['id']}", headers=headers("cara-test"))
    assert seen.status_code == 200 and "Company status, revised" in seen.text, seen.text


def test_a_sub_thread_title_does_not_repeat_a_hidden_parent_line(api):
    """A company thread's message that names a private task is restricted: a reader who cannot read that task reads
    its sub-thread's title through every read path, and it must not carry the message's first line."""
    secret = task(api, requester="human:cara", owner="human:cara")
    thread = api.post(
        "/api/v2/threads", json={"title": "Company", "audience": "company"}, headers=headers("ana-test")
    ).json()
    with api.app.state.store.transaction() as c:
        parent = threads.post(
            c, "human:cara", thread["id"], "Secret launch plan for the board", refs={"task": secret["id"]}
        )
    sub = api.post(
        "/api/v2/threads",
        json={"title": "ignored", "audience": "company", "parent_message_id": parent["id"]},
        headers=headers("cara-test"),
    )
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
    thread = api.post(
        "/api/v2/threads", json={"title": "Company", "audience": "company"}, headers=headers("ana-test")
    ).json()
    with api.app.state.store.transaction() as c:
        parent = threads.post(c, "human:cara", thread["id"], text, refs={"task": secret["id"]})
    sub = api.post(
        "/api/v2/threads",
        json={"title": "", "audience": "company", "parent_message_id": parent["id"]},
        headers=headers("cara-test"),
    )
    assert sub.status_code == 200, sub.text
    return thread, parent, sub.json()["id"]


def test_the_reader_of_a_hidden_parent_still_sees_the_sub_thread_title(api):
    """The title is derived on read: the parent's reader sees its first line, and nothing is stored for anyone to find."""
    _, _, sub_id = hidden_sub_thread(api)
    assert (
        api.get(f"/api/v2/threads/{sub_id}", headers=headers("cara-test")).json()["title"]
        == "Secret launch plan for the board"
    )
    assert api.get(f"/api/v2/threads/{sub_id}", headers=headers("ben-test")).json()["title"] == ""
    with api.app.state.store.read() as c:
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0] == ""


def test_a_title_stored_before_the_fix_is_hidden_from_a_reader_of_a_hidden_parent(api):
    """A sub-thread titled with its parent's first line (as one made before the title rule is): reads hide it from the
    reader who cannot read the parent."""
    _, _, sub_id = hidden_sub_thread(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET subject=? WHERE id=?", ("Secret launch plan for the board", sub_id))
    seen = api.get(f"/api/v2/threads/{sub_id}", headers=headers("ben-test"))
    assert seen.status_code == 200, seen.text
    assert "Secret launch plan" not in seen.text
    assert (
        api.get(f"/api/v2/threads/{sub_id}", headers=headers("cara-test")).json()["title"]
        == "Secret launch plan for the board"
    )


def runner_reply(c, thread_id, question, text, refs):
    """A runner's final reply: it carries its turn and answers a message, which makes it restricted by flag alone."""
    return threads.post(c, "bot:ops", thread_id, text, refs=refs, in_reply_to=question)


def test_a_sub_thread_on_an_ordinary_bot_reply_keeps_its_title(api):
    thread = api.post(
        "/api/v2/threads", json={"title": "Company", "audience": "company"}, headers=headers("ana-test")
    ).json()
    with api.app.state.store.transaction() as c:
        question = threads.post(c, "human:ana", thread["id"], "When is the launch?")
        reply = runner_reply(c, thread["id"], question["id"], "The launch is on Friday.", {"turn_id": "turn_1"})
        assert H.message(c, reply["id"])["restricted"] == 1
        sub_id = threads.sub_thread(c, "human:ana", reply["id"])["id"]
        # Nothing derived is stored: the title is the parent's first line, shown on read to readers of the parent.
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0] == ""
    for reader in ("ana-test", "ben-test"):
        seen = api.get(f"/api/v2/threads/{sub_id}", headers=headers(reader)).json()
        assert seen["title"] == "The launch is on Friday."
        assert seen["breadcrumb"][-1]["title"] == "The launch is on Friday."


def test_a_sub_thread_on_a_private_task_reply_stores_no_title(api):
    secret = task(api, requester="human:cara", owner="human:cara")
    thread = api.post(
        "/api/v2/threads", json={"title": "Company", "audience": "company"}, headers=headers("ana-test")
    ).json()
    with api.app.state.store.transaction() as c:
        question = threads.post(c, "human:ana", thread["id"], "What are the terms?")
        named = runner_reply(
            c,
            thread["id"],
            question["id"],
            "Terms are on the private task.",
            {"turn_id": "turn_2", "task": secret["id"]},
        )
        sub_id = threads.sub_thread(c, "human:cara", named["id"])["id"]
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (sub_id,)).fetchone()[0] == ""
    assert api.get(f"/api/v2/threads/{sub_id}", headers=headers("ben-test")).json()["title"] == ""
    assert (
        api.get(f"/api/v2/threads/{sub_id}", headers=headers("cara-test")).json()["title"]
        == "Terms are on the private task."
    )
    # And in the run's own private task thread.
    row, attempt = private_run(api)
    own = api.post(
        f"/api/v2/threads/{row['conversation_id']}/messages",
        json={"text": "Private progress"},
        headers=headers(attempt["token"]),
    )
    assert own.status_code == 200, own.text
    with api.app.state.store.transaction() as c:
        own_sub = threads.sub_thread(c, "human:ben", own.json()["id"])["id"]
        assert c.execute("SELECT subject FROM conversations WHERE id=?", (own_sub,)).fetchone()[0] == ""


def test_a_private_run_posts_to_no_other_private_task_thread_and_edits_its_own_message(api):
    row, attempt = private_run(api)
    other = task(api, requester="human:ben", owner="human:ana")
    other_post = api.post(
        f"/api/v2/threads/{other['conversation_id']}/messages",
        json={"text": "Not mine"},
        headers=headers(attempt["token"]),
    )
    assert other_post.status_code in (403, 404), other_post.text
    own = api.post(
        f"/api/v2/threads/{row['conversation_id']}/messages",
        json={"text": "Progress"},
        headers=headers(attempt["token"]),
    )
    assert own.status_code == 200, own.text
    edit = api.patch(
        f"/api/v2/messages/{own.json()['id']}", json={"text": "Progress, revised"}, headers=headers(attempt["token"])
    )
    assert edit.status_code == 200, edit.text


def company_thread_with_bot_and_ben(api):
    """A company thread Ben made, with the bot ops in it, as a native thread (audience company)."""
    thread = api.post(
        "/api/v2/threads", json={"title": "Company", "audience": "company"}, headers=headers("ben-test")
    ).json()
    with api.app.state.store.transaction() as c:
        threads.post(c, "bot:ops", thread["id"], "Hello from ops")
    return thread


def test_a_private_run_can_still_reply_in_its_own_private_task_thread_by_legacy_send(api):
    row, attempt = private_run(api)
    reply = api.post(
        f"/api/v2/conversations/{row['conversation_id']}/messages",
        json={"text": "Progress reply"},
        headers=headers(attempt["token"]),
    )
    assert reply.status_code == 200, reply.text


def test_a_stored_derived_title_is_cleared_when_its_parent_task_turns_private(api):
    """A company message that cites a public task gets a sub-thread titled with its first line. When the task turns
    private, the stored title must not still carry that line for every reader of the company thread."""
    public = task(api, private=False, requester="human:cara", owner="human:cara", title="Public item")
    thread = api.post(
        "/api/v2/threads", json={"title": "Company", "audience": "company"}, headers=headers("cara-test")
    ).json()
    with api.app.state.store.transaction() as c:
        parent = threads.post(
            c, "human:cara", thread["id"], "Quarterly launch figures for the board", refs={"task": public["id"]}
        )
    sub = api.post(
        "/api/v2/threads",
        json={"title": "", "audience": "company", "parent_message_id": parent["id"]},
        headers=headers("cara-test"),
    ).json()["id"]
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
    from backend.tests.helpers.api import setup_attempt
    from backend.tests.helpers.thread_api import new_thread

    _, _, attempt = setup_attempt(api, "ops")
    thread = new_thread(api, members=("bot:ops",))
    sent = api.post(
        f"/api/v2/threads/{thread['id']}/messages",
        json={"text": "Note for ops", "to": "bot:ops"},
        headers=headers("ana-test"),
    )
    assert sent.status_code == 200, sent.text
    message_id = sent.json()["id"]
    assert api.get(f"/api/v2/messages/{message_id}", headers=headers(attempt["token"])).status_code == 200
    removed = api.delete(f"/api/v2/threads/{thread['id']}/members/bot:ops", headers=headers("ana-test"))
    assert removed.status_code == 200, removed.text
    seen = api.get(f"/api/v2/messages/{message_id}", headers=headers(attempt["token"]))
    assert seen.status_code == 404, seen.text
    assert "Note for ops" not in seen.text


def _route_context(api, attempt):
    """A company thread with the bot ops in it and a message the bot wrote there, for the route table below."""
    thread = company_thread_with_bot_and_ben(api)
    added = api.post(f"/api/v2/threads/{thread['id']}/members", json={"actor": "bot:ops"}, headers=headers("ana-test"))
    assert added.status_code == 200, added.text
    with api.app.state.store.transaction() as c:
        posted = threads.post(c, "bot:ops", thread["id"], "Company status")
    return {"thread": thread["id"], "message": posted["id"], "token": attempt["token"], "ben": "human:ben"}


# Every route a run can use to put message text into a thread, with the body that would do it. A
# run on a private task uses each as any run does.
ROUTE_WRITES = [
    ("POST", "/api/v2/messages", lambda x: {"to": x["ben"], "text": "Shared", "conversation_id": x["thread"]}),
    ("POST", "/api/v2/conversations/{thread}/messages", lambda x: {"text": "Shared"}),
    ("POST", "/api/v2/threads/{thread}/messages", lambda x: {"text": "Shared"}),
    ("PATCH", "/api/v2/messages/{message}", lambda x: {"text": "Shared"}),
    ("POST", "/api/v2/threads", lambda x: {"title": "Shared", "audience": "members", "members": [x["ben"]]}),
    ("POST", "/api/v2/threads/dm", lambda x: {"members": [x["ben"]], "body": "Shared"}),
    ("POST", "/api/v2/threads/{thread}/members", lambda x: {"actor": "bot:finance"}),
    ("PUT", "/api/v2/threads/{thread}/follow", lambda x: {}),
    ("POST", "/api/v2/notes", lambda x: {"to": "bot:finance", "text": "Shared"}),
    ("POST", "/api/v2/updates", lambda x: {"kind": "daily", "body": "- Shared"}),
]


def test_a_private_run_writes_on_every_route_into_a_company_thread(api):
    row, attempt = private_run(api)
    ctx = _route_context(api, attempt)
    for method, path, body in ROUTE_WRITES:
        url = path.format(thread=ctx["thread"], message=ctx["message"])
        payload = body(ctx)
        kwargs = {"headers": headers(ctx["token"])}
        if payload is not None:
            kwargs["json"] = payload
        response = api.request(method, url, **kwargs)
        assert response.status_code == 200, f"{method} {path}: {response.status_code} {response.text}"
    # The company thread's readers read what the run posted there.
    timeline = api.get(f"/api/v2/threads/{ctx['thread']}/timeline", headers=headers("cara-test"))
    assert timeline.status_code == 200 and "Shared" in timeline.text, timeline.text


def test_a_private_run_files_private_subtasks_for_their_assignee_or_public_tasks_when_asked(api):
    row, attempt = private_run(api)
    ask = {"title": "Check the vendor terms", "body": "Read the terms and say what they cost."}
    # Left out, `private` is the private task's: the assignee reads it, the rest of the company does not.
    child = api.post(
        "/api/v2/tasks", json={"owner": "ana", **ask, "parent": row["id"]}, headers=headers(attempt["token"])
    )
    assert child.status_code == 200, child.text
    child = child.json()["task"]
    assert child["private"] is True
    assert api.get(f"/api/v2/tasks/{child['id']}", headers=headers("ana-test")).status_code == 200
    assert api.get(f"/api/v2/tasks/{child['id']}", headers=headers("cara-test")).status_code == 404
    # `private: false` files it company-visible, under the private task too.
    for parent in ({}, {"parent": row["id"]}):
        public = api.post(
            "/api/v2/tasks",
            json={"owner": "ana", **ask, "title": "Publish the price list", "private": False, **parent},
            headers=headers(attempt["token"]),
        )
        assert public.status_code == 200, public.text
        made = public.json()["task"]
        assert made["private"] is False
        assert api.get(f"/api/v2/tasks/{made['id']}", headers=headers("cara-test")).status_code == 200


def test_a_non_reader_still_sees_neither_the_private_task_nor_its_thread(api):
    row, attempt = private_run(api)
    posted = api.post(
        f"/api/v2/threads/{row['conversation_id']}/messages",
        json={"text": "Private progress on the packet"},
        headers=headers(attempt["token"]),
    )
    assert posted.status_code == 200, posted.text
    # Ben (its requester) reads both; Cara reads neither, by any read path.
    assert api.get(f"/api/v2/tasks/{row['id']}", headers=headers("ben-test")).status_code == 200
    assert api.get(f"/api/v2/threads/{row['conversation_id']}", headers=headers("ben-test")).status_code == 200
    assert api.get(f"/api/v2/tasks/{row['id']}", headers=headers("cara-test")).status_code == 404
    assert api.get(f"/api/v2/threads/{row['conversation_id']}", headers=headers("cara-test")).status_code in (403, 404)
    assert api.get(f"/api/v2/messages/{posted.json()['id']}", headers=headers("cara-test")).status_code == 404
    found = api.get("/api/v2/threads/search", params={"q": "packet"}, headers=headers("cara-test"))
    assert "Private progress" not in found.text and "Sensitive packet" not in found.text
    listed = api.get("/api/v2/tasks", headers=headers("cara-test"))
    assert row["id"] not in listed.text
