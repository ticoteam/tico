"""A message in a task's own thread whose whole provenance is that task is written plain (restricted 0), so the rail,
the badge and Catch up count it in SQL: the thread is read only by those who read its task (readable_sql's fence).
Anything more (another task in its refs, parent, inputs or run, files, a page) keeps it restricted, an edit can only
raise the flag, and for every reader a plain message answers the per-message check as its thread does. Fictional
people only: ana (manager), ben and cara (humans), the bot ops."""

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import task_privacy, threads
from backend.store import H
from backend.tests.helpers.api import assign, claim, headers, ready, runner, unrelated_task_managers  # noqa: F401


def post_as(api, thread_id, actor, text, **kw):
    with api.app.state.store.transaction() as c:
        return threads.post(c, actor, thread_id, text, **kw)


def listed(api, path, token):
    response = api.get(f"/api/v2/threads/{path}", headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def private_ref(api):
    """A private task of cara's that ben may not read."""
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
        return H.task_create(c, "human:cara", "Private plan", "Details.", "human:cara", private=True, lint=False)["id"]


def flag(api, message_id):
    with api.app.state.store.read_transaction() as c:
        return c.execute("SELECT restricted FROM messages WHERE id=?", (message_id,)).fetchone()[0]


def task_with_thread(api, *, private=False, requester="human:ana", owner="bot:ops", title="Ship pricing"):
    """A task and its own thread (the members thread tied to it, ben and cara in it)."""
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
        task = H.task_create(c, requester, title, "Details.", owner, private=private, lint=False)
    response = api.post(
        "/api/v2/threads",
        json={"title": title, "audience": "members", "members": ["human:ben", "human:cara"], "task_id": task["id"]},
        headers=headers("ana-test"),
    )
    assert response.status_code == 200, response.text
    return task, response.json()


def run_on(api, task_id, bot="ops", job=False):
    """The bot's run, working `task_id` (attempts.task_id). Its job is the assignment of a task the bot owns: `task_id`
    itself, or with `job`, another task made for it (so the run's provenance holds that one too)."""
    if job:
        with api.app.state.store.transaction() as c:
            H.task_create(c, "human:ana", "Wake ops", "", "bot:" + bot, lint=False)
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    attempt = claim(api, machine, bot)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET task_id=? WHERE id=?", (task_id, attempt["id"]))
    return attempt


def bot_reply(api, thread_id, attempt, parent_id, **refs):
    return post_as(api, thread_id, "bot:ops", "Done.", refs={"turn_id": attempt["id"], **refs}, in_reply_to=parent_id)


def test_a_post_and_a_run_reply_in_a_task_thread_that_name_only_its_task_are_plain(api):
    task, thread = task_with_thread(api)
    wake = post_as(api, thread["id"], "human:ana", "Please look", refs={"task": task["id"]})
    assert flag(api, wake["id"]) == 0
    attempt = run_on(api, task["id"])
    reply = bot_reply(api, thread["id"], attempt, wake["id"], task=task["id"], answers=[wake["id"]])
    assert flag(api, reply["id"]) == 0


def test_anything_beyond_the_thread_task_keeps_a_message_restricted(api):
    task, thread = task_with_thread(api)
    secret = private_ref(api)
    # refs naming another task
    assert flag(api, post_as(api, thread["id"], "human:ana", "See", refs={"task": secret})["id"]) == 1
    assert flag(api, post_as(api, thread["id"], "human:ana", "See", refs={"reason": f"task:{secret}"})["id"]) == 1
    # a parent naming another task
    _, other = task_with_thread(api, title="Other", owner="human:ben")
    outside = post_as(api, other["id"], "human:ana", "Elsewhere", refs={"task": secret})
    assert flag(api, post_as(api, thread["id"], "human:ana", "Re", in_reply_to=outside["id"])["id"]) == 1
    # a run on another task
    attempt = run_on(api, secret, job=True)
    wake = post_as(api, thread["id"], "human:ana", "Please look")
    assert flag(api, bot_reply(api, thread["id"], attempt, wake["id"])["id"]) == 1


def test_a_run_woken_with_a_reconcile_list_naming_another_task_writes_restricted(api):
    task, thread = task_with_thread(api)
    secret = private_ref(api)
    attempt = run_on(api, task["id"])
    wake = post_as(api, thread["id"], "human:ana", "Please look")
    assert flag(api, bot_reply(api, thread["id"], attempt, wake["id"])["id"]) == 0
    reconcile = post_as(api, thread["id"], "human:ana", "Wake", refs={"reconcile": {"active": [{"id": secret}]}})
    assert flag(api, reconcile["id"]) == 1
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (attempt["id"], reconcile["id"]))
    assert flag(api, bot_reply(api, thread["id"], attempt, wake["id"])["id"]) == 1


def test_files_and_carriers_keep_a_task_thread_message_restricted(api):
    task, thread = task_with_thread(api)
    with api.app.state.store.read_transaction() as c:
        conv = threads.thread_row(c, thread["id"])
        assert threads.restricted_for(conv, {}, None, attachments=True, c=c) == 1
        assert threads.restricted_for(conv, {"carries": "m-1"}, None, c=c) == 1
        assert threads.restricted_for(conv, {}, None, c=c) == 0
        assert threads.restricted_for(conv, {}, None) == 1  # no connection: nothing to authorize with


def test_a_page_named_by_a_task_thread_message_keeps_it_restricted(api, monkeypatch):
    task, thread = task_with_thread(api)
    from backend import files

    real = files.page_references
    monkeypatch.setattr(
        files, "page_references", lambda value, c=None, **kw: {"f"} if "file-x" in str(value) else real(value, c, **kw)
    )
    assert flag(api, post_as(api, thread["id"], "human:ana", "Review", refs={"target": "file-x"})["id"]) == 1
    parent = post_as(api, thread["id"], "human:ana", "Review", refs={"review": "file-x"})
    assert flag(api, post_as(api, thread["id"], "human:ana", "Re", in_reply_to=parent["id"])["id"]) == 1


def test_a_reader_who_cannot_read_the_task_is_shown_nothing_from_its_thread(api):
    task, thread = task_with_thread(api, private=True, requester="human:ana", owner="human:ana")
    plain = post_as(api, thread["id"], "human:ana", "Private numbers", refs={"task": task["id"]})
    assert flag(api, plain["id"]) == 0
    rail = listed(api, "rail", "ben-test")  # ben is a thread member but may not read the task
    assert thread["id"] not in [t["id"] for t in rail["pinned"] + rail["threads"]]
    catch_up = listed(api, "catch-up", "ben-test")
    assert thread["id"] not in [item["thread"]["id"] for item in catch_up["items"]]
    assert api.get(f"/api/v2/threads/{thread['id']}", headers=headers("ben-test")).status_code in (403, 404)


def test_a_run_that_read_a_private_task_writes_restricted_posts_its_readers_cannot_see(api):
    task, thread = task_with_thread(api)
    secret = private_ref(api)  # cara's; ben may not read it
    attempt = run_on(api, task["id"])
    leak = post_as(api, thread["id"], "human:cara", "Private plan", refs={"task": secret})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (attempt["id"], leak["id"]))
    wake = post_as(api, thread["id"], "human:ana", "Please look")
    reply = bot_reply(api, thread["id"], attempt, wake["id"])
    assert flag(api, reply["id"]) == 1
    with api.app.state.store.read_transaction() as c:
        row = H.message(c, reply["id"])
        assert not task_privacy.message_readable(c, "human:ben", row)
        assert task_privacy.message_readable(c, "human:cara", row)


def test_an_edit_raises_a_plain_post_whose_run_has_since_read_another_task(api):
    task, thread = task_with_thread(api)
    attempt = run_on(api, task["id"])
    wake = post_as(api, thread["id"], "human:ana", "Please look")
    reply = bot_reply(api, thread["id"], attempt, wake["id"])
    assert flag(api, reply["id"]) == 0
    secret = private_ref(api)
    leak = post_as(api, thread["id"], "human:cara", "Private plan", refs={"task": secret})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (attempt["id"], leak["id"]))
        threads.edit_message(c, "bot:ops", reply["id"], "Done, with the plan.")
    assert flag(api, reply["id"]) == 1


def test_an_edit_never_lowers_the_flag(api):
    task, thread = task_with_thread(api)
    secret = private_ref(api)
    posted = post_as(api, thread["id"], "human:ana", "See", refs={"task": secret})
    with api.app.state.store.transaction() as c:
        threads.edit_message(c, "human:ana", posted["id"], "See the plan")
    assert flag(api, posted["id"]) == 1


def test_every_plain_message_answers_the_per_message_check_as_its_thread_does(api):
    """Over the messages this module's shapes write, for each reader: a plain message is readable exactly when its
    task is (what the thread's fence gives)."""
    task, thread = task_with_thread(api)
    private, secret_thread = task_with_thread(api, private=True, requester="human:ana", owner="human:ana", title="P")
    secret = private_ref(api)
    attempt = run_on(api, task["id"])
    written = []
    for target, tid in ((thread, task["id"]), (secret_thread, private["id"])):
        wake = post_as(api, target["id"], "human:ana", "Look", refs={"task": tid})
        written += [
            wake,
            post_as(api, target["id"], "human:cara", "Plan", refs={"task": secret}),
            post_as(api, target["id"], "human:ana", "Re", in_reply_to=wake["id"]),
            post_as(api, target["id"], "human:ana", "Note", refs={"via": "assistant", "trigger": "x", "wake": "y"}),
        ]
    written.append(bot_reply(api, thread["id"], attempt, written[0]["id"], task=task["id"]))
    plain = 0
    with api.app.state.store.read_transaction() as c:
        for message in written:
            row = H.message(c, message["id"])
            conv = threads.thread_row(c, row["conversation_id"])
            if flag(api, row["id"]) != 0:
                continue
            plain += 1
            for reader in ("human:ana", "human:ben", "human:cara"):
                expected = task_privacy.readable(c, reader, {conv["task_id"]})
                assert task_privacy.message_readable(c, reader, row) == expected, (reader, row["body"])
    assert plain >= 5


def test_the_rail_and_catch_up_never_ask_the_per_message_check_for_plain_task_thread_posts(api, monkeypatch):
    """The point of the flag: a reader with unread posts across many task threads costs no per-message check."""
    attempt, posted = None, []
    for n in range(12):
        task, thread = task_with_thread(api, title=f"Task {n}")
        if attempt is None:
            attempt = run_on(api, task["id"])
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE attempts SET task_id=? WHERE id=?", (task["id"], attempt["id"]))
            c.execute("DELETE FROM attempt_inputs WHERE attempt_id=?", (attempt["id"],))
        wake = post_as(api, thread["id"], "human:ana", f"Please look {n}", refs={"task": task["id"]})
        posted.append(wake["id"])
    assert all(flag(api, mid) == 0 for mid in posted)
    asked, real = [], task_privacy.message_readable
    monkeypatch.setattr(
        task_privacy, "message_readable", lambda c, p, m, **kw: asked.append(m["id"]) or real(c, p, m, **kw)
    )
    listed(api, "rail", "ben-test")
    listed(api, "catch-up", "ben-test")
    assert not set(asked) & set(posted)
