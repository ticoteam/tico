"""Helpers that other tests share, moved out of test_threads_migration_e2e.py so they import no test file."""

import dataclasses
from datetime import datetime, timezone
from backend import demo, devcopy
from backend.auth import Auth, Identity
from backend.store import H, Store


NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)  # the instant test_demo.py builds the demo at


AGENT = "standing"  # the harness label of a bot's standing credential; Auth.conversation only checks it is set


# ----------------------------------------------------------------------------- the worlds
def build(world, directory):
    """The demo world, or a dev copy of it made the way a dev server gets its data. The copy keeps the demo's company
    settings and has no registry folder, as a dev copy never does: its roster lives in the database."""
    settings = demo.build(directory / "demo", now=NOW)
    if world == "demo":
        return settings
    dest = directory / "devcopy" / "hub.sqlite"
    # Keep the demo's whole history, including its older weeks, so the copy's messages are the ones the golden pins.
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
