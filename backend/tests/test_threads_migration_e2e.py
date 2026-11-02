"""The migration keeps every reader set: who can read each conversation and message, before and after the threads build.

Spec section 4 and section 9 (`_threads-plan.md`) and docs/dev/threads-contract.md section 7 move every existing
conversation onto an audience and every message onto a per-message check. This file pins who can read what today,
on the demo world (backend/demo.py) and on a dev copy of it (backend/devcopy.py), so the migration is checked against
that baseline rather than against itself.

Actors are everyone on the roster (the company owner as `Auth.owner_identity`) and every bot. A bot is read as an
agent with its standing credential, the way it reads outside a run: a run's grants come from its attempt and end
with the run, so the run-scoped grants belong to the run tests.

Today's readers of a conversation are the actors who pass `Auth.conversation`. A message's readers are the
conversation's readers who also pass `task_privacy.message_readable`, the per-message check. The golden
(backend/tests/fixtures/threads/reader-sets-<world>.json) holds `{"conversations": {id: [actors]}, "messages":
{id: [actors]}}`, with keys and actor lists sorted. A message is written only where its readers differ from its
conversation's, so the common case is one line per conversation and a message hidden from some reader stands out.
The file is written one entry per line, so a change shows as the lines it touched.

Regenerate after a deliberate change to who reads what, and review the diff:
    TICO_WRITE_READER_GOLDENS=1 python3 -m pytest -q backend/tests/test_threads_migration_e2e.py

The comparisons run `threads.migrate(c)` and assert `threads.readable` gives the same sets. Until
backend/threads.py exists they skip; nothing is faked. The freshness test runs now: it fails when the golden no longer
matches what today's predicates read, so a golden is never compared against a stale baseline.
"""

import dataclasses
import importlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import demo, devcopy, task_privacy
from backend.app import create_app
from backend.auth import Auth, Identity, standing
from backend.store import H, Problem, Store

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)      # the instant test_demo.py builds the demo at
GOLDENS = Path(__file__).resolve().parent / "fixtures" / "threads"
WRITE = os.environ.get("TICO_WRITE_READER_GOLDENS") == "1"
WORLDS = ("demo", "devcopy")
SKIP = "not built yet: threads core (docs/dev/threads-contract.md section 2.4)"
AGENT = "standing"          # the harness label of a bot's standing credential; Auth.conversation only checks it is set


# ----------------------------------------------------------------------------- the worlds
def build(world, directory):
    """The demo world, or a dev copy of it made the way a dev server gets its data. The copy keeps the demo's company
    settings and has no registry folder, as a dev copy never does: its roster lives in the database."""
    settings = demo.build(directory / "demo", now=NOW)
    if world == "demo":
        return settings
    dest = directory / "devcopy" / "hub.sqlite"
    # devcopy cuts history back from the real clock, while the demo's history is dated from NOW: a 30-day window
    # would drop every message. Ten years keeps all of it, so the copy's messages are the ones the golden pins.
    devcopy.build(settings.db_path, dest, days=3650)
    return dataclasses.replace(settings, db_path=dest, registry_dir=dest.parent / "registry",
                               blob_dir=dest.parent / "blobs", demo=False, demo_public=False,
                               local_owner_token_file=None, rehearsal=True)


def threads_module():
    """`backend.threads` once the threads core has landed, else None."""
    try:
        return importlib.import_module("backend.threads")
    except ModuleNotFoundError as missing:
        if missing.name != "backend.threads":
            raise
        return None


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


def message_owners(settings):
    """{message id: conversation id} for every message in the world, deleted ones included."""
    store, _ = open_store(settings)
    try:
        with store.read_transaction() as c:
            return {row["id"]: row["conversation_id"] for row in c.execute("SELECT id,conversation_id FROM messages")}
    finally:
        close(store)


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
                if row["audience"] == "home" and uuid.UUID(row["id"]).version == 4:
                    continue    # a Home threads made for itself while the demo seeded (an update card): not legacy data
                conversations[row["id"]] = {actor for actor, identity in who.items()
                                            if can_read(auth, c, identity, row["id"])}
            messages = {}
            for row in c.execute("SELECT * FROM messages ORDER BY id").fetchall():
                if row["conversation_id"] not in conversations:
                    continue
                readers = conversations[row["conversation_id"]]
                messages[row["id"]] = (row["conversation_id"], {
                    actor for actor in readers if task_privacy.message_readable(c, actor, dict(row))})
        return conversations, messages
    finally:
        close(store)


# ----------------------------------------------------------------------------- the golden form
def golden_form(conversations, messages):
    """The golden: each conversation's readers, and each message's only where they differ from its conversation's."""
    return {"conversations": {cid: sorted(readers) for cid, readers in conversations.items()},
            "messages": {mid: sorted(readers) for mid, (cid, readers) in messages.items()
                         if readers != conversations.get(cid, set())}}


def full_from_golden(golden, owner_of):
    """The golden expanded back to every conversation and message of the world (`owner_of`, from the same world)."""
    conversations = {cid: set(readers) for cid, readers in golden["conversations"].items()}
    messages = {mid: (cid, set(golden["messages"].get(mid, golden["conversations"].get(cid, []))))
                for mid, cid in owner_of.items() if cid in conversations}   # a skipped Home's messages stay out too
    return conversations, messages


def labeled(conversations, messages):
    rows = {f"conversation {cid}": readers for cid, readers in conversations.items()}
    rows.update({f"message {mid} in conversation {cid}": readers for mid, (cid, readers) in messages.items()})
    return rows


def dump(golden):
    """The golden as JSON, one entry per line with keys and actors sorted, so two writer runs give the same bytes."""
    sections = []
    for name in ("conversations", "messages"):
        entries = [f"{json.dumps(key)}:{json.dumps(readers, separators=(',', ':'))}"
                   for key, readers in sorted(golden[name].items())]
        sections.append(f'"{name}":{{' + ("\n" + ",\n".join(entries) + "\n" if entries else "") + "}")
    return "{\n" + ",\n".join(sections) + "\n}\n"


def golden_path(world):
    return GOLDENS / f"reader-sets-{world}.json"


def differences(expected, got):
    """One line per row and actor where `got` disagrees with `expected`, both {row label: readers}."""
    lines = []
    for row in sorted(set(expected) | set(got)):
        want, have = set(expected.get(row, ())), set(got.get(row, ()))
        for actor in sorted(want ^ have):
            lines.append(f"{row}: {actor} expected {'readable' if actor in want else 'hidden'}, "
                         f"got {'readable' if actor in have else 'hidden'}")
    return lines


def report(lines, world, what):
    return f"{len(lines)} reader difference(s) in the {world} world {what}:\n" + "\n".join(lines[:60])


# ----------------------------------------------------------------------------- the freshness check (always runs)
@pytest.mark.parametrize("world", WORLDS)
def test_goldens_are_todays_reader_sets(world, tmp_path):
    """The goldens are what today's predicates read: writer mode regenerates them, and otherwise this fails when they
    no longer match, so the migration is never compared with a stale baseline."""
    settings = build(world, tmp_path)
    conversations, messages = today_sets(settings)
    now = golden_form(conversations, messages)
    if WRITE:
        GOLDENS.mkdir(parents=True, exist_ok=True)
        golden_path(world).write_text(dump(now))
        return
    assert golden_path(world).exists(), (
        f"no golden at {golden_path(world)}: regenerate with TICO_WRITE_READER_GOLDENS=1")
    stored = json.loads(golden_path(world).read_text())
    lines = differences(labeled(*full_from_golden(stored, message_owners(settings))), labeled(conversations, messages))
    assert not lines, report(lines, world, "against today's predicates; regenerate with TICO_WRITE_READER_GOLDENS=1")
    assert golden_path(world).read_text() == dump(now), "the golden is not in writer form; regenerate it"


# ----------------------------------------------------------------------------- the migration (skips until threads)
def migrate(settings, threads):
    store, _ = open_store(settings)
    try:
        with store.transaction() as c:
            threads.migrate(c)
    finally:
        close(store)


def threads_sets(threads, settings, expected):
    """The reader sets threads reads, after `migrate`, for every conversation and message in `expected` (a full form
    of the world, `full_from_golden`). A row missing after the migration reads as unreadable by everyone."""
    store, auth = open_store(settings)
    try:
        conversations, messages = {}, {}
        with store.read_transaction() as c:
            viewers = {actor: threads.viewer(auth, who) for actor, who in identities(c, auth).items()}
            for cid in expected[0]:
                row = c.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
                conversations[cid] = {actor for actor, viewer in viewers.items()
                                      if row is not None and threads.readable(c, viewer, dict(row))}
            for mid, (cid, _) in expected[1].items():
                row = c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
                messages[mid] = (cid, {actor for actor, viewer in viewers.items()
                                       if row is not None and threads.readable(c, viewer, dict(row))})
        return conversations, messages
    finally:
        close(store)


@pytest.mark.parametrize("world", WORLDS)
def test_migration_keeps_every_reader_set(world, tmp_path):
    threads = threads_module()
    if threads is None:
        pytest.skip(SKIP)
    settings = build(world, tmp_path)
    expected = full_from_golden(json.loads(golden_path(world).read_text()), message_owners(settings))
    migrate(settings, threads)
    got = threads_sets(threads, settings, expected)
    lines = differences(labeled(*expected), labeled(*got))
    assert not lines, report(lines, world, "after threads.migrate (each line: expected before, got after)")


def test_removing_a_person_does_not_widen_any_reader_set(tmp_path):
    """Taking someone off the org chart after the migration may narrow reader sets but never widens them: the roster is
    read live, as it is today. Ben has no reports (the bots report to the Assistant), so his removal moves nobody else's
    access. He is primary for the support and inbox bots, the people whose shared rooms would change."""
    threads = threads_module()
    if threads is None:
        pytest.skip(SKIP)
    settings = demo.build(tmp_path / "demo", now=NOW)
    expected = full_from_golden(json.loads(golden_path("demo").read_text()), message_owners(settings))
    migrate(settings, threads)
    before = threads_sets(threads, settings, expected)
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
    widened = [line for line in differences(labeled(*before), labeled(*after))
               if "expected hidden, got readable" in line]
    assert not widened, report(widened, "demo", "after a roster removal (each line: before, got after)")
