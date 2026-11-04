"""Measures opening Home (GET /api/v2/places/person:me) and the reads the same page makes beside it, on a fictional
company the size of a busy one: 70 bots, a few hundred threads with messages and deliveries, a month of daily updates
from every bot, and a large message history.

    python3 -m pytest bench/perf-web-home.py -n 0 -s -q

Per read it prints the statements one call takes and the median wall time of RUNS calls, and per Home card the same.
"""
import contextlib
import os
import json
import random
import statistics
import time

from backend import bot_settings as BS, threads
from backend.store import H
from backend.tests import test_api
from backend.tests.test_api import headers

api = test_api.api                                   # the fixture, bound so that tests can take it
BOTS = [f"bot{n:02d}" for n in range(70)]
THREADS, DMS, POSTS = int(os.environ.get("THREADS", 1200)), int(os.environ.get("DMS", 150)), 6
UPDATE_DAYS = 30
FILLER = 120_000                                     # older messages in closed threads: the history a busy hub has
RUNS = 7


def seed(api):
    store = api.app.state.store
    rnd = random.Random(7)
    with store.transaction() as c:
        H.sync_registry(c, {s: {"name": s, "runtime": "fake", "status": "active"} for s in BOTS},
                        {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]})
        for s in BOTS:
            c.execute("INSERT OR IGNORE INTO bot_config(bot,owner_id) VALUES(?,'ana')", (s,))
            BS.write(c, s, {"name": s, "runtime": "fake"})
    people = ["human:ana", "human:ben", "human:cara"]
    made = []
    with store.transaction() as c:
        for n in range(THREADS):
            starter = rnd.choice(people + ["bot:" + b for b in BOTS[:20]])
            text = f"Thread {n} kickoff" + (" @ana please look" if n % 3 == 0 else "")
            out = threads.post(c, starter, None, text, start={"title": f"Topic {n}", "audience": "company"})
            made.append(out.get("thread_id") or out.get("conversation_id"))
        for n in range(DMS):
            bot = "bot:" + BOTS[n % len(BOTS)]
            out = threads.post(c, "human:ana", None, f"Hi {bot} #{n}", start={"dm": [bot], "title": ""})
            made.append(out.get("thread_id") or out.get("conversation_id"))
    for chunk in range(0, len(made), 40):
        with store.transaction() as c:
            for tid in made[chunk:chunk + 40]:
                for k in range(POSTS):
                    who = rnd.choice(people + ["bot:" + b for b in BOTS])
                    text = f"Note {k} on it" + (" @ana" if rnd.random() < 0.15 else "")
                    try:
                        threads.post(c, who, tid, text)
                    except Exception:
                        pass
    with store.transaction() as c:
        now = H.now()
        rows = []
        for d in range(UPDATE_DAYS):
            day = H.shift(now, days=-d)[:10]
            for b in BOTS:
                rows.append((H.new_id(), b, "daily", day, f"{b} did things on {day}", f"- {b} shipped a thing\n- and another",
                             H.shift(now, days=-d, minutes=-BOTS.index(b)), H.shift(now, days=-d), None, None, None,
                             json.dumps([]), None, 0))
        c.executemany("INSERT INTO updates(id,bot,kind,day,headline,body,created_at,updated_at,slides,drafted_by,edited_by,"
                      "inputs,source,meetings) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        # Ana has read the older ones.
        c.execute("INSERT OR IGNORE INTO update_reads(update_id,actor,read_at) SELECT id,'human:ana',? FROM updates "
                  "WHERE day<?", (now, H.shift(now, days=-3)[:10]))
        # Replies to a few updates.
        for uid, in c.execute("SELECT id FROM updates ORDER BY created_at DESC LIMIT 20").fetchall():
            c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at) "
                      "SELECT ?,conversation_id,'human:ana',recipient_id,kind,'Nice',?,? FROM messages LIMIT 1",
                      (H.new_id(), json.dumps({"update": uid}), now))
        # The history: copies of the messages above in a closed thread, so every scan of messages pays for it.
        closed = c.execute("SELECT id FROM conversations WHERE parent_id IS NULL ORDER BY created_at LIMIT 1").fetchone()[0]
        base = c.execute("SELECT max(seq) FROM messages").fetchone()[0] or 0
        c.execute("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<?) "
                  "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at,seq) "
                  "SELECT lower(hex(randomblob(16))),?,'bot:bot01',NULL,'say','old history line '||i,"
                  "'{\"task\":null}',?,?+i FROM n", (FILLER, closed, H.shift(now, days=-60), base + 1_000_000))
    return made


@contextlib.contextmanager
def counting(store, seen):
    originals = {name: getattr(store, name) for name in ("read", "read_transaction")}

    def traced(opened):
        @contextlib.contextmanager
        def borrowed():
            with opened() as c:
                c.set_trace_callback(seen.append)
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)
        return borrowed
    try:
        for name, opened in originals.items():
            setattr(store, name, traced(opened))
        yield
    finally:
        for name, opened in originals.items():
            setattr(store, name, opened)


def measure(api, path, token="ana-test"):
    store, times, seen = api.app.state.store, [], []
    r = api.get(path, headers=headers(token))
    assert r.status_code == 200, r.text
    with counting(store, seen):
        api.get(path, headers=headers(token))
    for _ in range(RUNS):
        t = time.perf_counter()
        api.get(path, headers=headers(token))
        times.append((time.perf_counter() - t) * 1000)
    return len(seen), statistics.median(times), len(r.content)


def test_home_and_its_neighbours(api):
    t = time.perf_counter()
    seed(api)
    with api.app.state.store.read() as c:
        counts = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("conversations", "messages", "message_deliveries", "updates", "tasks")}
    print(f"\nseeded in {time.perf_counter() - t:.1f}s: {counts}")
    home = api.get("/api/v2/places/person:me", headers=headers("ana-test")).json()
    print("cards:", {p["card"]["template"]: (p["card"]["data"] or {}).get("count", (p["card"]["data"] or {}).get("unread"))
                     for p in home["pins"] if "card" in p})
    for path in ("/api/v2/places/person:me", "/api/v2/threads?limit=50", "/api/v2/threads/rail?limit=100", "/api/v2/updates?limit=40",
                 "/api/v2/needs-you?limit=50", "/api/v2/needs-you", "/api/status", "/api/employees", "/api/v2/status"):
        try:
            n, ms, size = measure(api, path)
        except AssertionError as exc:
            print(f"{path:45s} failed: {str(exc)[:120]}")
            continue
        print(f"{path:45s} {n:5d} statements {ms:8.1f} ms {size / 1024:8.1f} KB")
    # Each Home card alone: the saved place's cards, one GET /api/v2/cards?ids= each.
    saved = api.put("/api/v2/places/person:me", json={}, headers=headers("ana-test")).json()
    for pin in saved["pins"]:
        card = pin.get("card") or {}
        if card.get("id"):
            n, ms, size = measure(api, f"/api/v2/cards?ids={card['id']}")
            print(f"card {card['template']:20s} {n:5d} statements {ms:8.1f} ms {size / 1024:8.1f} KB")
    if os.environ.get("PROFILE"):
        import cProfile, pstats
        prof = cProfile.Profile()
        prof.enable()
        for _ in range(3):
            api.get(os.environ.get("PROFILE_PATH", "/api/v2/places/person:me"), headers=headers("ana-test"))
        prof.disable()
        pstats.Stats(prof).sort_stats("cumulative").print_stats(45)
    if os.environ.get("STATEMENTS"):
        seen = []
        with counting(api.app.state.store, seen):
            api.get(os.environ.get("STATEMENTS"), headers=headers("ana-test"))
        import collections, re
        shapes = collections.Counter(re.sub(r"'[^']*'|\b\d+\b", "?", q)[:160] for q in seen)
        for q, k in shapes.most_common(15):
            print(f"{k:5d}  {q}")
