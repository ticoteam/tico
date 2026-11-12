"""A disposable hub for the fault rig: the real migrations, the real engine, a fake clock and ids drawn from the seed.

One template database is built per process (migrations, people, bots, a computer, General and the Work pipeline) and
copied for each world, so a world starts in a few milliseconds. Every statement goes through `RigConnection`, which
counts statements and can crash a write part way: it raises `Crash`, a BaseException, as a process that died would stop.
The transaction rolls back and the rig carries on with a new Store on the same file, as a restarted server would.

Nothing reads the wall clock (time_machine holds it still) and every id comes from the world's seed, so a seed always
runs the same way."""

import datetime as dt
import json
import os
import random
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import time_machine

# A world calls no outside service. The engine reads these when it acts, so a world sets them while it runs and puts
# back what was there, and a test process that imports the rig keeps its own settings for every other test.
OFFLINE = {
    "TICO_UPDATE_CHECK": "off",
    "TICO_RUNNER_CONTAINER_PROBE": "off",
    "TICO_SQLITE_SYNC": "off",
    "TICO_SUPPORT": "off",
    "TICO_TELEMETRY": "off",
    "TICO_HQ_URL": "http://127.0.0.1:9",
}


@contextmanager
def offline() -> Iterator[None]:
    saved = {name: os.environ.get(name) for name in OFFLINE}
    os.environ.update(OFFLINE)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


START = dt.datetime(2030, 1, 7, 9, 0, tzinfo=dt.timezone.utc)
ANA, BEN, CARA = "human:ana", "human:ben", "human:cara"
PEOPLE = (ANA, BEN, CARA)
BUILDER, WRITER, REVIEWER, MANAGER = "bot:builder", "bot:writer", "bot:reviewer", "bot:manager"
BOTS = (BUILDER, WRITER, REVIEWER, MANAGER)
# A software company's world (World(seed, software=True)) also has the bots the Product and Engineering presets name,
# each found by its template (backend/pipeline_seats.py): the Builder is the Builder above.
# PM is the Product Owner; PPM and EM are the Product Manager and the Engineering Manager.
PM, REVIEWER_1, REVIEWER_2, RELEASE = "bot:pm", "bot:rev1", "bot:rev2", "bot:rm"
PPM, EM = "bot:product-manager", "bot:engineering-manager"
ROLE_TEMPLATES = {
    PM: "segment-product-owner",
    PPM: "product-manager",
    EM: "engineering-manager",
    BUILDER: "builder",
    REVIEWER_1: "reviewer",
    REVIEWER_2: "reviewer",
    RELEASE: "release-owner",
}
SOFTWARE_BOTS = (PM, PPM, EM, REVIEWER_1, REVIEWER_2, RELEASE)
# Who owns each bot on the org chart: the person a paused bot's row goes to.
BOT_OWNERS = {
    "builder": "ben",
    "writer": "cara",
    "reviewer": "ana",
    "manager": "ana",
    "pm": "ana",
    "product-manager": "ana",
    "engineering-manager": "ben",
    "rev1": "ben",
    "rev2": "ben",
    "rm": "ana",
}
# The Builder runs on Tico Cloud's own model: out of credit stops it and no other bot.
TICO_MODEL = {"builder": "tico-standard"}
GENERAL = "general"
WORK = "work"
# The Work pipeline: a by-hand stage, a stage that needs nothing, two stages with a limit, a stage that waits on the
# tasks it files, a people-only OK, and a requirement nobody may push past.
WORK_RECORD: dict[str, Any] = {
    "id": WORK,
    "name": "Work",
    "goal": "Ideas that become shipped work.",
    "prefix": "W",
    "manager_id": MANAGER,
    "stages": [
        {"name": "Ideas", "owner": ANA, "by_hand": True, "requirements": [{"kind": "owner_done"}]},
        {"name": "On deck", "owner": "assignee", "requirements": []},
        {
            "name": "Draft",
            "owner": "assignee",
            "task_limit": 2,
            "expected_minutes": 120,
            "expected_bots_only": True,
            "requirements": [{"kind": "owner_done"}],
        },
        {"name": "Build", "owner": "assignee", "requirements": [{"kind": "linked_done"}]},
        {
            "name": "Review",
            "owner": "assignee",
            "task_limit": 2,
            "requirements": [
                {"kind": "ok", "person_id": REVIEWER, "line": "Reviewer checks the draft"},
                {"kind": "ok", "person_id": BEN, "who_oks": "people_only", "line": "Ben signs off"},
            ],
        },
        {"name": "Ship", "owner": "assignee", "requirements": [{"kind": "owner_done", "can_push_past": False}]},
    ],
}


class Crash(BaseException):
    """The process died part way through a write. A BaseException, so no `except Exception` in the engine catches it."""


class Meter:
    """Counts statements, and crashes the one after `crash_after` more have run. One per process: every connection
    reports here."""

    def __init__(self) -> None:
        self.count = 0
        self.crash_at: int | None = None
        self.crashed = 0
        self.trace: list[str] | None = None

    def arm(self, after: int) -> None:
        self.crash_at = self.count + after

    def disarm(self) -> None:
        self.crash_at = None

    def tick(self, statement: str) -> None:
        if self.crash_at is not None and self.count >= self.crash_at:
            self.crash_at = None
            self.crashed += 1
            raise Crash(f"crash before statement {self.count + 1}: {statement.strip()[:80]}")
        self.count += 1
        if self.trace is not None:
            self.trace.append(statement)


METER = Meter()


class RigConnection(sqlite3.Connection):
    """A connection that reports each statement to METER, which may crash it."""

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> sqlite3.Cursor:  # type: ignore[override]
        METER.tick(sql)
        return super().execute(sql, *args, **kwargs)

    def executemany(self, sql: str, *args: Any, **kwargs: Any) -> sqlite3.Cursor:  # type: ignore[override]
        METER.tick(sql)
        return super().executemany(sql, *args, **kwargs)

    def executescript(self, sql: str) -> sqlite3.Cursor:  # type: ignore[override]
        METER.tick(sql)
        return super().executescript(sql)


def settle_stopped(c: sqlite3.Connection) -> int:
    """Tico's own recovery of a run that stopped with nothing done (execution.auto_reconcile: a run that used no tool
    and no OK is safe to repeat, and is settled on the next sweep). The rig's bots do nothing in a run that crashes, so
    its job is settled before each never-stuck pass instead of being held for a person (task_wakes.RUN_HELD); settled
    as left, so the ladder wakes the bot again ("cut off"). A run held for a check (`job.held`) waits for its review,
    as on a hub. Returns how many it settled."""
    return c.execute(
        "UPDATE jobs SET state='canceled' WHERE state='uncertain' AND NOT EXISTS (SELECT 1 FROM events e "
        "WHERE e.action='job.held' AND e.target=jobs.attempt_id)"
    ).rowcount


def ts(at: dt.datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def parse(value: str) -> dt.datetime:
    return dt.datetime.strptime(value.rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=dt.timezone.utc)


class Ids:
    """Seeded ids for uuid.uuid4 and both uuid7 makers, put back on `close`."""

    def __init__(self, rng: random.Random) -> None:
        from backend import hubdb, threads

        self.rng = rng
        self.saved = (uuid.uuid4, hubdb.uuid7, threads.uuid7)
        uuid.uuid4 = self.uuid4
        hubdb.uuid7 = threads.uuid7 = self.uuid7

    def uuid4(self) -> uuid.UUID:
        return uuid.UUID(int=self.rng.getrandbits(128), version=4)

    def uuid7(self) -> str:
        from backend import hubdb

        ms = int(parse(hubdb.now()).timestamp() * 1000)
        raw = bytearray(ms.to_bytes(6, "big") + self.rng.getrandbits(80).to_bytes(10, "big"))
        raw[6] = (raw[6] & 0x0F) | 0x70
        raw[8] = (raw[8] & 0x3F) | 0x80
        return str(uuid.UUID(bytes=bytes(raw)))

    def close(self) -> None:
        from backend import hubdb, threads

        uuid.uuid4, hubdb.uuid7, threads.uuid7 = self.saved


def _settings(folder: Path) -> Any:
    from backend.auth import Identity
    from backend.config import Settings

    registry = folder / "registry"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / "hub-access.yaml").write_text(json.dumps({"owner": "ana@acme.example"}))
    return Settings(
        db_path=folder / "hub.db",
        registry_dir=registry,
        test_identities={"ana-test": Identity(ANA, "owner", "ana@acme.example")},
    )


def _store(settings: Any) -> Any:
    from backend import store as store_module

    from backend.blobs import Blobs

    store = store_module.Store(settings)
    store.factory = RigConnection
    store.pooled = store_module._pooled(RigConnection)
    store.blobs = Blobs(settings)  # what connections carry, as media.install sets it: Jev reads a page's bytes from it
    return store


_TEMPLATES: dict[bool, Path] = {}


def _people() -> dict[str, Any]:
    return {
        "people": [
            {"id": "ana", "name": "Ana", "email": "ana@acme.example"},
            {"id": "ben", "name": "Ben", "email": "ben@acme.example"},
            {"id": "cara", "name": "Cara", "email": "cara@acme.example"},
        ]
    }


def _fill(c: sqlite3.Connection, software: bool = False) -> None:
    """People, four bots on one computer, General managed by the Manager bot, and the Work pipeline. A software company
    also has the role bots, each with its template, a finished setup, the presets the end of setup makes
    (`ensure_presets`): Product and Engineering, and its GitHub App installed."""
    from backend import bot_settings as BS
    from backend import pipeline_settings as PS
    from backend import task_engine as E
    from backend.store import H, encode

    slugs = [b.split(":", 1)[1] for b in (*BOTS, *(SOFTWARE_BOTS if software else ()))]
    H.sync_registry(c, {s: {"name": s.title(), "runtime": "fake", "status": "active"} for s in slugs}, _people())
    c.execute(
        "INSERT OR REPLACE INTO registry_metadata(key, value) VALUES('people', ?)",
        (encode(_people()),),
    )
    c.execute(
        "INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES('r1','box','ana','h',?)",
        (H.now(),),
    )
    for slug in slugs:
        c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)", (slug, None, BOT_OWNERS[slug]))
        config: dict[str, Any] = {"name": slug.title(), "runtime": "fake", "status": "active"}
        if slug in TICO_MODEL:
            config["model"] = TICO_MODEL[slug]
        if software and f"bot:{slug}" in ROLE_TEMPLATES:
            config["template"] = ROLE_TEMPLATES[f"bot:{slug}"]
        BS.write(c, slug, config)
        c.execute("INSERT OR REPLACE INTO assignments VALUES(?,?,1,?,'human:ana')", (slug, "r1", H.now()))
    general = PS.preset(c, GENERAL)
    general["manager_id"] = MANAGER
    PS.save(c, E.SYSTEM, general)
    PS.save(c, E.SYSTEM, json.loads(json.dumps(WORK_RECORD)))
    if software:
        # Setup finished (Product and Engineering are made then, not at a first start), with no answer to "Software is
        # the product": a blank is yes.
        from backend import onboarding as O

        O.Onboarding._store(c, {**O.load(c), "completed": H.now()}, "human:ana")
        PS.ensure_presets(c)
        from backend.tests.helpers import engine as X

        X.github_installed(c)


def template(software: bool = False) -> Path:
    """The template database, built once per process for each kind of company."""
    found = _TEMPLATES.get(software)
    if found is not None and found.exists():
        return found
    folder = Path(tempfile.mkdtemp(prefix="never-stuck-template-"))
    settings = _settings(folder)
    travel = time_machine.travel(START - dt.timedelta(days=1), tick=False)
    travel.start()
    ids = Ids(random.Random(0))
    env = offline()
    env.__enter__()
    try:
        store = _store(settings)
        store.initialize()
        with store.transaction() as c:
            _fill(c, software)
        _close(store)
        with sqlite3.connect(settings.db_path) as raw:
            raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        raw.close()
    finally:
        env.__exit__(None, None, None)
        ids.close()
        travel.stop()
    _TEMPLATES[software] = settings.db_path
    return settings.db_path


def _close(store: Any) -> None:
    with store.pool_lock:
        idle, store.pool[:] = list(store.pool), []
    for c in idle:
        c.close()


class Refusal:
    """A write the engine refused, as the API answers it."""

    def __init__(self, rule: str, detail: str) -> None:
        self.rule, self.detail = rule, detail

    def __repr__(self) -> str:
        return f"Refusal({self.rule}: {self.detail})"

    def __bool__(self) -> bool:
        return False


class World:
    """One run. Use as a context manager: it starts the clock and the ids, copies the template, and stops all three."""

    def __init__(self, seed: int, software: bool = False) -> None:
        self.seed = seed
        self.software = software
        self.rng = random.Random(seed)
        self.folder = Path(tempfile.mkdtemp(prefix=f"never-stuck-{seed}-"))
        self.settings: Any = None
        self.store: Any = None
        self.travel: Any = None
        self.clock: Any = None
        self.ids: Ids | None = None
        self.env: Any = None
        self.restarts = 0

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self) -> "World":
        self.env = offline()
        self.env.__enter__()
        source = template(self.software)
        self.settings = _settings(self.folder)
        with sqlite3.connect(source) as src, sqlite3.connect(self.settings.db_path) as dst:
            src.backup(dst)
        src.close()
        dst.close()
        self.travel = time_machine.travel(START, tick=False)
        self.clock = self.travel.start()
        self.ids = Ids(random.Random(self.rng.random()))
        METER.disarm()
        self.store = _store(self.settings)
        return self

    def __exit__(self, *exc: object) -> None:
        METER.disarm()
        if self.store is not None:
            _close(self.store)
        if self.ids is not None:
            self.ids.close()
        if self.travel is not None:
            self.travel.stop()
        if self.env is not None:
            self.env.__exit__(None, None, None)
        shutil.rmtree(self.folder, ignore_errors=True)

    def restart(self) -> None:
        """The server starts again after a crash: a new Store, so nothing kept in memory survives."""
        _close(self.store)
        self.store = _store(self.settings)
        self.restarts += 1

    # ------------------------------------------------------------------ time
    def now(self) -> str:
        from backend.store import H

        return str(H.now())

    def at(self) -> dt.datetime:
        return parse(self.now())

    def shift(self, seconds: float) -> None:
        """Move the clock by `seconds`; a negative number moves it back."""
        if seconds >= 0:
            self.clock.shift(dt.timedelta(seconds=seconds))
        else:
            self.clock.move_to(self.at() + dt.timedelta(seconds=seconds))

    # ------------------------------------------------------------------ writes
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.store.transaction() as c:
            yield c

    def read(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        with self.store.read() as c:
            return fn(c)

    def act(self, actor: str, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """One write by `actor`, as an API call makes it: all of it or none of it, and a refusal is recorded (it counts
        toward quarantine as the server counts it). Returns what `fn` returned, or a Refusal."""
        from backend import store as store_module
        from backend.auth import Identity
        from backend.store import H

        identity = Identity(actor, "bot" if actor.startswith("bot:") else "human")
        out: Any = None
        with self.store.transaction() as c:
            c.execute("SAVEPOINT domain_write")
            try:
                out = fn(c)
                c.execute("RELEASE domain_write")
            except H.Refused as exc:
                problem = store_module.refused(c, identity, exc)
                out = Refusal(str(exc.rule), str(problem.detail))
        return out

    def task_row(self, task_id: str) -> dict[str, Any] | None:
        from backend import task_engine as E

        return self.read(lambda c: E.task(c, task_id))

    def new_task(
        self,
        by: str,
        title: str,
        pipeline: str = GENERAL,
        assignee: str | None = None,
        parent: str | None = None,
    ) -> Any:
        """A task in the first stage of `pipeline`, made the way every task is (`H.task_create`), given to `assignee`
        or to whoever files it. Returns its id, or a Refusal."""
        from backend.store import H

        def make(c: sqlite3.Connection) -> str:
            row = H.task_create(
                c,
                by,
                title,
                "What to do and how to tell it is done.",
                assignee or by,
                parent_id=parent,
                pipeline=pipeline,
                lint=False,
            )
            return str(row["id"])

        return self.act(by, make)
