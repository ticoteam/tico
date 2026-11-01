"""A second start of the server rewrites almost none of the database. Litestream uploads the pages a start changes, so a
start that rewrites most of the file uploads most of it on every restart (scripts/page_churn.py)."""
import importlib.util
import os
import shutil
import subprocess
import sys

import pytest

from backend.config import ROOT
from backend.tests.test_replication import prepare

spec = importlib.util.spec_from_file_location("page_churn", ROOT / "scripts" / "page_churn.py")
page_churn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(page_churn)

ENVIRONMENT_ID = "page-churn"

# The app's start-up and shutdown (the lifespan in backend/app.py), in a process of its own: every connection is closed
# when it exits, so the file on disk is the whole database, as Litestream sees it between restarts.
LIFESPAN = """
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.config import Settings
with TestClient(create_app(Settings.from_env())):
    pass
"""


def start(data, backups):
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", ""), "PYTHONPATH": str(ROOT),
           "TICO_DATA_DIR": str(data), "TICO_DB": str(data / "hub.sqlite"), "TICO_REGISTRY_DIR": str(data / "registry"),
           "TICO_BLOB_DIR": str(data / "blobs"), "TICO_BACKUP_DIR": str(backups),
           "TICO_TEAM_NAME": "Acme", "TICO_OWNER_EMAIL": "owner@example.com", "TICO_APP_NAME": "Acme",
           "TICO_ASSISTANT_NAME": "Assistant", "TICO_ENVIRONMENT_ID": ENVIRONMENT_ID,
           "TICO_PUBLIC_URL": "http://127.0.0.1:8765", "TICO_LOCAL_OWNER_TOKEN_FILE": str(data / "local-owner.token"),
           "TICO_UPDATE_CHECK": "off", "TICO_TELEMETRY": "off", "TICO_SUPPORT": "off"}
    result = subprocess.run([sys.executable, "-c", LIFESPAN], cwd=ROOT, env=env, capture_output=True, text=True,
                            timeout=300)
    assert result.returncode == 0, result.stderr[-3000:]


@pytest.mark.slow
def test_a_second_start_rewrites_under_one_percent_of_the_database(tmp_path):
    result, data = prepare(tmp_path, TICO_ENVIRONMENT_ID=ENVIRONMENT_ID)
    assert result.returncode == 0, result.stderr
    page_churn.fill_synthetic(data / "hub.sqlite")
    copies = []
    for number in (1, 2):
        result, _ = prepare(tmp_path, TICO_ENVIRONMENT_ID=ENVIRONMENT_ID)
        assert result.returncode == 0, result.stderr
        start(data, tmp_path / "backups")
        assert not (data / "hub.sqlite-wal").exists(), "a connection is still open: the copy would miss its pages"
        copies.append(tmp_path / f"after-start-{number}.sqlite")
        shutil.copy(data / "hub.sqlite", copies[-1])
    differing, total = page_churn.changed_pages(*copies)
    assert differing < total * 0.01, f"{differing} of {total} pages changed on the second start"
