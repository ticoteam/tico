"""Creating and removing a company environment (clients/environments.py).

Everything runs against a temporary environments root and a temporary HOME, and the database
seed is replaced: no backend process starts and nothing touches the operator's own setup.
"""

import os
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


class Remove(Base):
    def test_removing_refuses_while_the_company_still_has_data(self):
        self.create()
        (ENV.path("acme") / "hub.sqlite").write_text("")
        with self.assertRaises(ValueError) as refused:
            ENV.remove("acme")
        self.assertIn("--delete-data", str(refused.exception))
        self.assertTrue(ENV.path("acme").exists())
