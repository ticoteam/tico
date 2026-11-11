"""Helpers that other tests share, moved out of test_mail_live.py so they import no test file."""

import pytest
from backend import google_workspace, mailbox_access
from backend.tests.helpers.assistant import assistant_turn


SHARED = "legal@acme.example"


@pytest.fixture(autouse=True)
def no_cached_tokens():
    google_workspace.forget_tokens()
    yield
    google_workspace.forget_tokens()


@pytest.fixture
def live(api, workspace_key, gmail_http):
    with api.app.state.store.transaction() as c:
        mailbox_access.ensure_mailbox(c, SHARED, kind="shared", created_by="human:ana")
    return gmail_http


@pytest.fixture
def assistant_token(api):
    _, attempt = assistant_turn(api, "ana-test", "Read my mail and the shared mailboxes I can access.")
    return attempt["token"]
