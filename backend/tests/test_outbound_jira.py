"""Jira's sender for the outbound gate (backend/outbound_jira.py, docs/outbound-actions.md "Jira"): a transition moves
the issue through the transition Jira offers to the status, a comment carries the action's marker, an effect already
there is done without a write, a write whose answer was lost is settled from Jira's record, a 429 waits its Retry-After,
only a record integration with a shared read and write key takes writes, only from a bot holding Read and write on it,
an answer cut off or misshapen writes nothing, the connection's site stays while an action is pending, and Jira writes
go on their own by default."""

import base64
import ipaddress
import json
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from backend import egress, page_owners
from backend.auth import Identity
from backend import outbound_actions as OA
from backend import outbound_jira as OJ
from backend.item_sources import jira as JS
from backend.store import H
from backend.tests.helpers.api import post
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.tasks_board import api, headers  # noqa: F401  (fixture)

HOST = "acme.atlassian.net"
TOKEN = "jira-synthetic-acme-0011223344"
WORKFLOW = {
    "To Do": [("11", "Start", "In Progress"), ("31", "Finish", "Done")],
    "In Progress": [("31", "Finish", "Done")],
}


class FakeJira:
    """Jira Cloud at the network edge: one issue's status, its workflow's transitions and its comments. `answer` (a
    status, headers) answers the next write instead; `lose` makes the next write land but its answer never arrive."""

    def __init__(self):
        self.status: dict[str, str] = {}
        self.comments: dict[str, list[dict]] = {}
        self.writes: list[httpx.Request] = []
        self.answer: tuple[int, dict] | None = None
        self.lose = False
        self.garble: str = ""  # a lookup path ending this answers 200 with a page that is not JSON

    def __call__(self, request: httpx.Request) -> httpx.Response:
        wanted = "Basic " + base64.b64encode(f"ops@acme.example:{TOKEN}".encode()).decode()
        if request.headers.get("authorization") != wanted:
            return httpx.Response(401, json={"errorMessages": ["synthetic"]})
        parts = request.url.path.split("/")  # /rest/api/3/issue/<key>[/<what>]
        key, what = parts[5], (parts[6] if len(parts) > 6 else "")
        if key not in self.status:
            return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
        if request.method == "GET":
            if self.garble and request.url.path.endswith(self.garble):
                return httpx.Response(200, text="<html>Jira is thinking</html>")
            if what == "transitions":
                offered = [{"id": i, "name": n, "to": {"name": to}} for i, n, to in WORKFLOW.get(self.status[key], [])]
                return httpx.Response(200, json={"transitions": offered})
            if what == "comment":
                return httpx.Response(200, json={"comments": list(reversed(self.comments.get(key, [])))})
            status = {
                "name": self.status[key],
                "statusCategory": {"key": "done" if self.status[key] == "Done" else "new"},
            }
            return httpx.Response(
                200, json={"key": key, "fields": {"status": status, "updated": "2026-11-21T10:00:00.000+0000"}}
            )
        self.writes.append(request)
        if self.answer:
            status, more = self.answer
            self.answer = None
            return httpx.Response(status, headers=more, json={"errorMessages": ["synthetic"]})
        body = json.loads(request.content)
        if what == "transitions":
            target = next(to for i, _, to in WORKFLOW[self.status[key]] if i == body["transition"]["id"])
            self.status[key] = target
            response = httpx.Response(204)
        else:
            made = {"id": str(10000 + len(self.comments.setdefault(key, []))), "body": body["body"]}
            self.comments[key].append(made)
            response = httpx.Response(201, json=made)
        if self.lose:
            self.lose = False
            raise httpx.ReadError("connection reset after the write")
        return response


def _tool(api, name, key_level="read_write", record=True):
    made = post(api, "api-tools", {"name": name, "base_url": f"https://{HOST}", "auth_scheme": "basic_password"})
    body = {"name": "Jira key", "value": TOKEN, "username": "ops@acme.example", "access_level": key_level}
    post(api, f"api-tools/{made['id']}/keys", body)
    if record:
        r = api.patch(f"/api/v2/api-tools/{made['id']}", json={"record_owner": "outside"}, headers=headers("ana-test"))
        assert r.status_code == 200, r.text
    return made["id"]


def _key(api, tool, level):
    with _store(api).read() as c:
        return c.execute("SELECT id FROM credentials WHERE tool_id=? AND access_level=?", (tool, level)).fetchone()[0]


def _grant(api, key, bot):
    with _store(api).transaction() as c:
        api.app.state.vault.grant(c, Identity("human:ana", "owner", "ana@acme.example"), key, bot)


@pytest.fixture
def jira(api, monkeypatch):
    """A Jira site that is a record integration with a shared read and write key, and a fake Jira behind it."""
    setup(api)
    vendor = FakeJira()
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address("93.184.215.14")])
    monkeypatch.setattr(JS, "TRANSPORT", httpx.MockTransport(vendor))
    vendor.tool = _tool(api, "Jira Acme")
    post(
        api,
        f"api-tools/{vendor.tool}/keys",
        {"name": "Jira read", "value": TOKEN, "username": "ops@acme.example", "access_level": "read"},
    )
    _grant(api, _key(api, vendor.tool, "read_write"), "bot:ops")  # ops may write through it; cpo may only read
    _grant(api, _key(api, vendor.tool, "read"), "bot:cpo")
    return vendor


def _store(api):
    return api.app.state.store


def _queue(api, connection, operation, payload, key="k1", issue="hm-12"):
    with _store(api).transaction() as c:
        row, _ = OA.enqueue(
            c,
            _store(api).settings,
            actor="bot:ops",
            channel="jira",
            operation=operation,
            destination=issue,
            occurrence_key=key,
            payload=payload,
            connection_id=connection,
        )
    return row


def _row(api, action_id):
    with _store(api).read() as c:
        return OA.get(c, action_id)


def _attempt(api):
    return OA.attempt(_store(api), OA.claim(_store(api)))


def test_a_transition_goes_through_the_transition_jira_offers_to_the_status(api, jira):
    jira.status["HM-12"] = "To Do"
    row = _queue(api, jira.tool, "transition", {"to": "done"})
    assert (row["destination"], row["audience"], row["state"]) == ("HM-12", "external", "queued")
    assert json.loads(row["policy_decision"])["choice"] == "on_their_own"
    assert _attempt(api) == "done"
    assert [json.loads(w.content) for w in jira.writes] == [{"transition": {"id": "31"}}]
    after = _row(api, row["id"])
    assert jira.status["HM-12"] == "Done" and json.loads(after["receipt"]) == {"transition_id": "31", "status": "Done"}


def test_an_issue_already_in_the_status_is_done_without_a_write(api, jira):
    jira.status["HM-12"] = "Done"
    row = _queue(api, jira.tool, "transition", {"to": "Done"})
    assert _attempt(api) == "done" and jira.writes == []
    assert json.loads(_row(api, row["id"])["receipt"]) == {"status": "Done", "already": True}


def test_a_status_jira_offers_no_transition_to_fails_without_a_write(api, jira):
    jira.status["HM-12"] = "To Do"
    row = _queue(api, jira.tool, "transition", {"to": "Shipped"})
    assert _attempt(api) == "failed" and jira.writes == []
    assert "it offers: Done, In Progress" in _row(api, row["id"])["last_error"]


def test_a_comment_whose_answer_was_lost_is_found_by_its_marker_and_settled_done(api, jira):
    jira.status["HM-12"] = "In Progress"
    jira.lose = True
    row = _queue(api, jira.tool, "comment", {"body": "Released in v1.2.\n\nThanks!"})
    assert _attempt(api) == "unknown"
    assert (
        _row(api, row["id"])["last_error"] == "RuntimeError: Jira could not be reached"
    )  # never the transport's words
    [comment] = jira.comments["HM-12"]
    mark = OJ.marker("k1")
    assert re.fullmatch(r"\[tico:[0-9a-f]{10}\]", mark)
    assert [p["content"][0]["text"] for p in comment["body"]["content"]] == ["Released in v1.2.", "Thanks!", mark]
    # A retry of the same action finds it rather than writing a second one, and the reconcile settles the row.
    unknown = _row(api, row["id"])
    assert OJ.dispatch(_store(api), unknown) == {"comment_id": "10000", "already": True}
    with _store(api).transaction() as c:  # the write key gone: the reconcile only reads, with the read key
        c.execute("UPDATE credentials SET ciphertext=NULL WHERE id=?", (_key(api, jira.tool, "read_write"),))
    OA.reconcile_pass(_store(api))
    after = _row(api, row["id"])
    assert after["state"] == "done" and json.loads(after["receipt"])["comment_id"] == "10000"
    assert len(jira.comments["HM-12"]) == 1 and len(jira.writes) == 1


def test_an_unknown_comment_on_an_issue_jira_no_longer_shows_stays_unknown_for_the_owner(api, jira):
    jira.status["HM-12"] = "In Progress"
    jira.lose = True
    row = _queue(api, jira.tool, "comment", {"body": "Released."})
    assert _attempt(api) == "unknown"
    del jira.status["HM-12"]  # Jira's 404: gone, or the key no longer sees it; neither proves the comment didn't land
    OA.reconcile_pass(_store(api))
    assert _row(api, row["id"])["state"] == "unknown"


def test_a_429_requeues_the_write_after_its_retry_after(api, jira):
    jira.status["HM-12"] = "To Do"
    jira.answer = (429, {"Retry-After": "30"})
    row = _queue(api, jira.tool, "comment", {"body": "Picked up."})
    assert _attempt(api) == "requeued"
    after = _row(api, row["id"])
    assert (after["state"], after["provider_attempted_at"]) == ("queued", None)
    assert H.shift(H.now(), seconds=20) < after["next_attempt_at"] <= H.shift(H.now(), seconds=31)
    assert jira.comments.get("HM-12", []) == []


def test_only_a_record_integration_with_a_shared_write_key_takes_writes(api, jira):
    jira.status["HM-12"] = "To Do"
    for connection in (_tool(api, "Jira personal", record=False), _tool(api, "Jira reader", key_level="read"), ""):
        with pytest.raises(OA.Problem) as refused:
            _queue(api, connection, "transition", {"to": "Done"}, key=f"k-{connection}")
        assert refused.value.status == 422
    with pytest.raises(OA.Problem):
        _queue(api, jira.tool, "transition", {"to": "Done"}, key="k-bad", issue="not an issue")


def test_jira_writes_go_on_their_own_until_the_company_says_otherwise(api, jira):
    jira.status["HM-12"] = "To Do"
    policy = api.get("/api/v2/outbound-actions/policy", headers=headers("ana-test")).json()
    assert policy["effective"]["jira:external"] == "on_their_own" and "jira" in policy["channels"]
    r = api.put(
        "/api/v2/outbound-actions/policy",
        json={"settings": {"jira:external": "ask_first"}},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    assert _queue(api, jira.tool, "comment", {"body": "Asked first."})["state"] == "waiting_approval"


def _as_bot(api, slug):
    """The bot signed in with its own agent credential."""
    with _store(api).transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            (slug, "test", "x-" + slug, H.now(), "human:ana"),
        )
    api.app.state.auth.settings.test_identities[slug + "-bot"] = Identity(f"bot:{slug}", "bot", agent="test")
    return slug + "-bot"


def test_only_a_bot_with_read_and_write_on_the_connection_queues_a_write_and_a_revoke_stops_it(api, jira):
    jira.status["HM-12"] = "To Do"
    body = {"channel": "jira", "operation": "comment", "destination": "HM-12", "connection_id": jira.tool}
    with _store(api).transaction() as c:
        page_owners.save(c, "human:ana", "tools", "cmo")  # the Tools page's owner reads every tool, writes none
    r = api.post(  # cpo holds the read key only
        "/api/v2/outbound-actions",
        json={**body, "occurrence_key": "cpo", "payload": {"body": "x"}},
        headers=headers(_as_bot(api, "cpo")),
    )
    assert r.status_code == 403, r.text
    with pytest.raises(OA.Problem) as refused:  # the Tools owner sees the tool in its own run; that is no write
        with _store(api).transaction() as c:
            OA.enqueue(
                c,
                _store(api).settings,
                actor="bot:cmo",
                channel="jira",
                operation="comment",
                destination="HM-12",
                occurrence_key="cmo",
                payload={"body": "x"},
                connection_id=jira.tool,
            )
    assert refused.value.status == 403
    made = api.post(
        "/api/v2/outbound-actions",
        json={**body, "occurrence_key": "ok", "payload": {"body": "Hi."}},
        headers=headers(_as_bot(api, "ops")),
    )
    assert made.status_code == 200 and made.json()["action"]["state"] == "queued", made.text
    with _store(api).transaction() as c:  # the grant taken back before it goes
        c.execute("UPDATE credential_grants SET revoked_at=? WHERE subject='bot:ops'", (H.now(),))
    assert _attempt(api) == "blocked" and jira.writes == []
    assert "Read and write" in _row(api, made.json()["action"]["id"])["last_error"]


def test_the_connections_site_stays_while_an_action_to_it_is_pending(api, jira):
    jira.status["HM-12"] = "To Do"
    _queue(api, jira.tool, "comment", {"body": "Soon."})
    move = {"base_url": "https://beta.atlassian.net"}
    r = api.patch(f"/api/v2/api-tools/{jira.tool}", json=move, headers=headers("ana-test"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "connection_sites", r.text
    assert _attempt(api) == "done"
    assert api.patch(f"/api/v2/api-tools/{jira.tool}", json=move, headers=headers("ana-test")).status_code == 200


@pytest.mark.parametrize("garble", ["/HM-12", "/comment", "/transitions"])
def test_a_lookup_answer_that_cannot_be_read_writes_nothing_and_tries_again(api, jira, garble):
    jira.status["HM-12"] = "To Do"
    jira.garble = garble
    operation, payload = ("comment", {"body": "Hi."}) if garble == "/comment" else ("transition", {"to": "Done"})
    row = _queue(api, jira.tool, operation, payload)
    assert _attempt(api) == "requeued" and jira.writes == []
    assert _row(api, row["id"])["state"] == "queued"


def test_a_cut_off_answer_is_said_so(api, jira, monkeypatch):
    monkeypatch.setattr(JS, "MAX_BODY", 10)
    jira.status["HM-12"] = "To Do"
    reply = JS.call("GET", f"https://{HOST}/rest/api/3/issue/HM-12", {"Authorization": jira_auth()}, {})
    assert (reply.status, reply.body, reply.cut) == (200, None, True)


def jira_auth():
    return "Basic " + base64.b64encode(f"ops@acme.example:{TOKEN}".encode()).decode()


def test_only_a_comment_ending_in_the_marker_counts_as_written(api, jira):
    jira.status["HM-12"] = "To Do"
    quoted = {"type": "paragraph", "content": [{"type": "text", "text": f"Tico wrote {OJ.marker('k1')} earlier"}]}
    jira.comments["HM-12"] = [{"id": "9", "body": {"type": "doc", "version": 1, "content": [quoted]}}]
    _queue(api, jira.tool, "comment", {"body": "Hi."})
    assert _attempt(api) == "done" and len(jira.writes) == 1


def test_the_jira_sender_may_be_imported_first():
    code = "import backend.outbound_jira; from backend import outbound_actions as OA; assert 'jira' in OA.ADAPTERS"
    root = Path(__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
