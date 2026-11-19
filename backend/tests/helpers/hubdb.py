"""Helpers that other tests share, moved out of test_hubdb.py so they import no test file."""

import tempfile
import unittest
from pathlib import Path
from backend import hubdb as H
from backend.tests.helpers.migrations import forget_from


EMPLOYEES = {
    "assistant": {
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


class HubCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        # The tables the task engine writes (assignments, pipelines, jobs) are made when the store starts, not by
        # hubdb's migrations: start one on the file first.
        from backend.store import Store
        from backend.tests.helpers.api import api_settings

        Store(api_settings(Path(self.dir.name))).initialize()
        self.conn = H.connect(Path(self.dir.name) / "hub.db")
        self.addCleanup(self.conn.close)
        H.sync_registry(self.conn, EMPLOYEES, PEOPLE)

    def before(self, version):
        """The database as an install from before migration `version` left it: H.connect has applied every migration
        file (hubdb.migrate), so the records from that one on are forgotten and migrations.apply runs them again."""
        forget_from(self.conn, version)
        self.conn.commit()

    def refused(self, rule, fn, *a, **kw):
        with self.assertRaises(H.Refused) as got:
            fn(*a, **kw)
        self.assertEqual(got.exception.rule, rule, got.exception.detail)
        return got.exception
