"""A message in a task's own thread whose whole provenance is that task is written plain (restricted 0), so the rail,
the badge and Catch up count it in SQL: the thread is read only by those who read its task (readable_sql's fence).
Anything more (another task in its refs, parent, inputs or run, files, a page) keeps it restricted, an edit can only
raise the flag, and for every reader a plain message answers the per-message check as its thread does. Fictional
people only: ana (manager), ben and cara (humans), the bot ops."""

import pytest

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
            attempt = run_on(api, task["id"])["id"]
        with api.app.state.store.transaction() as c:
            if n:  # the bot's run on this task: a finished copy of the first, as each task's own run is
                aid = H.new_id()
                c.execute("CREATE TEMP TABLE IF NOT EXISTS bench_run AS SELECT * FROM attempts WHERE 0")
                c.execute("DELETE FROM bench_run")
                c.execute("INSERT INTO bench_run SELECT * FROM attempts WHERE id=?", (attempt,))
                c.execute(
                    "UPDATE bench_run SET id=?, task_id=?, token_hash=NULL, state='completed', job_id=NULL",
                    (aid, task["id"]),
                )
                c.execute("INSERT INTO attempts SELECT * FROM bench_run")
                attempt = aid
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


def test_a_run_that_read_a_private_meetings_room_writes_restricted(api):
    """M1: meeting provenance (a run's input in a room bound to a private meeting) names no task, so only the meeting
    check refuses it; a post already plain becomes restricted when the run takes that input in."""
    task, thread = task_with_thread(api)
    meeting = api.post(
        "/api/v2/live-meetings", json={"title": "Plan", "visibility": "private"}, headers=headers("ana-test")
    )
    assert meeting.status_code == 200, meeting.text
    attempt = run_on(api, task["id"])
    wake = post_as(api, thread["id"], "human:ana", "Please look")
    before = bot_reply(api, thread["id"], attempt, wake["id"])
    assert flag(api, before["id"]) == 0
    with api.app.state.store.transaction() as c:
        room = H.open_conversation(c, "human:ana", ["bot:ops"], kind="chat", subject="Call notes")
        c.execute(
            "UPDATE conversations SET room_key=? WHERE id=?", ("live-meeting:" + meeting.json()["id"], room["id"])
        )
        said = H.say(c, "human:ana", "bot:ops", "From the call", conversation_id=room["id"])
        c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (attempt["id"], said["id"]))
    assert flag(api, before["id"]) == 1
    assert flag(api, bot_reply(api, thread["id"], attempt, wake["id"])["id"]) == 1


def test_a_run_whose_input_names_a_page_writes_restricted(api, monkeypatch):
    """M2: the run's input messages and their reply chains are read for pages too, not only the reply's own refs."""
    task, thread = task_with_thread(api)
    from backend import files

    real = files.page_references
    monkeypatch.setattr(
        files, "page_references", lambda value, c=None, **kw: {"f"} if "file-x" in str(value) else real(value, c, **kw)
    )
    attempt = run_on(api, task["id"])
    wake = post_as(api, thread["id"], "human:ana", "Please look")
    assert flag(api, bot_reply(api, thread["id"], attempt, wake["id"])["id"]) == 0
    page = post_as(api, thread["id"], "human:ana", "Review", refs={"review": "file-x"})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (attempt["id"], page["id"]))
    assert flag(api, bot_reply(api, thread["id"], attempt, wake["id"])["id"]) == 1


def test_a_replicated_edit_raises_a_plain_post_whose_run_has_since_read_another_task(api):
    """M4: a Slack edit (apply_revision) re-runs the rule as a Tico edit does."""
    task, thread = task_with_thread(api)
    attempt = run_on(api, task["id"])
    wake = post_as(api, thread["id"], "human:ana", "Please look")
    origin = {"origin_id": "0190-acme@slack:acme", "revision": 0, "install": "slack:acme"}
    reply = post_as(
        api, thread["id"], "bot:ops", "Done.", refs={"turn_id": attempt["id"]}, in_reply_to=wake["id"], origin=origin
    )
    assert flag(api, reply["id"]) == 0
    secret = private_ref(api)
    leak = post_as(api, thread["id"], "human:cara", "Private plan", refs={"task": secret})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (attempt["id"], leak["id"]))
        assert threads.apply_revision(c, {"origin_id": reply["origin_id"], "revision": 1}, body="Done, with the plan.")
    assert flag(api, reply["id"]) == 1


def test_refs_changed_after_the_post_are_checked_again_before_the_write_commits(api):
    """Files attached, a goal or a page added after the insert: the stored row is judged again, and only raised."""
    task, thread = task_with_thread(api)
    secret = private_ref(api)
    attached = post_as(api, thread["id"], "human:ana", "See attached")
    named = post_as(api, thread["id"], "human:ana", "See")
    kept = post_as(api, thread["id"], "human:ana", "Fine")
    assert {flag(api, m["id"]) for m in (attached, named, kept)} == {0}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET refs=? WHERE id=?", (H._dump({"attachments": [{"id": "v-1"}]}), attached["id"]))
        c.execute("UPDATE messages SET refs=? WHERE id=?", (H._dump({"task": secret}), named["id"]))
        c.execute("UPDATE messages SET refs=? WHERE id=?", (H._dump({"task": task["id"]}), kept["id"]))
    assert (flag(api, attached["id"]), flag(api, named["id"]), flag(api, kept["id"])) == (1, 1, 0)
    with api.app.state.store.read_transaction() as c:
        assert not c.execute("SELECT 1 FROM messages_plain_dirty").fetchone()


def test_the_backfill_marks_plain_only_what_a_new_post_would_be_and_runs_once(api):
    task, thread = task_with_thread(api)
    secret = private_ref(api)
    attempt = run_on(api, task["id"])
    wake = post_as(api, thread["id"], "human:ana", "Please look", refs={"task": task["id"], "trigger": "x"})
    reply = bot_reply(api, thread["id"], attempt, wake["id"], answers=[wake["id"]])
    other = post_as(api, thread["id"], "human:ana", "See", refs={"task": secret})
    old = post_as(api, thread["id"], "human:ana", "Last month")
    edited = post_as(api, thread["id"], "human:ana", "Edited")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET restricted=1 WHERE conversation_id=?", (thread["id"],))
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), days=-40), old["id"]))
        c.execute("UPDATE messages SET revision=1 WHERE id=?", (edited["id"],))
        c.execute("DELETE FROM registry_metadata WHERE key=?", (threads.PLAIN_KEY,))  # the app's start ran it
    looked = []
    for _ in range(10):
        with api.app.state.store.transaction() as c:
            looked.append(threads.plain_backfill_batch(c, batch=2))
        if not looked[-1]:
            break
    assert looked[-1] == 0 and sum(looked) >= 3
    flags = {m["id"]: flag(api, m["id"]) for m in (wake, reply, other, old, edited)}
    assert flags == {wake["id"]: 0, reply["id"]: 0, other["id"]: 1, old["id"]: 1, edited["id"]: 1}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET restricted=1 WHERE id=?", (wake["id"],))
        assert threads.plain_backfill_batch(c) == 0  # done: a restart reads nothing
        c.execute("DELETE FROM registry_metadata WHERE key=?", (threads.PLAIN_KEY,))
        assert threads.plain_backfill_batch(c) > 0  # deleting its key runs it again
    assert flag(api, wake["id"]) == 0


def production_shaped(api, unread, threads_n=10):
    """Task threads ben is in, each with keeper wakes (task, trigger, wake, news; one in 25 with a reconcile list naming
    another task) and the bot's run replies to them (turn_id, answers), `unread` posts in all."""
    attempt, made, n = None, [], 0
    other = None
    for t in range(threads_n):
        task, thread = task_with_thread(api, title=f"Task {t}")
        other = other or task["id"]
        if attempt is None:
            attempt = run_on(api, task["id"])["id"]
        with api.app.state.store.transaction() as c:
            if n:  # the bot's run on this task: a finished copy of the first, as each task's own run is
                aid = H.new_id()
                c.execute("CREATE TEMP TABLE IF NOT EXISTS bench_run AS SELECT * FROM attempts WHERE 0")
                c.execute("DELETE FROM bench_run")
                c.execute("INSERT INTO bench_run SELECT * FROM attempts WHERE id=?", (attempt,))
                c.execute(
                    "UPDATE bench_run SET id=?, task_id=?, token_hash=NULL, state='completed', job_id=NULL",
                    (aid, task["id"]),
                )
                c.execute("INSERT INTO attempts SELECT * FROM bench_run")
                attempt = aid
            # A person's comment, as each woken thread starts: what puts the thread in ben's Catch up.
            made.append(threads.post(c, "human:cara", thread["id"], f"Comment {t}", refs={"task": task["id"]})["id"])
            for i in range((unread - threads_n) // threads_n // 2):
                refs = {"task": task["id"], "trigger": "comment", "wake": f"w-{n}", "news": [f"n-{n}"]}
                if n % 25 == 24:
                    refs["reconcile"] = {"active": [{"id": other}]}
                wake = threads.post(c, H.KEEPER, thread["id"], f"Wake {n}", refs=refs)
                reply = threads.post(
                    c,
                    "bot:ops",
                    thread["id"],
                    f"Reply {n}",
                    refs={"turn_id": attempt, "answers": [wake["id"]]},
                    in_reply_to=wake["id"],
                )
                made += [wake["id"], reply["id"]]
                n += 1
    return made


def statements_of(api, path):
    from backend.tests.helpers.statement_scope import measuring, recorder

    store, seen = api.app.state.store, []
    take, give = store._take, store._give

    def tracing():
        c = take()
        c.set_trace_callback(recorder(seen))
        return c

    def untracing(c):
        c.set_trace_callback(None)
        give(c)

    store._take, store._give = tracing, untracing
    try:
        with measuring(seen):
            listed(api, path, "ben-test")
    finally:
        store._take, store._give = take, give
    return len(seen)


@pytest.mark.parametrize("unread,threads_n", [(50, 5), (200, 20), (1000, 100)])
def test_the_bench_rail_and_catch_up_on_production_shaped_posts(api, capsys, monkeypatch, unread, threads_n):
    """Criterion 7's bench, on synthetic posts shaped like production's: the rail and Catch up for a reader with
    `unread` new posts across `threads_n` task threads, then the same posts all restricted (what the rule before this
    wrote). Plain posts need no per-message check and fewer statements; it prints statements, checks and the median of
    five loads in ms for both. It asserts no timing."""
    import time

    made = production_shaped(api, unread, threads_n=threads_n)
    calls, real = [], task_privacy.message_readable
    monkeypatch.setattr(
        task_privacy, "message_readable", lambda c, p, m, **kw: calls.append(m["id"]) or real(c, p, m, **kw)
    )

    def load():
        listed(api, "rail", "ben-test")  # warm: the first load after a write reads the runs' provenance
        calls.clear()
        statements = statements_of(api, "rail") + statements_of(api, "catch-up")
        checks, ms = len(calls), []
        for _ in range(5):
            started = time.perf_counter()
            listed(api, "rail", "ben-test")
            listed(api, "catch-up", "ben-test")
            ms.append(round((time.perf_counter() - started) * 1000))
        return statements, checks, sorted(ms)[2]

    plain = load()
    with api.app.state.store.transaction() as c:
        c.execute(f"UPDATE messages SET restricted=1 WHERE id IN ({','.join('?' * len(made))})", made)
    restricted = load()
    with capsys.disabled():
        print(
            f"\n{unread} unread in {threads_n} threads (statements, checks, ms): plain {plain}, restricted {restricted}"
        )
    assert plain[1] == 0 and restricted[1] > 0
    assert plain[0] < restricted[0]
