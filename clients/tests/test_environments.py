"""Creating and removing a company environment (clients/environments.py).

Everything runs against a temporary environments root and a temporary HOME, and the database
seed is replaced: no backend process starts and nothing touches the operator's own setup.
"""

import json
import os
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clients import environments as ENV

OWNER = {"owner_email": "dana@acme.test", "owner_name": "Dana Ruiz"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patch = mock.patch.dict(os.environ, {"TICO_ENVIRONMENTS_DIR": str(self.root / "environments"),
                                             "HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)
        (self.root / "home").mkdir()
        self.seeded = []
        self.real_seed = ENV.seed_database
        seed = mock.patch.object(ENV, "seed_database",
                                 lambda directory, workspace, values, **kw: self.seeded.append(values) or ["coo", "sales"])
        seed.start()
        self.addCleanup(seed.stop)

    def create(self, slug="acme", **kwargs):
        kwargs.setdefault("providers", "anthropic")
        return ENV.create(slug, company=kwargs.pop("company", "Acme"), **OWNER, **kwargs)


class Providers(Base):

    def test_the_choice_reaches_the_server_env_and_fills_what_was_left_out(self):
        report = self.create(providers="openai,anthropic", default_runtime="claude")
        self.assertEqual(report["environment"]["providers"],
                         {"enabled": ["openai", "anthropic"], "runtime": "claude", "model": "claude-opus-5-5"})
        env = dict(line.split("=", 1) for line in (ENV.path("acme") / "server.env").read_text().splitlines())
        self.assertEqual((env["TICO_ENABLED_PROVIDERS"], env["TICO_DEFAULT_RUNTIME"], env["TICO_DEFAULT_MODEL"]),
                         ("openai,anthropic", "claude", "claude-opus-5-5"))

class Remove(Base):
    def test_removing_refuses_while_the_company_still_has_data(self):
        self.create()
        (ENV.path("acme") / "hub.sqlite").write_text("")
        with self.assertRaises(ValueError) as refused:
            ENV.remove("acme")
        self.assertIn("--delete-data", str(refused.exception))
        self.assertTrue(ENV.path("acme").exists())
