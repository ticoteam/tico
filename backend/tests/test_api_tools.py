"""API tools (backend/api_tools.py): a level picks its key, a check tells a rejected key from a working one, a member
sees only what they hold, and no key value is ever in an answer except the vault's reveal.

Ana owns the company and Ben is an admin (both credential administrators); Cara is a member."""

import json
from types import SimpleNamespace

import pytest

from backend import api_tools
from backend.store import Problem
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.credentials import explicit_only, setup
from backend.tests.helpers.api_tools import key, level, tool

SECRET_RW = "acme-close-synthetic-rw-0011223344"
SECRET_R = "acme-close-synthetic-ro-5566778899"


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
    rows = lambda token: {r["id"]: r for r in get(api, "tool-index", token)["rows"]}  # noqa: E731
    assert "api:" + tid in rows("ben-test")
    assert "api:" + tid not in rows("cara-test")
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


def test_a_check_marks_a_401_key_rejected_and_a_2xx_working(api, monkeypatch):
    setup(api)
    tid = tool(api)["id"]
    key(api, tid, SECRET_RW)
    level(api, tid, "bot:ops", "read_write")
    sent = []

    def answer(url, hdrs, params):
        sent.append((url, hdrs, params))
        return api_tools.Reply(answer.code)

    monkeypatch.setattr(api_tools, "send_check", answer)
    answer.code = 401
    got = post(api, f"api-tools/{tid}/check", {})
    assert (got["ok"], got["status_code"], got["detail"]) == (False, 401, "Key rejected")
    assert sent[0][0] == "https://api.close.com/api/v1/me/" and sent[0][1]["Authorization"].startswith("Basic ")
    row = {r["id"]: r for r in get(api, "tool-index")["rows"]}["api:" + tid]
    assert row["status"] == {"tone": "fail", "text": "Key rejected"} and row["needs_you"]
    answer.code = 200
    assert post(api, f"api-tools/{tid}/check", {})["detail"] == "Working"
    assert get(api, f"api-tools/{tid}")["status"] == {"tone": "ok", "text": "Working"}
    answer.code = 500
    assert post(api, f"api-tools/{tid}/check", {})["detail"].startswith("Check failed")
    # The background loop checks a tool once its last check is old, its key on the proxy or on computers alike.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tool_checks SET checked_at='2000-01-01T00:00:00Z'")
    with api.app.state.store.read() as c:
        assert api_tools.due(c) == [tid]
    api.patch(f"/api/v2/api-tools/{tid}", json={"delivery": "computer"}, headers=headers())
    with api.app.state.store.read() as c:
        assert api_tools.due(c) == [tid]


def test_no_answer_carries_a_key_value_but_the_vaults_reveal(api, monkeypatch):
    setup(api)
    monkeypatch.setattr(api_tools, "send_check", lambda url, hdrs, params: api_tools.Reply(200))
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


def test_a_webhook_or_signing_secret_of_a_known_service_is_no_tools_key_and_reaches_the_bot_as_itself(api):
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
    """Settings' vault (and a chat card, and a computer's migration) store through the vault: each tool key lands on a
    tool in the same write. A known service's key fills that service's tool while it has no Read and write key."""
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
    # A key a bot already uses from its computer's files (the file migration) stays on computers, known service or not.
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO credentials(id,name,kind,env,preview,ciphertext,nonce,source,created_at,updated_at,"
            "updated_by) VALUES('c-file','STRIPE_API_KEY (ops)','api_key','STRIPE_API_KEY','',x'01',x'01',"
            "'Computer migration','2026-11-05T00:00:00Z','2026-11-05T00:00:00Z','runner:r1')"
        )
        filed = api_tools.ensure_tool(c, SimpleNamespace(actor="runner:r1"), "c-file", from_computer=True)
        assert tuple(c.execute("SELECT service, delivery FROM tools WHERE id=?", (filed,)).fetchone()) == (
            "stripe",
            "computer",
        )
