#!/usr/bin/env python3
"""Scale check for the threads migration (`threads.migrate`, run by `hubdb.connect` at every server start).

    build    make a hub DB at the pre-threads schema (main's hubdb.py, from --schema-src) and fill it with synthetic
             history: conversations of every scope, messages, task comments and to_actor messages.
    start    open a DB the way the server does (`hubdb.connect`, which runs every migration, threads first) and report
             the time, the longest write-lock hold (a probe connection retries BEGIN IMMEDIATE), peak RSS and file growth.
    compare  check that a migrated DB matches a reference DB: every migrated column and delivery row (random ids and
             timestamps aside), and the reader sets of a sample of conversations and messages.

--schema-src is a directory holding main's `backend/` and `clients/`, made with
    git archive origin/main backend clients | tar -x -C DIR
so the old schema is main's own, not this branch's. Example (on RunsOn, see the report):
    python3 scripts/threads_migrate_scale.py build --db DB --schema-src SRC --conversations 50000 --messages 2000000
    python3 scripts/threads_migrate_scale.py start --db DB --out OUT.json [--kill-after SECONDS]
    python3 scripts/threads_migrate_scale.py compare --db DB --reference REF.sqlite
"""
import argparse
import hashlib
import json
import os
import random
import resource
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HUMANS, BOTS = 100, 200
SHARED_OPEN_MAX = BOTS                  # one open shared room per bot, as the old code left them
ROOMS = ("assistant", "ceo", "docs", "coo", "cfo", "cmo", "cto", "hr", "ops", "sales", "support", "legal",
         "finance", "people", "product", "growth", "design", "data", "security", "strategy")
EPOCH = datetime(2025, 1, 1, tzinfo=timezone.utc)

def uid(n):
    return str(uuid.UUID(int=n + 1, version=4))


def stamp(seconds):
    return (EPOCH + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ----------------------------------------------------------------------------- build
def build(args):
    db = Path(args.db)
    if db.exists():
        raise SystemExit(f"{db} exists; remove it first")
    db.parent.mkdir(parents=True, exist_ok=True)
    make = ("import sys; from backend import hubdb; hubdb.connect(sys.argv[1]).close(); "
            "print('user_version', hubdb.connect(sys.argv[1]).execute('PRAGMA user_version').fetchone()[0])")
    env = dict(os.environ, PYTHONPATH=args.schema_src)
    out = subprocess.run([sys.executable, "-c", make, str(db)], cwd=args.schema_src, env=env,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("schema build failed:\n" + out.stderr[-3000:])
    print("pre-threads schema:", out.stdout.strip(), flush=True)

    rng = random.Random(args.seed)
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.execute("PRAGMA synchronous=OFF")
    t0 = time.time()
    humans = [f"human:h{i:03d}" for i in range(HUMANS)]
    bots = [f"bot:b{i:03d}" for i in range(BOTS)]
    conn.execute("BEGIN")
    conn.executemany("INSERT INTO humans(id,name,email,teams_json) VALUES(?,?,?,?)",
                     [(h[6:], f"Person {i}", f"person{i}@acme.example", "[]") for i, h in enumerate(humans)])
    conn.executemany("INSERT INTO bots(slug,display_name,runtime,host,state,created) VALUES(?,?,?,?,?,?)",
                     [(b[4:], f"Bot {i}", "claude", "dispatcher", "active", stamp(0)) for i, b in enumerate(bots)])

    # Conversation mix: direct, shared rooms (one open per bot, the rest closed), personal, task. Main's unique
    # indexes allow one open personal room per (owner, room) and one open shared room per room key.
    mix = [("direct", args.conversations * 40 // 100), ("shared", args.conversations * 20 // 100),
           ("personal", args.conversations * 20 // 100)]
    mix.append(("task", args.conversations - sum(n for _, n in mix)))
    convs, tasks, conv_rows = [], [], []
    n_conv = 0
    for kind, count in mix:
        for k in range(count):
            n_conv += 1
            cid = f"{n_conv:032x}"
            human = humans[rng.randrange(HUMANS)]
            bot = bots[rng.randrange(BOTS)]
            task_id = f"{10_000_000 + n_conv:032x}"
            if kind == "direct":
                row = dict(scope="direct", kind="direct", room_key=None, owner=None, task_id=None,
                           people=[human, bot], closed=None)
            elif kind == "shared":
                # The first 200 are each bot's one open room (its Home once migrated); the rest are closed rooms.
                row = dict(scope="shared", kind="shared", room_key=bots[k % BOTS][4:], owner=None, task_id=None,
                           people=[human, bot], closed=None if k < SHARED_OPEN_MAX else stamp(n_conv * 60))
            elif kind == "personal":
                # One open personal room per (owner, room): the first 2,000 are open (100 people x 20 rooms).
                owner = humans[k % HUMANS]
                row = dict(scope="personal", kind="personal", room_key=ROOMS[(k // HUMANS) % len(ROOMS)],
                           owner=owner, task_id=None, people=[owner, bot],
                           closed=None if k < 2000 else stamp(n_conv * 60))
            else:
                tasks.append((task_id, f"Task {n_conv}", "open", stamp(n_conv)))
                row = dict(scope="task", kind="task", room_key=None, owner=None, task_id=task_id,
                           people=[human, bot], closed=None)
            convs.append((cid, row))
            conv_rows.append((cid, row["kind"], row["scope"], row["room_key"], row["owner"], row["task_id"],
                              json.dumps(row["people"]), stamp(n_conv), None, row["closed"]))
    conn.executemany("INSERT INTO tasks(id,title,status,created,updated) VALUES(?,?,?,?,?)",
                     [(t[0], t[1], t[2], t[3], t[3]) for t in tasks])
    conn.executemany("INSERT INTO conversations(id,kind,scope,room_key,owner_actor,task_id,participants_json,created,"
                     "last_message_at,closed_at) VALUES(?,?,?,?,?,?,?,?,?,?)", conv_rows)
    conn.execute("COMMIT")

    # Messages, in SQL (a Python row loop is too slow for 2M rows): one statement per conversation, ids and times
    # rising with the insert order, so the primary key grows at its end. Each conversation's count is 1-79 (mean 40).
    counts = [rng.randint(1, 2 * args.messages // args.conversations - 1) for _ in convs]
    scale = args.messages / sum(counts)
    counts = [max(1, round(c * scale)) for c in counts]
    start = 0
    conn.execute("BEGIN")
    insert = (
        "WITH RECURSIVE j(k) AS (SELECT 1 UNION ALL SELECT k+1 FROM j WHERE k < :count), "
        "raw AS (SELECT k, printf('%032x', :start+k) AS id, "
        "  strftime('%Y-%m-%dT%H:%M:%fZ', '2025-01-01', ((:start+k)*37)||' seconds') AS created, "
        "  CASE WHEN abs(random())%2=0 THEN :human ELSE :bot END AS from_actor, "
        "  CASE WHEN abs(random())%2=0 THEN CASE WHEN abs(random())%7=0 THEN :other ELSE :bot END END AS to_actor, "
        "  CASE WHEN abs(random())%100<85 THEN 'say' WHEN abs(random())%100<90 THEN 'ask' ELSE 'notice' END AS kind, "
        "  lower(hex(randomblob(20+abs(random())%100))) AS body, "
        "  CASE WHEN :is_task=1 AND abs(random())%10<7 THEN '{\"task\":\"'||:task||'\",\"comment\":true}' "
        "       WHEN abs(random())%100<5 THEN '{\"quiet\":true}' "
        "       WHEN abs(random())%100<2 THEN '{\"task\":\"'||:task||'\"}' END AS refs_json, "
        "  CASE WHEN k>1 AND abs(random())%10=0 THEN printf('%032x', :start+k-1) END AS in_reply_to, "
        "  abs(random())%100 AS r1, abs(random())%100 AS r2, abs(random())%1000 AS r3 FROM j) "
        "INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs_json,in_reply_to,created,"
        "delivered_at,read_at,deleted_at) "
        "SELECT id, :cid, from_actor, to_actor, kind, body, refs_json, in_reply_to, created, "
        "  CASE WHEN r1<90 THEN created END, CASE WHEN r1<90 AND r2<80 THEN created END, "
        "  CASE WHEN r3<10 THEN created END FROM raw")
    for index, ((cid, row), count) in enumerate(zip(convs, counts)):
        params = {"count": count, "start": start, "cid": cid, "human": row["people"][0], "bot": row["people"][1],
                  "other": bots[rng.randrange(BOTS)], "is_task": 1 if row["kind"] == "task" else 0,
                  "task": row["task_id"] or tasks[rng.randrange(len(tasks))][0]}
        conn.execute(insert, params)
        conn.execute("UPDATE conversations SET last_message_at=(SELECT created FROM messages WHERE id=?) WHERE id=?",
                     (f"{start + count:032x}", cid))
        start += count
        if index % 5000 == 4999:
            conn.execute("COMMIT")
            conn.execute("BEGIN")
    conn.execute("COMMIT")
    conn.close()
    print(f"built {n_conv} conversations, {start} messages, {BOTS} bots, {HUMANS} people, {len(tasks)} tasks "
          f"in {time.time() - t0:.1f}s; file {db.stat().st_size / 1e6:.0f} MB", flush=True)


# ----------------------------------------------------------------------------- start
class Probe(threading.Thread):
    """Watches the write lock from a second connection: BEGIN IMMEDIATE fails while another writer holds it. Records
    each blocked stretch (the longest is the longest write-lock hold, to the probe's 10 ms resolution) and the WAL's
    largest size."""

    def __init__(self, db):
        super().__init__(daemon=True)
        self.db, self.stop = str(db), threading.Event()
        self.holds, self.wal_max, self.blocked_since = [], 0, None

    def run(self):
        conn = sqlite3.connect(self.db, timeout=0, isolation_level=None)
        while not self.stop.is_set():
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("ROLLBACK")
                if self.blocked_since is not None:
                    self.holds.append(time.time() - self.blocked_since)
                    self.blocked_since = None
            except sqlite3.OperationalError:
                if self.blocked_since is None:
                    self.blocked_since = time.time()
            try:
                self.wal_max = max(self.wal_max, os.stat(self.db + "-wal").st_size)
            except FileNotFoundError:
                pass
            time.sleep(0.01)
        conn.close()


def files_bytes(db):
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.stat(str(db) + suffix).st_size
        except FileNotFoundError:
            pass
    return total


def peak_rss_mb():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024      # bytes on macOS, KB on Linux


def start(args):
    from backend import hubdb

    db = Path(args.db)
    before = files_bytes(db)
    probe = Probe(db)
    probe.start()
    t0 = time.time()
    conn = hubdb.connect(str(db))
    total = time.time() - t0
    probe.stop.set()
    probe.join()
    done = conn.execute("SELECT 1 FROM registry_metadata WHERE key='threads-migration'").fetchone() is not None
    messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()
    result = {"seconds": round(total, 2), "longest_lock_hold_s": round(max(probe.holds, default=0), 2),
              "lock_holds_over_5s": sum(1 for h in probe.holds if h > 5), "lock_holds": len(probe.holds),
              "peak_rss_mb": round(peak_rss_mb()), "db_bytes_before": before, "db_bytes_after": files_bytes(db),
              "wal_max_bytes": probe.wal_max, "threads_migrated": done, "messages": messages}
    print(json.dumps(result), flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n")


def start_then_kill(args):
    """Runs `start` in a child and kills it with SIGKILL after --kill-after seconds: a crash partway through."""
    child = subprocess.Popen([sys.executable, __file__, "start", "--db", args.db], cwd=ROOT)
    time.sleep(args.kill_after)
    if child.poll() is None:
        child.send_signal(signal.SIGKILL)
        child.wait()
        print(f"killed mid-start after {args.kill_after}s (pid {child.pid})", flush=True)
    else:
        print(f"child finished before the kill ({child.returncode})", flush=True)


# ----------------------------------------------------------------------------- compare
DELIVERY_COLUMNS = ("message_id", "recipient", "mode", "reason", "cause", "state", "revision", "delivered_at",
                    "read_at")
CHECKS = {
    "conversations": "SELECT id,audience,owner_actor,root_id,path,last_seq,last_unread_seq,archived_at,scope,room_key "
                     "FROM conversations ORDER BY id",
    "messages": "SELECT id,conversation_id,seq,restricted,legacy_audience,deleted_at,revision FROM messages ORDER BY id",
    "message_deliveries": "SELECT " + ",".join(DELIVERY_COLUMNS) + " FROM message_deliveries "
                          "ORDER BY message_id, recipient",
}


def digest(db, check):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        h, n = hashlib.sha256(), 0
        for row in conn.execute(CHECKS[check]):
            h.update(repr(row).encode())
            n += 1
        return n, h.hexdigest()[:16]
    finally:
        conn.close()


def origin_ok(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        missing = conn.execute("SELECT COUNT(*) FROM messages WHERE origin_id IS NULL").fetchone()[0]
        dupes = conn.execute("SELECT COUNT(*) FROM (SELECT origin_id FROM messages GROUP BY origin_id "
                             "HAVING COUNT(*)>1)").fetchone()[0]
        seq_dupes = conn.execute("SELECT COUNT(*) FROM (SELECT seq FROM messages GROUP BY seq HAVING COUNT(*)>1)"
                                 ).fetchone()[0]
        return {"origin_missing": missing, "origin_duplicates": dupes, "seq_duplicates": seq_dupes}
    finally:
        conn.close()


def reader_sample(db, sample, viewers):
    """The (viewer, row) pairs threads.readable allows over an evenly spaced sample of `sample` conversations and of
    `sample` messages, as a count and a digest. The same predicate runs on both DBs, so equal digests mean equal reader
    sets. Viewers are bare Identity objects (no Auth), so this checks the stored rows and their audiences; the column
    digests above cover the rest of what the migration writes."""
    from backend import threads
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        objects = [threads.Viewer(None, who) for who in viewers]
        found, errors = [], {}
        for table in ("conversations", "messages"):
            total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            step = max(1, total // sample)
            for row in conn.execute(f"SELECT * FROM {table} WHERE rowid % ? = 0", (step,)):
                for index, viewer in enumerate(objects):
                    try:
                        if threads.readable(conn, viewer, dict(row)):
                            found.append(f"{table}:{row['id']}:{index}")
                    except sqlite3.OperationalError as error:       # a table the bare hub lacks (bot_config)
                        errors[str(error)] = errors.get(str(error), 0) + 1
                        found.append(f"{table}:{row['id']}:{index}:error:{error}")
        if errors:
            print("reader sample errors (same on both DBs when the digests match):", errors, flush=True)
        return len(found), hashlib.sha256("\n".join(sorted(found)).encode()).hexdigest()[:16]
    finally:
        conn.close()


def compare(args):
    report = {}
    for check in CHECKS:
        a, b = digest(args.db, check), digest(args.reference, check)
        report[check] = {"rows": a[0], "digest": a[1], "reference_digest": b[1], "same": a == b}
    rng = random.Random(3)
    humans = [f"human:h{i:03d}" for i in range(HUMANS)]
    from backend.auth import Identity
    viewers = [Identity(a, "human", email="") for a in rng.sample(humans, 20)]
    viewers += [Identity(f"bot:b{i:03d}", "bot", agent="standing") for i in rng.sample(range(BOTS), 20)]
    sampled = reader_sample(args.db, args.sample, viewers)
    reference = reader_sample(args.reference, args.sample, viewers)
    report["reader_sets_sample"] = {"pairs": sampled[0], "digest": sampled[1], "reference_digest": reference[1],
                                    "same": sampled == reference}
    report["message_checks"] = origin_ok(args.db)
    report["same"] = (all(report[c]["same"] for c in CHECKS) and report["reader_sets_sample"]["same"]
                      and not any(report["message_checks"].values()))
    print(json.dumps(report, indent=2))
    if not report["same"]:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--db", required=True)
    b.add_argument("--schema-src", required=True)
    b.add_argument("--conversations", type=int, default=50_000)
    b.add_argument("--messages", type=int, default=2_000_000)
    b.add_argument("--seed", type=int, default=7)
    s = sub.add_parser("start")
    s.add_argument("--db", required=True)
    s.add_argument("--out")
    k = sub.add_parser("kill")
    k.add_argument("--db", required=True)
    k.add_argument("--kill-after", type=float, required=True)
    c = sub.add_parser("compare")
    c.add_argument("--db", required=True)
    c.add_argument("--reference", required=True)
    c.add_argument("--sample", type=int, default=500, help="conversations and messages sampled for reader sets")
    args = parser.parse_args()
    {"build": build, "start": start, "kill": start_then_kill, "compare": compare}[args.cmd](args)


if __name__ == "__main__":
    main()
