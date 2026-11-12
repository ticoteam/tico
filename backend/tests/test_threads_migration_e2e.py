"""The migration keeps every reader set: who can read each conversation and message, before and after the threads build.

Spec section 4 and section 9 (`_threads-plan.md`) and docs/dev/threads-contract.md section 7 move every existing
conversation onto an audience and every message onto a per-message check. This file checks that migration on the demo
world (backend/demo.py) and on a dev copy of it (backend/devcopy.py). Each world is built, its reader sets are read with
today's pre-threads predicates in the same run, then `threads.migrate(c)` runs and `threads.readable` must give exactly
the same sets. Nothing is stored: the baseline is always what the predicates read live.

Actors are everyone on the roster (the company owner as `Auth.owner_identity`) and every bot. A bot is read as an
agent with its standing credential, the way it reads outside a run: a run's grants come from its attempt and end
with the run, so the run-scoped grants belong to the run tests.

Today's readers of a conversation are the actors who pass `Auth.conversation`. A message's readers are the
conversation's readers who also pass `task_privacy.message_readable`, the per-message check. A failure lists one line
per row and actor where the migrated reader sets differ from today's.

The demo world is built once per worker that runs this module (its module fixture keeps the module on one worker:
backend/tests/worker_schedule.py) and kept untouched (`world`); each test migrates its own copy of it, so no test sees
another's migration. The build is most of the module's time, and pytest's per-test window (faulthandler_timeout) counts
fixture setup, so the first test only builds the world and checks it holds the rows the migration moves: the reader-set
tests after it each start from a built world. For the same reason the demo world's migration (`migrated_demo`) is
shared by the demo test, which checks it kept every reader set, and the roster-removal test, which starts from it. Each reader set is read in one snapshot with one viewer per actor, as
one request reads many threads (`threads.Viewer`), from rows fetched in bulk.
"""

import dataclasses
import importlib
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
import time_machine
from fastapi.testclient import TestClient

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.helpers.threads_migration_e2e import NOW, close, identities, open_store
from backend import demo, devcopy, task_privacy
from backend.app import create_app
from backend.auth import standing


@pytest.fixture(scope="module", autouse=True)
def demo_clock():
    """The demo's readers and the dev copy's retention window use the build's day."""
    with time_machine.travel(NOW, tick=False):
        yield


# ----------------------------------------------------------------------------- the worlds
@pytest.fixture(scope="module")
def world(demo_clock, tmp_path_factory):
    """The demo world, built once for the module: (its settings, its folder). Nothing opens its database; each test
    copies the folder (`copy_folder`, `devcopy_world`) and migrates the copy."""
    directory = tmp_path_factory.mktemp("threads-migration-world")
    settings = demo.build(directory / "demo", now=NOW)
    return settings, directory / "demo"


def copy_folder(world, directory):
    """A copy of the demo world's folder (database, registry, blobs, token file), with every setting that named a path
    in the original naming the same path in the copy."""
    settings, source = world
    target = directory / "demo"
    shutil.copytree(source, target)

    def moved(value):
        if isinstance(value, Path) and value.is_relative_to(source):
            return target / value.relative_to(source)
        if isinstance(value, str) and value.startswith(str(source) + "/"):
            return str(target) + value[len(str(source)) :]
        return value

    fields = [f.name for f in dataclasses.fields(settings) if f.init]
    copied = dataclasses.replace(settings, **{name: moved(getattr(settings, name)) for name in fields})
    # A test that wrote to the shared world would change what the next test starts from.
    paths = (copied.db_path, copied.registry_dir, copied.blob_dir, copied.local_owner_token_file)
    assert all(path is None or path.is_relative_to(target) for path in paths), copied
    return copied


def devcopy_world(world, directory):
    """A dev copy of the demo world, made the way a dev server gets its data. The copy keeps the demo's company
    settings and has no registry folder, as a dev copy never does: its roster lives in the database."""
    settings = copy_folder(world, directory)
    dest = directory / "devcopy" / "hub.sqlite"
    # Keep the demo's whole history, including its older weeks, so every message of the copy is compared.
    devcopy.build(settings.db_path, dest, days=3650)
    return dataclasses.replace(
        settings,
        db_path=dest,
        registry_dir=dest.parent / "registry",
        blob_dir=dest.parent / "blobs",
        demo=False,
        demo_public=False,
        local_owner_token_file=None,
        rehearsal=True,
    )


def viewers(threads, c, auth):
    """One viewer per actor for this snapshot, as one request reads many threads with one."""
    return {actor: threads.viewer(auth, who) for actor, who in identities(c, auth).items()}


# ----------------------------------------------------------------------------- today's predicates
def today_sets(settings):
    """(conversations, messages) read with the pre-threads predicates: {conversation id: readers} and
    {message id: (conversation id, readers)}. A conversation is read as Auth.conversation reads it (C1, threads.denial,
    for the actor's viewer); a message by its conversation's readers who pass the per-message check."""
    threads = importlib.import_module("backend.threads")
    store, auth = open_store(settings)
    try:
        with store.read_transaction() as c:
            auth.sync_access(c)
            who = viewers(threads, c, auth)
            conversations = {}
            for row in c.execute("SELECT * FROM conversations ORDER BY id").fetchall():
                # Every Home thread counts. (The demo patches uuid4 to a digest of its counter, so a Home thread's
                # version nibble is chance: skipping "version 4" dropped real Home threads on some seeds.)
                row = dict(row)
                conversations[row["id"]] = {
                    actor for actor, viewer in who.items() if threads.denial(c, viewer, row) is None
                }
            messages = {}
            for row in c.execute("SELECT * FROM messages ORDER BY id").fetchall():
                if row["conversation_id"] not in conversations:
                    continue
                readers = conversations[row["conversation_id"]]
                row = dict(row)
                messages[row["id"]] = (
                    row["conversation_id"],
                    {actor for actor in readers if task_privacy.message_readable(c, actor, row)},
                )
        return conversations, messages
    finally:
        close(store)


# ----------------------------------------------------------------------------- comparing
def labeled(conversations, messages):
    rows = {f"conversation {cid}": readers for cid, readers in conversations.items()}
    rows.update({f"message {mid} in conversation {cid}": readers for mid, (cid, readers) in messages.items()})
    return rows


def differences(expected, got):
    """One line per row and actor where `got` disagrees with `expected`, both {row label: readers}."""
    lines = []
    for row in sorted(set(expected) | set(got)):
        want, have = set(expected.get(row, ())), set(got.get(row, ()))
        for actor in sorted(want ^ have):
            lines.append(
                f"{row}: {actor} expected {'readable' if actor in want else 'hidden'}, "
                f"got {'readable' if actor in have else 'hidden'}"
            )
    return lines


def report(lines, world, what):
    return f"{len(lines)} reader difference(s) in the {world} world {what}:\n" + "\n".join(lines[:60])


def untouched_copy(settings, directory):
    """The world's database copied before anything reads it, so today's sets are read from the copy: opening a Store and
    reading through Auth must not leave state behind for the migration to find."""
    copy = directory / "baseline" / "hub.sqlite"
    copy.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(settings.db_path)) as source, closing(sqlite3.connect(copy)) as target:
        source.backup(target)
    return dataclasses.replace(settings, db_path=copy)


# ----------------------------------------------------------------------------- the migration
def migrate(settings, threads):
    store, _ = open_store(settings)
    try:
        with store.transaction() as c:
            threads.migrate(c)
    finally:
        close(store)


def threads_sets(threads, settings, expected):
    """The reader sets threads reads, after `migrate`, for every conversation and message in `expected` (today's sets,
    from `today_sets`), each checked for every actor. A row missing after the migration reads as unreadable by
    everyone."""
    store, auth = open_store(settings)
    try:
        with store.read_transaction() as c:
            who = viewers(threads, c, auth)
            threads_by_id = {row["id"]: dict(row) for row in c.execute("SELECT * FROM conversations")}
            messages_by_id = {row["id"]: dict(row) for row in c.execute("SELECT * FROM messages")}
            conversations = {}
            for cid in expected[0]:
                row = threads_by_id.get(cid)
                # thread_readable is C1 (threads.denial) kept per viewer, which each message's check below asks again.
                conversations[cid] = (
                    {actor for actor, viewer in who.items() if threads.thread_readable(c, viewer, row)}
                    if row
                    else set()
                )
            messages = {}
            for mid, (cid, _) in expected[1].items():
                row = messages_by_id.get(mid)
                messages[mid] = (
                    cid,
                    {actor for actor, viewer in who.items() if threads.readable(c, viewer, row)} if row else set(),
                )
        return conversations, messages
    finally:
        close(store)


def migrated(settings, directory):
    """Today's reader sets read (on an untouched copy), then the world migrated and its sets read again: (today's sets,
    the sets after the migration)."""
    threads = importlib.import_module("backend.threads")
    expected = today_sets(untouched_copy(settings, directory))
    migrate(settings, threads)
    return expected, threads_sets(threads, settings, expected)


def assert_kept(expected, got, world_name):
    lines = differences(labeled(*expected), labeled(*got))
    assert not lines, report(lines, world_name, "after threads.migrate (each line: expected before, got after)")


@pytest.fixture(scope="module")
def migrated_demo(world, tmp_path_factory):
    """The demo world migrated once: (its settings, its folder, today's sets, the sets after the migration). The demo
    test checks the two sets match; the removal test starts from a copy of the migrated folder, so each test's window
    holds only its own reads."""
    directory = tmp_path_factory.mktemp("threads-migration-demo")
    settings = copy_folder(world, directory)
    return (settings, directory / "demo", *migrated(settings, directory))


def test_the_demo_world_holds_every_legacy_shape_the_migration_moves(world, tmp_path):
    """Runs first, so the world's build lands in this test's window and not in a reader-set test's. The reader-set
    tests prove only as much as the world holds: legacy roots (no stored audience) of each scope, task threads and
    per-message restricted rows are what `threads.migrate` moves, so a demo that lost them would let a widening pass."""
    with closing(sqlite3.connect(untouched_copy(world[0], tmp_path).db_path)) as c:
        roots = {
            scope
            for (scope,) in c.execute(
                "SELECT DISTINCT scope FROM conversations WHERE audience IS NULL AND COALESCE(root_id,id)=id"
            )
        }
        restricted = c.execute("SELECT COUNT(*) FROM messages WHERE restricted=1").fetchone()[0]
    assert {"direct", "personal", "task"} <= roots, roots
    assert restricted, "no per-message restricted rows"


def test_the_devcopy_world_keeps_every_reader_set(world, tmp_path):
    assert_kept(*migrated(devcopy_world(world, tmp_path), tmp_path), "devcopy")


def test_the_demo_world_keeps_every_reader_set(migrated_demo):
    _, _, expected, got = migrated_demo
    assert_kept(expected, got, "demo")


def test_a_roster_removal_after_the_migration_widens_no_reader_set(migrated_demo, tmp_path):
    """Taking someone off the org chart after the migration may narrow reader sets but never widens them: the roster is
    read live, as it is today. Ben has no reports (the bots report to the Assistant), so his removal moves nobody else's
    access. He is primary for the support and inbox bots, the people whose shared rooms would change."""
    settings, folder, expected, before = migrated_demo
    settings = copy_folder((settings, folder), tmp_path)
    api = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    api.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    reply = api.post("/api/v2/people/ben", json={"left": True}, headers={"Idempotency-Key": "roster-ben-left"})
    assert reply.status_code == 200, reply.text[:300]
    store, _ = open_store(settings)
    try:
        with store.read_transaction() as c:
            assert standing(c, "ben") == "left"
    finally:
        close(store)
    after = threads_sets(importlib.import_module("backend.threads"), settings, expected)
    # Compared with the reader sets just before the removal, so only what the removal itself did is counted.
    widened = [
        line for line in differences(labeled(*before), labeled(*after)) if "expected hidden, got readable" in line
    ]
    assert not widened, report(widened, "demo", "after a roster removal (each line: before, got after)")
