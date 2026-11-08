"""Upgrade and preservation (e2e/README.md "upgrade"): a populated database at the current schema is copied, upgraded
by the same `Store.initialize()` a server runs at boot, and must keep every row, every column and every viewer's view."""
import sqlite3
from contextlib import closing

import pytest

from backend.store import Store
from backend.tests.test_api import api  # noqa: F401  (the fixture)
from e2e import renames, scenario
from e2e.preserve import compare, integrity, table_state
from e2e.server import serve, settings_for, snapshot


def visible(view, universe):
    """A viewer's readable ids, limited to `universe`: the tasks and messages the database held before the checks."""
    return {who: {kind: sorted(set(ids) & universe[kind]) for kind, ids in seen.items()} for who, seen in view.items()}


def ids_in(path):
    with closing(sqlite3.connect(path)) as conn:
        return {"tasks": {r[0] for r in conn.execute("SELECT id FROM tasks")},
                "messages": {r[0] for r in conn.execute("SELECT id FROM messages")}}


@pytest.mark.slow
def test_an_upgrade_keeps_every_row_column_and_viewers_view(api, tmp_path):  # noqa: F811
    scenario.populate(api)
    source = api.app.state.store.settings.db_path
    scenario.populate_direct(source)
    raw = tmp_path / "raw" / "hub.sqlite"
    snapshot(source, raw)
    universe = ids_in(raw)      # what the database held before any viewer check wrote its own turn
    # The fixture seeds rows after its own boot, so the baseline is a database a server has booted once: the boot
    # settles what the seeding left open, and the viewers are read from that same database.
    with serve(settings_for(tmp_path / "settle", raw)) as client:
        before_viewers = scenario.viewer_sets(client)
    populated = tmp_path / "populated" / "hub.sqlite"
    snapshot(raw, populated)
    rename_map = renames.active()
    before = table_state(populated)

    # The upgrade path: a copy of the populated database, initialized the way a server boots it.
    upgraded = tmp_path / "upgraded" / "hub.sqlite"
    snapshot(populated, upgraded)
    settings = settings_for(tmp_path / "upgrade-settings", upgraded)
    Store(settings).initialize(seed_market=False)
    assert compare(before, table_state(upgraded), rename_map) == []
    assert integrity(upgraded) == []
    first = table_state(upgraded)

    # A second initialize changes no data.
    Store(settings).initialize(seed_market=False)
    assert compare(first, table_state(upgraded), {}) == []

    # Two boots of a server on the upgraded copy (its startup runs more than initialize): nothing changes between them.
    with serve(settings_for(tmp_path / "boot-1", upgraded)):
        pass
    booted = table_state(upgraded)
    assert compare(before, booted, rename_map) == []
    with serve(settings_for(tmp_path / "boot-2", upgraded)):
        pass
    assert compare(booted, table_state(upgraded), {}) == []
    assert integrity(upgraded) == []

    # Every viewer reads the same scenario tasks and messages as before the upgrade. Each viewer check runs its own bot
    # turn, so the messages it writes are new and are left out of the comparison.
    with serve(settings_for(tmp_path / "viewers", upgraded)) as client:
        after_viewers = scenario.viewer_sets(client)
    before_seen, after_seen = visible(before_viewers, universe), visible(after_viewers, universe)
    problems = [f"{who}.{kind}: {len(set(before_seen[who][kind]) - set(after_seen[who][kind]))} no longer readable, "
                f"{len(set(after_seen[who][kind]) - set(before_seen[who][kind]))} newly readable"
                for who in before_seen for kind in ("tasks", "messages")
                if before_seen[who][kind] != after_seen[who][kind]]
    assert problems == []
