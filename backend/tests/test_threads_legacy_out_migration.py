"""Migration 20261116172132_threads_legacy_out: a database made now has the message indexes the threads migration used
to build (one read names messages_thread_human with INDEXED BY), and running it again changes nothing."""

import importlib

from backend.tests.helpers.api import headers  # noqa: F401  (the api fixture)

MIGRATION = importlib.import_module("backend.migrations.20261116172132_threads_legacy_out")
NAMES = {"messages_seq", "messages_origin", "messages_thread_seq", "messages_thread_human", "messages_legacy_audience"}


def indexes(c):
    return {r[0]: r[1] for r in c.execute("SELECT name, sql FROM sqlite_master WHERE type='index'")}


def test_a_new_database_has_the_message_indexes_and_a_second_run_changes_nothing(api):
    with api.app.state.store.transaction() as c:
        before = indexes(c)
        assert NAMES <= set(before)
        MIGRATION.up(c)
        assert indexes(c) == before
