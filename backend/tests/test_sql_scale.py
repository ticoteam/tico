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
            ("SELECT count(*) FROM turns WHERE started>=?", [day]),
            ("SELECT id FROM attempts LIMIT 1", []),
            ("SELECT count(*) FROM attempts WHERE created>=?", [day]),
            ("SELECT count(*) FROM messages", [])]


def populate(store, attempt, seed=7):
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
        task_ids, room_ids = ids(TASKS), ids(ROOMS)
        tasks, private = [], set()
        for i, tid in enumerate(task_ids):
            owner = "bot:" + BOTS[i % len(BOTS)]
            hidden = i % 33 == 0
            if hidden:
                private.add(tid)
            tasks.append((tid, f"Task {i}", "Body", "human:ana" if hidden else rng.choice(PEOPLE),
                          "bot:inbox" if hidden else owner, rng.choice(("open", "done", "closed")),
                          room_ids[i] if i < 4000 else None, stamp(rng.randrange(180 * 1440)), 1 if hidden else 0,
                          100_000 + i))
        c.executemany("INSERT INTO tasks(id,title,body,requester,owner,status,conversation_id,created,private,number) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)", tasks)
        rooms = []
        for i, cid in enumerate(room_ids):
            if i < 4000:
                rooms.append((cid, "task", task_ids[i], json.dumps([tasks[i][3], tasks[i][4]]), "direct", None, None))
            else:
                person, bot = rng.choice(PEOPLE), rng.choice(BOTS)
                rooms.append((cid, "chat", None, json.dumps([person, "bot:" + bot]), "personal", person,
                              f"{bot}:{i}"))
        c.executemany("INSERT INTO conversations(id,kind,task_id,participants_json,scope,owner_actor,room_key,created) "
                      "VALUES(?,?,?,?,?,?,?,'2026-01-01T00:00:00.000000Z')", rooms)
        messages, jobs, attempts, turns, inputs, events = [], [], [], [], [], []
        last, root, waiting = {}, {}, {}
        message_ids, attempt_ids = ids(MESSAGES), ids(MESSAGES // 5)
        runs = iter(attempt_ids)
        for i, mid in enumerate(message_ids):
            r = rng.randrange(ROOMS)
            cid, task = room_ids[r], task_ids[r] if r < 4000 else None
            bot = "bot:inbox" if task in private else "bot:" + BOTS[r % len(BOTS)] if r < 4000 else json.loads(rooms[r][3])[1]
            created = stamp(MESSAGES - i)        # one message a minute, the newest now
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
        c.executemany("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs_json,in_reply_to,"
                      "created) VALUES(?,?,?,?,?,?,?,?,?)", messages)
        c.executemany("INSERT INTO jobs(id,message_id,bot,state,created,attempt_id) VALUES(?,?,?,?,?,?)", jobs)
        c.executemany("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created,"
                      "started,finished) VALUES(?1,?2,substr(?3,5),?4,0,'synthetic-'||?1,'done',?5,?5,?5,?5)", attempts)
        c.executemany("INSERT INTO turns(id,bot,started,finished,message_id,task_id,trigger_kind,outcome) "
                      "VALUES(?,?,?,?3,?,?,'message','acted')", turns)
        c.executemany("INSERT OR IGNORE INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", inputs)
        c.executemany("INSERT INTO events(id,ts,actor,action,target,detail_json) VALUES"
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
