"""Per-bot permissions: who may see, read and write to a bot (backend/bot_access.py, docs/permissions.md).

One company, one Legal bot, and the people and bots that come at it: the owner (Ana), the person the
bot reports to (Ben, its manager), a member of the Legal team (Cara), someone outside it (Dee) and
two other bots. Each test sets the bot's access, then asks every route the same question of each
caller and checks the answer against the rule.
"""

import pytest
import yaml

from backend import bot_access as BA
from backend.auth import Identity
from backend.store import H, encode
from backend.tests.test_api import api, get, headers, post, put  # noqa: F401  (fixture)

ALL = ("ana", "ben", "cara", "dee", "stranger", "analyst")
EVERYONE = {"everyone": True}


def call(api, method, path, token, body=None):
    kwargs = {"headers": headers(token)}
    if body is not None:
        kwargs["json"] = body
    return getattr(api, method)("/api/v2/" + path, **kwargs)


def bot(api, slug, reports_to=None):
    return post(api, "bots", {"slug": slug, "display_name": slug.title(), "description": "A test bot.",
                              "reports_to": reports_to, "status": "active", "repo": "emp-" + slug,
                              "thread_mode": "personal", "model": "hermes-profile",
                              "effort": "as-configured", "harness": "hermes",
                              "operator": "ana", "owners": ["ana"], "runner_id": None})


@pytest.fixture
def world(api):
    """The base fixture's company plus Dee, a Legal team, the Legal bot under Ben, and two bots."""
    store = api.app.state.store
    store.settings.test_identities["dee-test"] = Identity("human:dee", "human", "dee@acme.example")
    with store.transaction() as c:
        people = [{"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
                  {"id": "ben", "email": "ben@acme.example", "primary_for": ["cpo", "product-design", "ops"]},
                  {"id": "cara", "email": "cara@acme.example", "team": "legal"},
                  {"id": "dee", "email": "dee@acme.example", "team": "sales"}]
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode({"people": people}),))
        H.sync_registry(c, {}, {"people": people})
    bot(api, "counsel", reports_to="human:ben")
    bot(api, "stranger")
    bot(api, "analyst")
    tokens = {"ana": "ana-test", "ben": "ben-test", "cara": "cara-test", "dee": "dee-test"}
    tokens.update({slug: post(api, f"bots/{slug}/agent-credential", {})["token"] for slug in ("stranger", "analyst")})
    task = post(api, "tasks", {"owner": "counsel", "title": "Review the NDA", "body": "Redline it."})
    with store.transaction() as c:
        c.execute("INSERT INTO updates(id,bot,kind,day,headline,body,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                  ("u1", "counsel", "daily", "2026-09-29", "Two contracts reviewed", "- Two contracts reviewed",
                   H.now(), H.now()))
    made = api.post("/api/v2/files/uploads", json={"name": "memo.md", "text": "# Memo", "scope": "bot"},
                    headers=headers(post(api, "bots/counsel/agent-credential", {})["token"]))
    assert made.status_code == 200, made.text
    return api, tokens, task


def set_access(api, see=None, read=None, write=None, token="ana-test", expected=200):
    current = get(api, "bots/counsel/access", token)
    body = {"see": see or EVERYONE, "read": read or EVERYONE, "write": write or EVERYONE,
            "revision": current["revision"]}
    r = api.put("/api/v2/bots/counsel/access", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def outcomes(api, tokens, method, path, body=None, callers=ALL):
    return {who: call(api, method, path, tokens[who], body).status_code for who in callers}


# ------------------------------------------------------------------ the rule, route by route

def test_visible_requests_only_lets_everyone_ask_and_only_readers_look(world):
    """Legal's example: everyone sees the bot and may send it a request; the Legal team reads its work."""
    api, tokens, task = world
    set_access(api, see=EVERYONE, read={"teams": ["legal"], "bots": ["analyst"]}, write=EVERYONE)
    readers = {"ana", "ben", "cara", "analyst"}
    expect = lambda ok, no: {who: (ok if who in readers else no) for who in ALL}   # noqa: E731

    for path in ("bots", "org"):
        for who in ALL:
            data = get(api, path, tokens[who])
            row = next(b for b in (data["bots"] if path == "org" else data)
                       if (b.get("id") or b.get("slug")) == "counsel")
            assert row["access"] == {"see": True, "read": who in readers, "write": True}, (path, who)
    # A caller who may only see it is told its name and who runs it, and nothing of its work.
    dee = next(b for b in get(api, "bots", "dee-test") if b["slug"] == "counsel")
    assert dee["display_name"] == "Counsel" and dee["reports_to"] == "human:ben" and "status" not in dee
    assert "queued" not in dee and "assignment" not in dee
    assert "status" in next(b for b in get(api, "bots", "cara-test") if b["slug"] == "counsel")

    assert outcomes(api, tokens, "get", "status?bot=counsel") == expect(200, 403)
    assert outcomes(api, tokens, "get", "bots/counsel/history") == expect(200, 403)
    assert outcomes(api, tokens, "get", "bots/counsel/routines", callers=("ana", "ben", "cara", "dee")) == {
        "ana": 200, "ben": 200, "cara": 200, "dee": 403}
    listed = {who: len(get(api, "updates?bot=counsel", tokens[who])["updates"]) for who in ALL}
    assert listed == {who: int(who in readers) for who in ALL}
    assert get(api, "updates/unread", "dee-test")["unread"] == 0 and get(api, "updates/unread", "cara-test")["unread"] == 1
    assert outcomes(api, tokens, "get", "updates/u1") == {who: (200 if who in readers else 403) for who in ALL}
    files = {who: get(api, "bots/counsel/files", tokens[who])["total"] for who in ("ana", "ben", "cara", "dee")}
    assert files == {"ana": 1, "ben": 1, "cara": 1, "dee": 0}

    assert {who: task["id"] in [t["id"] for t in get(api, "tasks", tokens[who])["tasks"]]
            for who in ("ana", "ben", "cara", "dee")} == {"ana": True, "ben": True, "cara": True, "dee": False}
    assert outcomes(api, tokens, "get", "tasks/" + task["id"], callers=("ana", "ben", "cara", "dee")) == {
        "ana": 200, "ben": 200, "cara": 200, "dee": 403}

    # Everyone may write to it: chat, task, note, and a comment that wakes it.
    for who in ALL:
        assert call(api, "post", "chat/counsel", tokens[who], {"text": "Is this clause standard?"}).status_code == 200, who
        assert call(api, "post", "tasks", tokens[who], {"owner": "counsel", "title": f"Check for {who}",
                                                         "body": "Please."}).status_code == 200, who
        assert call(api, "post", "notes", tokens[who], {"to": "counsel", "text": f"A note from {who}"}).status_code == 200, who
    assert call(api, "post", f"tasks/{task['id']}/comments", "ana-test", {"text": "Any news?"}).status_code == 200


def test_private_hides_the_bot_from_everyone_but_its_audience_and_managers(world):
    api, tokens, task = world
    set_access(api, see={"people": ["cara"]}, read={"people": ["cara"]}, write={"people": ["cara"]})
    inside = {"ana", "ben", "cara"}
    for path in ("bots", "org"):
        for who in ALL:
            data = get(api, path, tokens[who])
            slugs = [(b.get("id") or b.get("slug")) for b in (data["bots"] if path == "org" else data)]
            assert ("counsel" in slugs) == (who in inside), (path, who)
    gone = {who: 200 if who in inside else 404 for who in ALL}
    assert outcomes(api, tokens, "get", "status?bot=counsel") == gone
    assert outcomes(api, tokens, "get", "bots/counsel/history") == gone
    assert outcomes(api, tokens, "get", "bots/counsel/updates") == gone
    assert outcomes(api, tokens, "get", "bots/counsel/files", callers=("ana", "ben", "cara", "dee")) == {
        "ana": 200, "ben": 200, "cara": 200, "dee": 404}
    assert outcomes(api, tokens, "get", "tasks/" + task["id"], callers=("ana", "ben", "cara", "dee")) == {
        "ana": 200, "ben": 200, "cara": 200, "dee": 404}
    assert outcomes(api, tokens, "post", "chat/counsel", body={"text": "hello"}) == gone
    assert outcomes(api, tokens, "post", "notes", body={"to": "counsel", "text": "a note"}) == gone
    assert outcomes(api, tokens, "post", "tasks", body={"owner": "counsel", "title": "A task", "body": "Do it."}) == gone
    assert outcomes(api, tokens, "post", "messages", body={"to": "counsel", "text": "hi"}) == gone
    assert outcomes(api, tokens, "post", "tasks/dry-run", body={"owner": "counsel", "title": "A task", "body": "x"}
                    )["dee"] == 200      # the check answers, and says the bot is not one they can name
    dry = call(api, "post", "tasks/dry-run", tokens["dee"], {"owner": "counsel", "title": "A task", "body": "x"}).json()
    assert dry["ok"] is False and "not a bot or a person" in dry["problems"][0]
    # SQL counts do not leak it either.
    for who, seen in (("dee", 0), ("cara", 1)):
        r = call(api, "post", "sql", tokens[who], {"sql": "SELECT count(*) FROM bots WHERE slug='counsel'"})
        assert r.json()["rows"] == [[seen]], who
    r = call(api, "post", "sql", tokens["dee"], {"sql": "SELECT count(*) FROM tasks"})
    assert r.json()["rows"] == [[0]]


def test_read_without_write_can_look_but_not_ask(world):
    api, tokens, task = world
    set_access(api, see=EVERYONE, read=EVERYONE, write={"teams": ["legal"]})
    can = {"ana", "ben", "cara"}
    assert outcomes(api, tokens, "get", "status?bot=counsel") == {who: 200 for who in ALL}
    refused = {who: 200 if who in can else 403 for who in ALL}
    assert outcomes(api, tokens, "post", "chat/counsel", body={"text": "hello"}) == refused
    assert outcomes(api, tokens, "post", "notes", body={"to": "counsel", "text": "a note"}) == refused
    assert outcomes(api, tokens, "post", "tasks", body={"owner": "counsel", "title": "A task", "body": "Do it."}) == refused
    r = call(api, "post", "chat/counsel", tokens["dee"], {"text": "hello"})
    assert r.json()["error"]["code"] == "forbidden" and "Write access" in r.json()["error"]["detail"]
    assert outcomes(api, tokens, "post", f"tasks/{task['id']}/comments", body={"text": "Any news?"},
                    callers=("cara", "dee", "stranger")) == {"cara": 200, "dee": 403, "stranger": 403}
    # Running a task early wakes the bot, which is a request to it.
    assert call(api, "post", f"tasks/{task['id']}/run-now", tokens["dee"], {}).status_code == 403


def test_a_bot_is_checked_like_a_person_and_may_answer_one_that_wrote_to_it(world):
    api, tokens, task = world
    set_access(api, see={"bots": ["analyst"]}, read={"bots": ["analyst"]}, write={"bots": ["analyst"]})
    assert outcomes(api, tokens, "post", "chat/counsel", body={"text": "hello"},
                    callers=("analyst", "stranger")) == {"analyst": 200, "stranger": 404}
    assert [b["slug"] for b in get(api, "bots", tokens["stranger"])].count("counsel") == 0
    assert call(api, "get", "status?bot=counsel", tokens["analyst"]).status_code == 200
    # Counsel wrote to the stranger in the course of its work; the stranger may answer, and nothing more.
    counsel = post(api, "bots/counsel/agent-credential", {})["token"]
    post(api, "messages", {"to": "stranger", "text": "Which contract?", "kind": "ask"}, token=counsel)
    assert call(api, "post", "messages", tokens["stranger"], {"to": "counsel", "text": "The NDA."}).status_code == 200
    assert call(api, "get", "status?bot=counsel", tokens["stranger"]).status_code == 403


def test_the_owner_the_manager_and_the_bot_itself_always_have_full_access(world):
    api, tokens, task = world
    set_access(api, see={"people": ["dee"]}, read={"people": ["dee"]}, write={"people": ["dee"]})
    for who in ("ana", "ben"):
        row = next(b for b in get(api, "bots", tokens[who]) if b["slug"] == "counsel")
        assert row["access"] == {"see": True, "read": True, "write": True}
    counsel = post(api, "bots/counsel/agent-credential", {})["token"]
    assert call(api, "get", "status?bot=counsel", counsel).status_code == 200
    # A bot administrator has it for the bots run under their own name.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='ben' WHERE bot='counsel'")
        c.execute("UPDATE bot_config SET reports_to=NULL WHERE bot='counsel'")
    row = next(b for b in get(api, "bots", "ben-test") if b["slug"] == "counsel")
    assert row["access"] == {"see": True, "read": True, "write": True}
    # Someone who may read or write can see it, whatever the See list says.
    set_access(api, see={"people": ["ana"]}, read={"people": ["dee"]}, write={"people": ["cara"]})
    def mine(who):
        return next(b["access"] for b in get(api, "bots", tokens[who]) if b["slug"] == "counsel")
    assert mine("dee") == {"see": True, "read": True, "write": False}
    assert mine("cara") == {"see": True, "read": False, "write": True}


def test_write_without_read_shows_only_your_own_threads_and_tasks(world):
    api, tokens, task = world
    set_access(api, see=EVERYONE, read={"teams": ["legal"]}, write=EVERYONE)
    mine = post(api, "chat/counsel", {"text": "Dee's private question"}, token="dee-test")
    theirs = post(api, "chat/counsel", {"text": "Cara's question"}, token="cara-test")
    dees_task = post(api, "tasks", {"owner": "counsel", "title": "Dee's contract", "body": "Please look."}, token="dee-test")
    others_task = post(api, "tasks", {"owner": "counsel", "title": "Cara's contract", "body": "Please look."}, token="cara-test")
    # Dee's own thread and task, and nothing else the bot is doing.
    assert get(api, f"conversations/{mine['conversation_id']}/messages", "dee-test")
    get(api, f"conversations/{theirs['conversation_id']}/messages", "dee-test", expected=403)
    ids = [t["id"] for t in get(api, "tasks", "dee-test")["tasks"]]
    assert ids == [dees_task["id"]]
    assert get(api, "tasks/" + dees_task["id"], "dee-test")["task"]["title"] == "Dee's contract"
    get(api, "tasks/" + others_task["id"], "dee-test", expected=403)
    get(api, "tasks/" + task["id"], "dee-test", expected=403)
    assert get(api, "conversations?chat_with=counsel", "dee-test")["conversations"][0]["id"] == mine["conversation_id"]
    # She can talk on her own task; the bot's status, run log and files stay out of view.
    assert call(api, "post", f"tasks/{dees_task['id']}/comments", "dee-test", {"text": "Any news?"}).status_code == 200
    get(api, "status?bot=counsel", "dee-test", expected=403)
    assert get(api, "bots/counsel/files", "dee-test")["total"] == 0
    # The counts through SQL are hers too.
    r = call(api, "post", "sql", "dee-test", {"sql": "SELECT count(*) FROM tasks WHERE owner='bot:counsel'"})
    assert r.json()["rows"] == [[1]]


# ------------------------------------------------------------------ editing it

def test_access_is_edited_by_managers_with_revisions_and_undo(world):
    api, tokens, task = world
    current = get(api, "bots/counsel/access", "ana-test")
    assert current["see"] == current["read"] == current["write"] == {
        "everyone": True, "people": [], "teams": [], "bots": []}
    assert {"id": "legal", "name": "Legal"} in current["teams"]
    body = {"see": EVERYONE, "read": {"teams": ["legal"], "people": ["ben"]}, "write": EVERYONE,
            "revision": current["revision"]}
    for who in ("cara", "dee"):                 # they may use it, not change who may
        assert call(api, "get", "bots/counsel/access", tokens[who]).status_code == 403
        assert call(api, "put", "bots/counsel/access", tokens[who], body).status_code == 403
    saved = put(api, "bots/counsel/access", body, token="ben-test")   # the bot reports to Ben
    assert saved["read"]["teams"] == ["legal"] and saved["revision"] == current["revision"] + 1
    assert call(api, "put", "bots/counsel/access", "ben-test", body).status_code == 409     # stale revision
    for bad, code in (({"read": {"teams": ["nope"]}}, 404), ({"read": {"people": ["nobody"]}}, 404),
                      ({"write": {"bots": ["ghost"]}}, 404)):
        r = call(api, "put", "bots/counsel/access", "ana-test", {**body, **bad, "revision": saved["revision"]})
        assert r.status_code == code, r.text
    unchanged = call(api, "put", "bots/counsel/access", "ana-test", {**body, "revision": saved["revision"]})
    assert unchanged.status_code == 409 and unchanged.json()["error"]["code"] == "unchanged"
    # Undo is the owner's, from the settings history, like the people who use it.
    history = get(api, "settings/history", "ana-test")["changes"]
    change = next(c for c in history if c["field"] == "access")
    assert change["can_undo"] and change["after"]["read"]["teams"] == ["legal"]
    revision = get(api, "bots/counsel/access", "ana-test")["revision"]
    post(api, f"settings/history/{change['id']}/undo", {"expected_revision": revision}, token="ana-test")
    assert get(api, "bots/counsel/access", "ana-test")["read"]["everyone"] is True
    with api.app.state.store.read() as c:
        assert c.execute("SELECT access_json FROM bot_config WHERE bot='counsel'").fetchone()[0] is None
        assert c.execute("SELECT count(*) FROM events WHERE action='bot.access_changed'").fetchone()[0] == 1


# ------------------------------------------------------------------ the migration

@pytest.mark.slow
def test_the_old_private_and_routing_lists_are_retired_with_one_note_for_the_owner(tmp_path, caplog):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.config import Settings
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({
        "owner": "ana@acme.example", "private_owners": ["inbox"],
        "routing_permissions": {"inbox": ["ops"]}}))

    def boot():
        return create_app(Settings(db_path=tmp_path / "hub.db", registry_dir=registry, test_identities={
            "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
            "ben-test": Identity("human:ben", "human", "ben@acme.example")}))
    with caplog.at_level("WARNING", logger="tico.access"):
        app = boot()
        with TestClient(app) as client:
            with app.state.store.transaction() as c:
                H.sync_registry(c, {"inbox": {"name": "inbox", "runtime": "fake", "status": "active"}},
                                {"people": [{"id": "ana", "email": "ana@acme.example"},
                                            {"id": "ben", "email": "ben@acme.example"}]})
                c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES('inbox','{}','ana')")
            # The bot the file called private is Open: Ben sees, reads and writes to it.
            row = next(b for b in get(client, "bots", "ben-test") if b["slug"] == "inbox")
            assert row["access"] == {"see": True, "read": True, "write": True}
            notes = [k for k in get(client, "health")["checks"] if k["id"] == "bot_access"]
            assert len(notes) == 1 and "no longer used" in notes[0]["summary"] and "Settings > Bots" in notes[0]["summary"]
            assert all(k["id"] != "bot_access" for k in get(client, "health", "ben-test")["checks"])   # the owner's note
            assert call(client, "post", "health/bot-access/dismiss", "ben-test", {}).status_code == 403
            assert post(client, "health/bot-access/dismiss", {})["dismissed"] is True
            assert all(k["id"] != "bot_access" for k in get(client, "health")["checks"])
    assert sum("no longer used" in r.getMessage() for r in caplog.records) == 1
    # A later start neither warns nor writes the note again.
    caplog.clear()
    with caplog.at_level("WARNING", logger="tico.access"):
        with TestClient(boot()) as client:
            assert all(k["id"] != "bot_access" for k in get(client, "health")["checks"])
    assert not caplog.records


def test_the_stored_shape_round_trips_and_open_is_null():
    assert BA.stored(BA.document(None)) is None
    private = BA.document({"see": {"everyone": True}, "read": {"people": ["ana", "ana", " "], "teams": ["legal"]}})
    assert private["read"] == {"everyone": False, "people": ["ana"], "teams": ["legal"], "bots": []}
    assert private["write"]["everyone"] is True and private["see"]["everyone"] is True   # a level left out is Open
    assert BA.document(BA.stored(private)) == private
    assert BA.document("not json") == BA.document(None)
