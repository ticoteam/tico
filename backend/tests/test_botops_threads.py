"""BotOps and threads: a thread write BotOps makes for a person runs as that person (docs/threads.md), so the follow,
read or message that changes is the person's own, and the thread's rights are theirs. BotOps' own messages stay
its own."""
from fastapi import Request

from backend import threads
from backend.botops_act import default_delegable
from backend.tests.test_api import api, headers  # noqa: F401  (fixture)
from backend.tests.test_member_bots import botops, turn  # noqa: F401  (fixture)


def test_thread_writes_run_as_the_person_who_asked_and_botops_messages_do_not():
    for method, path in [("POST", "threads"), ("PATCH", "threads/t1"), ("POST", "threads/t1/messages"),
                         ("POST", "threads/t1/read"), ("POST", "threads/t1/read-all"), ("POST", "threads/t1/members"),
                         ("DELETE", "threads/t1/members/human:ben"), ("PUT", "threads/t1/follow"),
                         ("DELETE", "threads/t1/follow"), ("PATCH", "messages/m1"),
                         ("POST", "threads/messages/m1/task")]:
        assert default_delegable(method, "/api/v2/" + path, {}), (method, path)
    # BotOps' own message (the legacy send) is its own, never the requester's.
    assert not default_delegable("POST", "/api/v2/messages", {"text": "Working on it"})


def test_thread_deletes_reactions_dms_uploads_and_call_steps_run_as_the_person_who_asked():
    for method, path in [("DELETE", "messages/m1"), ("PUT", "messages/m1/reactions/:thumbsup:"),
                         ("DELETE", "messages/m1/reactions/:thumbsup:"), ("POST", "threads/dm"),
                         ("POST", "threads/t1/open-earlier"), ("POST", "uploads/threads/t1/messages"),
                         ("POST", "slack/links"), ("DELETE", "slack/links/L1"), ("POST", "slack/links/L1/resume"),
                         ("POST", "threads/t1/calls"), ("POST", "calls/c1/end"), ("POST", "calls/c1/leave"),
                         ("POST", "calls/c1/bots"), ("DELETE", "calls/c1/bots/bot:ops"),
                         ("POST", "calls/c1/action-items/retry"), ("PUT", "inbound/inboxes/ibx1/audience")]:
        assert default_delegable(method, "/api/v2/" + path, {}), (method, path)


def test_joining_a_call_is_not_delegated_because_its_token_is_the_persons():
    # A join answers with a media token good for five minutes: BotOps does not take one for a person.
    assert not default_delegable("POST", "/api/v2/calls/c1/join", {})
    assert not default_delegable("POST", "/api/v2/calls/c1/token", {})


def _follow_route(api):
    """A thread route that sets the follow state of whoever the call ran as, and answers with that identity."""
    def follow(thread: str, request: Request):
        who = request.state.identity
        with api.app.state.store.transaction() as c:
            threads.follow(c, threads.viewer(None, who), thread, notify="muted")
        return {"actor": who.actor}
    api.app.add_api_route("/api/v2/threads/{thread}/follow", follow, methods=["PUT"])
    api.app.router.routes.insert(0, api.app.router.routes.pop())


def test_botops_mutes_a_thread_for_the_person_who_asked_not_for_itself(api, botops):
    with api.app.state.store.transaction() as c:
        thread = threads.create(c, "human:ben", title="Launch", audience="members", members=["bot:botops"])
    attempt = turn(api, botops, person="ben-test", text="Mute the launch thread for me")
    _follow_route(api)
    tried = api.put(f"/api/v2/threads/{thread['id']}/follow", json={}, headers=headers(attempt["token"]))
    assert tried.status_code == 200, tried.text
    assert tried.json()["actor"] == "human:ben"    # the app adds actor_name to any response that names an actor
    with api.app.state.store.transaction() as c:
        rows = {r["actor"]: r["notify"] for r in
                c.execute("SELECT actor, notify FROM thread_followers WHERE thread_id=?", (thread["id"],))}
    assert rows.get("human:ben") == "muted"
    # Its own follow is the default a member gets when it is added, and the mute did not touch it.
    assert rows["bot:botops"] == "live"
