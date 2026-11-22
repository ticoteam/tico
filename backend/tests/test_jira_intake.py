"""Jira read side and find-or-create (docs/dev/pipelines-v3.md "Outside items", "Intake"): a linked issue's state is read
only through a company-wide connection's own key and saved per connection; a trigger bound to a connection marks its
issue due and ignores what it cannot read; a webhook's mark made during a read survives it; a create naming an item's
primary link returns the task holding it, after the same rule check, and two creates at once file one task; a merge
keeps the primary link with the kept task."""

import base64
import ipaddress
import json
import threading

import httpx
import pytest

from backend import egress
from backend import hubdb as H
from backend import item_metadata as IM
from backend.item_sources import jira as JS
from backend.tests.helpers.api import headers, post, put
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.mcp import call
from backend.tests.helpers.org_rule_check import color_rule, ruled  # noqa: F401 - fixture
from backend.tests.helpers.triggers import hook

PUBLIC = "93.184.215.14"
TOKENS = {
    "acme.atlassian.net": "jira-synthetic-acme-0011223344",
    "beta.atlassian.net": "jira-synthetic-beta-5566778899",
}


class FakeJira:
    """Jira Cloud at the network edge: each site's issues, answered to GET /rest/api/3/issue/<key> with that site's key.
    `during` runs while a read is out (a webhook landing mid-read)."""

    def __init__(self):
        self.issues: dict[tuple[str, str], dict] = {}
        self.seen: list[httpx.Request] = []
        self.during = None
        self.tools: dict[str, str] = {}

    def set(self, host, key, status, category, updated, summary="Fix the export"):
        self.issues[(host, key)] = {"status": status, "category": category, "updated": updated, "summary": summary}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.during:
            self.during()
        host = request.headers["host"]
        wanted = "Basic " + base64.b64encode(f"ops@acme.example:{TOKENS[host]}".encode()).decode()
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
    """Two Jira sites, each a record integration (an administrator marked it as holding records) with a shared read
    key, and a fake Jira behind both. Nobody is given access to either: Tico reads with the connection's key."""
    setup(api)
    vendor = FakeJira()
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address(PUBLIC)])
    monkeypatch.setattr(JS, "TRANSPORT", httpx.MockTransport(vendor))
    for name, host in (("Jira Acme", "acme.atlassian.net"), ("Jira Beta", "beta.atlassian.net")):
        made = post(api, "api-tools", {"name": name, "base_url": f"https://{host}", "auth_scheme": "basic_password"})
        body = {"name": "Jira read", "value": TOKENS[host], "username": "ops@acme.example", "access_level": "read"}
        post(api, f"api-tools/{made['id']}/keys", body)
        _patch_tool(api, made["id"], {"record_owner": "outside"})
        vendor.tools[host] = made["id"]
    return vendor


def _patch_tool(api, tool_id, body, token="ana-test", expected=200):
    r = api.patch(f"/api/v2/api-tools/{tool_id}", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def _create(api, ref, title="Fix the export", token="ana-test", expected=200, **more):
    body = {"owner": "ben", "title": title, "message": "From Jira.", "primary_link": ref, **more}
    return post(api, "tasks", body, token, expected=expected)


def _id(made):
    return (made.get("task") or made)["id"]


def _refresh(api):
    return JS.refresh(api.app.state.store, api.app.state.vault)


def _meta(api, key, connection):
    with api.app.state.store.read() as c:
        return IM.get(c, "jira", "issue", key, connection)


def _links(api, key):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute("SELECT task_id, connection_id, is_primary FROM task_links WHERE item_key=?", (key,))
        ]


def _due(api, key, connection):
    with api.app.state.store.transaction() as c:
        IM.mark_due(c, "jira", "issue", key, connection)


def test_an_issues_state_is_read_only_through_a_company_wide_connection_and_saved_per_connection(api, jira):
    acme, beta = jira.tools["acme.atlassian.net"], jira.tools["beta.atlassian.net"]
    jira.set("acme.atlassian.net", "PROJ-7", "In Progress", "indeterminate", "2026-11-21T10:00:00.000+0000")
    jira.set("beta.atlassian.net", "PROJ-7", "Done", "done", "2026-11-21T11:00:00.000+0000", "Other site")
    first = _id(_create(api, f"jira:issue:proj-7@{acme}"))
    second = _id(_create(api, "https://beta.atlassian.net/browse/PROJ-7"))
    assert first != second  # the same key on two connections is two items

    assert _refresh(api) == 2
    a, b = _meta(api, "PROJ-7", acme), _meta(api, "PROJ-7", beta)
    assert a["fields"] == {
        "state": "open",
        "status": "In Progress",
        "category": "indeterminate",
        "updated": "2026-11-21T10:00:00.000+0000",
        "connection_id": acme,
    }
    assert (b["fields"]["state"], b["fields"]["status"], b["fields"]["connection_id"]) == ("closed", "Done", beta)
    assert a["final_at"] is None and b["final_at"] is None  # an issue can always be reopened
    assert _refresh(api) == 0  # fresh: nothing is read again until the backstop or a webhook

    # Nothing but a record integration with a shared key is read: unmarked, or a bot's own key on a computer.
    for change, undo in (
        ({"record_owner": ""}, {"record_owner": "outside"}),
        ({"delivery": "computer"}, {"delivery": "proxy"}),
    ):
        _patch_tool(api, acme, change)
        _due(api, "PROJ-7", acme)
        seen = len(jira.seen)
        assert _refresh(api) == 0 and len(jira.seen) == seen
        assert _meta(api, "PROJ-7", acme)["due_at"] is not None
        _patch_tool(api, acme, undo)
    assert _patch_tool(api, acme, {"record_owner": ""}, token="cara-test", expected=403)  # an administrator's mark


def test_a_trigger_bound_to_a_connection_marks_its_issue_due_and_ignores_what_it_cannot_read(api, jira):
    acme, beta = jira.tools["acme.atlassian.net"], jira.tools["beta.atlassian.net"]
    jira.set("acme.atlassian.net", "PROJ-9", "To Do", "new", "2026-11-21T10:00:00.000+0000")
    jira.set("beta.atlassian.net", "PROJ-9", "To Do", "new", "2026-11-21T10:00:00.000+0000")
    _create(api, f"jira:issue:PROJ-9@{acme}")
    _create(api, f"jira:issue:PROJ-9@{beta}")
    _refresh(api)
    made = post(api, "triggers", {"name": "Jira"})["trigger"]
    assert post(api, f"triggers/{made['id']}", {"connection_id": acme})["trigger"]["connection_id"] == acme
    assert post(api, f"triggers/{made['id']}", {"connection_id": "tool_nope"}, expected=422)

    jira.set("acme.atlassian.net", "PROJ-9", "In Review", "indeterminate", "2026-11-21T10:05:00.000+0000")
    event = {"webhookEvent": "jira:issue_updated", "issue": {"key": "PROJ-9", "fields": {"status": {}}}}
    r = hook(api, made["url"], body=json.dumps(event).encode(), headers={"Idempotency-Key": "j1"})
    assert r.status_code == 202 and r.json().get("rules") in (None, 0, []), r.text
    assert _meta(api, "PROJ-9", acme)["due_at"] is not None
    assert _meta(api, "PROJ-9", beta)["due_at"] is None  # the trigger is the acme connection's only
    assert _refresh(api) == 1
    assert _meta(api, "PROJ-9", acme)["fields"]["status"] == "In Review"

    # Payloads that do not read as an issue event are accepted and change nothing.
    for n, junk in enumerate(
        (b"[1, 2]", b'{"webhookEvent": 7}', b'{"webhookEvent": "jira:issue_x", "issue": {"key": 9}}')
    ):
        assert hook(api, made["url"], body=junk, headers={"Idempotency-Key": f"bad{n}"}).status_code == 202
    assert _meta(api, "PROJ-9", acme)["due_at"] is None


def test_a_webhook_mark_made_while_a_read_is_out_survives_it(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    jira.set("acme.atlassian.net", "PROJ-3", "To Do", "new", "2026-11-21T10:00:00.000+0000")
    _create(api, f"jira:issue:PROJ-3@{acme}")
    _refresh(api)
    _due(api, "PROJ-3", acme)

    def webhook_lands():
        jira.during = None
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE item_metadata SET due_at='2999-01-01T00:00:00Z' WHERE item_key='PROJ-3'")

    jira.during = webhook_lands
    assert _refresh(api) == 1
    assert _meta(api, "PROJ-3", acme)["due_at"] == "2999-01-01T00:00:00Z"  # read again on the next pass
    assert _refresh(api) == 1 and _meta(api, "PROJ-3", acme)["due_at"] is None


def test_a_create_naming_its_items_primary_link_finds_the_task_holding_it(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    made = _create(api, f"jira:issue:PROJ-1@{acme}")
    task = _id(made)
    assert "found" not in made
    (link,) = _links(api, "PROJ-1")
    assert (link["task_id"], link["connection_id"], link["is_primary"]) == (task, acme, 1)

    again = _create(api, "https://acme.atlassian.net/browse/proj-1", title="Another title")
    assert (_id(again), again["found"]) == (task, True)
    with api.app.state.store.transaction() as c:  # a closed task is found as it is: never reopened
        H.task_close(c, "human:ana", task, "Done elsewhere.")
    closed = _create(api, f"jira:issue:PROJ-1@{acme}")
    assert (_id(closed), closed["task"]["status"]) == (task, "closed")
    other = _create(api, f"jira:issue:PROJ-2@{acme}")  # another item, the same title: a task of its own
    assert "found" not in other and _id(other) != task

    # An agent does it over MCP with tico_task_create; a reference that names no item is refused.
    failed, answer = call(
        api, "tico_task_create", {"title": "Fix it", "owner": "ben", "primary_link": f"jira:issue:PROJ-2@{acme}"}
    )
    assert (failed, answer["task"]["id"], answer["found"]) == (False, _id(other), True)
    assert _create(api, "jira:issue:nope@" + acme, expected=422)["error"]["code"] == "primary_link"


def test_a_block_rule_holds_a_find_or_create_like_any_create(ruled, jira):  # noqa: F811
    api, _ = ruled
    acme = jira.tools["acme.atlassian.net"]
    color_rule(api)
    body = {
        "owner": "ana",
        "title": "Pick a palette",
        "body": "Our favorite color is red.",
        "primary_link": f"jira:issue:PROJ-4@{acme}",
    }
    got = post(api, "tasks", body, token="ops-test")
    assert got["held"] and _links(api, "PROJ-4") == []


def test_two_creates_at_once_file_one_task(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    barrier, out = threading.Barrier(2), []

    def create(n: int) -> None:
        barrier.wait()
        out.append(_create(api, f"jira:issue:PROJ-5@{acme}", title=f"Race {n}"))

    threads = [threading.Thread(target=create, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len({_id(r) for r in out}) == 1 and sorted("found" in r for r in out) == [False, True]
    assert [link["is_primary"] for link in _links(api, "PROJ-5")] == [1]


def test_a_merge_keeps_the_primary_link_and_a_restore_never_makes_a_second(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    filed = _id(_create(api, f"jira:issue:PROJ-11@{acme}"))
    kept = _id(post(api, "tasks", {"owner": "ben", "title": "The same work", "body": "Dupe."}))
    post(api, f"tasks/{filed}/merge", {"into": kept})
    assert _id(_create(api, f"jira:issue:PROJ-11@{acme}")) == kept  # the next create finds the kept task

    # A deleted task's primary link comes back plain when another task was filed for the item meanwhile.
    post(api, f"tasks/{kept}/delete", {})
    again = _id(_create(api, f"jira:issue:PROJ-11@{acme}"))
    assert again != kept
    post(api, f"tasks/{kept}/restore", {})
    rows = {(link["task_id"], link["is_primary"]) for link in _links(api, "PROJ-11")}
    assert rows == {(filed, 0), (kept, 0), (again, 1)}


def test_a_merge_into_a_task_linking_the_url_through_another_connection_keeps_the_claim(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    filed = _id(_create(api, f"jira:issue:PROJ-12@{acme}"))
    with api.app.state.store.transaction() as c:  # a second connection on the same site
        c.execute(
            "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
            "updated_at) VALUES('jira-acme-2','Jira Acme 2','','https://acme.atlassian.net','bearer','','proxy',"
            "'human:ana',?,?)",
            ("2026-11-21T00:00:00Z", "2026-11-21T00:00:00Z"),
        )
    kept = _id(post(api, "tasks", {"owner": "ben", "title": "The same work", "body": "Dupe."}))
    url = "https://acme.atlassian.net/browse/PROJ-12"
    post(api, f"tasks/{kept}/links", {"url": url, "connection_id": "jira-acme-2"})
    post(api, f"tasks/{filed}/merge", {"into": kept})
    rows = {(link["task_id"], link["connection_id"], link["is_primary"]) for link in _links(api, "PROJ-12")}
    assert (kept, acme, 1) in rows and (kept, "jira-acme-2", 0) in rows and (filed, acme, 0) in rows


def test_a_trigger_binds_only_to_a_record_integration_and_loses_its_binding_with_it(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    plain = post(api, "api-tools", {"name": "Plain Jira", "base_url": "https://gamma.atlassian.net"})["id"]
    made = post(api, "triggers", {"name": "Jira"})["trigger"]
    assert post(api, f"triggers/{made['id']}", {"connection_id": plain}, expected=422)["error"]["code"] == "trigger"
    post(api, f"triggers/{made['id']}", {"connection_id": acme})
    post(api, f"triggers/{made['id']}/delete", {})
    again = post(api, "triggers", {"name": "Jira"})["trigger"]  # the same id made again starts unbound
    assert again["id"] == made["id"] and again.get("connection_id", "") == ""
    post(api, f"triggers/{made['id']}", {"connection_id": acme})
    r = api.delete(f"/api/v2/api-tools/{acme}", headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT connection_id FROM triggers WHERE id=?", (made["id"],)).fetchone()[0] == ""


def test_a_webhook_during_the_first_read_is_kept(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    jira.set("acme.atlassian.net", "PROJ-13", "To Do", "new", "2026-11-21T10:00:00.000+0000")
    _create(api, f"jira:issue:PROJ-13@{acme}")
    assert _meta(api, "PROJ-13", acme) is None  # never read
    event = json.dumps({"webhookEvent": "jira:issue_updated", "issue": {"key": "PROJ-13"}})

    def webhook_lands():
        jira.during = None
        with api.app.state.store.transaction() as c:
            JS.webhook(c, acme, json.loads(event))

    jira.during = webhook_lands
    assert _refresh(api) == 1
    assert _meta(api, "PROJ-13", acme)["due_at"] is not None  # the push is read on the next pass


def test_marked_issues_are_read_before_stale_ones_and_a_bad_answer_is_retried_alone(api, jira, monkeypatch):
    acme = jira.tools["acme.atlassian.net"]
    for key in ("PROJ-21", "PROJ-22", "PROJ-23"):
        jira.set("acme.atlassian.net", key, "To Do", "new", "2026-11-21T10:00:00.000+0000")
        _create(api, f"jira:issue:{key}@{acme}", title=key)
    _refresh(api)
    with api.app.state.store.transaction() as c:  # all three an hour stale; PROJ-23 marked by a webhook
        c.execute("UPDATE item_metadata SET read_at='2000-01-01T00:00:00Z'")
        IM.mark_due(c, "jira", "issue", "PROJ-23", acme)
    with api.app.state.store.read() as c:
        assert [key for key, _, _ in JS.due_reads(c, "2026-11-22T00:00:00Z", per_connection=1)] == ["PROJ-23"]

    # A 200 whose fields are not an issue's goes the failed read's way, and the rest of the pass still reads.
    jira.issues[("acme.atlassian.net", "PROJ-21")]["status"] = ["not", "a", "status"]
    assert _refresh(api) == 2
    assert "no status" in _meta(api, "PROJ-21", acme)["read_error"]


def test_a_read_still_out_at_its_deadline_fails_alone_and_holds_its_place_until_it_ends(api, jira, monkeypatch):
    acme = jira.tools["acme.atlassian.net"]
    for key in ("PROJ-31", "PROJ-32"):
        jira.set("acme.atlassian.net", key, "To Do", "new", "2026-11-21T10:00:00.000+0000")
        _create(api, f"jira:issue:{key}@{acme}", title=key)
    stuck, workers = threading.Event(), []
    jira.during = lambda: stuck.wait(30)  # Jira never answers (a lookup, connect or TLS that hangs)
    monkeypatch.setattr(JS, "_wait", lambda worker, seconds: workers.append((worker, seconds)))  # the deadline passes
    monkeypatch.setattr(JS, "MAX_IN_FLIGHT", 1)
    try:
        assert _refresh(api) == 0
        assert len(jira.seen) == 1  # the timed-out read is still out: no second read starts
    finally:
        stuck.set()
        for worker, _ in workers:
            worker.join(30)
    assert [seconds for _, seconds in workers] == [JS.TIMEOUT_S]
    timed_out = [
        key for key in ("PROJ-31", "PROJ-32") if "timed out" in ((_meta(api, key, acme) or {}).get("read_error") or "")
    ]
    assert len(timed_out) == 1 and not JS.busy()


def test_a_claim_on_a_task_the_caller_cannot_read_is_refused_without_naming_it(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    private = _id(_create(api, f"jira:issue:PROJ-41@{acme}", private=True))
    got = _create(api, f"jira:issue:PROJ-41@{acme}", token="cara-test", expected=409)
    assert got["error"]["detail"] == "This item is already filed" and private not in json.dumps(got)
    # The dry run checks the primary link's form only: which task holds the item, the create alone reads.
    checked = post(
        api, "tasks/dry-run", {"title": "x", "owner": "ben", "primary_link": f"jira:issue:PROJ-41@{acme}"}, "cara-test"
    )
    assert "found" not in checked and private not in json.dumps(checked)
    wrong = post(api, "tasks/dry-run", {"title": "x", "owner": "ben", "primary_link": "jira:issue:nope@x"}, "cara-test")
    assert wrong["ok"] is False
    # Only Jira issues are primary links for now: a GitHub PR links the ordinary way.
    pr = _create(api, "https://github.com/ticoteam/tico/pull/12", expected=422)
    assert pr["error"]["code"] == "primary_link"


def test_a_retry_from_before_the_new_fields_keeps_its_answer(api, jira):
    from backend import models as M
    from backend.store import digest, encode

    body = {"owner": "ben", "title": "Old client", "message": "Hi."}
    first = post(api, "tasks", body, key="old-create")
    legacy = M.TaskCreate.model_validate(body).model_dump(exclude={"primary_link"})
    with api.app.state.store.transaction() as c:  # what the server before this change stored for that key
        c.execute("UPDATE idempotency SET request_hash=? WHERE key='old-create'", (digest(encode(legacy)),))
    assert _id(post(api, "tasks", body, key="old-create")) == _id(first)


def test_a_claim_gone_before_the_write_is_judged_before_it_is_filed(api, jira, monkeypatch):
    from backend import ask_ingest
    from backend import item_sources as IS

    acme = jira.tools["acme.atlassian.net"]
    real, calls, judged = IS.lookup, [], []

    def lookup(c, text):
        calls.append(text)
        primary, held_by = real(c, text)
        return (primary, "a-task-gone-by-the-write") if len(calls) == 1 else (primary, held_by)

    monkeypatch.setattr(IS, "lookup", lookup)
    preflight = ask_ingest.preflight
    monkeypatch.setattr(ask_ingest, "preflight", lambda *a, **k: judged.append(a) or preflight(*a, **k))
    made = _create(api, f"jira:issue:PROJ-61@{acme}")
    assert "found" not in made and len(judged) == 1  # skipped while held, judged once it was not
    assert [link["is_primary"] for link in _links(api, "PROJ-61")] == [1]


def test_only_a_tool_on_one_jira_site_is_an_integration(api, jira):
    other = post(api, "api-tools", {"name": "Records", "base_url": "https://records.example.com"})["id"]
    body = {"name": "Read", "value": "records-synthetic-0011223344", "access_level": "read"}
    post(api, f"api-tools/{other}/keys", body)
    _patch_tool(api, other, {"record_owner": "outside"})
    assert _create(api, f"jira:issue:PROJ-51@{other}", expected=422)["error"]["code"] == "primary_link"
    made = post(api, "triggers", {"name": "Records"})["trigger"]
    assert post(api, f"triggers/{made['id']}", {"connection_id": other}, expected=422)["error"]["code"] == "trigger"
    with api.app.state.store.read() as c:
        assert set(JS.integrations(c)) == set(jira.tools.values())


def test_marked_issues_go_first_across_connections(api, jira):
    acme, beta = jira.tools["acme.atlassian.net"], jira.tools["beta.atlassian.net"]
    _create(api, f"jira:issue:PROJ-71@{acme}", title="A")
    _create(api, f"jira:issue:PROJ-72@{beta}", title="B")
    with api.app.state.store.transaction() as c:  # acme's never read; beta's marked by a webhook
        JS.webhook(c, beta, {"webhookEvent": "jira:issue_updated", "issue": {"key": "PROJ-72"}})
    with api.app.state.store.read() as c:
        assert [key for key, _, _ in JS.due_reads(c, "2026-11-22T00:00:00Z")] == ["PROJ-72", "PROJ-71"]


def test_a_jira_bound_trigger_keeps_only_the_event_and_the_issue_key(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    made = post(api, "triggers", {"name": "Jira"})["trigger"]
    post(api, f"triggers/{made['id']}", {"connection_id": acme})
    event = {"webhookEvent": "jira:issue_updated", "issue": {"key": "PROJ-81", "fields": {"summary": "Secret plan"}}}
    hook(api, made["url"], body=json.dumps(event).encode(), headers={"Idempotency-Key": "k1"})
    with api.app.state.store.read() as c:
        kept = c.execute("SELECT body FROM trigger_bodies WHERE trigger_id=?", (made["id"],)).fetchone()[0]
    assert json.loads(kept) == {"webhookEvent": "jira:issue_updated", "issue": {"key": "PROJ-81"}}


def test_a_jira_bound_trigger_refuses_and_replays_only_the_event_and_the_issue_key(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    _create(api, f"jira:issue:PROJ-91@{acme}")
    made = post(api, "triggers", {"name": "Jira"})["trigger"]
    event = {"webhookEvent": "jira:issue_updated", "issue": {"key": "PROJ-91", "fields": {"summary": "Secret plan"}}}
    full = json.dumps(event).encode()
    assert hook(api, made["url"], body=full, headers={"Idempotency-Key": "before"}).status_code == 202  # unbound
    post(api, f"triggers/{made['id']}", {"connection_id": acme, "hourly": 2})
    post(api, f"triggers/{made['id']}/deliveries/before/replay", {})  # kept before the binding: no Jira event
    assert (_meta(api, "PROJ-91", acme) or {}).get("due_at") is None

    assert hook(api, made["url"], body=full, headers={"Idempotency-Key": "first"}).status_code == 202
    assert hook(api, made["url"], body=full, headers={"Idempotency-Key": "second"}).status_code == 429  # refused
    with api.app.state.store.read() as c:
        kept = dict(c.execute("SELECT delivery, body FROM trigger_bodies WHERE trigger_id=?", (made["id"],)).fetchall())
    slim = {"webhookEvent": "jira:issue_updated", "issue": {"key": "PROJ-91"}}
    assert json.loads(kept["first"]) == slim and json.loads(kept["second"]) == slim
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE item_metadata SET due_at=NULL WHERE item_key='PROJ-91'")
    post(api, f"triggers/{made['id']}/deliveries/first/replay", {})  # kept since the binding: its event runs again
    assert _meta(api, "PROJ-91", acme)["due_at"] is not None


def test_only_a_tool_everyone_sees_holds_records(api, jira):
    acme = jira.tools["acme.atlassian.net"]
    r = api.patch(f"/api/v2/api-tools/{acme}", json={"audience": {"people": ["ana"]}}, headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:  # made Only listed, it holds records no longer
        assert c.execute("SELECT record_owner FROM tools WHERE id=?", (acme,)).fetchone()[0] == ""
    assert _patch_tool(api, acme, {"record_owner": "outside"}, expected=422)["error"]["code"] == "record_owner"
    hidden = _create(api, f"jira:issue:PROJ-95@{acme}", expected=422)["error"]["detail"]
    missing = _create(api, "jira:issue:PROJ-95@no-such-tool", expected=422)["error"]["detail"]
    assert hidden == missing  # a hidden tool answers as a missing one
