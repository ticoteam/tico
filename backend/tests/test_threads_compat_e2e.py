"""What clients read and send on today's conversation surfaces, pinned before the native threads build replaces them.

docs/dev/threads-contract.md (section 10) keeps `/api/v2/conversations/*` as aliases for one release, and the `hub` CLI
and MCP old names with them. Each test here is a behaviour a client depends on today: the paths a person or a bot
calls, the fields it parses, the cursor it walks, the answer a person who may not read a chat gets, and the claim
fields the runner keys its session on. The refactor has to keep each one true, or change the client on purpose.

Checks already made by test_api.py, test_chat_goals.py, test_openapi_v2.py, test_payload_budgets.py, test_views.py,
test_chat_outline.py, test_shared_bots.py, test_mcp.py, clients/tests/test_hubcli.py and test_runner_wire_contract.py
are not repeated here.
"""

import json

import pytest

from backend.store import H
from backend.tests.test_api import api, headers, post, setup_attempt  # noqa: F401  (the api fixture)
from backend.tests.test_mcp import call
from clients import hubcli, remotecli
from clients.tico import APIError

BODY = "A long reply about the launch plan and its checks. " * 110     # about 5.5 KB a message
CHAT_PAGE = {"limit": 50, "max_bytes": 163840}                         # the web app's chat page (ui/app/chat.js)


def read(api, cid, token="ana-test", **query):
    r = api.get(f"/api/v2/conversations/{cid}/messages", params=query, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def message_ids(c, cid):
    return [r[0] for r in c.execute("SELECT id FROM messages WHERE conversation_id=? ORDER BY rowid", (cid,))]


class ApiClient:
    """What `clients.remotecli` calls on its HTTP client, answered in-process by the API test client."""

    def __init__(self, api, token):
        self.api, self.token = api, token

    def get(self, path, /, **query):
        params = {k: v for k, v in query.items() if v is not None}
        r = self.api.get("/api/v2/" + path, params=params, headers=headers(self.token))
        if r.status_code != 200:
            raise APIError(r.json()["error"]["code"], r.json()["error"]["detail"], status=r.status_code)
        return r.json()


def test_the_conversation_list_is_the_callers_own_and_only_the_owner_filters_by_bot(api):
    mine = post(api, "chat/ops", {"text": "Ana's question"})
    theirs = post(api, "chat/ops", {"text": "Ben's question"}, "ben-test")
    listed = api.get("/api/v2/conversations", headers=headers())
    assert listed.status_code == 200 and listed.json()["actor"] == "human:ana"
    rows = listed.json()["conversations"]
    assert mine["conversation_id"] in {r["id"] for r in rows}
    assert theirs["conversation_id"] not in {r["id"] for r in rows}
    # The fields the chat list reads: which room it is, its kind, and whether it belongs to a task.
    assert {"id", "kind", "task_id", "participants"} <= set(rows[0])
    ids = lambda r: {c["id"] for c in r.json()["conversations"]}
    assert mine["conversation_id"] in ids(api.get("/api/v2/conversations", params={"chat_with": "ops"}, headers=headers()))
    owner_view = api.get("/api/v2/conversations", params={"bot": "ops"}, headers=headers())
    assert owner_view.status_code == 200 and mine["conversation_id"] in ids(owner_view)
    assert theirs["conversation_id"] not in ids(owner_view), "a personal chat is its person's, owner or not"
    refused = api.get("/api/v2/conversations", params={"bot": "ops"}, headers=headers("ben-test"))
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden"


def test_a_person_opens_a_chat_with_a_bot_posts_in_it_and_reads_the_post_back(api):
    created = post(api, "conversations", {"participants": ["bot:ops"], "kind": "chat"})
    assert created["id"] and created["kind"] == "chat" and "bot:ops" in created["participants"]
    chat = post(api, "chat/ops", {"text": "Hello"})
    assert chat["conversation_id"] == created["id"], "opening the chat again is the bot page's own chat"
    cid = created["id"]
    sent = post(api, f"conversations/{cid}/messages", {"text": "Second line"})["message"]
    assert sent["conversation_id"] == cid and sent["body"] == "Second line"
    page = read(api, cid)
    assert [m["id"] for m in page["messages"]][-2:] == [chat["id"], sent["id"]]
    assert page["messages"][-1]["body"] == "Second line"


def test_a_chat_another_person_may_not_read_looks_missing_and_leaks_no_text(api):
    secret = "Ana's private plan for the Acme renewal"
    cid = post(api, "chat/ops", {"text": secret})["conversation_id"]
    # A hidden chat looks missing (Auth.conversation answers 404 not_found, docs/dev/threads-contract.md): the same answer
    # as a chat that does not exist, so no reader can tell them apart.
    nowhere = "00000000-0000-4000-8000-000000000000"
    reads = [("GET", f"conversations/{cid}/messages"), ("GET", f"conversations/{cid}/snapshot"),
             ("GET", f"conversations/{cid}/outline")]
    for method, path in reads:
        r = api.request(method, "/api/v2/" + path, headers=headers("ben-test"))
        missing = api.request(method, "/api/v2/" + path.replace(cid, nowhere), headers=headers("ben-test"))
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", (path, r.text[:200])
        assert r.status_code == missing.status_code and r.json()["error"] == missing.json()["error"], path
        assert secret not in r.text, path
    r = api.post(f"/api/v2/conversations/{cid}/messages", json={"text": "Injected"}, headers=headers("ben-test"))
    missing = api.post(f"/api/v2/conversations/{nowhere}/messages", json={"text": "Injected"}, headers=headers("ben-test"))
    assert r.status_code == 404 and r.json()["error"] == missing.json()["error"] and secret not in r.text
    listed = api.get("/api/v2/conversations", headers=headers("ben-test")).json()["conversations"]
    assert cid not in {c["id"] for c in listed}
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE body='Injected'").fetchone()[0] == 0


def test_a_chat_pages_back_by_limit_and_by_bytes_and_every_message_is_read_once(api):
    cid = post(api, "chat/ops", {"text": "First"})["conversation_id"]
    with api.app.state.store.transaction() as c:
        for n in range(39):
            H.say(c, "human:ana", "bot:ops", f"{n} {BODY}", conversation_id=cid)
        order = message_ids(c, cid)
    # `limit` gives the newest n, oldest first, and `next_before` is the oldest of them: `before` is exclusive.
    five = read(api, cid, limit=5)
    assert [m["id"] for m in five["messages"]] == order[-5:]
    assert five["has_more"] is True and five["next_before"] == order[-5]
    assert [m["id"] for m in read(api, cid, limit=5, before=five["next_before"])["messages"]] == order[-10:-5]
    # The web app's page (50 messages, 160 KB): the byte cap ends the first page, and walking `next_before` back
    # reads every message once, in order.
    seen, cursor, pages = [], None, 0
    while True:
        query = {**CHAT_PAGE, **({"before": cursor} if cursor else {})}
        page = read(api, cid, **query)
        pages += 1
        sizes = [len(json.dumps(m, default=str).encode()) for m in page["messages"]]
        assert len(page["messages"]) == 1 or sum(sizes) <= CHAT_PAGE["max_bytes"], sizes
        seen = [m["id"] for m in page["messages"]] + seen
        if not page["has_more"]:
            break
        cursor = page["next_before"]
    assert seen == order and pages > 1


def test_the_chat_page_read_is_a_snapshot_and_the_outline_lists_the_prompts(api):
    cid = post(api, "chat/ops", {"text": "Check the release notes"})["conversation_id"]
    snap = api.get(f"/api/v2/conversations/{cid}/snapshot", params=CHAT_PAGE, headers=headers()).json()
    assert {"messages", "has_more", "next_before", "execution", "goal"} <= set(snap)
    assert snap["messages"][-1]["body"] == "Check the release notes"
    outline = api.get(f"/api/v2/conversations/{cid}/outline", headers=headers()).json()
    row = outline["prompts"][0]
    assert {"id", "created", "text", "task"} <= set(row)
    assert row["text"] == "Check the release notes" and row["task"] is None


def test_an_older_chat_stays_in_the_list_behind_many_newer_conversations(api):
    cid = post(api, "chat/ops", {"text": "Old question"})["conversation_id"]
    # The control: before the newer conversations exist, the chat is listed for both reads.
    assert cid in {c["id"] for c in api.get("/api/v2/conversations", headers=headers()).json()["conversations"]}
    with api.app.state.store.transaction() as c:
        row = dict(c.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone())
        later = H.shift(H.now(), seconds=3600)
        # Ben's chats, newer than Ana's: no room key, so they do not collide with her room's unique key.
        rows = [dict(row, id=H.new_id(), subject=f"Newer {n}", created=later, last_message_at=later,
                     participants_json='["human:ben"]', owner_actor="human:ben", room_key=None) for n in range(520)]
        cols = list(row)
        c.executemany(f"INSERT INTO conversations({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                      [[r[k] for k in cols] for r in rows])
    everything = {c["id"] for c in api.get("/api/v2/conversations", headers=headers()).json()["conversations"]}
    assert cid in everything
    by_bot = api.get("/api/v2/conversations", params={"bot": "ops"}, headers=headers()).json()["conversations"]
    assert cid in {c["id"] for c in by_bot}


def test_hub_history_is_the_conversation_show_alias_and_pages_back_like_the_rest_call(api, monkeypatch, capsys):
    cid = post(api, "chat/ops", {"text": "Message 0"})["conversation_id"]
    with api.app.state.store.transaction() as c:
        for n in range(1, 201):
            H.say(c, "human:ana", "bot:ops", f"Message {n}", conversation_id=cid)
        order = message_ids(c, cid)
    monkeypatch.setattr(remotecli, "Client", lambda url, token, **kw: ApiClient(api, token))
    monkeypatch.setenv("HUB_API_URL", "http://tico.test/api/v2")
    monkeypatch.setenv("HUB_TOKEN", "ana-test")
    monkeypatch.delenv("HUB_BOT", raising=False)
    monkeypatch.delenv("HUB_EMPLOYEE", raising=False)

    assert hubcli.main(["history", cid]) == 0
    newest = json.loads(capsys.readouterr().out)
    rest = read(api, cid)
    assert newest == {"conversation": rest["conversation"], "messages": rest["messages"],
                      "has_more": rest["has_more"], "next_before": rest["next_before"]}
    assert [m["id"] for m in newest["messages"]] == order[-200:] and newest["has_more"] is True
    # The runner's prompt tells a bot to read further back with `--before` and the page's `next_before`.
    assert hubcli.main(["conversation", "show", cid, "--before", newest["next_before"]]) == 0
    older = json.loads(capsys.readouterr().out)
    assert [m["id"] for m in older["messages"]] == order[:-200] and older["has_more"] is False


def test_an_old_mcp_name_is_refused_with_its_new_name_and_writes_nothing(api):
    cid = post(api, "chat/ops", {"text": "Read this back"})["conversation_id"]
    with api.app.state.store.transaction() as c:
        H.say(c, "bot:ops", "human:ana", "Done.", conversation_id=cid)
        note = c.execute("SELECT id FROM messages WHERE body='Done.'").fetchone()[0]

    def read_at():
        with api.app.state.store.read() as c:
            return c.execute("SELECT read_at FROM messages WHERE id=?", (note,)).fetchone()[0]

    # hubtools._call answers an old name with `renamed` and the new name; it does not run the old name.
    err, out = call(api, "hub_history", {"conversation": cid})
    assert err and out["error"] == "renamed" and out["renamed_to"] == "hub_conversation_show" and "messages" not in out
    err, out = call(api, "hub_say", {"to": "ops", "text": "Said under the old name"})
    assert err and out["error"] == "renamed" and out["renamed_to"] == "hub_message_send"
    err, out = call(api, "hub_ack", {"message_id": note})
    assert err and out["error"] == "renamed" and out["renamed_to"] == "hub_message_mark_read"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE body='Said under the old name'").fetchone()[0] == 0
    assert read_at() is None

    # The new names answer the same request, so a bot that retries under them gets its answer.
    err, page = call(api, "hub_conversation_show", {"conversation": cid})
    rest = read(api, cid)
    assert not err and page == {"conversation": rest["conversation"], "messages": rest["messages"],
                                "has_more": rest["has_more"], "next_before": rest["next_before"]}
    # hub_message_send answers with the message itself (POST /messages), not wrapped as the reply route wraps it.
    err, sent = call(api, "hub_message_send", {"to": "ops", "text": "Said under the new name",
                                               "conversation_id": cid})
    assert not err and sent["conversation_id"] == cid and sent["body"] == "Said under the new name"
    assert "Said under the new name" in [m["body"] for m in read(api, cid)["messages"]]
    err, _ = call(api, "hub_message_mark_read", {"message_id": note})
    assert not err and read_at() is not None


def test_the_runner_claim_carries_the_conversation_and_epochs_its_session_is_keyed_on(api):
    # runner-rs/src/execute.rs keys a turn's session on attempt.conversation.id and moves it with session_epoch and
    # bot_session_epoch; prompt.rs reads conversation.kind and .scope and the history's message ids. The wire schema
    # leaves the conversation object open, so these fields are pinned here.
    _, msg, attempt = setup_attempt(api)
    conversation = attempt["conversation"]
    assert conversation["id"] == msg["conversation_id"] == attempt["message"]["conversation_id"]
    assert (conversation["kind"], conversation["scope"], conversation["owner_actor"]) == ("chat", "personal", "human:ana")
    assert isinstance(attempt["session_epoch"], int) and isinstance(attempt["bot_session_epoch"], int)
    assert msg["id"] in [m["id"] for m in attempt["history"]]


def test_a_legacy_reply_and_a_to_send_reach_ben_in_a_native_dm_ana_started(api):
    # Ana starts a native DM with Ben: Ben is a thread member, not a participant row. Old clients and old runners send
    # through the legacy routes, which must find Ben there, both to reply in the conversation and with `to=`.
    started = api.post("/api/v2/threads/dm", json={"members": ["human:ben"], "body": "Hi Ben"}, headers=headers())
    assert started.status_code == 200, started.text
    cid = started.json()["id"]

    reply = api.post(f"/api/v2/conversations/{cid}/messages", json={"text": "Legacy reply"}, headers=headers())
    assert reply.status_code == 200, reply.text

    send = api.post("/api/v2/messages", json={"to": "human:ben", "text": "Legacy send",
                                              "conversation_id": cid}, headers=headers())
    assert send.status_code == 200, send.text

    stranger = api.post("/api/v2/messages", json={"to": "human:cara", "text": "Not for Cara",
                                                  "conversation_id": cid}, headers=headers())
    assert stranger.status_code == 403, stranger.text             # a non-member is still refused

    with api.app.state.store.read() as c:
        texts = {r[0] for r in c.execute("SELECT body FROM messages WHERE conversation_id=? AND to_actor='human:ben'",
                                         (cid,))}
    assert {"Legacy reply", "Legacy send"} <= texts
