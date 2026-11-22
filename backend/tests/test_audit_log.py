"""The redesign's audit trail (backend/audit_log.py): rows written with their change, read newest first, paged,
the owner seeing all and anyone else only their own."""

import json
import sqlite3
from contextlib import closing
from urllib.parse import urlencode

import pytest

from backend import audit_log, mailbox_access, outside_intake
from backend.entities import store as entities
from backend.tests.helpers.api import get, headers, post


def _write(api, *changes):
    with api.app.state.store.transaction() as c:
        for actor, obj, before, after in changes:
            audit_log.write(c, actor, obj, before, after)


def test_a_recorded_change_is_append_only_read_newest_first_and_each_member_reads_only_their_own(api):
    _write(
        api,
        ("human:ana", "task_cap:1", None, {"per_day": 5}),
        ("human:ben", "task_cap:1", {"per_day": 5}, {"per_day": 7}),
        ("human:ana", "automation:a", None, {"n": 1}),
        ("human:ben", "automation:b", None, {"n": 2}),
    )
    rows = get(api, "audit?object=task_cap:1")["rows"]
    assert [(r["actor"], r["before"], r["after"]) for r in rows] == [
        ("human:ben", {"per_day": 5}, {"per_day": 7}),
        ("human:ana", None, {"per_day": 5}),
    ]
    assert {r["object"] for r in get(api, "audit", token="ben-test")["rows"]} == {"task_cap:1", "automation:b"}
    assert {r["actor"] for r in get(api, "audit", token="ben-test")["rows"]} == {"human:ben"}
    # A separate connection, as a script or a second process would hold: the database itself refuses both.
    with closing(sqlite3.connect(str(api.app.state.store.settings.db_path), timeout=5)) as other:
        with pytest.raises(sqlite3.IntegrityError, match="audit_log is append-only"):
            other.execute("UPDATE audit_log SET actor='human:mallory'")
        with pytest.raises(sqlite3.IntegrityError, match="audit_log is append-only"):
            other.execute("DELETE FROM audit_log")
        other.rollback()
    assert get(api, "audit?object=task_cap:1")["rows"] == rows


def test_the_cli_and_the_tool_read_the_same_rows_and_a_member_reads_only_their_own(api):
    from backend.tests.helpers.automation_cli import Client
    from backend.tests.helpers.mcp import call as mcp
    from clients import hubcli, remotecli

    _write(
        api,
        ("human:ana", "task_cap:1", None, {"per_day": 5}),
        ("human:ben", "task_cap:1", {"per_day": 5}, {"per_day": 7}),
    )
    first = remotecli.via_tool(
        Client(api, "ana-test"), hubcli.parser().parse_args(["audit", "--object", "task_cap:1", "--limit", "1"])
    )
    assert [r["actor"] for r in first["rows"]] == ["human:ben"] and first["next"]
    rest = remotecli.via_tool(
        Client(api, "ana-test"),
        hubcli.parser().parse_args(["audit", "--object", "task_cap:1", "--before", first["next"]]),
    )
    assert [r["actor"] for r in rest["rows"]] == ["human:ana"]
    err, mine = mcp(api, "tico_audit", {}, token="ben-test")
    assert not err and {r["actor"] for r in mine["rows"]} == {"human:ben"}, mine


def test_outside_thread_and_message_changes_in_audit_require_access_to_the_root_inbox(api):
    store = api.app.state.store
    _write(api, ("human:ana", "task_cap:1", None, {"per_day": 5}))
    sender = "person@customer.example"
    with store.transaction() as c:
        mailbox = mailbox_access.ensure_mailbox(c, "ben@acme.example", person_id="ben", created_by="human:ben")["id"]
        entity = entities.upsert(c, "human:ben", "person", match={"email": sender})["id"]
        outside_intake.save_message(
            c,
            channel="gmail",
            connection_id=mailbox,
            owner_id="ben",
            source_thread_id="support",
            source_message_id="mail-1",
            sender=sender,
            verified_id=sender,
            title="Outside subject",
            body="Outside message",
        )
        root = c.execute("SELECT id FROM conversations WHERE entity_id=?", (entity,)).fetchone()[0]
        parent = c.execute("SELECT id FROM messages WHERE conversation_id=?", (root,)).fetchone()[0]
    reply = post(api, f"threads/{root}/messages", {"text": "Internal note", "parent_message_id": parent}, "ben-test")
    child = reply["thread_id"]
    for tid, patch in ((child, {"title": "Follow-up"}), (root, {"title": "Support"}), (root, {"archived": True})):
        response = api.patch(f"/api/v2/threads/{tid}", json=patch, headers=headers("ben-test"))
        assert response.status_code == 200, response.text

    objects = (f"thread:{root}", f"thread:{child}", f"message:{reply['id']}")
    events = {
        "sql": "SELECT target FROM events WHERE action='thread.updated' AND target IN (?,?)",
        "params": [root, child],
    }
    assert [r["object"] for r in get(api, "audit?limit=1")["rows"]] == ["task_cap:1"]
    assert get(api, "audit?limit=1")["next"] is None
    assert post(api, "sql", events)["rows"] == []
    for obj, count in zip(objects, (2, 1, 1), strict=True):
        assert get(api, f"audit?object={obj}") == {"rows": [], "next": None}
        assert len(get(api, f"audit?object={obj}", token="ben-test")["rows"]) == count

    with store.transaction() as c:
        grant = mailbox_access.grant(c, "human:ben", mailbox, "person", "ana", "read")
    assert set(objects) <= {r["object"] for r in get(api, "audit")["rows"]}
    assert len(post(api, "sql", events)["rows"]) == 3
    with store.transaction() as c:
        mailbox_access.revoke(c, "human:ben", mailbox, grant["id"])
    assert set(objects).isdisjoint(r["object"] for r in get(api, "audit")["rows"])
    assert post(api, "sql", events)["rows"] == []


def test_pages_never_skip_a_row_by_count_by_shared_time_or_by_byte_budget(api, monkeypatch):
    from backend import audit_log as A

    def every(prefix, limit):
        seen, cursor = [], None
        while True:
            page = get(api, "audit?" + urlencode({"limit": limit, **({"before": cursor} if cursor else {})}))
            seen += [r["object"] for r in page["rows"] if r["object"].startswith(prefix)]
            if not (cursor := page["next"]):
                return seen

    _write(api, *[("human:ana", f"group:{i}", None, {"i": i}) for i in range(5)])
    first = get(api, "audit?limit=2")
    assert len(first["rows"]) == 2 and first["next"]
    rest = get(api, "audit?" + urlencode({"limit": 10, "before": first["next"]}))
    assert len(rest["rows"]) == 3 and rest["next"] is None
    with monkeypatch.context() as patch:
        patch.setattr(A.H, "now", lambda: "2026-10-28T00:00:00.000000Z")
        _write(api, *[("human:ana", f"tie:{i}", None, {"i": i}) for i in range(5)])
    assert sorted(every("tie:", 2)) == [f"tie:{i}" for i in range(5)]
    _write(api, *[("human:ana", f"blob:{i}", None, {"blob": "x" * 90_000}) for i in range(6)])
    page = get(api, "audit?limit=50")
    assert 0 < len(page["rows"]) < 7 + 5 + 5 and page["next"]
    assert sorted(every("blob:", 50)) == [f"blob:{i}" for i in range(6)]
