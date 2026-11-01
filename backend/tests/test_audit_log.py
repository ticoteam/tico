"""The redesign's audit trail (backend/audit_log.py): rows written with their change, read newest first, paged,
the owner seeing all and anyone else only their own."""
import json
from urllib.parse import urlencode

from backend import audit_log
from backend.tests.test_api import api, get  # noqa: F401


def _write(api, *changes):
    with api.app.state.store.transaction() as c:
        for actor, obj, before, after in changes:
            audit_log.write(c, actor, obj, before, after)


def test_owner_reads_every_row_newest_first_with_before_and_after(api):
    _write(api, ("human:ana", "task_cap:1", None, {"per_day": 5}),
           ("human:ben", "task_cap:1", {"per_day": 5}, {"per_day": 7}))
    rows = get(api, "audit?object=task_cap:1")["rows"]
    assert [(r["actor"], r["before"], r["after"]) for r in rows] == [
        ("human:ben", {"per_day": 5}, {"per_day": 7}), ("human:ana", None, {"per_day": 5})]


def test_anyone_else_reads_only_their_own_changes(api):
    _write(api, ("human:ana", "automation:a", None, {"n": 1}), ("human:ben", "automation:b", None, {"n": 2}))
    assert [r["object"] for r in get(api, "audit", token="ben-test")["rows"]] == ["automation:b"]


def test_pages_by_before(api):
    _write(api, *[("human:ana", f"group:{i}", None, {"i": i}) for i in range(5)])
    first = get(api, "audit?limit=2")
    assert len(first["rows"]) == 2 and first["next"]
    rest = get(api, "audit?" + urlencode({"limit": 10, "before": first["next"]}))
    assert len(rest["rows"]) == 3 and rest["next"] is None


def test_rows_sharing_a_time_are_never_skipped(api, monkeypatch):
    from backend import audit_log as A
    monkeypatch.setattr(A.H, "now", lambda: "2026-10-28T00:00:00.000000Z")
    _write(api, *[("human:ana", f"group:{i}", None, {"i": i}) for i in range(5)])
    seen, cursor = [], None
    while True:
        page = get(api, "audit?" + urlencode({"limit": 2, **({"before": cursor} if cursor else {})}))
        seen += [r["object"] for r in page["rows"]]
        if not (cursor := page["next"]):
            break
    assert sorted(seen) == [f"group:{i}" for i in range(5)]


def test_a_page_stops_at_the_byte_budget_and_the_next_page_picks_up_the_rest(api):
    _write(api, *[("human:ana", f"group:{i}", None, {"blob": "x" * 90_000}) for i in range(6)])
    first = get(api, "audit?limit=50")
    assert 0 < len(first["rows"]) < 7 and first["next"]
    seen, cursor = [], None
    while True:
        page = get(api, "audit?" + urlencode({"limit": 50, **({"before": cursor} if cursor else {})}))
        seen += [r["object"] for r in page["rows"]]
        if not (cursor := page["next"]):
            break
    assert sorted(seen) == [f"group:{i}" for i in range(6)]


def test_the_cli_pages_the_same_rows_the_owner_reads(api):
    from backend.tests.test_automation_cli import Client
    from clients import hubcli, remotecli

    _write(api, ("human:ana", "task_cap:1", None, {"per_day": 5}),
           ("human:ben", "task_cap:1", {"per_day": 5}, {"per_day": 7}))
    first = remotecli.via_tool(Client(api, "ana-test"), hubcli.parser().parse_args(
        ["audit", "--object", "task_cap:1", "--limit", "1"]))
    assert [r["actor"] for r in first["rows"]] == ["human:ben"] and first["next"]
    rest = remotecli.via_tool(Client(api, "ana-test"), hubcli.parser().parse_args(
        ["audit", "--object", "task_cap:1", "--before", first["next"]]))
    assert [r["actor"] for r in rest["rows"]] == ["human:ana"]


def test_the_tool_lets_a_member_read_only_their_own_changes(api):
    from backend.tests.test_mcp import call as mcp

    _write(api, ("human:ana", "task_cap:1", None, {"per_day": 5}),
           ("human:ben", "task_cap:1", {"per_day": 5}, {"per_day": 7}))
    err, mine = mcp(api, "hub_audit", {}, token="ben-test")
    assert not err and {r["actor"] for r in mine["rows"]} == {"human:ben"}, mine
