"""Reconnect tasks (backend/reconnect_tasks.py): a tool whose key the vendor rejects gives the Tools page's owner ONE
"Reconnect" task whose key card asks the person who connected it for a new key; a repeat adds nothing, and the task
ends itself once the tool works again or its key is replaced. A limited key (a 403, a 401 naming a missing scope) is no
rejection.

Ana owns the company and Ben is an admin (both credential administrators); Cara is a member. Ops stands in as the Tools
page's owner."""

from backend import api_tools, reconnect_tasks
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers.api import get, post, put
from backend.tests.helpers.api_tools import key, level, tool
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.held_runs import agent_ops

OLD = "acme-close-synthetic-old-0011223344"
NEW = "acme-close-synthetic-new-5566778899"


def checks(api, monkeypatch):
    def answer(url, hdrs, params):
        return answer.reply

    answer.reply = api_tools.Reply(401)
    monkeypatch.setattr(api_tools, "send_check", answer)
    return answer


def connected(api, owner=True):
    """Close CRM, connected by Ana, its key used by ops; ops owns the Tools page when `owner`."""
    setup(api)
    if owner:
        put(api, "page-owners/tools", {"bot": "ops"})
    tid = tool(api)["id"]
    kid = key(api, tid, OLD)["id"]
    level(api, tid, "bot:cpo", "read_write")
    return tid, kid


def tasks(api, tid):
    with api.app.state.store.read() as c:
        return [
            dict(H.task(c, r[0]))
            for r in c.execute(
                "SELECT DISTINCT json_extract(detail,'$.task') FROM events WHERE action=? AND target=?",
                (reconnect_tasks.EVENT, tid),
            )
        ]


def card(api, task_id):
    with api.app.state.store.read() as c:
        wait = dict(c.execute("SELECT * FROM task_reconnects WHERE task_id=?", (task_id,)).fetchone())
        request = dict(
            c.execute(
                "SELECT * FROM credential_requests WHERE conversation_id=(SELECT conversation_id "
                "FROM tasks WHERE id=?)",
                (task_id,),
            ).fetchone()
        )
    return wait, request


def thread(api, task_id):
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT count(*) FROM messages WHERE conversation_id=(SELECT conversation_id FROM tasks WHERE id=?)",
            (task_id,),
        ).fetchone()[0]


def test_a_rejected_key_gives_the_tools_owner_one_task_whose_card_replaces_the_key_for_the_connector(api, monkeypatch):
    tid, kid = connected(api)
    answer = checks(api, monkeypatch)
    assert post(api, f"api-tools/{tid}/check", {})["detail"] == "Key rejected"
    [task] = tasks(api, tid)
    assert (task["title"], task["owner_id"], task["status"], task["private"]) == (
        "Reconnect Close CRM",
        "bot:ops",
        "open",
        0,
    )
    wait, request = card(api, task["id"])
    assert (wait["key"], wait["person"], wait["service"]) == (f"tool:{tid}", "human:ana", "secret")
    assert (request["requester"], request["credential_id"], request["status"]) == ("human:ana", kid, "pending")
    shown = get(api, f"credential-requests/{request['id']}")
    assert shown["can_save"] and shown["status"] == "pending"
    get(api, f"credential-requests/{request['id']}", "cara-test", expected=404)  # the card is Ana's
    said = thread(api, task["id"])

    # The same rejection again, from the check and from proxied calls: nothing new, nobody told again.
    assert post(api, f"api-tools/{tid}/check", {})["detail"] == "Key rejected"
    with api.app.state.store.transaction() as c:
        assert reconnect_tasks.start(c, tid, "again") is None
        assert reconnect_tasks.called(c, tid, 401) is None
    assert [t["id"] for t in tasks(api, tid)] == [task["id"]] and thread(api, task["id"]) == said
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credential_requests").fetchone()[0] == 1

    # Ana pastes a new key on the card: it replaces the key in place (cpo keeps its level) and the task is done.
    saved = post(api, f"credential-requests/{request['id']}/save", {"value": NEW})
    assert saved["status"] == "saved"
    assert post(api, f"credentials/{kid}/reveal", {})["value"] == NEW
    with api.app.state.store.read() as c:
        assert api_tools.subject_level(c, tid, "bot:cpo") == "read_write"
        assert H.task(c, task["id"])["status"] == "done"
        assert c.execute(
            "SELECT ended, woke_at IS NOT NULL FROM task_reconnects WHERE task_id=?", (task["id"],)
        ).fetchone()[:] == ("fixed", 1)  # it wakes nobody: the work is over
        events = [r[0] for r in c.execute("SELECT detail FROM events WHERE action='tool.key_updated'")]
    assert all(OLD not in e and NEW not in e for e in events)

    # Rejected again later: a new task, since the old one is over.
    answer.reply = api_tools.Reply(401)
    post(api, f"api-tools/{tid}/check", {})
    assert len(tasks(api, tid)) == 2


def test_the_task_ends_itself_when_the_tool_works_again(api, monkeypatch):
    tid, _ = connected(api)
    answer = checks(api, monkeypatch)
    post(api, f"api-tools/{tid}/check", {})
    [task] = tasks(api, tid)
    _, request = card(api, task["id"])
    answer.reply = api_tools.Reply(200)  # someone fixed it at the vendor
    assert post(api, f"api-tools/{tid}/check", {})["detail"] == "Working"
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "done"
        assert c.execute("SELECT status FROM credential_requests WHERE id=?", (request["id"],)).fetchone()[0] == "saved"
    assert not get(api, f"credential-requests/{request['id']}")["can_save"]


def test_the_working_health_check_leaves_a_reconnect_task_in_its_side_stage(api, monkeypatch):
    tid, _ = connected(api)
    answer = checks(api, monkeypatch)
    post(api, f"api-tools/{tid}/check", {})
    [task] = tasks(api, tid)
    _, request = card(api, task["id"])
    with api.app.state.store.transaction() as c:
        record = PS.read(c, task["pipeline_id"])
        record["stages"].append({"name": "Hold", "side": True, "owner": "assignee"})
        PS.save(c, "human:ana", record)
        held = H.task_move(c, "human:ana", task["id"], "Hold")

    answer.reply = api_tools.Reply(200)
    assert post(api, f"api-tools/{tid}/check", {})["detail"] == "Working"
    wait, updated_request = card(api, task["id"])
    assert (wait["ended"], updated_request["id"], updated_request["status"]) == ("fixed", request["id"], "saved")
    with api.app.state.store.read() as c:
        after = E.task(c, task["id"])
        assert (after["status"], after["visit"]["id"], after["version"]) == (
            "open",
            held["visit"]["id"],
            held["version"],
        )
        assert not c.execute(
            "SELECT 1 FROM events WHERE action='tool.reconnect_task_closed' AND target=?", (tid,)
        ).fetchone()
        assert not c.execute("SELECT 1 FROM refusals WHERE rule='task_side_stage'").fetchone()


def test_proxied_calls_open_it_after_rejections_in_a_row_and_a_2xx_ends_it(api):
    tid, kid = connected(api)

    def call(c, code):
        c.execute(
            "INSERT INTO tool_calls(id,tool_id,credential_id,actor,bot,access_level,method,path,status_code,result,"
            "detail,duration_ms,created_at) VALUES(?,?,?,'bot:cpo','cpo','read_write','GET','/x',?,'sent','',1,?)",
            (H.new_id(), tid, kid, code, H.now()),
        )
        return reconnect_tasks.called(c, tid, code)

    with api.app.state.store.transaction() as c:
        assert [call(c, 401) for _ in range(api_tools.REJECTED_AFTER - 1)] == [None] * (api_tools.REJECTED_AFTER - 1)
        assert call(c, 403) is None and call(c, 500) is None
    assert tasks(api, tid) == []
    with api.app.state.store.transaction() as c:
        for _ in range(api_tools.REJECTED_AFTER):
            call(c, 401)
    [task] = tasks(api, tid)
    with api.app.state.store.transaction() as c:
        call(c, 200)
        assert H.task(c, task["id"])["status"] == "done"


def test_a_limited_key_opens_nothing(api, monkeypatch):
    tid, _ = connected(api)
    answer = checks(api, monkeypatch)
    answer.reply = api_tools.Reply(403)  # a known key without the scope for this path
    assert post(api, f"api-tools/{tid}/check", {})["detail"] == api_tools.LIMITED
    answer.reply = api_tools.Reply(401, missing_scope=True)  # a 401 that says a permission is missing
    assert post(api, f"api-tools/{tid}/check", {})["detail"] == api_tools.LIMITED
    answer.reply = api_tools.Reply(500)
    assert post(api, f"api-tools/{tid}/check", {})["detail"].startswith("Check failed")
    assert tasks(api, tid) == []


def test_without_a_tools_owner_agent_ops_takes_it_and_a_member_connector_falls_back_to_an_admin(api, monkeypatch):
    tid, kid = connected(api, owner=False)
    checks(api, monkeypatch)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tools SET created_by='human:cara' WHERE id=?", (tid,))  # a member, who cannot change it
    assert post(api, f"api-tools/{tid}/check", {})["detail"] == "Key rejected"  # nobody to take it: the check stands
    assert tasks(api, tid) == []
    agent_ops(api)
    post(api, f"api-tools/{tid}/check", {})
    [task] = tasks(api, tid)
    assert task["owner_id"] == "bot:" + H.FLEET_MAINTAINER
    wait, request = card(api, task["id"])
    assert wait["person"] == request["requester"] == "human:ana"  # the owner, first of the credential admins
    assert request["credential_id"] == kid


def test_deleting_the_tool_closes_its_task_and_its_card_asks_nothing(api, monkeypatch):
    tid, _ = connected(api)
    checks(api, monkeypatch)
    post(api, f"api-tools/{tid}/check", {})
    [task] = tasks(api, tid)
    _, request = card(api, task["id"])
    assert api.delete(f"/api/v2/api-tools/{tid}", headers={"Authorization": "Bearer ana-test"}).json()["deleted"]
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "closed"
        assert c.execute("SELECT status FROM credential_requests WHERE id=?", (request["id"],)).fetchone()[0] == (
            "canceled"
        )


def test_a_runs_401_with_a_key_no_check_reaches_opens_its_tools_task(api):
    setup(api)
    put(api, "page-owners/tools", {"bot": "ops"})
    made = post(api, "credentials", {"name": "Acme internal", "env": "ACME_TOKEN", "secret": OLD})
    with api.app.state.store.read() as c:
        own = c.execute("SELECT tool_id FROM credentials WHERE id=?", (made["id"],)).fetchone()[0]
    level(api, own, "bot:cpo", "read_write")
    close = tool(api)["id"]  # a tool its check reaches: the check decides, not one run's call
    key(api, close, NEW)
    level(api, close, "bot:cpo", "read_write")
    with api.app.state.store.read() as c:
        close_env = c.execute("SELECT env FROM credentials WHERE tool_id=?", (close,)).fetchone()[0]
    refused = "HTTP/1.1 401 Unauthorized\n{\"error\":\"invalid api key\"}"
    calls = [
        {"command": f"curl -H 'Authorization: Bearer ${close_env}' https://api.close.com/x", "output_tail": refused},
        {
            "command": "curl -H 'Authorization: Bearer $ACME_TOKEN' https://acme.example/x",
            "output_tail": "HTTP/1.1 403",
        },
        {
            "command": "curl https://acme.example/x -H \"X-Key: ${ACME_TOKEN}\"",
            "output_tail": '401 {"error":"missing_permissions"}',
        },
    ]
    with api.app.state.store.transaction() as c:
        assert reconnect_tasks.failed_calls(c, "cpo", calls) == []
        assert reconnect_tasks.failed_calls(c, "ops", [{**calls[1], "output_tail": refused}]) == []  # not ops' key
        assert reconnect_tasks.failed_calls(c, "cpo", [{**calls[1], "output_tail": refused}]) == [own]
        assert reconnect_tasks.failed_calls(c, "cpo", [{**calls[1], "output_tail": refused}]) == []  # one task
    [task] = tasks(api, own)
    assert task["owner_id"] == "bot:ops" and tasks(api, close) == []
