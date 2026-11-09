"""Helpers that other tests share, moved out of test_threads_core.py so they import no test file."""

import pytest
import time_machine
from backend import threads
from backend.store import H
from backend.tests.demo_template import copy_demo
from backend.tests.helpers.threads_migration_e2e import NOW, build, close, identities, open_store


@pytest.fixture(scope="module")
def demo_template(tmp_path_factory):
    """Each worker seeds once; tests only open private copies of this directory. A module that imports `world` imports
    this too."""
    # The function-scoped bot_ids_are_their_slugs fixture has not run during module setup.
    with pytest.MonkeyPatch.context() as monkeypatch, time_machine.travel(NOW, tick=False):
        monkeypatch.setattr(H, "new_bot_id", lambda slug: slug)
        return build("demo", tmp_path_factory.mktemp("threads-demo-template"))


@pytest.fixture
def world(tmp_path, demo_template):
    """A private copy of the template, read on the day it was built (including clocks outside hubdb.now)."""
    with time_machine.travel(NOW, tick=False):
        settings = copy_demo(demo_template, tmp_path / "demo")
        store, auth = open_store(settings)
        with store.transaction() as c:
            auth.sync_access(c)
            who = identities(c, auth)
        yield store, auth, who
        close(store)


def owner_of(c, auth):
    return auth.owner_identity(c)


def people(who):
    return sorted(a for a in who if a.startswith("human:"))


def made_threads(c, auth, who):
    """One stored-audience thread of each kind a person makes, as the owner, plus a bot's Home."""
    owner = threads.viewer(auth, owner_of(c, auth))
    other = next(a for a in people(who) if a != owner.actor)
    from backend import views

    groups = sorted((views.roster(c, shared=True).get("org_groups") or {}).keys())
    made = {
        "company": threads.create(c, owner, title="All hands", audience="company")["id"],
        "members": threads.create(c, owner, title="Launch", audience="members", members=[other])["id"],
        "personal": threads.create(c, owner, title="Notes", audience="personal")["id"],
    }
    if groups:
        made["team"] = threads.create(c, owner, title="Team", audience="team", audience_group_id=groups[0])["id"]
    bot = sorted(a for a in who if a.startswith("bot:"))[0]
    made["home"] = threads.home(c, bot, create=True)["id"]
    return made
