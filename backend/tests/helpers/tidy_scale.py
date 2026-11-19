"""A fictional company the size and shape of the hosted TIDY one on Nov 19, for backend/tests/test_query_budgets.py.

TIDY then: 18.5k tasks (94% finished), 19k conversations, 40k messages, 30k followers, 170 bots over 10 computers. Its
reads ran 200 to 700 statements a page, one or a few per row, which cost nothing on a test's few rows and seconds there.

The shapes come from the benches that measured those reads (bench/perf-hub-overload.py, perf-status-health.py,
perf-holds-pages.py, perf-runner-polls.py and backend/tests/helpers/runner_grants.py): bots on computers with plans,
usage-limit holds and paused bots, open tasks in a pipeline with OK asks, threads with posts, bot statuses naming
finished or missing tasks, pull requests, cloud machines, tools and keys, worktrees. The engine makes OPEN of the open
tasks and the threads; the rest is copied from them in bulk SQL (`fill`), which is what keeps the build to seconds.
Only fictional data."""

import importlib.util
import json
import random
from pathlib import Path

from backend.store import H

ROOT = Path(__file__).resolve().parents[3]
OPEN = 250  # open tasks the engine makes; copies bring them to OPEN_TOTAL
OPEN_TOTAL = 1100
TASKS = 18500
MESSAGES = 40000
FOLLOWERS = 30000
THREADS = 100
RUNNERS = 10
WORKTREES = 300
ROOM_MESSAGES = 3  # each finished task's room gets this many posts until MESSAGES is reached
# The two triggers that let a task change status or stage only through the engine (task_engine.enter and move): the
# copies below are made finished and in place, as a company years old has them, so these are lifted while they are.
GUARDS = ("tasks_enter_only", "tasks_move_only")


def bench(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "bench" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(c, table):
    return [row[1] for row in c.execute(f"PRAGMA table_info({table})")]


def fill(c):
    """Copies of the engine's open tasks up to OPEN_TOTAL open and TASKS in all, each with its own room (a copy of its
    template's), ROOM_MESSAGES posts in the finished ones' rooms up to MESSAGES, and the requester and owner of each
    copy following its room, up to FOLLOWERS."""
    guards = {
        name: sql
        for name, sql in c.execute("SELECT name, sql FROM sqlite_schema WHERE type='trigger'")
        if name in GUARDS
    }
    for name in guards:
        c.execute(f"DROP TRIGGER {name}")
    c.execute(
        "CREATE TEMP TABLE scale_template AS SELECT row_number() OVER (ORDER BY rowid) - 1 AS k, id, conversation_id "
        "FROM tasks WHERE status='open' AND pipeline_id IS NOT NULL AND conversation_id IS NOT NULL"
    )
    templates = c.execute("SELECT count(*) FROM scale_template").fetchone()[0]
    existing = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
    copies = TASKS - existing
    reopened = OPEN_TOTAL - c.execute("SELECT count(*) FROM tasks WHERE status='open'").fetchone()[0]
    c.execute(
        "CREATE TEMP TABLE scale_copy AS WITH RECURSIVE n(i) AS (SELECT 0 UNION ALL SELECT i+1 FROM n WHERE i<?-1) "
        "SELECT i, printf('scale-task-%06d', i) AS id, printf('scale-room-%06d', i) AS room, "
        "(SELECT id FROM scale_template WHERE k = i % ?) AS template, i < ? AS open, "
        "strftime('%Y-%m-%dT%H:%M:%fZ', ?, printf('-%d minutes', 30 + i * 3)) AS at FROM n",
        (copies, templates, reopened, H.now()),
    )
    tasks = _columns(c, "tasks")
    given = {
        "id": "x.id",
        "number": "NULL",
        "conversation_id": "x.room",
        "status": "CASE WHEN x.open THEN 'open' ELSE 'done' END",
        "stage_id": "CASE WHEN x.open THEN t.stage_id ELSE NULL END",
        "stage_entered_at": "CASE WHEN x.open THEN x.at ELSE NULL END",
        "created_at": "x.at",
        "updated_at": "x.at",
        "done_at": "CASE WHEN x.open THEN NULL ELSE x.at END",
        "closed_at": "CASE WHEN x.open THEN NULL ELSE x.at END",
        "occurrence": "NULL",
        "occurrence_step": "NULL",
        "request_id": "NULL",
    }
    c.execute(
        f"INSERT INTO tasks({','.join(tasks)}) SELECT {','.join(given.get(col, 't.' + col) for col in tasks)} "
        "FROM scale_copy x JOIN tasks t ON t.id = x.template"
    )
    visits = _columns(c, "visits")
    given = {"id": "printf('scale-visit-%06d', x.i)", "task_id": "x.id", "entered_at": "x.at", "last_change_at": "x.at"}
    c.execute(  # an open copy is in its template's stage, as the engine's visit there says
        f"INSERT INTO visits({','.join(visits)}) SELECT {','.join(given.get(col, 'v.' + col) for col in visits)} "
        "FROM scale_copy x JOIN visits v ON v.task_id = x.template AND v.left_at IS NULL WHERE x.open"
    )
    rooms = _columns(c, "conversations")
    given = {
        "id": "x.room",
        "task_id": "x.id",
        "root_id": "NULL",
        "path": "NULL",
        "parent_id": "NULL",
        "parent_message_id": "NULL",
        "room_key": "NULL",
        "created_at": "x.at",
        "updated_at": "x.at",
        "last_message_at": "x.at",
        "last_seq": f"CASE WHEN x.open THEN 0 ELSE {ROOM_MESSAGES} END",
        "last_unread_seq": "0",
    }
    c.execute(
        f"INSERT INTO conversations({','.join(rooms)}) SELECT {','.join(given.get(col, 'r.' + col) for col in rooms)} "
        "FROM scale_copy x JOIN scale_template s ON s.id = x.template JOIN conversations r ON r.id = s.conversation_id"
    )
    rows = (MESSAGES - c.execute("SELECT count(*) FROM messages").fetchone()[0]) // ROOM_MESSAGES
    for seq in range(1, ROOM_MESSAGES + 1):
        c.execute(
            "INSERT INTO messages(id,conversation_id,sender_id,kind,body,created_at,seq,refs) "
            "SELECT printf('scale-msg-%06d-%d', x.i, ?), x.room, CASE WHEN ? % 2 THEN t.owner_id ELSE t.requester_id END, "
            "'say', printf('Update %d on finished item %d', ?, x.i), x.at, ?, json_object('task', x.id) "
            "FROM scale_copy x JOIN tasks t ON t.id = x.id WHERE NOT x.open ORDER BY x.i LIMIT ?",
            (seq, seq, seq, seq, rows),
        )
    c.execute(
        "UPDATE conversations SET last_seq=0, last_message_at=created_at WHERE id LIKE 'scale-room-%' "
        "AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=conversations.id)"
    )
    followers = FOLLOWERS - c.execute("SELECT count(*) FROM thread_followers").fetchone()[0]
    c.execute(
        "INSERT OR IGNORE INTO thread_followers(id,thread_id,actor,reason,last_read_seq,created_at,updated_at) "
        "SELECT printf('scale-follow-%06d-%s', x.i, k.k), x.room, CASE k.k WHEN 'o' THEN t.owner_id ELSE t.requester_id "
        f"END, 'participant', {ROOM_MESSAGES}, x.at, x.at FROM scale_copy x JOIN tasks t ON t.id = x.id "
        "JOIN (SELECT 'o' AS k UNION ALL SELECT 'r') k ORDER BY x.i, k.k LIMIT ?",
        (followers,),
    )
    for sql in guards.values():
        c.execute(sql)
    c.execute("DROP TABLE scale_template")
    c.execute("DROP TABLE scale_copy")
    # The read caches key on these clocks; the copies moved rows they count, so readers must not reuse what they held.
    c.execute("UPDATE provenance_clock SET value=value+1")
    c.execute("UPDATE row_clock SET value=value+1")


def runners_side(c, bots):
    """The computers' own rows: a present worktree for WORKTREES of the bots' open tasks, on that bot's computer, and a
    running turn on r0 for the event upload. Returns {runner id: [(link id, branch)]} and the running attempt's id."""
    links: dict = {}
    rows = c.execute(
        "SELECT t.id, substr(t.owner_id, 5), a.runner_id FROM tasks t JOIN assignments a ON a.bot = substr(t.owner_id, 5) "
        "WHERE t.status='open' AND t.owner_id LIKE 'bot:%' ORDER BY t.rowid LIMIT ?",
        (WORKTREES,),
    ).fetchall()
    now, sha = H.now(), "a" * 40
    for k, (task_id, slug, runner) in enumerate(rows):
        detail = {
            "owner": "bot:" + slug,
            "checkout_state": "ready",
            "expected_head": sha,
            "checkout_target": "origin/main",
            "expected_base": "b" * 64,
            "last_commit": sha,
            "dirty_files": 0,
            "last_activity": 1_700_000_000.0,
            "activity_at": now,
        }
        lid, branch = f"scale-tree-{k:04d}", f"tico/{task_id[:8]}-work-{k}"
        c.execute(
            "INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at,repo,branch,computer_id,path,detail,"
            "updated_at) VALUES(?,?,'worktree','','present',?,?,'acme/product',?,?,?,?,?)",
            (lid, task_id, "bot:" + slug, now, branch, runner, f"wt/{k}", json.dumps(detail), now),
        )
        links.setdefault(runner, []).append((lid, branch))
    bot, generation = c.execute(
        "SELECT a.bot, a.generation FROM assignments a JOIN bots b ON b.id=a.bot WHERE a.runner_id='r0' AND "
        "b.state='active' ORDER BY a.bot LIMIT 1"
    ).fetchone()
    task_id, room = c.execute(
        "SELECT id, conversation_id FROM tasks WHERE status='open' AND owner_id=? ORDER BY rowid LIMIT 1",
        ("bot:" + bot,),
    ).fetchone()
    from backend.enqueue import enqueue_stored

    c.execute(
        "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs) "
        "VALUES('scale-wake',?,'human:ana',?,'say','Please carry on',?,?)",
        (room, "bot:" + bot, now, json.dumps({"task": task_id})),
    )
    enqueue_stored(c, "scale-wake")
    c.execute(
        "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,lane) "
        "VALUES('scale-attempt','scale-wake',?,'r0',?,'scale','running',?,?,?)",
        (bot, generation, H.shift(now, hours=6), now, "task:" + task_id),
    )
    c.execute("UPDATE jobs SET state='running',attempt_id='scale-attempt' WHERE id='scale-wake'")
    # Every other wake the seeds queued has had its run: a claim finds nothing, as most of a computer's claims do.
    c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    return links, "scale-attempt"


def build(api):
    """Seed the company into the app's database; returns what the runner routes need: {"links", "attempt", "bots"}."""
    store = api.app.state.store
    status = bench("perf-status-health")
    overload = status.overload
    overload.OPEN, overload.CLOSED, overload.THREADS = OPEN, 0, THREADS
    engine_made = overload.seed

    def seeded(api):
        engine_made(api)
        with store.transaction() as c:
            fill(c)

    overload.seed = seeded
    status.seed(api)  # the overload company, filled, then bot statuses, pull requests and cloud machines
    from backend.tests.helpers import runner_grants

    with store.transaction() as c:
        runner_grants.seed(c, overload.BOTS, random.Random(19), tools=60, runners=RUNNERS)
    holds = bench("perf-holds-pages")
    holds._seed = lambda api: None  # its company is the one above; only its plans, holds and pauses are added
    holds.seed(api)
    with store.transaction() as c:
        links, attempt = runners_side(c, overload.BOTS)
    return {"links": links, "attempt": attempt, "bots": overload.BOTS}


def counts(c):
    return {
        t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("bots", "runners", "tasks", "conversations", "messages", "thread_followers")
    } | {"open_tasks": c.execute("SELECT count(*) FROM tasks WHERE status='open'").fetchone()[0]}
