"""The synthetic company the gate runs against, built deterministically from a seed, with only fictional data.

`build` writes it into a fresh database through the store (bulk history) and the in-process API (computers, live tasks,
routines), and returns the manifest the sender needs: who signs in with which token, which computer runs which bots,
and the threads, tasks and runs people open. Building a year of history takes minutes, so `cached` keeps one database
per seed and size and copies it before each run (the spec's rabbit hole)."""

import dataclasses
import hashlib
import json
import random
import shutil
import time
from pathlib import Path

DOMAIN = "example.test"

# History per day of the production company on 2026-11-12 (tico health metrics, first ~12 hours, ~49 bots), doubled to a
# day: tasks 340, messages 2,700, runs 1,100, run events 170,000. The full size keeps a year of tasks and messages at
# this rate scaled to 300 bots where that fits a build of minutes, and fewer run events (retention prunes them in
# production too); the numbers used are written in every report.
CAPABILITIES = [
    "task_lanes_v2",
    "session_roots_v1",
    "task_goals_v1",
    "capacity_continue_v1",
    "source_continue_v1",
    "turns_at_once_v1",
    "private_lanes_v1",
    "browser_exit_v1",
    "threads_v1",
]


@dataclasses.dataclass(frozen=True)
class Sizes:
    people: int = 50
    bots: int = 300
    computers: int = 8
    routines: int = 900
    open_tasks: int = 600  # live tasks, owned by bots, that people open, comment on and move
    threads: int = 1500  # company threads people read and post in
    days: int = 365  # how far back the history reaches
    done_tasks: int = 40_000  # closed history tasks, each with its own conversation
    messages: int = 400_000  # history messages spread over the history tasks and threads
    runs: int = 60_000  # finished runs in the history
    run_events: int = 1_000_000  # their events
    updates_days: int = 90  # daily updates per bot

    def key(self):
        return hashlib.sha1(json.dumps(dataclasses.asdict(self), sort_keys=True).encode()).hexdigest()[:10]


FULL = Sizes()
SMOKE = Sizes(
    people=5,
    bots=20,
    computers=2,
    routines=30,
    open_tasks=40,
    threads=20,
    days=30,
    done_tasks=200,
    messages=2000,
    runs=200,
    run_events=2000,
    updates_days=5,
)


def person(i):
    return f"p{i:03d}"


def bot_slug(i):
    return f"b{i:03d}"


def token(actor):
    return f"gate-{actor}"


def settings(folder, *, scheduler, release_commit=""):
    """The server's settings: the test fixture's (backend/tests/helpers/api.py) with the flight recorder and, for a full
    run, the scheduler on, so routines fire. People sign in with fixed bearer tokens (test identities), the way the
    contract benchmarks do; runners with the enrollment tokens the API hands out."""
    from backend.tests.helpers.api import api_settings

    base = api_settings(Path(folder))
    return dataclasses.replace(
        base, flight_recorder=True, scheduler_enabled=scheduler, release_commit=release_commit or base.release_commit
    )


def identities(manifest, settings_):
    """Register the people's tokens (test identities live in settings, not the database)."""
    from backend.auth import Identity

    for p in manifest["people"]:
        settings_.test_identities[p["token"]] = Identity("human:" + p["id"], "human", p["email"])


def _api(client, method, path, body=None, token_="ana-test", expected=200):
    import uuid

    r = client.request(
        method,
        "/api/v2/" + path,
        json=body,
        headers={"Authorization": "Bearer " + token_, "Idempotency-Key": str(uuid.uuid4())},
    )
    if r.status_code != expected:
        raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:300]}")
    return r.json()


def build(client, seed, sizes):
    """Write the company into the app's database (already holding the test fixture's base company) and return the
    manifest. Deterministic for a seed and sizes, apart from generated ids and tokens."""
    from backend import bot_settings as BS, threads
    from backend.store import H, encode
    from backend.tests.helpers.company import seed as base_company

    app = client.app
    store = app.state.store
    rnd = random.Random(seed)
    started = time.monotonic()
    people = [
        {"id": person(i), "email": f"{person(i)}@{DOMAIN}", "token": token(person(i))} for i in range(sizes.people)
    ]
    slugs = [bot_slug(i) for i in range(sizes.bots)]
    with store.transaction() as c:
        base_company(c)
        H.sync_registry(
            c,
            {s: {"name": f"Acme {s}", "runtime": "fake", "status": "active"} for s in slugs},
            {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]},
        )
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for p in people:
            c.execute("INSERT OR IGNORE INTO humans(id,name,email) VALUES(?,?,?)", (p["id"], p["id"], p["email"]))
            roster["people"].append({"id": p["id"], "email": p["email"]})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(roster),))
        for i, s in enumerate(slugs):
            c.execute("INSERT OR IGNORE INTO bot_config(bot,owner_id) VALUES(?,?)", (s, people[i % len(people)]["id"]))
            BS.write(c, s, {"name": f"Acme {s}", "runtime": "fake"})
        bot_ids = {s: H.resolve_bot(c, s)["id"] for s in slugs}
    manifest = {"seed": seed, "sizes": dataclasses.asdict(sizes), "people": people, "bots": slugs}
    identities(manifest, store.settings)

    # Computers: real enrollment, assignment and readiness, as a runner does; enough turns at once for the stress level.
    computers = []
    for n in range(sizes.computers):
        code = _api(client, "POST", "enrollments", {"operator": "ana"})["code"]
        machine = _api(client, "POST", "runners/enroll", {"code": code, "label": f"Gate {n}", "platform": "test"})
        mine = slugs[n :: sizes.computers]
        for s in mine:
            _api(client, "POST", f"bots/{s}/assignment", {"runner_id": machine["runner_id"], "expected_generation": 0})
        _api(
            client,
            "POST",
            "runners/heartbeat",
            {
                "version": "gate",
                "platform": "test",
                "readiness": {bot_ids[s]: True for s in mine},
                "capabilities": CAPABILITIES,
            },
            machine["token"],
        )
        _api(client, "POST", f"runners/{machine['runner_id']}/turns-at-once", {"turns_at_once": 256})
        computers.append(
            {
                "runner_id": machine["runner_id"],
                "token": machine["token"],
                "bots": mine,
                "readiness": {bot_ids[s]: True for s in mine},
            }
        )
    manifest["computers"] = computers

    # Threads people read and post in.
    thread_ids = []
    with store.transaction() as c:
        for n in range(sizes.threads):
            starter = "human:" + people[n % len(people)]["id"]
            out = threads.post(
                c, starter, None, f"Topic {n} kickoff", start={"title": f"Topic {n}", "audience": "company"}
            )
            thread_ids.append(out.get("thread_id") or out.get("conversation_id"))
    manifest["threads"] = thread_ids

    # Live tasks, through the API so the engine and the bots' wakes are as in production. Their runs (claimed by the
    # turns) are what people open in the run view.
    open_tasks = []
    for n in range(sizes.open_tasks):
        who = people[n % len(people)]
        task = _api(
            client,
            "POST",
            "tasks",
            {"owner": slugs[rnd.randrange(len(slugs))], "title": f"Acme item {n}", "body": "Synthetic work item."},
            who["token"],
        )
        task = task.get("task", task)
        open_tasks.append({"id": task["id"], "requester": who["id"]})
    manifest["tasks"] = open_tasks

    # Routines: wake a bot on a schedule, spread over the day; one in ten every 15 minutes, so some fire in any run.
    routines = 0
    for n in range(sizes.routines):
        minute = n % 60
        cron = (
            f"{n % 15},{n % 15 + 15},{n % 15 + 30},{n % 15 + 45} * * * *"
            if n % 10 == 0
            else f"{minute} {n // 60 % 24} * * *"
        )
        _api(
            client,
            "POST",
            "automations",
            {
                "name": f"Acme routine {n}",
                "owner": "person:" + people[n % len(people)]["id"],
                "trigger": {"type": "schedule", "cron": cron, "tz": "UTC"},
                "steps": [
                    {
                        "id": "wake",
                        "type": "action",
                        "enabled": True,
                        "do": "wake_bot",
                        "args": {"bot": slugs[n % len(slugs)], "text": "Routine check"},
                    }
                ],
            },
            people[n % len(people)]["token"],
        )
        routines += 1

    history(store, rnd, sizes, people, slugs, bot_ids, thread_ids, [m["runner_id"] for m in computers])
    with store.read() as c:
        manifest["rows"] = {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in (
                "tasks",
                "conversations",
                "messages",
                "jobs",
                "attempts",
                "attempt_events",
                "updates",
                "automations",
            )
        }
    manifest["build_seconds"] = round(time.monotonic() - started, 1)
    return manifest


def history(store, rnd, sizes, people, slugs, bot_ids, thread_ids, runners):
    """A year of closed tasks with their conversations, messages, runs, run events and daily updates, in bulk."""
    from backend import task_engine as E
    from backend.store import H, encode

    now = H.now()
    actors = ["human:" + p["id"] for p in people]

    def at(day_back, minute):
        return H.shift(now, days=-day_back, minutes=-minute)

    chunk = 2000
    task_rows = []
    for n in range(sizes.done_tasks):
        back = rnd.randrange(1, sizes.days)
        slug = slugs[rnd.randrange(len(slugs))]
        task_rows.append(
            (f"h-task-{n}", f"h-conv-{n}", actors[n % len(actors)], "bot:" + bot_ids[slug], at(back, n % 1440))
        )
    for start in range(0, len(task_rows), chunk):
        with store.transaction() as c:
            for tid, cid, req, owner, created in task_rows[start : start + chunk]:
                c.execute(
                    "INSERT INTO conversations(id,kind,participants,created_at,task_id) VALUES(?,'task',?,?,?)",
                    (cid, encode([req, owner]), created, tid),
                )
                # A task starts open in no stage (the store's guard); history ones are closed by the engine's one writer.
                c.execute(
                    "INSERT INTO tasks(id,title,body,requester_id,owner_id,status,conversation_id,created_at,updated_at) "
                    "VALUES(?,?,'Synthetic history item',?,?,'open',?,?,?)",
                    (tid, f"Acme history {tid}", req, owner, cid, created, created),
                )
                E._write(c, tid, status="closed")
                c.execute("UPDATE tasks SET done_at=?,closed_at=? WHERE id=?", (created, created, tid))

    # Messages: most in history task conversations, the rest in the company threads.
    sizes_text = (40, 200, 1200)
    msg_rows = []
    for n in range(sizes.messages):
        if thread_ids and n % 5 == 0:
            cid, created = thread_ids[n % len(thread_ids)], at(rnd.randrange(1, sizes.days), n % 1440)
        else:
            t = task_rows[n % len(task_rows)]
            cid, created = t[1], t[4]
        body = "Acme note " + "x" * rnd.choice(sizes_text)
        msg_rows.append((f"h-msg-{n}", cid, actors[n % len(actors)], body, created))
    for start in range(0, len(msg_rows), chunk * 5):
        with store.transaction() as c:
            c.executemany(
                "INSERT INTO messages(id,conversation_id,sender_id,kind,body,created_at,refs) "
                "VALUES(?,?,?,'say',?,?,'{}')",
                msg_rows[start : start + chunk * 5],
            )

    # Finished runs: a message, its completed job and its completed attempt, then the run's events.
    per_run = max(1, sizes.run_events // max(1, sizes.runs))
    for start in range(0, sizes.runs, chunk):
        with store.transaction() as c:
            for n in range(start, min(sizes.runs, start + chunk)):
                t = task_rows[n % len(task_rows)]
                bot = t[3][4:]
                mid, aid, created = f"h-run-msg-{n}", f"h-att-{n}", t[4]
                c.execute(
                    "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs) "
                    "VALUES(?,?,?,?,'say','Please look',?,?)",
                    (mid, t[1], t[2], t[3], created, encode({"task": t[0]})),
                )
                c.execute(
                    "INSERT INTO jobs(id,message_id,bot_id,state,created_at,attempt_id) VALUES(?,?,?,'completed',?,?)",
                    (mid, mid, bot, created, aid),
                )
                c.execute(
                    "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,lease_until,state,created_at,"
                    "started_at,finished_at,last_seq,lane,task_id,final_text) "
                    "VALUES(?,?,?,?,0,?,?,'completed',?,?,?,?,?,?,'Done.')",
                    (
                        aid,
                        mid,
                        bot,
                        runners[n % len(runners)],
                        "h-" + aid,
                        created,
                        created,
                        created,
                        created,
                        per_run,
                        "task:" + t[0],
                        t[0],
                    ),
                )
                c.executemany(
                    "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,?,'delta',?,?)",
                    [
                        (
                            aid,
                            k + 1,
                            json.dumps({"text": "step " + "y" * rnd.choice(sizes_text), "delta_kind": "text"}),
                            created,
                        )
                        for k in range(per_run)
                    ],
                )

    with store.transaction() as c:
        rows = []
        for d in range(sizes.updates_days):
            day = H.shift(now, days=-d - 1)[:10]
            for s in slugs:
                rows.append(
                    (
                        H.new_id(),
                        bot_ids[s],
                        "daily",
                        day,
                        f"{s} on {day}",
                        "- shipped a thing",
                        at(d + 1, 0),
                        at(d + 1, 0),
                    )
                )
        c.executemany(
            "INSERT INTO updates(id,bot,kind,day,headline,body,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", rows
        )


def cached(cache_dir, seed, sizes, release_commit=""):
    """The company's database for this seed and size, built once into `cache_dir` and reused: (db path, manifest)."""
    from fastapi.testclient import TestClient

    from backend.app import create_app

    cache = Path(cache_dir) / f"company-{seed}-{sizes.key()}"
    db, manifest_path = cache / "hub.db", cache / "manifest.json"
    if db.exists() and manifest_path.exists():
        return db, json.loads(manifest_path.read_text())
    if cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True)
    app = create_app(settings(cache, scheduler=False, release_commit=release_commit))
    with TestClient(app) as client:
        manifest = build(client, seed, sizes)
    # Reused at later commits for before-and-after runs; the report names the commit that built it.
    manifest["built_at_commit"] = release_commit or None
    app.state.store.close() if hasattr(app.state.store, "close") else None
    manifest_path.write_text(json.dumps(manifest, indent=1))
    return db, manifest


def copy(db, folder):
    """A fresh copy of the cached database for one run, so every run starts from the same company."""
    import sqlite3
    from contextlib import closing

    folder = Path(folder)
    target = folder / "hub.db"
    with (
        closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(target)) as out,
    ):
        source.backup(out)
        # The computers have been online all along (execution.waking: no last contact), not just woken: a computer
        # back from a silence waits AWAKE_SETTLE (two minutes) for work, which would hold the first level's turns.
        out.execute("UPDATE runner_states SET last_seen_at=NULL, awake_since=NULL")
        out.commit()
    return target
