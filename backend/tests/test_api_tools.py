"""API tools (backend/api_tools.py): the data migration keeps every grant and leaves each key on its computers, a level
picks its key, a check tells a rejected key from a working one, a member sees only what they hold, and no key value is
ever in an answer except the vault's reveal.

Ana owns the company and Ben is an admin (both credential administrators); Cara is a member."""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import api_tools
from backend.config import Settings
from backend.store import Problem, Store
from backend.tests.test_api import api, get, headers, post, put  # noqa: F401  (fixture)
from backend.tests.test_credentials import setup

FIXTURE = Path(__file__).parent / "fixtures" / "schema_old_main.sql"
SECRET_RW = "acme-close-synthetic-rw-0011223344"
SECRET_R = "acme-close-synthetic-ro-5566778899"


def test_the_migration_makes_a_computer_tool_per_service_key_and_keeps_every_grant(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text())
    now = "2026-10-01T00:00:00Z"
    rows = (("c-close", "Close API key", "api_key", "CLOSE_API_KEY", ""),
            ("c-own", "Acme internal", "token", "ACME_TOKEN", ""),
            ("c-model", "OpenAI", "api_key", "OPENAI_API_KEY", ""),
            ("c-computer", "Claude on the Mac", "api_key", "ANTHROPIC_API_KEY", "computers:r1"),
            ("c-bank", "Bank login", "password", "", "finance"),
            ("c-empty", "Stripe", "api_key", "STRIPE_API_KEY", ""))
    for cid, name, kind, env, source in rows:
        conn.execute("INSERT INTO credentials(id,name,kind,env,preview,ciphertext,nonce,source,created,updated,"
                     "updated_by,created_by) VALUES(?,?,?,?,'',?,?,?,?,?,'human:ana','human:ana')",
                     (cid, name, kind, env, None if cid == "c-empty" else b"x", None if cid == "c-empty" else b"n",
                      source, now, now))
    grants = (("g1", "c-close", "bot:ops", None), ("g2", "c-close", "human:cara", "2026-10-02T00:00:00Z"),
              ("g3", "c-own", "bot:coo", None), ("g4", "c-model", "computers", None))
    for gid, cid, subject, revoked in grants:
        conn.execute("INSERT INTO credential_grants(id,credential_id,subject,granted_by,created,revoked) "
                     "VALUES(?,?,?,'human:ana',?,?)", (gid, cid, subject, now, revoked))
    conn.commit()
    conn.close()
    Store(Settings(db_path=path)).initialize()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    tools = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM tools")}
    keys = {r["id"]: dict(r) for r in conn.execute("SELECT id, tool_id, access_level FROM credentials")}
    after = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM credential_grants")}
    conn.close()
    assert set(tools) == {"close-crm", "acme-internal"}
    close = tools["close-crm"]
    assert (close["delivery"], close["base_url"], close["auth_scheme"], close["check_path"]) == \
        ("computer", "https://api.close.com", "basic_user", "/api/v1/me/")
    assert tools["acme-internal"]["delivery"] == "computer" and tools["acme-internal"]["base_url"] == ""
    assert keys["c-close"] == {"id": "c-close", "tool_id": "close-crm", "access_level": "read_write"}
    assert all(keys[k]["tool_id"] is None for k in ("c-model", "c-computer", "c-bank", "c-empty"))
    # Every grant is there, revoked or not, at the level it always had.
    assert set(after) == {"g1", "g2", "g3", "g4"}
    assert after["g2"]["revoked_at"] == "2026-10-02T00:00:00Z" and after["g1"]["revoked_at"] is None
    assert {g["access_level"] for g in after.values()} == {"read_write"}


def tool(api, name="Close CRM", **body):
    return post(api, "api-tools", {"name": name, **body})


def key(api, tid, value, level="read_write", token="ana-test", expected=200):
    return post(api, f"api-tools/{tid}/keys", {"name": f"Close {level}", "value": value, "access_level": level},
                token, expected=expected)


def level(api, tid, subject, lv, token="ana-test", expected=200):
    return put(api, f"api-tools/{tid}/access/{subject}", {"level": lv}, token, expected=expected)


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
    # One key: Read is that key held at Read (the proxy then allows only reads).
    level(api, tid, "bot:ops", "read")
    assert choose(api, tid, "bot:ops") == (full, "read", "read")
    read = key(api, tid, SECRET_R, "read")["id"]
    key(api, tid, SECRET_R + "x", "read", expected=409)       # one key per level
    level(api, tid, "bot:ops", "read")
    assert choose(api, tid, "bot:ops") == (read, "read", "read")
    level(api, tid, "bot:ops", "read_write")
    assert choose(api, tid, "bot:ops") == (full, "read_write", "read_write")
    with api.app.state.store.read() as c:
        held = {r[0] for r in c.execute("SELECT credential_id FROM credential_grants WHERE subject='bot:ops' "
                                        "AND revoked_at IS NULL")}
    assert held == {full}                                     # never two values for one variable
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
    assert [w["id"] for w in row["who"]] == ["cara"]          # her own row only, not the bots Ana gave it to
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
        return answer.code
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
    # The background loop checks a proxied tool once its last check is old; a computer tool never.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tool_checks SET checked_at='2000-01-01T00:00:00Z'")
    with api.app.state.store.read() as c:
        assert api_tools.due(c) == [tid]
    api.patch(f"/api/v2/api-tools/{tid}", json={"delivery": "computer"}, headers=headers())
    with api.app.state.store.read() as c:
        assert api_tools.due(c) == []


def test_no_answer_carries_a_key_value_but_the_vaults_reveal(api, monkeypatch):
    setup(api)
    monkeypatch.setattr(api_tools, "send_check", lambda url, hdrs, params: 200)
    seen = []
    tid = tool(api)["id"]
    seen.append(json.dumps(key(api, tid, SECRET_RW)))
    rid = key(api, tid, SECRET_R, "read")["id"]
    seen.append(json.dumps(level(api, tid, "human:cara", "read")))
    r = api.patch(f"/api/v2/api-tools/{tid}/keys/{rid}", json={"value": SECRET_R + "-new", "expires_on": "2030-01-01"},
                  headers=headers())
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
        logged = json.dumps([dict(r) for r in c.execute("SELECT * FROM events")] +
                            [dict(r) for r in c.execute("SELECT * FROM tool_checks")])
    assert SECRET_RW not in logged and SECRET_R not in logged
    assert api.delete(f"/api/v2/api-tools/{tid}", headers=headers()).json()["deleted"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credentials WHERE tool_id=?", (tid,)).fetchone()[0] == 0


def test_a_key_stored_outside_tools_gets_a_tool_on_the_proxy_only_for_a_known_service(api):
    """Settings' vault (and a chat card, and a computer's migration) store through the vault: each tool key lands on a
    tool in the same write. A known service's key fills that service's tool while it has no Read and write key."""
    setup(api)
    tid = tool(api)["id"]                                     # Close CRM, no key yet
    close = post(api, "credentials", {"name": "Close API key", "env": "CLOSE_API_KEY", "secret": SECRET_RW})["id"]
    own = post(api, "credentials", {"name": "Acme internal", "env": "ACME_TOKEN", "secret": SECRET_R})["id"]
    post(api, "credentials", {"name": "Bank", "kind": "password", "secret": "synthetic-password"})
    post(api, "credentials", {"name": "OpenAI", "env": "OPENAI_API_KEY", "secret": SECRET_R + "-model"})
    second = post(api, "credentials", {"name": "Close backup", "env": "CLOSE_API_KEY", "secret": SECRET_RW + "-2"})["id"]
    with api.app.state.store.read() as c:
        keys = {r["id"]: r["tool_id"] for r in c.execute("SELECT id, tool_id FROM credentials")}
        tools = {r["id"]: dict(r) for r in c.execute("SELECT id, delivery, service FROM tools")}
    assert keys[close] == tid and tools[tid]["delivery"] == "proxy"
    assert keys[second] not in (None, tid) and tools[keys[second]]["delivery"] == "proxy"   # Close already had its key
    assert tools[keys[own]]["delivery"] == "computer" and tools[keys[own]]["service"] == ""
    assert len(tools) == 3                                    # the password and the model key are no tool's
    rows = {r["id"]: r for r in get(api, "tool-index")["rows"]}
    assert {"api:" + tid, "api:" + keys[own], "api:" + keys[second]} <= set(rows)
    assert rows["api:" + keys[own]]["status"]["text"] == "Key on computers"
    # A key a bot already uses from its computer's files (the file migration) stays on computers, known service or not.
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO credentials(id,name,kind,env,preview,ciphertext,nonce,source,created_at,updated_at,"
                  "updated_by) VALUES('c-file','STRIPE_API_KEY (ops)','api_key','STRIPE_API_KEY','',x'01',x'01',"
                  "'Computer migration','2026-11-05T00:00:00Z','2026-11-05T00:00:00Z','runner:r1')")
        filed = api_tools.ensure_tool(c, SimpleNamespace(actor="runner:r1"), "c-file", from_computer=True)
        assert tuple(c.execute("SELECT service, delivery FROM tools WHERE id=?", (filed,)).fetchone()) == ("stripe", "computer")
