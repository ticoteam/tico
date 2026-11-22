"""Jira read side and create-or-find intake (docs/dev/pipelines-v3.md "Outside items", "Intake"): a linked issue's state
is read through its own connection's key and saved per connection; a Jira webhook makes the next pass read it again; one
outside item has one task, found again by every intake, reopened when it is ready after a withdrawal, never filed twice
even by two intakes at once; a stale event changes nothing; a primary link's issue closing asks the task's owner once,
and reopening takes the ask back."""

import base64
import ipaddress
import json
import threading

import httpx
import pytest

from backend import egress
from backend import hubdb as H
from backend import item_intake as II
from backend import item_metadata as IM
from backend.item_sources import jira as JS
from backend.tests.helpers.api import post, put
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.mcp import call
from backend.tests.helpers.triggers import hook

PUBLIC = "93.184.215.14"
TOKENS = {
    "acme.atlassian.net": "jira-synthetic-acme-0011223344",
    "beta.atlassian.net": "jira-synthetic-beta-5566778899",
}


class FakeJira:
    """Jira Cloud at the network edge: each site's issues, answered to GET /rest/api/3/issue/<key> with the right key."""

    def __init__(self):
        self.issues: dict[tuple[str, str], dict] = {}
        self.seen: list[httpx.Request] = []

    def set(self, host, key, status, category, updated, summary="Fix the export"):
        self.issues[(host, key)] = {"status": status, "category": category, "updated": updated, "summary": summary}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        host = request.headers["host"]
        wanted = "Basic " + base64.b64encode(f"ana@acme.example:{TOKENS[host]}".encode()).decode()
        if request.headers.get("authorization") != wanted:
            return httpx.Response(401, json={"errorMessages": ["synthetic"]})
        key = request.url.path.rsplit("/", 1)[1]
        issue = self.issues.get((host, key))
        if issue is None:
            return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
        status = {"name": issue["status"], "statusCategory": {"key": issue["category"], "name": issue["status"]}}
        fields = {"summary": issue["summary"], "status": status, "updated": issue["updated"]}
        return httpx.Response(200, json={"key": key, "fields": fields})


@pytest.fixture
def jira(api, monkeypatch):
    """Two Jira sites, each a connection with its own key that Ana may use, and a fake Jira behind both."""
    setup(api)
    vendor = FakeJira()
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address(PUBLIC)])
    monkeypatch.setattr(JS, "TRANSPORT", httpx.MockTransport(vendor))
    vendor.tools = {}
    for name, host in (("Jira Acme", "acme.atlassian.net"), ("Jira Beta", "beta.atlassian.net")):
        made = post(api, "api-tools", {"name": name, "base_url": f"https://{host}", "auth_scheme": "basic_password"})
        body = {"name": "Jira read", "value": TOKENS[host], "username": "ana@acme.example", "access_level": "read"}
        post(api, f"api-tools/{made['id']}/keys", body)
        put(api, f"api-tools/{made['id']}/access/human:ana", {"level": "read"})
        vendor.tools[host] = made["id"]
    return vendor


def _intake(api, connection, key, updated=None, token="ana-test", expected=200, **more):
    body = {"connection_id": connection, "source": "jira", "item_type": "issue", "key": key, **more}
    if updated:
        body["updated_at"] = updated
    return post(api, "inbound/items", body, token, expected=expected)


def _new(api, connection, key, updated=None, **more):
    return _intake(api, connection, key, updated, title="Fix the export", owner="ben", message="From Jira.", **more)


def _plain(api, title):
    """A task for Ben filed the ordinary way, with no item."""
    made = post(api, "tasks", {"owner": "ben", "title": title, "body": "Plain."})
    return (made.get("task") or made)["id"]


def _refresh(api):
    return JS.refresh(api.app.state.store, api.app.state.vault)


def _meta(api, key, connection):
    with api.app.state.store.read() as c:
        return IM.get(c, "jira", "issue", key, connection)


def _asks(api, task_id):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT q.*, m.refs FROM question_marks q JOIN messages m ON m.id=q.message_id WHERE q.task_id=? "
                "ORDER BY q.created_at",
                (task_id,),
            )
        ]


def _status(api, task_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0]


def test_an_issues_state_is_read_through_its_own_connection_and_saved_per_connection(api, jira):
    acme, beta = jira.tools["acme.atlassian.net"], jira.tools["beta.atlassian.net"]
    jira.set("acme.atlassian.net", "PROJ-7", "In Progress", "indeterminate", "2026-11-21T10:00:00.000+0000")
    jira.set("beta.atlassian.net", "PROJ-7", "Done", "done", "2026-11-21T11:00:00.000+0000", "Other site")
    first = _new(api, acme, "proj-7")["task"]["id"]
    second = _new(api, beta, "PROJ-7")["task"]["id"]
    assert first != second  # the same key on two connections is two items

    assert _refresh(api) == 2
    a, b = _meta(api, "PROJ-7", acme), _meta(api, "PROJ-7", beta)
    assert a["fields"] == {
        "state": "open",
        "status": "In Progress",
        "category": "indeterminate",
        "summary": "Fix the export",
        "updated": "2026-11-21T10:00:00.000+0000",
        "connection_id": acme,
    }
    assert (b["fields"]["state"], b["fields"]["status"], b["fields"]["connection_id"]) == ("closed", "Done", beta)
    assert a["final_at"] is None and b["final_at"] is None  # an issue can always be reopened
    assert {r.headers["host"] for r in jira.seen} == {"acme.atlassian.net", "beta.atlassian.net"}
    assert _refresh(api) == 0  # fresh: nothing is read again until the backstop or a webhook

    # Nobody on the tasks linking an issue may use the connection: nothing is read, the reason is kept.
    put(api, f"api-tools/{acme}/access/human:ana", {"level": "none"})
    with api.app.state.store.transaction() as c:
        IM.mark_due(c, "jira", "issue", "PROJ-7", acme)
    seen = len(jira.seen)
    assert _refresh(api) == 0
    assert len(jira.seen) == seen
    gone = _meta(api, "PROJ-7", acme)
    assert "may use" in gone["read_error"] and gone["fields"]["status"] == "In Progress"


def test_a_jira_webhook_makes_the_next_pass_read_the_issue_again(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    jira.set("acme.atlassian.net", "PROJ-9", "To Do", "new", "2026-11-21T10:00:00.000+0000")
    _new(api, acme, "PROJ-9")
    _refresh(api)
    assert _meta(api, "PROJ-9", acme)["fields"]["status"] == "To Do"

    jira.set("acme.atlassian.net", "PROJ-9", "In Review", "indeterminate", "2026-11-21T10:05:00.000+0000")
    made = post(api, "triggers", {"name": "Jira"})["trigger"]
    event = {
        "webhookEvent": "jira:issue_updated",
        # The payload's own words are not trusted: only the issue it names is read again, through the connection.
        "issue": {
            "key": "PROJ-9",
            "self": "https://acme.atlassian.net/rest/api/2/issue/10009",
            "fields": {"status": {}},
        },
    }
    r = hook(api, made["url"], body=json.dumps(event).encode(), headers={"Idempotency-Key": "j1"})
    assert r.status_code == 202, r.text
    assert _meta(api, "PROJ-9", acme)["due_at"] is not None
    assert _refresh(api) == 1
    assert _meta(api, "PROJ-9", acme)["fields"]["status"] == "In Review"


def test_create_or_find_gives_one_task_per_item_and_a_stale_event_changes_nothing(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    made = _new(api, acme, "PROJ-1", "2026-11-21T10:00:00Z")
    assert (made["created"], made["resumed"], made["stale"]) == (True, False, False)
    task = made["task"]["id"]
    with api.app.state.store.read() as c:
        link = dict(c.execute("SELECT * FROM task_links WHERE task_id=?", (task,)).fetchone())
    assert (link["source"], link["item_key"], link["connection_id"], link["is_primary"]) == ("jira", "PROJ-1", acme, 1)
    assert link["url"] == "https://acme.atlassian.net/browse/PROJ-1"

    again = _intake(api, acme, "proj-1", "2026-11-21T10:30:00Z")  # found by its item: no title needed
    assert (again["task"]["id"], again["created"], again["stale"]) == (task, False, False)
    other = _new(api, acme, "PROJ-2")
    assert other["created"] and other["task"]["id"] != task

    # An event no newer than the last one acted on, or older than what a read saw, changes nothing.
    assert _intake(api, acme, "PROJ-1", "2026-11-21T10:30:00Z")["stale"] is True
    seen = {"fields": {"status": {"name": "Backlog", "statusCategory": {"key": "new"}}, "updated": "2026-11-21T12:00Z"}}
    with api.app.state.store.transaction() as c:  # a read saw PROJ-3 change at noon
        JS.save_issue(c, "PROJ-3", acme, JS.fields_of(seen, acme), 1)
    late = _new(api, acme, "PROJ-3", "2026-11-21T11:00:00Z")
    assert (late["task"], late["created"], late["stale"]) == (None, False, True)

    # An agent does the same over MCP (tico_task_intake): the same task, and a key that is no Jira key refused.
    args = {"connection_id": acme, "source": "jira", "item_type": "issue", "key": "PROJ-1"}
    failed, answer = call(api, "tico_task_intake", args)
    assert (failed, answer["task"]["id"], answer["created"]) == (False, task, False)
    assert call(api, "tico_task_intake", {**args, "key": "Acme smoke"})[1]["error"] == "key"

    # A caller who may not use the connection files nothing.
    assert _new(api, acme, "PROJ-4", token="ben-test", expected=403)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM task_links WHERE item_key='PROJ-4'").fetchone()[0] == 0


def test_two_intakes_at_once_file_one_task(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    store, barrier, out = api.app.state.store, threading.Barrier(2), []

    def claim(n: int) -> None:
        barrier.wait()
        with store.transaction() as c:
            out.append(
                II.intake(
                    c,
                    "human:ana",
                    ("jira", "issue", "PROJ-5", acme),
                    title=f"Race {n}",
                    updated_at=None,
                    create=lambda: H.task_create(c, "human:ana", f"Race {n}", "Go.", "human:ben")["id"],
                    see=lambda row: None,
                )
            )

    threads = [threading.Thread(target=claim, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(r["created"] for r in out) == [False, True]
    assert len({r["task_id"] for r in out}) == 1
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM task_links WHERE item_key='PROJ-5' AND is_primary=1").fetchone()[0] == 1


def test_a_primary_issue_closing_asks_the_owner_once_and_reopening_takes_it_back(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    with api.app.state.store.transaction() as c:  # Agent Ops asks for a task with no bot of its own
        H.sync_registry(c, {"botops": {"name": "botops", "status": "active"}}, None)
    jira.set("acme.atlassian.net", "PROJ-6", "In Progress", "indeterminate", "2026-11-21T10:00:00.000+0000")
    task = _new(api, acme, "PROJ-6", "2026-11-21T09:00:00Z")["task"]["id"]
    side = _plain(api, "Related work")
    post(api, f"tasks/{side}/links", {"url": "https://acme.atlassian.net/browse/PROJ-6"})  # a secondary link
    _refresh(api)
    assert _asks(api, task) == []

    jira.set("acme.atlassian.net", "PROJ-6", "Withdrawn", "done", "2026-11-21T11:00:00.000+0000")
    with api.app.state.store.transaction() as c:
        IM.mark_due(c, "jira", "issue", "PROJ-6", acme)
    _refresh(api)
    asks = _asks(api, task)
    assert len(asks) == 1 and asks[0]["person_id"] == "human:ben" and asks[0]["cleared_at"] is None
    assert json.loads(asks[0]["refs"])[II.CLOSED_REF] == f"jira:issue:PROJ-6@{acme}"
    assert "Settle this task or keep it?" in asks[0]["line"]
    assert _asks(api, side) == []  # a secondary link closing asks nobody
    jira.set("acme.atlassian.net", "PROJ-6", "Withdrawn", "done", "2026-11-21T11:01:00.000+0000")
    with api.app.state.store.transaction() as c:
        IM.mark_due(c, "jira", "issue", "PROJ-6", acme)
    _refresh(api)
    assert len(_asks(api, task)) == 1  # still closed: no second ask

    jira.set("acme.atlassian.net", "PROJ-6", "In Progress", "indeterminate", "2026-11-21T12:00:00.000+0000")
    with api.app.state.store.transaction() as c:
        IM.mark_due(c, "jira", "issue", "PROJ-6", acme)
    _refresh(api)
    (ask,) = _asks(api, task)
    assert ask["cleared_at"] is not None and ask["cleared_reason"] == "withdrawn"
    assert _status(api, task) == "open"


def test_ready_after_a_withdrawal_resumes_the_same_task(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    jira.set("acme.atlassian.net", "PROJ-8", "Ready", "indeterminate", "2026-11-21T10:00:00.000+0000")
    task = _new(api, acme, "PROJ-8", "2026-11-21T10:00:00.000+0000")["task"]["id"]
    jira.set("acme.atlassian.net", "PROJ-8", "Withdrawn", "done", "2026-11-21T11:00:00.000+0000")
    _refresh(api)
    with api.app.state.store.transaction() as c:  # the owner settles it
        H.task_close(c, "human:ben", task, "Withdrawn in Jira.")
    assert _status(api, task) == "closed"

    # The ready event that filed it, delivered again, does not reopen it; a newer ready does, the same task.
    assert _intake(api, acme, "PROJ-8", "2026-11-21T10:00:00.000+0000")["stale"] is True
    assert _status(api, task) == "closed"
    back = _intake(api, acme, "PROJ-8", "2026-11-21T12:00:00.000+0000")
    assert (back["task"]["id"], back["created"], back["resumed"], back["stale"]) == (task, False, True, False)
    assert _status(api, task) == "open"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM task_links WHERE item_key='PROJ-8'").fetchone()[0] == 1


def test_a_merge_takes_the_primary_link_along_and_a_restore_never_makes_a_second(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    filed = _new(api, acme, "PROJ-11")["task"]["id"]
    kept = _plain(api, "The same work")
    post(api, f"tasks/{filed}/merge", {"into": kept})
    assert _intake(api, acme, "PROJ-11")["task"]["id"] == kept  # the next intake finds the kept task

    # A deleted task's primary link comes back plain when another task was filed for the item meanwhile.
    post(api, f"tasks/{kept}/delete", {})
    again = _new(api, acme, "PROJ-11")
    assert again["created"] and again["task"]["id"] != kept
    post(api, f"tasks/{kept}/restore", {})
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT task_id, is_primary FROM task_links WHERE item_key='PROJ-11' ORDER BY is_primary"
        ).fetchall()
    assert sorted(map(tuple, rows)) == sorted([(filed, 0), (kept, 0), (again["task"]["id"], 1)])
