"""A person asks another bot for something only BotOps can do: the bot hands it on with the person's own message, and
BotOps acts as them; a hand-off that lends nothing reaches the person as a Confirm card, not a refusal."""
import re

from fastapi import Request

from backend.tests.test_api import api, assign, claim, get, headers, post, ready  # noqa: F401  (fixture)
from backend.tests.test_member_bots import botops, finish  # noqa: F401  (fixture)
from backend.store import H


def _identity_route(api, path="/api/v2/qa-handoff-identity", method="GET"):
    """A route at `path` that answers with whose rights the call ran with, ahead of any real route there."""
    def identity(request: Request):
        who = request.state.identity
        return {"actor": who.actor, "role": who.role}
    api.app.add_api_route(path, identity, methods=[method])
    api.app.router.routes.insert(0, api.app.router.routes.pop())


def _hand_off(api, machine, cite, said="Give yourself read access to the acme site repository"):
    """Ana says something to Ops in her chat with it; Ops files work for BotOps, citing her message or not; BotOps picks
    it up."""
    post(api, "chat/ops", {"text": said}, token="ana-test")
    ops = claim(api, machine, "ops")
    task = post(api, "tasks", {"owner": "botops", "title": "Give Ops read access to the acme site repository",
                               "body": "Ana asked for it in her chat with Ops.", **({"request_id": cite} if cite else {})},
                token=ops["token"])
    finish(api, machine, ops)
    return task, claim(api, machine, "botops")


def test_a_bot_hands_botops_the_persons_own_request_and_botops_acts_as_them(api, botops, monkeypatch):
    from backend import botops_act
    monkeypatch.setattr(botops_act, "CARRIED", botops_act.CARRIED + [("GET", re.compile("/api/v2/qa-handoff-identity"))])
    assign(api, botops, "ops")
    ready(api, botops, ["botops", "ops"])
    _identity_route(api)
    task, attempt = _hand_off(api, botops, "turn")
    with api.app.state.store.read() as c:
        carried = c.execute("SELECT detail FROM events WHERE action='botops.request_carried' AND target=?",
                            (task["id"],)).fetchone()
        assert H._json(carried["detail"], {})["lends"] is True
        quoted = c.execute("SELECT body FROM messages WHERE to_actor='bot:botops' AND body LIKE '%in their words%'"
                           ).fetchone()
        assert "> Give yourself read access" in quoted["body"]
    assert get(api, "qa-handoff-identity", attempt["token"]) == {"actor": "human:ana", "role": "owner"}

    # A task finished and reopened no longer lends: the request it carried was done.
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:ops", task["id"], status="done", note="done")
        H.task_update(c, "bot:ops", task["id"], status="open", note="one more thing")
    assert get(api, "qa-handoff-identity", attempt["token"]) == {"actor": "bot:ops", "role": "bot"}
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM task_events WHERE task_id=? AND field='status'", (task["id"],))

    # Once the message is more than a week old it lends nothing: BotOps has only Ops' rights again.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created_at=? WHERE id=?",
                  (H.shift(H.now(), days=-8), H._json(carried["detail"], {})["message_id"]))
    assert get(api, "qa-handoff-identity", attempt["token"]) == {"actor": "bot:ops", "role": "bot"}


def test_a_hand_off_that_lends_nothing_puts_a_confirm_card_to_the_person(api, botops):
    assign(api, botops, "ops")
    ready(api, botops, ["botops", "ops"])
    task, attempt = _hand_off(api, botops, None)        # Ops did not cite her message
    tried = api.put("/api/v2/bots/ops/repositories", json={"mode": "all", "all_access": "read"},
                    headers=headers(attempt["token"]))
    assert tried.status_code == 200, tried.text
    card = tried.json()
    assert card["needs_confirm"] is True and card["action"]["status"] == "pending"
    with api.app.state.store.read() as c:
        row = c.execute("SELECT owner,method,path,proposer FROM assistant_actions WHERE id=?",
                        (card["action"]["id"],)).fetchone()
        assert dict(row) == {"owner": "human:ana", "method": "PUT", "path": "/api/v2/bots/ops/repositories",
                             "proposer": "botops"}
        # Nothing changed and no free-text task was filed for her: the card is in her chat with BotOps.
        assert not c.execute("SELECT 1 FROM tasks WHERE owner='human:ana' AND requester='bot:botops'").fetchone()
        said = H.message(c, card["message_id"])
        assert said["to_actor"] == "human:ana" and said["from_actor"] == "bot:botops"


def test_a_hand_off_to_add_an_mcp_server_with_a_placeholder_header_is_the_persons_confirm_card(api, botops):
    """A `${VAR}` header names a credential without holding one, so the person gets the card; a literal still gets none."""
    from backend.tests.test_botops_parity import mcp_tool
    assign(api, botops, "ops")
    ready(api, botops, ["botops", "ops"])
    _, attempt = _hand_off(api, botops, None)
    card = api.post("/api/v2/bots/ops/tools", json=mcp_tool(), headers=headers(attempt["token"]))
    assert card.status_code == 200 and card.json()["needs_confirm"] is True, card.text
    literal = api.post("/api/v2/bots/ops/tools", json=mcp_tool(authorization="Bearer abcdef"),
                       headers=headers(attempt["token"]))
    assert literal.json()["error"]["code"] == "secret_in_request" and "abcdef" not in literal.text


def test_a_cited_message_never_raises_access_with_the_persons_rights(api, botops):
    """Ana only said thanks; Ops cites it anyway and asks for write access to every repository for itself. BotOps tries
    it with Ops' rights, and Ana gets a Confirm card: nothing changes until she taps it."""
    assign(api, botops, "ops")
    ready(api, botops, ["botops", "ops"])
    _, attempt = _hand_off(api, botops, "turn", said="Thanks, that's all for today")
    before = get(api, "bots/ops/repositories", "ana-test")
    tried = api.put("/api/v2/bots/ops/repositories", json={"mode": "all", "all_access": "write"},
                    headers=headers(attempt["token"]))
    assert tried.status_code == 200, tried.text
    assert tried.json()["needs_confirm"] is True
    assert get(api, "bots/ops/repositories", "ana-test") == before
    with api.app.state.store.read() as c:
        assert c.execute("SELECT owner FROM assistant_actions WHERE id=?",
                         (tried.json()["action"]["id"],)).fetchone()["owner"] == "human:ana"


def test_a_carried_request_lends_only_for_work_items_never_settings_or_bots(api, botops):
    """Undoing a settings change could give Ops back access Ana took away; copying, moving or switching a bot could
    reach other computers and credentials. A carried request makes none of them with her rights: only tasks, docs and
    goals (botops_act.CARRIED)."""
    assign(api, botops, "ops")
    ready(api, botops, ["botops", "ops"])
    guarded = ["/api/v2/settings/history/h1/undo", "/api/v2/bots/ops/copy", "/api/v2/bots/ops/placement",
               "/api/v2/settings/transitions/t1/apply-without-checkpoint"]
    for path in guarded + ["/api/v2/tasks/qa-handoff"]:
        _identity_route(api, path, "POST")
    _, attempt = _hand_off(api, botops, "turn", said="Thanks, that's all for today")
    for path in guarded:
        tried = api.post(path, json={}, headers=headers(attempt["token"]))
        assert (tried.json()["actor"], tried.json()["role"]) == ("bot:ops", "bot"), path
    tried = api.post("/api/v2/tasks/qa-handoff", json={}, headers=headers(attempt["token"]))
    assert (tried.json()["actor"], tried.json()["role"]) == ("human:ana", "owner")


def test_a_task_botops_files_with_lent_rights_lends_nothing_later(api, botops):
    """With rights a 'Thanks' lent, BotOps files itself 'Give Ops write on every repository'. That task's own run keeps
    Ops' rights: the write is Ana's Confirm card, not a change made as her. A comment BotOps writes on its own task is its own
    words, never hers, so it never starts a run as her own comment would."""
    assign(api, botops, "ops")
    ready(api, botops, ["botops", "ops"])
    task, attempt = _hand_off(api, botops, "turn", said="Thanks, that's all for today")
    filed = post(api, "tasks", {"owner": "botops", "title": "Give Ops write on every repository", "body": "x"},
                 token=attempt["token"])
    said = post(api, f"tasks/{task['id']}/comments", {"text": "Filed the follow-up"}, token=attempt["token"])
    with api.app.state.store.read() as c:
        assert H.task(c, filed["id"])["requester"] == "human:ana"
        comment = c.execute("SELECT from_actor FROM messages WHERE id=?", ((said.get("comment") or said)["id"],)).fetchone()
        assert comment["from_actor"] == "bot:botops"
    finish(api, botops, attempt)
    later = claim(api, botops, "botops")
    with api.app.state.store.read() as c:
        started = c.execute("SELECT m.* FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
                            "WHERE a.id=?", (later["attempt_id"] if "attempt_id" in later else later["id"],)).fetchone()
        assert H.message_task_id(H.message(c, started["id"])) == filed["id"]
    before = get(api, "bots/ops/repositories", "ana-test")
    tried = api.put("/api/v2/bots/ops/repositories", json={"mode": "all", "all_access": "write"},
                    headers=headers(later["token"]))
    assert tried.status_code in (200, 403), tried.text
    assert tried.status_code == 403 or tried.json().get("needs_confirm") is True
    assert get(api, "bots/ops/repositories", "ana-test") == before
