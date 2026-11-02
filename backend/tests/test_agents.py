"""An external agent (a Hermes profile) as a bot: registered, seen, reachable, never dispatched to."""

import json

from backend.store import H
from backend.tests.test_api import api, assign, get, headers, post, runner  # noqa: F401
from backend.tests.test_mcp import call, rpc


def hermes_bot(api, slug="scout", status="active", token="ana-test", **overrides):
    return post(api, "bots", {"slug": slug, "display_name": "Scout", "description": "A Hermes profile.",
                              "reports_to": None, "status": status, "repo": "emp-" + slug,
                              "thread_mode": "personal", "model": "hermes-profile",
                              "effort": "as-configured", "harness": "hermes",
                              "operator": "ana", "owners": ["ana"], "runner_id": None, **overrides},
                token=token)


def credential(api, slug="scout"):
    return post(api, "bots/" + slug + "/agent-credential", {})


def bot_id(api, slug="scout"):
    with api.app.state.store.read() as c:
        return H.resolve_bot(c, slug)["id"]     # jobs, agents, health issues and actors are keyed by the id


def beat(api, token, **fields):
    return post(api, "agents/heartbeat", {"version": "0.19.0", "platform": "darwin",
                                          "model": "gpt-5.6-luna", "provider": "openai-codex",
                                          "profile": "scout", **fields}, token=token)


def issues(api, slug):
    bot = bot_id(api, slug)
    return [i for i in api.get("/api/status", headers=headers()).json()["health_issues"] if i["bot"] == bot]


def test_a_message_to_a_hermes_bot_waits_in_its_inbox_and_the_agent_answers_over_mcp(api):
    hermes_bot(api)
    scout = bot_id(api)
    token = credential(api)["token"]
    beat(api, token)
    msg = post(api, "chat/scout", {"text": "What did you find today?"})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id=?", (scout,)).fetchone()[0] == 0, "no runner will claim it"
    # The page has nothing to say about execution: no job, no machine to wait for.
    snapshot = api.get("/api/v2/conversations/" + msg["conversation_id"] + "/snapshot", headers=headers()).json()
    assert snapshot["execution"] is None
    # The agent sees it in its inbox through the same tools every bot has.
    failed, inbox = call(api, "hub_message_list", token=token)
    assert not failed and [m["body"] for m in inbox["messages"]] == ["What did you find today?"]
    assert beat(api, token)["waiting"]["messages"] == 1
    # It may read the conversation it is in, and answer in it.
    page = get(api, "conversations/" + msg["conversation_id"] + "/messages", token=token)
    assert [m["body"] for m in page] == ["What did you find today?"]
    failed, sent = call(api, "hub_message_send", {"to": "ana", "text": "Three leads, all in Austin.",
                                          "conversation_id": msg["conversation_id"]}, token=token)
    assert not failed and sent["sender_id"] == H.bot_actor(scout)
    failed, acked = call(api, "hub_message_mark_read", {"message_id": msg["id"]}, token=token)
    assert not failed and acked == {"read": True}
    assert call(api, "hub_message_list", token=token)[1]["messages"] == []
    assert [m["body"] for m in get(api, "conversations/" + msg["conversation_id"] + "/messages")] == [
        "What did you find today?", "Three leads, all in Austin."]
    # One credential is the whole bot: every room it is in is readable, a room it is not in is not.
    post(api, "bots/scout/owners", {"owners": ["ana", "ben"], "expected_revision":
         next(b for b in get(api, "bots") if b["slug"] == "scout")["revision"]})
    other = post(api, "chat/scout", {"text": "Private question."}, token="ben-test")
    assert other["conversation_id"] != msg["conversation_id"]
    assert [m["body"] for m in get(api, "conversations/" + other["conversation_id"] + "/messages", token=token)] == ["Private question."]
    ops = post(api, "chat/ops", {"text": "Hello Tico."})
    assert api.get("/api/v2/conversations/" + ops["conversation_id"] + "/messages",
                   headers=headers(token)).status_code == 403


def test_agent_credentials_rotate_revoke_and_follow_the_bots_state(api):
    hermes_bot(api)
    scout = bot_id(api)
    first = credential(api)["token"]
    assert get(api, "me", token=first)["actor"] == H.bot_actor(scout)
    second = credential(api)["token"]
    assert second != first
    assert api.get("/api/v2/me", headers=headers(first)).status_code == 401
    assert get(api, "me", token=second)["actor"] == H.bot_actor(scout)
    # A paused bot's credential stops working until a person reactivates it.
    post(api, "bots/scout/definition", {"status": "paused", "expected_revision":
         next(b for b in get(api, "bots") if b["slug"] == "scout")["revision"]})
    r = api.get("/api/v2/me", headers=headers(second))
    assert r.status_code == 409 and "paused" in r.json()["error"]["detail"]
    post(api, "bots/scout/definition", {"status": "active", "expected_revision":
         next(b for b in get(api, "bots") if b["slug"] == "scout")["revision"]})
    assert get(api, "me", token=second)["actor"] == H.bot_actor(scout)
    assert post(api, "bots/scout/agent-credential/revoke", {}) == {"bot": scout, "revoked": True}
    assert api.get("/api/v2/me", headers=headers(second)).status_code == 401
    assert api.post("/api/v2/bots/scout/agent-credential/revoke", json={}, headers=headers()).status_code == 404
    (issue,) = issues(api, "scout")
    assert issue["title"] == "Scout has no agent credential"
    # A bot on a computer never gets one.
    r = api.post("/api/v2/bots/ops/agent-credential", json={}, headers=headers())
    assert r.status_code == 422 and "registered computer" in r.json()["error"]["detail"]
    # Only the bot's manager mints one.
    assert api.post("/api/v2/bots/scout/agent-credential", json={}, headers=headers("cara-test")).status_code == 403


def test_the_mcp_door_lists_ack_and_the_agent_cannot_act_as_a_runner(api):
    hermes_bot(api)
    token = credential(api)["token"]
    tools = {t["name"] for t in rpc(api, "tools/list", token=token)["result"]["tools"]}
    assert "hub_message_mark_read" in tools and "hub_message_list" in tools
    # Runner-only doors stay shut to an agent credential.
    assert api.post("/api/v2/runners/heartbeat", json={"version": "x", "platform": "x", "readiness": {}},
                    headers=headers(token)).status_code == 403
    assert api.post("/api/v2/jobs/claim", json={"bot": "scout"}, headers=headers(token)).status_code == 403
    # And a runner credential cannot heartbeat as an agent.
    machine = runner(api)
    assert api.post("/api/v2/agents/heartbeat", json={}, headers=headers(machine["token"])).status_code == 403


def test_agent_unread_inbox_is_privacy_checked_in_one_batch(api, monkeypatch):
    """Health/status used to re-fetch each unread inbox message; one readable_messages pass covers them."""
    from backend import task_privacy as privacy

    hermes_bot(api)
    scout = bot_id(api)
    token = credential(api)["token"]
    beat(api, token)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE agents SET last_seen_at=? WHERE bot=?", (H.shift(H.now(), seconds=-600), scout))
    for i in range(12):
        post(api, "chat/scout", {"text": f"Note {i}"})
    calls = []
    real = privacy.readable_messages

    def once(c, actor, messages):
        calls.append(len(list(messages)))
        return real(c, actor, messages)

    monkeypatch.setattr(privacy, "readable_messages", once)
    (issue,) = issues(api, "scout")
    assert "12 unread messages waiting in its inbox" in issue["detail"]
    # Other status paths may call readable_messages with empty lists; the agent inbox is one batched pass.
    assert calls.count(12) == 1


def test_agent_listing_reads_access_and_config_once(api, monkeypatch):
    """Operations embeds agents.listing; per-agent bot_access + bot_config reads used to scale with agents."""
    from backend import agents, read_cache
    from backend.auth import Auth

    hermes_bot(api, slug="scout")
    credential(api, "scout")
    hermes_bot(api, slug="ranger", display_name="Ranger")
    post(api, "bots/ranger/agent-credential", {})
    scout, ranger = bot_id(api, "scout"), bot_id(api, "ranger")
    access_calls, config_calls = [], []
    real_accesses = Auth.bot_accesses
    real_configs = read_cache.configs

    def accesses(self, c, who, slugs=None):
        access_calls.append(list(slugs) if slugs is not None else None)
        return real_accesses(self, c, who, slugs)

    def configs(c, slugs=None):
        config_calls.append(list(slugs) if slugs is not None else None)
        return real_configs(c, slugs)

    monkeypatch.setattr(Auth, "bot_accesses", accesses)
    monkeypatch.setattr(read_cache, "configs", configs)
    store, auth = api.app.state.store, api.app.state.auth
    who = store.settings.test_identities["ana-test"]
    with store.read() as c:
        rows = agents.listing(c, who, auth)
    assert {r["bot"] for r in rows} >= {scout, ranger}
    assert len(access_calls) == 1 and set(access_calls[0]) >= {scout, ranger}
    assert any(calls and set(calls) >= {scout, ranger} for calls in config_calls)
