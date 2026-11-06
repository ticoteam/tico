"""The redesign's audit trail (backend/audit_log.py): rows written with their change, read newest first, paged,
the owner seeing all and anyone else only their own."""
import json
import sqlite3
from contextlib import closing
from urllib.parse import urlencode

import pytest

from backend import audit_log
from backend.tests.test_api import api, get  # noqa: F401


def _write(api, *changes):
    with api.app.state.store.transaction() as c:
        for actor, obj, before, after in changes:
            audit_log.write(c, actor, obj, before, after)


def test_a_recorded_change_is_append_only_read_newest_first_and_each_member_reads_only_their_own(api):
    _write(api, ("human:ana", "task_cap:1", None, {"per_day": 5}),
           ("human:ben", "task_cap:1", {"per_day": 5}, {"per_day": 7}),
           ("human:ana", "automation:a", None, {"n": 1}), ("human:ben", "automation:b", None, {"n": 2}))
    rows = get(api, "audit?object=task_cap:1")["rows"]
    assert [(r["actor"], r["before"], r["after"]) for r in rows] == [
        ("human:ben", {"per_day": 5}, {"per_day": 7}), ("human:ana", None, {"per_day": 5})]
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
    from backend.tests.test_automation_cli import Client
    from backend.tests.test_mcp import call as mcp
    from clients import hubcli, remotecli

    _write(api, ("human:ana", "task_cap:1", None, {"per_day": 5}),
           ("human:ben", "task_cap:1", {"per_day": 5}, {"per_day": 7}))
    first = remotecli.via_tool(Client(api, "ana-test"), hubcli.parser().parse_args(
        ["audit", "--object", "task_cap:1", "--limit", "1"]))
    assert [r["actor"] for r in first["rows"]] == ["human:ben"] and first["next"]
    rest = remotecli.via_tool(Client(api, "ana-test"), hubcli.parser().parse_args(
        ["audit", "--object", "task_cap:1", "--before", first["next"]]))
    assert [r["actor"] for r in rest["rows"]] == ["human:ana"]
    err, mine = mcp(api, "hub_audit", {}, token="ben-test")
    assert not err and {r["actor"] for r in mine["rows"]} == {"human:ben"}, mine


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
