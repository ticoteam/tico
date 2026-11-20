"""Mail spec v1.1 PR 5: no computer syncs, books or holds mail, and the server keeps no copy of it.

The copy tables are gone from a new database and from `tico sql`. The routes the retired computer-side connectors job
polled (v0.6.0-rc.1 through v0.6.20) are gone too: the floor (runner_versions.MIN_RUNNER_RELEASE, 0.6.21) pauses such a
computer with the reason until it updates. No code imports the retired mail CLI, and the migration that drops the
copies settles the appointments a computer was asked to book, once.
"""

import importlib
import subprocess
from pathlib import Path

import pytest

from backend.tests.helpers.api import get, headers, post, runner  # noqa: F401
from backend.tests.helpers.sql import column, query

MIGRATION = importlib.import_module("backend.migrations.20261117062123_drop_mail_copies")
ROOT = Path(__file__).resolve().parents[2]
COPIES = ("mail_fts", "mail_messages", "mail_mailboxes", "connector_snapshots", "connector_reports")
BOX = "ana@acme.example"
# What the retired connectors job asked every 45 seconds (runner_support/connectors.py at v0.6.0-rc.1 through v0.6.20),
# with the bodies it sent, and the answer that once meant "nothing to do".
POLLS = (
    ("GET", "runners/connectors", None, {"connectors": []}),
    ("GET", "connectors/calendar/targets", None, {"people": [], "hours": 24}),
    ("GET", "connectors/mail/targets", None, {"mailboxes": [], "acts_for": []}),
    ("POST", "connectors/mail/google/claim", {"holds_mail": True}, {"connect": None}),
    ("POST", "connectors/calendar/actions/claim", {}, {"action": None}),
    ("POST", "automations/mail-actions/claim", {"mailbox": BOX}, {"actions": []}),
    ("POST", "connectors/email/sends/claim", {"mailbox": BOX}, {"send": None}),
)
REPORTS = (
    ("connectors/mail/google/gc-1/result", {"status": "failed", "kind": "error", "message": "x", "hint": "y"}),
    (
        "connectors/calendar/actions/act-1/result",
        {"status": "unknown", "event_id": "", "meeting_url": "", "error": "x"},
    ),
    ("automations/mail-actions/ma-1/result", {"ok": False, "detail": "x", "retry": True}),
    ("connectors/email/sends/es-1/result", {"status": "unknown", "detail": "x", "lease_id": "l-1"}),
    ("connectors/calendar/snapshots", {"snapshots": [{"email": BOX, "events": []}], "ok_accounts": [BOX]}),
    ("connectors/health", {"service": "mail", "failing": [], "ok_accounts": [BOX]}),
    ("connectors/mail/messages", {"mailbox": BOX, "messages": [], "deleted": [], "synced_at": "2026-11-17T00:00:00Z"}),
)
# Reached only with work a claim handed out.
GONE = ("connectors/email/sends/preflight", "connectors/email/sends/es-1/dispatch")
# The rc.1 local mail CLI's token asks (connectors/mail/oauth.py server_workspace_token and server_token).
TOKENS = (
    ("connectors/mail/workspace/token", {"mailbox": BOX, "service": "gmail"}),
    (
        "connectors/mail/google/token",
        {"mailbox": BOX, "service": "gmail", "refresh_token": "rt-1", "scopes": ["gmail.modify"]},
    ),
)


def tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_a_new_database_has_no_mail_copy_and_tico_sql_lists_none(api):
    with api.app.state.store.read() as c:
        assert not set(COPIES) & tables(c)
        assert "runner_id" not in {r[1] for r in c.execute("PRAGMA table_info(calendar_actions)")}
    listed = column(api, "SELECT name FROM sqlite_master WHERE type IN ('table','view')")
    assert not set(COPIES) & set(listed)
    for table in COPIES:
        assert query(api, f"SELECT * FROM {table}", expected=422)["error"]["code"] == "sql"


def test_the_retired_connectors_jobs_routes_are_gone(api):
    """Below the floor now: none of what that job asked is served, to a computer or anyone else."""
    machine = runner(api)
    asks = [(method, path, body) for method, path, body, _ in POLLS]
    asks += [("POST", path, body) for path, body in REPORTS + TOKENS]
    asks += [("POST", path, {"mailbox": BOX}) for path in GONE]
    for method, path, body in asks:
        r = api.request(method, "/api/v2/" + path, json=body, headers=headers(machine["token"]))
        assert r.status_code in (404, 405), (path, r.status_code, r.text)


def test_no_code_imports_the_retired_mail_cli():
    files = subprocess.run(
        ["git", "ls-files", "*.py", "*.sh", "Dockerfile", "*.rs"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert not [name for name in files if name.startswith("connectors/mail/") or name == "runner_support/connectors.py"]
    stale = (
        "from connectors.mail",
        "import connectors.mail",
        "from connectors import mail",
        "runner_support.connectors",
        "runner_support import connectors",
    )
    here = Path(__file__).resolve().relative_to(ROOT).as_posix()
    found = [
        name
        for name in files
        if name != here
        and (ROOT / name).is_file()
        and any(word in (ROOT / name).read_text(errors="replace") for word in stale)
    ]
    assert found == []


def copies_as_0_6_left_them(c):
    """The tables a 0.6 server kept, and a calendar action for each state a computer could leave it in."""
    c.execute("CREATE TABLE mail_mailboxes(address TEXT PRIMARY KEY, runner_id TEXT)")
    c.execute("CREATE TABLE mail_messages(mailbox TEXT NOT NULL, msg_id TEXT NOT NULL, PRIMARY KEY(mailbox,msg_id))")
    c.execute("CREATE INDEX mail_messages_thread ON mail_messages(mailbox,msg_id)")
    c.execute("CREATE VIRTUAL TABLE mail_fts USING fts5(subject, body)")
    c.execute("CREATE TABLE connector_snapshots(kind TEXT NOT NULL, owner TEXT NOT NULL, PRIMARY KEY(kind,owner))")
    c.execute("CREATE TABLE connector_reports(service TEXT NOT NULL, runner_id TEXT NOT NULL)")
    c.execute("INSERT INTO mail_messages VALUES('ana@acme.example','m-1')")
    c.execute("ALTER TABLE calendar_actions ADD COLUMN runner_id TEXT")
    for id_, status, runner_id in (
        ("pending", "pending", None),
        ("claimed", "running", "r-1"),
        ("server", "running", None),
        ("done", "succeeded", "r-1"),
    ):
        c.execute(
            "INSERT INTO calendar_actions(id,requested_by,calendar_email,summary,start,end,status,runner_id,created_at,"
            "updated_at) VALUES(?,'human:ana','ana@acme.example','Intro','2026-11-20T10:00:00Z','2026-11-20T10:30:00Z',"
            "?,?,'2026-11-16T00:00:00Z','2026-11-16T00:00:00Z')",
            (id_, status, runner_id),
        )
    c.execute(
        "INSERT OR REPLACE INTO service_health(service,last_error) VALUES('connector:mail','sync failed'),"
        "('connector:calendar',NULL),('gmail','')"
    )


@pytest.mark.parametrize("runs", [1, 2])
def test_the_migration_drops_the_copies_and_settles_what_a_computer_was_booking(api, runs):
    with api.app.state.store.transaction() as c:
        copies_as_0_6_left_them(c)
        for _ in range(runs):
            MIGRATION.up(c)
        assert not set(COPIES) & tables(c)
        assert "runner_id" not in {r[1] for r in c.execute("PRAGMA table_info(calendar_actions)")}
        states = {r["id"]: (r["status"], r["error"]) for r in c.execute("SELECT id,status,error FROM calendar_actions")}
        services = {r[0] for r in c.execute("SELECT service FROM service_health")}
    assert states["pending"] == ("failed", "No computer books calendars any more; book it again")
    assert states["claimed"][0] == "unknown" and "check the calendar" in states["claimed"][1]
    assert states["server"] == ("running", "")  # the server's own booking finishes on its own
    assert states["done"] == ("succeeded", "")
    assert "gmail" in services and not {"connector:mail", "connector:calendar"} & services
