"""Helpers that other tests share, moved out of test_read_batches.py so they import no test file."""

import pytest
from backend.store import H
from backend.tests.helpers.api import unrelated_task_managers


@pytest.fixture(autouse=True)
def privacy_parties(api):
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
        c.execute(
            "INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES('rn','Mac','ana','th',?)", (H.now(),)
        )
