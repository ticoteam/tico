"""Conversation visibility per role (e2e/README.md "roles"): the owner, a plain member and a bot each read exactly the
conversations they should, across personal rooms, the shared CPO room, a direct conversation between people and a DM to
a bot. The rules are backend/auth.py `conversation` and docs/conversation.md ("personal" rooms are not opened to the
owner; "shared" rooms need membership; "direct" rooms are open to the owner and to their participants; a bot turn
reads only the conversations its attempt was handed).

Each cell of the matrix is one request through the v2 API as that viewer. The expected answers are written here from the
rules, not read back from the code under test.
"""
import pytest

from backend.store import H
from backend.tests.test_api import api, headers  # noqa: F401  (the fixture)
from e2e import scenario

pytestmark = pytest.mark.slow

OWNER, MEMBER, SHARED_MEMBER = "ana-test", "cara-test", "ben-test"
READ, DENIED = 200, {403, 404}


def conversation_of(api, message_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT conversation_id FROM messages WHERE id=?", (message_id,)).fetchone()[0]


def read_status(client, token, cid):
    return client.get("/api/v2/conversations/%s/messages" % cid, headers=headers(token)).status_code


def listed(client, token, **params):
    response = client.get("/api/v2/conversations", headers=headers(token), params=params)
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()["conversations"]}


@pytest.fixture
def rooms(api):  # noqa: F811
    """The scenario, then the rooms the matrix needs: Cara's personal room with the ops bot, Ben's shared CPO room, a direct
    conversation between Ben and Cara. Returns the conversation ids by name."""
    client = api
    ids = scenario.populate(client)
    # Ana's DM to the ops bot is her personal room with it: one active room per human and bot (docs/conversation.md).
    assert conversation_of(client, ids["direct_message"]) == ids["conversation"]
    named = {"ana_ops": ids["conversation"]}                      # Ana's personal chat with ops (and her DM to it)
    with client.app.state.store.read() as c:                      # the thread of the task the ops bot owns
        named["open_task_thread"] = c.execute("SELECT id FROM conversations WHERE task_id=? AND kind='task'",
                                              (ids["open_task"],)).fetchone()[0]
    cara = scenario.Api(client).call("post", "chat/ops", token=MEMBER, json={"text": "Cara's question."})
    named["cara_ops"] = cara["conversation"]["id"]                # Cara's personal chat with ops
    ben = scenario.Api(client).call("post", "chat/cpo", token=SHARED_MEMBER, json={"text": "Group plan."})
    named["cpo_shared"] = ben["conversation"]["id"]               # the shared CPO room (Ben is its primary human)
    direct = scenario.Api(client).call("post", "conversations", token=SHARED_MEMBER,
                                       json={"participants": ["human:cara"], "kind": "chat", "subject": "Budget"})
    named["ben_cara_direct"] = direct["id"]
    return named


def test_each_viewer_reads_exactly_the_rooms_its_role_allows(api, rooms):  # noqa: F811
    # A bot turn on Ana's chat with ops: the bot's attempt is handed that one conversation and nothing else.
    machine, attempt = scenario.bot_turn(api)
    bot = attempt["token"]
    try:
        expected = {  # room -> {viewer: may read}
            "ana_ops":          {"owner": True,  "member": False, "shared_member": False, "bot": True},
            "cara_ops":         {"owner": False, "member": True,  "shared_member": False, "bot": False},
            # The task is public (no private flag), so its thread reads like the task does for everyone.
            "open_task_thread": {"owner": True,  "member": True,  "shared_member": True,  "bot": True},
            "cpo_shared":       {"owner": True,  "member": False, "shared_member": True,  "bot": False},
            "ben_cara_direct":  {"owner": True,  "member": True,  "shared_member": True,  "bot": False},
        }
        tokens = {"owner": OWNER, "member": MEMBER, "shared_member": SHARED_MEMBER, "bot": bot}
        for name, who in expected.items():
            for viewer, may in who.items():
                status = read_status(api, tokens[viewer], rooms[name])
                if may:
                    assert status == READ, f"{viewer} should read {name}, got {status}"
                else:
                    assert status in DENIED, f"{viewer} should not read {name}, got {status}"
    finally:
        scenario.finish_turn(api, machine, attempt)


def test_lists_show_only_the_conversations_each_viewer_is_in(api, rooms):  # noqa: F811
    machine, attempt = scenario.bot_turn(api)
    try:
        assert listed(api, OWNER) >= {rooms["ana_ops"]}
        assert not listed(api, OWNER) & {rooms["cara_ops"], rooms["ben_cara_direct"]}
        assert listed(api, MEMBER) >= {rooms["cara_ops"], rooms["ben_cara_direct"]}
        assert not listed(api, MEMBER) & {rooms["ana_ops"], rooms["cpo_shared"]}
        assert listed(api, SHARED_MEMBER) >= {rooms["cpo_shared"], rooms["ben_cara_direct"]}
        assert not listed(api, SHARED_MEMBER) & {rooms["ana_ops"], rooms["cara_ops"]}
        # The bot lists its turn's room, and the thread of a task it owns (task discussions go to their owners).
        assert listed(api, attempt["token"]) == {rooms["ana_ops"], rooms["open_task_thread"]}
    finally:
        scenario.finish_turn(api, machine, attempt)


def test_the_owners_view_of_a_bot_leaves_out_a_members_personal_room(api, rooms):  # noqa: F811
    # The owner may inspect a bot's conversations, but another human's personal room with that bot stays theirs.
    seen = listed(api, OWNER, bot="ops")
    assert rooms["ana_ops"] in seen
    assert rooms["cara_ops"] not in seen


def test_a_chat_with_a_bot_lists_only_the_callers_own_room(api, rooms):  # noqa: F811
    assert listed(api, MEMBER, chat_with="ops") == {rooms["cara_ops"]}
    assert listed(api, OWNER, chat_with="ops") == {rooms["ana_ops"]}


def test_the_outline_of_a_personal_room_is_refused_to_everyone_else(api, rooms):  # noqa: F811
    for token in (OWNER, SHARED_MEMBER):
        response = api.get("/api/v2/conversations/%s/outline" % rooms["cara_ops"], headers=headers(token))
        assert response.status_code in DENIED, (token, response.status_code)
    assert api.get("/api/v2/conversations/%s/outline" % rooms["cara_ops"], headers=headers(MEMBER)).status_code == READ
