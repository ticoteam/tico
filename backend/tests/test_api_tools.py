"""API tools (backend/api_tools.py): a level picks its key, a check tells a rejected key from a working one, a member
sees only what they hold, and no key value is ever in an answer except the vault's reveal.

Ana owns the company and Ben is an admin (both credential administrators); Cara is a member."""

import base64
import ipaddress
import json

import httpx
import pytest

from backend import api_tools, egress
from backend.credentials import FILE_MIGRATION
from backend.store import Problem
from backend.tests.helpers.api import assign, get, headers, post, ready, runner
from backend.tests.helpers.credentials import explicit_only, setup
from backend.tests.helpers.api_tools import key, level, tool

SECRET_RW = "acme-close-synthetic-rw-0011223344"
SECRET_R = "acme-close-synthetic-ro-5566778899"
PUBLIC = "93.184.215.14"


class Close:
    """Close's API at the network edge: answers every request with the status it is set to, and records each one."""

    def __init__(self):
        self.status, self.seen = 200, []

    def __call__(self, request):
        self.seen.append(request)
        body = {"id": "synthetic"} if self.status == 200 else {"error": "synthetic"}
        return httpx.Response(self.status, json=body)


@pytest.fixture
def close(monkeypatch):
    """Close's name resolves to a public address, and checks go to a fake Close at that address."""
    vendor = Close()
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address(PUBLIC)])
    monkeypatch.setattr(api_tools, "TRANSPORT", httpx.MockTransport(vendor))
    return vendor


def choose(api, tid, subject):
    with api.app.state.store.read() as c:
        row, lv = api_tools.key_for(c, tid, subject)
        return row["id"], lv, api_tools.subject_level(c, tid, subject)


def test_a_level_picks_its_key_and_setting_one_takes_the_others_away(api):
    setup(api)
    made = tool(api)
    tid = made["id"]
    assert (made["base_url"], made["delivery"]) == ("https://api.close.com", "proxy")
    full = key(api, tid, SECRET_RW)["id"]
    explicit_only(api)  # what explicit grants alone give
    # One key: Read is that key held at Read (the proxy then allows only reads).
    level(api, tid, "bot:ops", "read")
    assert choose(api, tid, "bot:ops") == (full, "read", "read")
    read = key(api, tid, SECRET_R, "read")["id"]
    key(api, tid, SECRET_R + "x", "read", expected=409)  # one key per level
    level(api, tid, "bot:ops", "read")
    assert choose(api, tid, "bot:ops") == (read, "read", "read")
    level(api, tid, "bot:ops", "read_write")
    assert choose(api, tid, "bot:ops") == (full, "read_write", "read_write")
    with api.app.state.store.read() as c:
        held = {
            r[0]
            for r in c.execute(
                "SELECT credential_id FROM credential_grants WHERE subject='bot:ops' AND revoked_at IS NULL"
            )
        }
    assert held == {full}  # never two values for one variable
    level(api, tid, "bot:ops", "none")
    with api.app.state.store.read() as c:
        assert api_tools.subject_level(c, tid, "bot:ops") is None
        with pytest.raises(Problem) as refused:
            api_tools.key_for(c, tid, "bot:ops")
    assert refused.value.status == 403 and "Who can use it" in refused.value.detail
    level(api, tid, "bot:ops", "read_write", "cara-test", expected=403)


def test_a_member_sees_only_the_tools_they_hold_and_cannot_manage_them(api):
    setup(api)
    tid = tool(api)["id"]
    key(api, tid, SECRET_RW)
    level(api, tid, "bot:ops", "read_write")
    rows = lambda token: {r["id"]: r for r in get(api, "tool-index", token)["rows"]}  # noqa: E731 - a lookup used 3 times
    assert "api:" + tid in rows("ben-test")
    assert rows("cara-test")["api:" + tid]["requestable"] is True  # its name alone, to ask for access
    assert rows("cara-test")["api:" + tid]["who"] == []
    get(api, f"api-tools/{tid}", "cara-test", expected=404)
    level(api, tid, "human:cara", "read")
    row = rows("cara-test")["api:" + tid]
    assert [w["id"] for w in row["who"]] == ["cara"]  # her own row only, not the bots Ana gave it to
    page = get(api, f"api-tools/{tid}", "cara-test")
    assert page["can_manage"] is False and [a["subject"] for a in page["access"]] == ["human:cara"]
    post(api, f"api-tools/{tid}/keys", {"name": "x", "value": "y" * 20}, "cara-test", expected=403)
    r = api.patch(f"/api/v2/api-tools/{tid}", json={"delivery": "computer"}, headers=headers("cara-test"))
    assert r.status_code == 403
    assert len(get(api, f"api-tools/{tid}")["access"]) == 2


def test_a_demoted_key_adder_can_remove_a_rotated_key_only_while_the_tool_is_visible(api):
    setup(api)
    tid = tool(api)["id"]
    cid = key(api, tid, SECRET_RW, token="ben-test")["id"]
    path = f"/api/v2/api-tools/{tid}/keys/{cid}"
    assert api.patch(path, headers=headers(), json={"value": SECRET_RW + "rotated"}).status_code == 200
    api.app.state.store.settings.credential_admins = ("ana@acme.example",)
    assert api.delete(path, headers=headers("ben-test")).status_code == 404
    assert api.delete(f"/api/v2/credentials/{cid}", headers=headers("ben-test")).status_code == 403

    r = api.patch(f"/api/v2/api-tools/{tid}", headers=headers(), json={"audience": {"people": ["ben", "cara"]}})
    assert r.status_code == 200, r.text
    page = get(api, f"api-tools/{tid}", "ben-test")
    assert not page["can_manage"] and page["keys"][0]["can_remove"] and not page["keys"][0]["preview"]
    assert not get(api, f"api-tools/{tid}", "cara-test")["keys"][0]["can_remove"]
    assert api.delete(path, headers=headers("cara-test")).status_code == 403
    assert get(api, f"api-tools/{tid}")["keys"][0]["id"] == cid
    assert api.delete(path, headers=headers("ben-test")).json()["deleted"]
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT actor FROM events WHERE action='tool.key_deleted' AND target=?", (tid,)).fetchone()[0]
            == "human:ben"
        )


def test_a_person_whose_computer_brought_a_key_over_can_remove_it_like_one_they_added(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='ben' WHERE bot='ops'")  # Ben's bot, on Ben's computer
    machine = runner(api, operator="ben")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key=?", (json.dumps(["ops"]), FILE_MIGRATION))
    values = {"bot": "ops", "credentials": [{"env": "QA_MOVED_KEY", "value": "fixture-moved-value-123456"}]}
    post(api, "runner-credential-migration", values, machine["token"])
    with api.app.state.store.read() as c:
        cid, tid = c.execute("SELECT id,tool_id FROM credentials WHERE env='QA_MOVED_KEY'").fetchone()
    path = f"/api/v2/api-tools/{tid}/keys/{cid}"
    # As in the rotated-key case above: Ben no longer manages credentials, and sees the tool through its audience.
    api.app.state.store.settings.credential_admins = ("ana@acme.example",)
    r = api.patch(f"/api/v2/api-tools/{tid}", headers=headers(), json={"audience": {"people": ["ben", "cara"]}})
    assert r.status_code == 200, r.text
    page = get(api, f"api-tools/{tid}", "ben-test")
    assert not page["can_manage"] and page["keys"][0]["can_remove"]
    assert not get(api, f"api-tools/{tid}", "cara-test")["keys"][0]["can_remove"]
    assert api.delete(path, headers=headers("cara-test")).status_code == 403
    assert api.delete(path, headers=headers("ben-test")).json()["deleted"]
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM credentials WHERE id=?", (cid,)).fetchone()
        assert (
            c.execute("SELECT actor FROM events WHERE action='tool.key_deleted' AND target=?", (tid,)).fetchone()[0]
            == "human:ben"
        )


def test_a_check_marks_a_401_key_rejected_and_a_2xx_working(api, close):
    setup(api)
    tid = tool(api)["id"]
    key(api, tid, SECRET_RW)
    level(api, tid, "bot:ops", "read_write")
    close.status = 401
    got = post(api, f"api-tools/{tid}/check", {})
    assert (got["ok"], got["status_code"], got["detail"]) == (False, 401, "Key rejected")
    sent = close.seen[0]
    assert sent.url.host == PUBLIC and sent.url.path == "/api/v1/me/" and sent.headers["Host"] == "api.close.com"
    assert base64.b64decode(sent.headers["Authorization"][len("Basic ") :]).decode().partition(":")[0] == SECRET_RW
    row = {r["id"]: r for r in get(api, "tool-index")["rows"]}["api:" + tid]
    assert row["status"] == {"tone": "fail", "text": "Key rejected"} and row["needs_you"]
    close.status = 200
    assert post(api, f"api-tools/{tid}/check", {})["detail"] == "Working"
    assert get(api, f"api-tools/{tid}")["status"] == {"tone": "ok", "text": "Working"}
    close.status = 500
    assert post(api, f"api-tools/{tid}/check", {})["detail"].startswith("Check failed")
    # The background loop checks a tool once its last check is old, its key on the proxy or on computers alike.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tool_checks SET checked_at='2000-01-01T00:00:00Z'")
    with api.app.state.store.read() as c:
        assert api_tools.due(c) == [tid]
    api.patch(f"/api/v2/api-tools/{tid}", json={"delivery": "computer"}, headers=headers())
    with api.app.state.store.read() as c:
        assert api_tools.due(c) == [tid]


def test_no_answer_carries_a_key_value_but_the_vaults_reveal(api, close):
    setup(api)
    seen = []
    tid = tool(api)["id"]
    seen.append(json.dumps(key(api, tid, SECRET_RW)))
    rid = key(api, tid, SECRET_R, "read")["id"]
    seen.append(json.dumps(level(api, tid, "human:cara", "read")))
    r = api.patch(
        f"/api/v2/api-tools/{tid}/keys/{rid}",
        json={"value": SECRET_R + "-new", "expires_on": "2030-01-01"},
        headers=headers(),
    )
    assert r.status_code == 200
    seen.append(r.text)
    seen.append(json.dumps(post(api, f"api-tools/{tid}/check", {})))
    for token in ("ana-test", "cara-test"):
        seen.append(json.dumps(get(api, f"api-tools/{tid}", token)))
        seen.append(json.dumps(get(api, f"api-tools/{tid}/activity", token)))
        seen.append(json.dumps(get(api, "tool-index", token)))
    seen.append(json.dumps(get(api, "credentials")))
    text = "\n".join(seen)
    assert SECRET_RW not in text and SECRET_R not in text
    assert post(api, f"credentials/{rid}/reveal", {}, "cara-test")["value"] == SECRET_R + "-new"
    with api.app.state.store.read() as c:
        logged = json.dumps(
            [dict(r) for r in c.execute("SELECT * FROM events")]
            + [dict(r) for r in c.execute("SELECT * FROM tool_checks")]
        )
    assert SECRET_RW not in logged and SECRET_R not in logged
    assert api.delete(f"/api/v2/api-tools/{tid}", headers=headers()).json()["deleted"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credentials WHERE tool_id=?", (tid,)).fetchone()[0] == 0


def test_a_webhook_or_signing_secret_of_a_known_service_gets_no_tool(api):
    """A bot checks a delivery's signature with the secret itself: put on GitHub's or Stripe's proxied tool, the run
    would hold a proxy token in its place and every signature would fail."""
    setup(api)
    made = [
        post(api, "credentials", {"name": "GitHub webhook secret", "env": "GITHUB_WEBHOOK_SECRET", "secret": SECRET_R}),
        post(api, "credentials", {"name": "Stripe", "env": "STRIPE_WEBHOOK_SECRET", "secret": SECRET_R + "-2"}),
        post(api, "credentials", {"name": "Slack signing secret", "env": "ACME_SLACK", "secret": SECRET_R + "-3"}),
    ]
    with api.app.state.store.read() as c:
        assert [c.execute("SELECT tool_id FROM credentials WHERE id=?", (m["id"],)).fetchone()[0] for m in made] == [
            None
        ] * 3
        assert c.execute("SELECT count(*) FROM tools").fetchone()[0] == 0


def test_a_key_stored_outside_tools_gets_a_tool_on_the_proxy_only_for_a_known_service(api):
    """Settings' vault and a chat card store through the vault: each tool key lands on a tool in the same write. A known
    service's key fills that service's tool while it has no Read and write key."""
    setup(api)
    tid = tool(api)["id"]  # Close CRM, no key yet
    close = post(api, "credentials", {"name": "Close API key", "env": "CLOSE_API_KEY", "secret": SECRET_RW})["id"]
    own = post(api, "credentials", {"name": "Acme internal", "env": "ACME_TOKEN", "secret": SECRET_R})["id"]
    post(api, "credentials", {"name": "Bank", "kind": "password", "secret": "synthetic-password"})
    post(api, "credentials", {"name": "OpenAI", "env": "OPENAI_API_KEY", "secret": SECRET_R + "-model"})
    # Model keys a bot is granted (pi through OpenRouter, Gemini, Grok): the CLI calls the vendor with them directly,
    # so they are never a tool's and never go through the proxy.
    for env in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY"):
        post(api, "credentials", {"name": env, "env": env, "secret": SECRET_R + "-" + env.lower()})
    second = post(api, "credentials", {"name": "Close backup", "env": "CLOSE_API_KEY", "secret": SECRET_RW + "-2"})[
        "id"
    ]
    with api.app.state.store.read() as c:
        keys = {r["id"]: r["tool_id"] for r in c.execute("SELECT id, tool_id FROM credentials")}
        tools = {r["id"]: dict(r) for r in c.execute("SELECT id, delivery, service FROM tools")}
    assert keys[close] == tid and tools[tid]["delivery"] == "proxy"
    assert keys[second] not in (None, tid) and tools[keys[second]]["delivery"] == "proxy"  # Close already had its key
    assert tools[keys[own]]["delivery"] == "computer" and tools[keys[own]]["service"] == ""
    assert len(tools) == 3  # the password and the model keys are no tool's
    rows = {r["id"]: r for r in get(api, "tool-index")["rows"]}
    assert {"api:" + tid, "api:" + keys[own], "api:" + keys[second]} <= set(rows)
    assert rows["api:" + keys[own]]["status"]["text"] == "Not checked" and not rows["api:" + keys[own]]["needs_you"]


def test_a_key_a_computer_migrates_from_its_files_stays_on_computers(api):
    """A key a bot already uses from its computer's files moves into the vault through the migration, and its tool
    stays on computers, a known service or not."""
    setup(api)
    machine = runner(api)
    assign(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key=?", (json.dumps(["ops"]), FILE_MIGRATION))
    values = {"bot": "ops", "credentials": [{"env": "STRIPE_API_KEY", "value": SECRET_R + "-stripe"}]}
    post(api, "runner-credential-migration", values, machine["token"])
    with api.app.state.store.read() as c:
        service, delivery = c.execute(
            "SELECT t.service, t.delivery FROM credentials k JOIN tools t ON t.id = k.tool_id "
            "WHERE k.env = 'STRIPE_API_KEY'"
        ).fetchone()
    assert (service, delivery) == ("stripe", "computer")


def test_a_tool_and_its_first_key_are_one_write_taken_once(api, monkeypatch):
    setup(api)
    body = {"name": "Stripe", "service": "stripe", "key": SECRET_RW}
    send = lambda: api.post("/api/v2/api-tools", json=body, headers=headers() | {"Idempotency-Key": "paste-1"})  # noqa: E731
    first, again = send(), send()
    # No key sent, none fingerprinted: a retry across the deploy that added `key` is the same request.
    plain = api_tools._Receipt(api_tools.ToolCreate(name="Stripe"), omit_none=("key",)).model_dump()
    assert "key" not in plain
    assert first.status_code == again.status_code == 200 and first.json()["id"] == again.json()["id"]
    page = get(api, f"api-tools/{first.json()['id']}")
    assert [k["access_level"] for k in page["keys"]] == ["read_write"] and SECRET_RW not in json.dumps(page)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tools WHERE service='stripe'").fetchone()[0] == 1

    # A second connection of the service gets its key in a variable of its own: the plain one stays the first's.
    second = api.post(
        "/api/v2/api-tools",
        json={"name": "Stripe sandbox", "service": "stripe", "key": SECRET_R},
        headers=headers() | {"Idempotency-Key": "paste-3"},
    ).json()["id"]
    envs = [get(api, f"api-tools/{t}")["keys"][0]["env"] for t in (first.json()["id"], second)]
    assert envs == ["STRIPE_API_KEY", "STRIPE_API_KEY_2"]

    def refused(*_args, **_kw):
        raise Problem("secret", "Made-up refusal", 422)

    monkeypatch.setattr(api_tools, "add_key", refused)
    r = api.post(
        "/api/v2/api-tools", json={**body, "name": "Stripe two"}, headers=headers() | {"Idempotency-Key": "paste-2"}
    )
    assert r.status_code == 422
    with api.app.state.store.read() as c:  # a refused key makes no tool
        assert c.execute("SELECT count(*) FROM tools WHERE service='stripe'").fetchone()[0] == 2


def test_an_agent_never_brings_a_tools_first_key(api):
    """A key is a person's own paste: refused on an agent's add, with a person's rights or not, and Agent Ops' body
    check names it as a secret on this route; the catalog's tool without one still goes."""
    from dataclasses import replace

    from backend import agent_ops_act
    from backend.auth import Identity

    setup(api)
    vault = api.app.state.vault
    lent = replace(Identity("human:ana", "owner"), via="assistant")
    body = api_tools.ToolCreate(name="Stripe", service="stripe", key=SECRET_RW)
    with api.app.state.store.transaction() as c, pytest.raises(Problem) as refused:
        api_tools.create_tool(c, lent, body, vault.admins, vault)
    assert refused.value.code == "secret_in_request" and SECRET_RW not in str(refused.value.detail)
    assert agent_ops_act.secret_in_request("POST", "api-tools", {"name": "Stripe", "key": "x"}) == "key"
    assert agent_ops_act.secret_in_request("POST", "api-tools", {"name": "Stripe", "service": "stripe"}) is None
