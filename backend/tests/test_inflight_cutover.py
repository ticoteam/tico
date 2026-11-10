"""Rows saved before the Release 1 cutover keep working after it, except what Tico 0.6 drops: an approval asked or decided
before the upgrade is gone after it (the Pipelines v3 cut-over carries none), while a webhook send queued before the API v2 rename, and a rule or automation saved under the old column names.

The old database is backend/tests/fixtures/schema_pre_cleanup.sql (schema 71), upgraded in place by the boot's own
path (Store.initialize). The renames that matter here are the ones the stored rows name."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from backend import automations as AU
from backend import hubdb as H
from backend import rules as R
from backend.config import Settings
from backend.migrations import renames
from backend.store import Store
from backend.tests.helpers.hubdb import EMPLOYEES, PEOPLE
from backend.tests.helpers.webhooks import make, net, post, run, sends, verify  # noqa: F401
from backend.tests.helpers.tasks_board import api  # noqa: F401

FIXTURE = Path(__file__).parent / "fixtures" / "schema_pre_cleanup.sql"
ANA, CMO = H.human_actor("ana"), H.bot_actor("cmo")
NOW = "2026-10-30T09:00:00Z"
RELEASE = {
    "url": "https://github.com/acme/site/releases/tag/v1.2.0",
    "version": "1.2.0",
    "commit": "a1",
    "content_sha256": "a" * 64,
}


def released_hash(payload):
    """The hash the released code stored for a payload: SHA-256 of its JSON with sorted keys (rule 6)."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@pytest.fixture
def upgraded(tmp_path):
    """A hub as the last release left it, with an approval asked and not decided, one decided and not spent, then
    booted by this code."""
    path = tmp_path / "hub.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text())
    conn.close()
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO approvals(id, kind, payload_json, payload_hash, requested_by, decided_by, decision, "
        "decided_at, created, ready_since, decision_owner, decided_for_json) "
        "VALUES('appr-open', 'publish', ?, ?, 'bot:cmo', NULL, NULL, NULL, ?, ?, 'human:ana', NULL)",
        (json.dumps(RELEASE, sort_keys=True), released_hash(RELEASE), NOW, NOW),
    )
    delegated = {"cited": ["msg-9"], "for": "human:ana", "run": "run-1"}
    conn.execute(
        "INSERT INTO approvals(id, kind, payload_json, payload_hash, requested_by, decided_by, decision, "
        "decided_at, created, ready_since, decision_owner, decided_for_json) "
        "VALUES('appr-yes', 'publish', ?, ?, 'bot:cmo', 'human:ana', 'approved', ?, ?, ?, 'human:ana', ?)",
        (
            json.dumps({**RELEASE, "commit": "b2"}, sort_keys=True),
            released_hash({**RELEASE, "commit": "b2"}),
            NOW,
            NOW,
            NOW,
            json.dumps(delegated, sort_keys=True),
        ),
    )
    conn.commit()
    conn.close()
    Store(Settings(db_path=path)).initialize()
    conn = H.connect(path)
    H.sync_registry(conn, EMPLOYEES, PEOPLE)
    yield conn
    conn.close()


def test_approvals_asked_and_decided_before_the_cutover_are_gone_after_it_and_nothing_spends_them(upgraded):
    """Tico 0.6 has no approval object (docs/dev/pipelines-v3-cutover.md): an OK is a requirement on a task, asked
    again on 0.6. So the asked approval and the decided one are both dropped with the table, nothing carries their
    decision, payload or delegation, and the payload hash the released code stored is still the one 0.6 makes."""
    conn = upgraded
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "approvals" not in tables
    assert not hasattr(H, "approval") and not hasattr(H, "approval_consume")
    for table in sorted(tables):
        text = " ".join(str(v) for row in conn.execute(f"SELECT * FROM {table}") for v in tuple(row) if v is not None)
        assert "appr-open" not in text and "appr-yes" not in text, table
    assert not conn.execute(
        "SELECT 1 FROM requirements WHERE visit_id IS NOT NULL LIMIT 1"
    ).fetchone()  # no OK on a task
    assert H.payload_hash(RELEASE) == released_hash(RELEASE)  # the same payload hashes the same way


def test_a_webhook_send_queued_before_the_run_rename_is_delivered_as_a_run_and_signed(api, net):
    made = make(api, events=["custom.*"])
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.order", "data": {"order": 42}})
    (row,) = sends(api)
    released = json.loads(row["body"])
    released["data"] = {"order": 42, "attempt_id": "att-1"}  # the body the released server queued
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE webhook_sends SET body=? WHERE id=?",
            (json.dumps(released, separators=(",", ":"), ensure_ascii=False), row["id"]),
        )
    assert run(api, net) == 1
    sent = net.seen[0]
    assert json.loads(sent.content)["data"] == {"order": 42, "run_id": "att-1"}
    assert verify(made["secret"], dict(sent.headers), sent.content)


def test_a_rule_condition_saved_under_the_old_owner_and_requester_names_still_matches_after_the_rename(monkeypatch):
    monkeypatch.setattr(
        renames,
        "COLUMNS",
        renames.COLUMNS + (("tasks", "owner", "owner_id", "3"), ("tasks", "requester", "requester_id", "3")),
    )
    row = {"id": "task-1", "status": "open", "owner_id": "bot:cmo", "requester_id": "human:ana", "type_id": "general"}
    ctx = R._Context(None, H.KEEPER, CMO, {}, None, row)
    assert ctx.matches({"owner": ["bot:cmo"]})
    assert ctx.matches({"requester": "human:ana"})
    assert ctx.matches({"role": "owner"})
    assert not ctx.matches({"owner": ["bot:ops"]})


def test_a_task_received_automation_still_hears_the_requester_after_the_rename(monkeypatch):
    monkeypatch.setattr(renames, "COLUMNS", renames.COLUMNS + (("tasks", "requester", "requester_id", "3"),))
    row = {"id": "task-1", "title": "Ship", "body": "b", "status": "open", "requester_id": "human:ana", "step_id": None}
    heard = AU._received_task(row, "bot:cmo")
    assert heard["requester"] == "human:ana" and heard["owner"] == "bot:cmo" and heard["title"] == "Ship"
