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
"""

import dataclasses
import importlib
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pytest
import time_machine
from fastapi.testclient import TestClient

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import demo, devcopy, task_privacy
from backend.app import create_app
from backend.auth import Auth, Identity, standing
from backend.store import H, Problem, Store

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)  # the instant test_demo.py builds the demo at
AGENT = "standing"  # the harness label of a bot's standing credential; Auth.conversation only checks it is set


@pytest.fixture(scope="module", autouse=True)
def demo_clock():
    """The demo's readers and the dev copy's retention window use the build's day."""
    with time_machine.travel(NOW, tick=False):
        yield


# ----------------------------------------------------------------------------- the worlds
def build(world, directory):
    """The demo world, or a dev copy of it made the way a dev server gets its data. The copy keeps the demo's company
    settings and has no registry folder, as a dev copy never does: its roster lives in the database."""
    settings = demo.build(directory / "demo", now=NOW)
    if world == "demo":
        return settings
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


def open_store(settings):
    store = Store(settings)
    return store, Auth(store)


def close(store):
    for c in store.pool:
        c.close()
    store.pool.clear()


def identities(c, auth):
    """Every actor's identity, built as the request path builds it: the owner as the owner, other people as people,
    bots as agents."""
    owner = auth.owner_identity(c)
    who = {owner.actor: owner}
    for person in H.humans(c):
        actor = "human:" + person["id"]
        who.setdefault(actor, Identity(actor, "human", email=str(person.get("email") or "").lower()))
    for bot in H.bots(c):
        actor = H.bot_actor(bot["slug"])
        who[actor] = Identity(actor, "bot", agent=AGENT)
    return who


# ----------------------------------------------------------------------------- today's predicates
def can_read(auth, c, who, conversation_id):
    try:
        auth.conversation(c, who, conversation_id)
    except Problem:
        return False
    return True


def today_sets(settings):
    """(conversations, messages) read with the pre-threads predicates: {conversation id: readers} and
    {message id: (conversation id, readers)}."""
    store, auth = open_store(settings)
    try:
        with store.read_transaction() as c:
            auth.sync_access(c)
            who = identities(c, auth)
            conversations = {}
            for row in c.execute("SELECT id,audience FROM conversations ORDER BY id").fetchall():
                # Every Home thread counts. (The demo patches uuid4 to a digest of its counter, so a Home thread's
                # version nibble is chance: skipping "version 4" dropped real Home threads on some seeds.)
                conversations[row["id"]] = {
                    actor for actor, identity in who.items() if can_read(auth, c, identity, row["id"])
                }
            messages = {}
            for row in c.execute("SELECT * FROM messages ORDER BY id").fetchall():
                if row["conversation_id"] not in conversations:
                    continue
                readers = conversations[row["conversation_id"]]
                messages[row["id"]] = (
                    row["conversation_id"],
                    {actor for actor in readers if task_privacy.message_readable(c, actor, dict(row))},
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
    from `today_sets`). A row missing after the migration reads as unreadable by everyone."""
    store, auth = open_store(settings)
    try:
        conversations, messages = {}, {}
        with store.read_transaction() as c:
            viewers = {actor: threads.viewer(auth, who) for actor, who in identities(c, auth).items()}
            for cid in expected[0]:
                row = c.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
                conversations[cid] = {
                    actor
                    for actor, viewer in viewers.items()
                    if row is not None and threads.readable(c, viewer, dict(row))
                }
            for mid, (cid, _) in expected[1].items():
                row = c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
                messages[mid] = (
                    cid,
                    {
                        actor
                        for actor, viewer in viewers.items()
                        if row is not None and threads.readable(c, viewer, dict(row))
                    },
                )
        return conversations, messages
    finally:
        close(store)


def migrated_world(world, tmp_path):
    """One build of `world`: its reader sets are read with today's predicates (on a copy), then it is migrated and must
    keep every reader set. Returns the migrated settings, the threads module, today's sets and the sets after the
    migration."""
    threads = importlib.import_module("backend.threads")
    settings = build(world, tmp_path)
    expected = today_sets(untouched_copy(settings, tmp_path))
    migrate(settings, threads)
    got = threads_sets(threads, settings, expected)
    lines = differences(labeled(*expected), labeled(*got))
    assert not lines, report(lines, world, "after threads.migrate (each line: expected before, got after)")
    return settings, threads, expected, got


def test_the_devcopy_world_keeps_every_reader_set(tmp_path):
    migrated_world("devcopy", tmp_path)


def test_the_demo_world_keeps_every_reader_set_and_a_removal_widens_none(tmp_path):
    """Taking someone off the org chart after the migration may narrow reader sets but never widens them: the roster is
    read live, as it is today. Ben has no reports (the bots report to the Assistant), so his removal moves nobody else's
    access. He is primary for the support and inbox bots, the people whose shared rooms would change."""
    settings, threads, expected, before = migrated_world("demo", tmp_path)
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
    after = threads_sets(threads, settings, expected)
    # Compared with the reader sets just before the removal, so only what the removal itself did is counted.
    widened = [
        line for line in differences(labeled(*before), labeled(*after)) if "expected hidden, got readable" in line
    ]
    assert not widened, report(widened, "demo", "after a roster removal (each line: before, got after)")
