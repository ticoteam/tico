"""Helpers that other tests share, moved out of test_hubdb.py so they import no test file."""

import tempfile
import unittest
import unittest.mock
from pathlib import Path
from backend import hubdb as H


EMPLOYEES = {
    "coo": {
        "display_name": "COO",
        "status": "active",
        "host": "keeper",
        "runtime": "codex",
        "model": "gpt-6-astra",
        "reasoning_effort": "medium",
        "dir": "/tmp/emp-coo",
    },
    "cmo": {"display_name": "CMO", "status": "active", "host": "keeper", "runtime": "codex"},
    "seo": {"display_name": "SEO", "status": "active", "runtime": "grok"},
    "analytics": {"display_name": "Analytics", "status": "active"},
    "legal": {"display_name": "Legal", "status": "active"},
    "game": {"display_name": "Project Game", "status": "planned"},
    "coach": {"display_name": "Coach", "status": "paused"},
}


PEOPLE = {
    "people": [
        {
            "id": "ana",
            "name": "Ana Rivera",
            "email": "ana@acme.example",
            "team": "leadership",
            "primary_for": ["marketing"],
        },
        {"id": "ben", "name": "Ben", "email": "ben@acme.example", "team": "product", "primary_for": ["product"]},
    ]
}


ANA = H.human_actor("ana")


CMO = H.bot_actor("cmo")


# The run table as an install from before migration 0072 had it: a run's record in `turns` (the attempts table keeps
# the lease). Its usage went to usage_facts in migration 0053, which also dropped turn_usage_segments.
LEGACY_TURNS = (
    "CREATE TABLE turns (id TEXT PRIMARY KEY, bot TEXT, thread_id TEXT, started_at TEXT, finished_at TEXT, "
    "trigger TEXT, message_id TEXT, task_id TEXT, exit TEXT, summary TEXT, input_tokens INTEGER, "
    "cached_tokens INTEGER, output_tokens INTEGER, model TEXT, provider TEXT, est_cost_usd REAL, "
    "billing TEXT, trigger_kind TEXT, trigger_source TEXT, writes INTEGER, outcome TEXT, "
    "did_nothing INTEGER, purpose TEXT)"
)


def with_legacy_run_tables(conn):
    """Put back `turns` (empty) on a file `H.connect` made, and forget migration 0072, so
    the numbered steps and the migrations run on them again as on an install from before it."""
    conn.execute("DELETE FROM schema_migrations WHERE version>='0072' AND version NOT LIKE 'legacy%'")
    conn.execute(LEGACY_TURNS)
    conn.commit()


class HubCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.conn = H.connect(Path(self.dir.name) / "hub.db")
        self.addCleanup(self.conn.close)
        H.sync_registry(self.conn, EMPLOYEES, PEOPLE)

    def before(self, version):
        """The database as an install from before migration `version` left it: H.connect has applied every migration
        file (hubdb.migrate), so the records from that one on are forgotten and migrations.apply runs them again."""
        self.conn.execute("DELETE FROM schema_migrations WHERE version>=? AND version NOT LIKE 'legacy%'", (version,))
        self.conn.commit()

    def refused(self, rule, fn, *a, **kw):
        with self.assertRaises(H.Refused) as got:
            fn(*a, **kw)
        self.assertEqual(got.exception.rule, rule, got.exception.detail)
        return got.exception
