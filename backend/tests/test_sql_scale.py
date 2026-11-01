"""POST /api/v2/sql stays inside a bot's 5 s limit on a production-sized history.

The gates that hide private work must cost what is hidden, not what has ever been said: a LIMIT 1 must not
authorize every room first, and a count over all messages must not run Python for each one.
"""
from datetime import timedelta
import json
import random
import time
import uuid

import pytest

from backend.store import H
from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401

TASKS, ROOMS, MESSAGES = 6000, 6000, 300_000
BOTS = ("coo", "ops", "cpo", "product-design", "finance", "inbox", "doc-updater")
PEOPLE = ("human:ana", "human:ben", "human:cara")


def queries():
    day = H.shift(H.now(), days=-1)
    return [("SELECT id FROM turns LIMIT 1", []),
            ("SELECT count(*) FROM turns WHERE started_at>=?", [day]),
            ("SELECT id FROM attempts LIMIT 1", []),
            ("SELECT count(*) FROM attempts WHERE created_at>=?", [day]),
            ("SELECT count(*) FROM messages", [])]


def populate(store, attempt, seed=7, tasks_n=TASKS, rooms_n=ROOMS, messages_n=MESSAGES):
    """About the shape of a busy team's database: task rooms and chat rooms, bot runs that answer messages and
    cite their task or run, replies, a few private tasks of the restricted inbox bot, and their run provenance."""
    rng = random.Random(seed)
    now = H.parse_ts(H.now())
    stamp = lambda minutes: (now - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"  # noqa: E731
    ids = lambda n: [str(uuid.UUID(int=rng.getrandbits(128), version=4)) for _ in range(n)]  # noqa: E731
    with store.transaction() as c:
        runner_id = c.execute("SELECT runner_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
        triggers = c.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name IN "
                             "('tasks','conversations','messages','jobs','attempts','turns','events','attempt_inputs')"
                             ).fetchall()
        for name, _ in triggers:
            c.execute(f'DROP TRIGGER "{name}"')
        task_ids, room_ids = ids(tasks_n), ids(rooms_n)
        tasks, private = [], set()
        for i, tid in enumerate(task_ids):
            owner = "bot:" + BOTS[i % len(BOTS)]
            hidden = i % 33 == 0
            if hidden:
                private.add(tid)
            tasks.append((tid, f"Task {i}", "Body", "human:ana" if hidden else rng.choice(PEOPLE),
                          "bot:inbox" if hidden else owner, rng.choice(("open", "done", "closed")),
                          room_ids[i] if i < tasks_n * 2 // 3 else None, stamp(rng.randrange(180 * 1440)), 1 if hidden else 0,
                          100_000 + i))
        c.executemany("INSERT INTO tasks(id,title,body,requester,owner,status,conversation_id,created_at,private,number) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)", tasks)
        rooms = []
        for i, cid in enumerate(room_ids):
            if i < tasks_n * 2 // 3:
                rooms.append((cid, "task", task_ids[i], json.dumps([tasks[i][3], tasks[i][4]]), "direct", None, None))
            else:
                person, bot = rng.choice(PEOPLE), rng.choice(BOTS)
                rooms.append((cid, "chat", None, json.dumps([person, "bot:" + bot]), "personal", person,
                              f"{bot}:{i}"))
        c.executemany("INSERT INTO conversations(id,kind,task_id,participants,scope,owner_actor,room_key,created_at) "
                      "VALUES(?,?,?,?,?,?,?,'2026-01-01T00:00:00.000000Z')", rooms)
        messages, jobs, attempts, turns, inputs, events = [], [], [], [], [], []
        last, root, waiting = {}, {}, {}
        message_ids, attempt_ids = ids(messages_n), ids(messages_n // 5)
        runs = iter(attempt_ids)
        for i, mid in enumerate(message_ids):
            r = rng.randrange(rooms_n)
            cid, task = room_ids[r], task_ids[r] if r < tasks_n * 2 // 3 else None
            bot = "bot:inbox" if task in private else "bot:" + BOTS[r % len(BOTS)] if r < tasks_n * 2 // 3 else json.loads(rooms[r][3])[1]
            created = stamp(messages_n - i)        # one message a minute, the newest now
            shape, sender, to, reply, refs = i % 10, rng.choice(PEOPLE), bot, None, None
            if shape in (1, 2):
                refs = "{}"
            elif shape == 3:                     # a keeper wake
                sender, refs = "keeper", json.dumps({"task": task or rng.choice(task_ids), "wake": "stalled"})
            elif shape in (4, 5):
                refs = json.dumps({"task": task or rng.choice(task_ids)})
            elif shape == 6:                     # an answer; now and then deep in a thread
                refs, reply = "{}", (last if i % 100 == 6 else root).get(cid)
            elif cid in waiting:                 # a bot's reply, as execution.stamp_reply records it
                aid, asked, taken = waiting.pop(cid)
                sender, to, reply = bot, rng.choice(PEOPLE), asked
                refs = json.dumps({"turn_id": aid, **({"task": task} if task else {}),
                                   "run": {"job_id": aid, "attempt_id": aid}, "answers": [asked, *taken]})
            messages.append((mid, cid, sender, to, "chat", "Message body", refs, reply, created))
            last[cid] = mid
            aid = next(runs, None) if H.is_bot(to) and shape in (0, 1, 3, 4, 5) and rng.random() < 0.35 else None
            if aid:
                jobs.append((aid, mid, H.actor_id(to), "done", created, aid))
                attempts.append((aid, aid, to, runner_id, created))
                turns.append((aid, H.actor_id(to), created, mid, task))
                taken = [root[cid]] if i % 50 == 0 and root.get(cid) not in (None, mid) else []
                inputs.extend((aid, m) for m in taken)
                if task and i % 40 == 0:
                    events.append((aid, json.dumps({"bot": H.actor_id(to), "tasks": [task]})))
                waiting[cid] = (aid, mid, taken)
            if not reply:
                root[cid] = mid
        c.executemany("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs,in_reply_to,"
                      "created_at) VALUES(?,?,?,?,?,?,?,?,?)", messages)
        c.executemany("INSERT INTO jobs(id,message_id,bot,state,created_at,attempt_id) VALUES(?,?,?,?,?,?)", jobs)
        c.executemany("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created_at,"
                      "started_at,finished_at) VALUES(?1,?2,substr(?3,5),?4,0,'synthetic-'||?1,'done',?5,?5,?5,?5)", attempts)
        c.executemany("INSERT INTO turns(id,bot,started_at,finished_at,message_id,task_id,trigger_kind,outcome) "
                      "VALUES(?,?,?,?3,?,?,'message','acted')", turns)
        c.executemany("INSERT OR IGNORE INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", inputs)
        c.executemany("INSERT INTO events(id,ts,actor,action,target,detail) VALUES"
                      "(lower(hex(randomblob(16))),'2026-01-01T00:00:00.000000Z','keeper','task.next-run.carried',?,?)",
                      events)
        for _, sql in triggers:
            c.execute(sql)
    return {"private": private, "tasks": task_ids, "rooms": room_ids, "messages": len(messages),
            "attempts": len(attempts)}


@pytest.mark.slow
def test_bot_and_member_reads_stay_under_five_seconds_on_production_sized_history(api):
    post(api, "chat/ops", {"text": "Summarize the week."})
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    attempt = claim(api, machine)
    world = populate(api.app.state.store, attempt)
    assert world["messages"] >= MESSAGES and world["attempts"] > 10_000
    for name, token in (("bot", attempt["token"]), ("member", "ben-test")):
        for sql, params in queries():
            started = time.monotonic()
            r = api.post("/api/v2/sql", json={"sql": sql, "params": params}, headers=headers(token))
            seconds = time.monotonic() - started
            assert r.status_code == 200, (name, sql, r.text)
            assert seconds < 5, (name, sql, seconds)


def test_settled_gates_read_exactly_the_rows_the_privacy_functions_allow(api):
    """The fast SQL gates only settle rows early: every caller reads the same messages, turns and attempts as when the
    per-row privacy functions decide each one, denied private tasks, unreadable rooms and run provenance included."""
    from backend import sql as S
    from backend.auth import Identity
    from backend.privacy_index import ReadIndex
    post(api, "chat/ops", {"text": "Summarize the week."})
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    attempt = claim(api, machine)
    world = populate(api.app.state.store, attempt, tasks_n=300, rooms_n=300, messages_n=3000)
    store, auth = api.app.state.store, api.app.state.auth
    denied, public = sorted(world["private"])[0], next(t for t in world["tasks"] if t not in world["private"])
    with store.transaction() as c:
        room = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (public,)).fetchone()[0]
        hidden_room = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (denied,)).fetchone()[0]
        secret = H.say(c, "human:ana", "bot:inbox", "Synthetic private note", conversation_id=hidden_room)
        # Rows the fast gate leaves to the privacy function, readable or not.
        for refs, reply in (({"task": public, "note": "say \"hi\""}, None),
                            ({"task": public, "comment": "see #/task/" + denied}, None),
                            ({"task": public, "answers": [secret["id"]]}, None),
                            ({"task": public}, secret["id"]),
                            ({"task": public, "turn_id": world_attempt(c, denied)}, None)):
            H.say(c, "bot:ops", "human:ben", "Synthetic edge", conversation_id=room, refs=refs, in_reply_to=reply)
        # Bot answers to a person's reply, the live rows the SQL gates leave to the provenance walk.
        for n in range(300):
            ask = H.say(c, "human:ben", "human:ana", "Synthetic question", conversation_id=room,
                        refs={"task": public})
            again = H.say(c, "human:ana", "human:ben", "Synthetic follow-up", conversation_id=room,
                          refs={"task": public}, in_reply_to=ask["id"])
            H.say(c, "bot:ops", "human:ana", "Synthetic answer", conversation_id=room,
                  refs={"task": public, "answers": [again["id"]]}, in_reply_to=again["id"])
        # Rows no stored walk decides: references of the wrong shape and a reply chain deeper than a walk follows.
        odd = H.say(c, "bot:ops", "human:ben", "Synthetic odd refs", conversation_id=room)["id"]
        c.execute("UPDATE messages SET refs='[1]' WHERE id=?", (odd,))
        deep = None
        for _ in range(105):
            deep = H.say(c, "human:ben", "bot:ops", "Synthetic chain", conversation_id=room, in_reply_to=deep)["id"]

    def settled(who):
        with store.read() as c:
            conn = S.connect(store.settings.db_path, c, auth, who)
            try:
                return {table: {r[0] for r in conn.execute(f"SELECT id FROM {table}")}
                        for table in ("messages", "turns", "attempts")}
            finally:
                conn.close()

    def reference(who):
        """The views' row rules with every message and run decided by the privacy functions alone."""
        with store.read() as c:
            index = ReadIndex(c, who, rooms=True)
            conn = S.connect(store.settings.db_path, c, auth, who)
            try:
                conn.set_authorizer(None)
                rooms = {r[0] for r in conn.execute(f'SELECT id FROM "{conn.private_prefix}readable_conversations"')}
                tasks = {r[0] for r in conn.execute(f'SELECT id FROM "{conn.private_prefix}visible_tasks"')}
            finally:
                conn.close()
            hidden = auth.unreadable_bots(c, who)
            messages = {r["id"] for r in c.execute("SELECT id,conversation_id,refs,in_reply_to FROM messages "
                                                   "WHERE deleted_at IS NULL")
                        if r["conversation_id"] in rooms and index.message(r["id"], r["conversation_id"],
                                                                           r["refs"], r["in_reply_to"])}
            jobs = {r[0] for r in c.execute("SELECT id,bot,message_id FROM jobs") if r[1] not in hidden and r[2] in messages}
            attempts = {r[0] for r in c.execute("SELECT id,job_id FROM attempts") if r[1] in jobs and index.attempt(r[0])}
            turns = {r[0] for r in c.execute("SELECT id,bot,message_id,task_id FROM turns")
                     if r[1] not in hidden and (r[2] is None or r[2] in messages)
                     and (r[3] is None or r[3] in tasks) and index.attempt(r[0])}
            return {"messages": messages, "turns": turns, "attempts": attempts}

    callers = [Identity("bot:ops", "bot", attempt_id=attempt["id"]), Identity("human:ben", "human"),
               Identity("human:ana", "owner")]

    def same(when):
        for who in callers:
            assert settled(who) == reference(who), (when, who.actor)
    same("before any row is settled")
    with store.read() as c:
        everything = c.execute("SELECT count(*) FROM messages").fetchone()[0]
    bot = settled(callers[0])["messages"]
    assert 0 < len(bot) < everything and secret["id"] not in bot

    # Settled rows are read from the stored index (backend/read_deps.py); every caller still reads the same rows.
    from backend import read_deps
    with store.read() as c:
        read_deps.sweep(c)
        indexed = c.execute("SELECT count(*) FROM read_index").fetchone()[0]
        assert indexed > everything // 2
        assert c.execute("SELECT count(*) FROM read_dirty").fetchone()[0] == 0
        assert not c.execute("SELECT 1 FROM read_index WHERE key IN (?,?)", ("m:" + odd, "m:" + deep)).fetchall()
    same("settled")
    # What keeps a count fast: settled rows never reach the per-row provenance walk, only the few left undecided.
    walked = []
    original = ReadIndex.message
    ReadIndex.message = lambda self, *args: walked.append(args[0]) or original(self, *args)
    try:
        with store.read() as c:
            conn = S.connect(store.settings.db_path, c, auth, callers[0])
            try:
                assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == len(bot)
            finally:
                conn.close()
    finally:
        ReadIndex.message = original
    assert len(walked) < 100, len(walked)

    # Each change that moves a row's provenance drops the rows it touched back to the gate at once, with no sweep
    # between the change and the read. A task turning private moves no provenance: readers deny it from the index.
    with store.transaction() as c:
        cited = c.execute("SELECT id,refs FROM messages WHERE refs LIKE '%\"task\"%' AND conversation_id=? "
                          "LIMIT 1", (room,)).fetchone()
        task = json.loads(cited["refs"])["task"]
    changes = [
        ("a cited task turns private", False,
         lambda c: c.execute("UPDATE tasks SET private=1, requester='human:ana', owner='bot:inbox' WHERE id=?",
                             (task,))),
        ("a message now cites a denied task", True,
         lambda c: c.execute("UPDATE messages SET refs=? WHERE id=?",
                             (json.dumps({"task": denied}), next(iter(sorted(bot)))))),
        ("a replied-to message is deleted", False,
         lambda c: c.execute("UPDATE messages SET deleted_at=? WHERE id=(SELECT in_reply_to FROM messages "
                             "WHERE in_reply_to IS NOT NULL AND conversation_id<>? LIMIT 1)", (H.now(), hidden_room))),
        ("a room moves to a denied task", True,
         lambda c: c.execute("UPDATE conversations SET task_id=? WHERE id=?", (denied, room))),
        ("a run carries a denied task", True, lambda c: world_attempt(c, sorted(world["private"])[1])),
    ]
    for when, moves, change in changes:
        with store.transaction() as c:
            change(c)
            assert (c.execute("SELECT count(*) FROM read_dirty").fetchone()[0] > 0) == moves, when
        same(when)
        with store.read() as c:
            read_deps.sweep(c)
        same(when + ", settled again")



def world_attempt(c, denied):
    """A finished run that carried a denied private task."""
    aid = c.execute("SELECT id FROM attempts ORDER BY created_at LIMIT 1").fetchone()[0]
    c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (aid, denied))
    return aid
