"""A task another bot asked BotOps for, and a person comments on it: BotOps adds its own task's worktree and files its ask
to a person with its own rights; what needs a person's rights becomes that person's Confirm card, never a dead end."""
from fastapi import Request

from backend.tests.test_api import api, assign, claim, get, headers, post, ready  # noqa: F401  (fixture)
from backend.tests.test_member_bots import botops, finish  # noqa: F401  (fixture)
from backend.store import H, Problem
from backend import task_relations as TR


def _route(api, path, method="POST", refuse=None):
    """A route at `path` that answers with whose rights the call ran with (or refuses as `refuse`), ahead of the real one."""
    def identity(request: Request):
        who = request.state.identity
        if refuse:
            raise Problem("forbidden", refuse, 403)
        return {"actor": who.actor, "role": who.role}
    api.app.add_api_route(path, identity, methods=[method])
    api.app.router.routes.insert(0, api.app.router.routes.pop())


def _commented(api, machine, comment="Chris here: go ahead and finish this"):
    """Ops files work for BotOps; BotOps' first run ends; Ana comments on the task, which starts BotOps' next run."""
    ops = claim(api, machine, "ops")
    task = post(api, "tasks", {"owner_id": "botops", "title": "Align Ops' mail instructions",
                               "body": "Update the docs only."}, token=ops["token"])
    finish(api, machine, ops)
    finish(api, machine, claim(api, machine, "botops"))
    post(api, f"tasks/{task['id']}/comments", {"text": comment}, token="ana-test")
    attempt = claim(api, machine, "botops")
    with api.app.state.store.read() as c:
        started = c.execute("SELECT m.* FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
                            "WHERE a.id=?", (attempt.get("attempt_id") or attempt["id"],)).fetchone()
        assert started["sender_id"] == "human:ana" and H.message_task_id(H.message(c, started["id"])) == task["id"]
        assert H.task(c, task["id"])["requester_id"] == "bot:ops"
    return task, attempt


def _ready(api, machine):
    assign(api, machine, "ops")
    ready(api, machine, ["botops", "ops"])
    post(api, "chat/ops", {"text": "Tidy your mail instructions"}, token="ana-test")


def test_botops_adds_its_own_tasks_worktree_with_its_own_rights(api, botops):
    _ready(api, botops)
    task, attempt = _commented(api, botops)
    for path in (f"/api/v2/tasks/{task['id']}/worktrees", f"/api/v2/tasks/{task['id']}/worktrees/attach"):
        _route(api, path)
        for cite in ({}, {"X-Tico-On-Behalf-Of": "turn"}):
            tried = api.post(path, json={"repo": "acme/site"}, headers={**headers(attempt["token"]), **cite})
            assert tried.status_code == 200, tried.text
            assert tried.json() == {"actor": "bot:botops", "role": "bot"}, (path, cite)


def test_a_worktree_on_someone_elses_task_still_borrows(api, botops):
    """Only BotOps' own task is its own work: a worktree on Ops' task goes through the requester's rights as before."""
    _ready(api, botops)
    _, attempt = _commented(api, botops)
    other = post(api, "tasks", {"owner_id": "ops", "title": "Ops' own work", "body": "x"}, token="ana-test")
    path = f"/api/v2/tasks/{other['id']}/worktrees"
    _route(api, path)
    tried = api.post(path, json={"repo": "acme/site"}, headers=headers(attempt["token"]))
    assert tried.json() == {"actor": "bot:ops", "role": "bot"}


def test_botops_files_a_subtask_for_a_person_on_its_own_task(api, botops):
    _ready(api, botops)
    task, attempt = _commented(api, botops)
    filed = api.post("/api/v2/tasks", headers=headers(attempt["token"]), json={
        "owner": "ana", "title": "Which mailbox should Ops send from?",
        "body": "Which mailbox should Ops send from: support@ or hello@?",
        "relations": [{"task": task["id"], "kind": "parent"}]})
    assert filed.status_code == 200, filed.text
    with api.app.state.store.read() as c:
        made = H.task(c, filed.json()["task"]["id"])
        # BotOps' own ask: it grants nothing, and it is not recorded as Ana's or Ops' request.
        assert (made["owner_id"], made["requester_id"]) == ("human:ana", "bot:botops")
        assert TR.parent_of(c, made["id"]) == task["id"]


def test_a_subtask_for_a_bot_or_with_no_parent_is_not_botops_own_ask(api, botops):
    """The own-task rule covers a person's ask under BotOps' task only: other new tasks keep the run's requester."""
    _ready(api, botops)
    task, attempt = _commented(api, botops)
    _route(api, "/api/v2/tasks/dry-run")
    for body in ({"owner": "ops", "relations": [{"task": task["id"], "kind": "parent"}]},
                 {"owner": "ana", "relations": []}):
        tried = api.post("/api/v2/tasks/dry-run", headers=headers(attempt["token"]),
                         json={"title": "x", "body": "x", **body})
        assert tried.status_code == 200, tried.text
        assert (tried.json()["actor"], tried.json()["role"]) == ("bot:ops", "bot"), body


def test_what_needs_a_persons_rights_becomes_a_confirm_card_for_the_person_who_commented(api, botops):
    """Ana's comment on Ops' task lends nothing. BotOps tries with Ops' rights, and what they refuse is Ana's Confirm
    card in her chat with BotOps, not a refusal that needs 'someone with task-moving rights'."""
    _ready(api, botops)
    task, attempt = _commented(api, botops)
    before = get(api, "bots/ops/repositories", "ana-test")
    tried = api.put("/api/v2/bots/ops/repositories", json={"mode": "all", "all_access": "write"},
                    headers=headers(attempt["token"]))
    assert tried.status_code == 200, tried.text
    card = tried.json()
    assert card["needs_confirm"] is True and card["action"]["status"] == "pending"
    assert get(api, "bots/ops/repositories", "ana-test") == before
    with api.app.state.store.read() as c:
        row = c.execute("SELECT owner,method,path,proposer FROM assistant_actions WHERE id=?",
                        (card["action"]["id"],)).fetchone()
        assert dict(row) == {"owner": "human:ana", "method": "PUT", "path": "/api/v2/bots/ops/repositories",
                             "proposer": "botops"}
        said = H.message(c, card["message_id"])
        assert said["recipient_id"] == "human:ana" and task["title"] in said["body"]


def test_a_relayed_comment_still_lends_nothing_and_gets_no_card(api, botops):
    """Words that came through a channel others write in are no one's own: a plain refusal, not a card."""
    _ready(api, botops)
    task, attempt = _commented(api, botops)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET refs=json_set(coalesce(refs,'{}'),'$.routing',1) WHERE id=("
                  "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?)",
                  (attempt.get("attempt_id") or attempt["id"],))
    tried = api.put("/api/v2/bots/ops/repositories", json={"mode": "all", "all_access": "write"},
                    headers=headers(attempt["token"]))
    assert tried.status_code == 403, tried.text
    assert tried.json()["error"]["code"] == "on_behalf_of"


def test_a_worktree_refusal_says_who_can_give_the_access(api, botops):
    _ready(api, botops)
    task, attempt = _commented(api, botops)
    path = f"/api/v2/tasks/{task['id']}/worktrees"
    _route(api, path, refuse="This bot needs write access to this repository")
    tried = api.post(path, json={"repo": "acme/site"}, headers=headers(attempt["token"]))
    assert tried.status_code == 403
    error = tried.json()["error"]
    assert "owner or admin" in error["fix"] and "Settings > Repositories" in error["fix"]
    assert error["link"].endswith("#/repositories")


def test_another_persons_comment_on_a_carried_request_lends_only_the_bots_rights(api, botops, monkeypatch):
    """Ops hands BotOps Ana's own request, which lends her rights. Ben, who moves tasks, comments on it: his words never
    carry Ana's rights. His run keeps Ops' rights, and what they refuse is Ben's Confirm card, not Ana's."""
    import re
    from backend import botops_act
    monkeypatch.setattr(botops_act, "CARRIED", botops_act.CARRIED + [("GET", re.compile("/api/v2/qa-own-identity"))])
    monkeypatch.setattr(H, "can_move", lambda c, actor: actor in ("human:ana", "human:ben"))
    assign(api, botops, "ops")
    ready(api, botops, ["botops", "ops"])
    _route(api, "/api/v2/qa-own-identity", "GET")
    post(api, "chat/ops", {"text": "Give yourself read access to the acme site repository"}, token="ana-test")
    ops = claim(api, botops, "ops")
    task = post(api, "tasks", {"owner": "botops", "title": "Give Ops read access", "body": "Ana asked.",
                               "request_id": "turn"}, token=ops["token"])
    finish(api, botops, ops)
    first = claim(api, botops, "botops")
    assert get(api, "qa-own-identity", first["token"]) == {"actor": "human:ana", "role": "owner"}
    finish(api, botops, first)
    post(api, f"tasks/{task['id']}/comments", {"text": "Make it write access while you're at it"}, token="ben-test")
    attempt = claim(api, botops, "botops")
    assert get(api, "qa-own-identity", attempt["token"]) == {"actor": "bot:ops", "role": "bot"}
    before = get(api, "bots/ops/repositories", "ana-test")
    tried = api.put("/api/v2/bots/ops/repositories", json={"mode": "all", "all_access": "write"},
                    headers=headers(attempt["token"]))
    assert tried.status_code == 200, tried.text
    assert tried.json()["needs_confirm"] is True
    assert get(api, "bots/ops/repositories", "ana-test") == before
    with api.app.state.store.read() as c:
        card = c.execute("SELECT owner FROM assistant_actions WHERE id=?", (tried.json()["action"]["id"],)).fetchone()
        assert card["owner"] == "human:ben"


def test_botops_own_ask_is_a_plain_ask(api, botops):
    """Roles, goals or other relations on a subtask would reach with BotOps' rights for whoever asked: those keep the
    run's requester."""
    _ready(api, botops)
    task, attempt = _commented(api, botops)
    _route(api, "/api/v2/tasks/dry-run")
    parent = [{"task": task["id"], "kind": "parent"}]
    for extra in ({"roles": {"reviewer": ["ana"]}}, {"goal_id": "g1"},
                  {"relations": parent + [{"task": task["id"], "kind": "related"}]}):
        tried = api.post("/api/v2/tasks/dry-run", headers=headers(attempt["token"]),
                         json={"title": "x", "body": "x", "owner": "ana", "relations": parent, **extra})
        assert (tried.json()["actor"], tried.json()["role"]) == ("bot:ops", "bot"), extra
    tried = api.post("/api/v2/tasks/dry-run", headers=headers(attempt["token"]),
                     json={"title": "x", "body": "x", "owner": "ana", "relations": parent, "due": "2026-11-01T00:00:00Z"})
    assert (tried.json()["actor"], tried.json()["role"]) == ("bot:botops", "bot")


def test_private_subtask_from_bot_requested_run_is_judged_in_default_rehearsal(api, botops):
    from backend.tests.test_ask_ingest import FakeModel, items, calls
    model = FakeModel(kind="own_work")
    api.app.state.judge = model
    _ready(api, botops)
    ops = claim(api, botops, "ops")
    task = post(api, "tasks", {"owner_id": "botops", "title": "Align Ops mail instructions",
                               "body": "Update the docs only."}, token=ops["token"])
    finish(api, botops, ops)
    attempt = claim(api, botops, "botops")
    filed = api.post("/api/v2/tasks", headers=headers(attempt["token"]), json={
        "owner_id": "ana", "title": "Which mailbox should Ops send from?",
        "body": "Choose support@ or hello@", "visibility": "private",
        "relations": [{"task": task["id"], "kind": "parent"}]})
    assert filed.status_code == 200, filed.text
    made = filed.json()["task"]
    assert (made["owner_id"], made["requester_id"]) == ("human:ana", "bot:ops")
    [item], [call] = items(api), calls(api)
    assert (item["mode"], item["outcome"], item["would"]) == ("rehearsal", "through", "sent_back")
    assert item["asker"] == "bot:ops" and item["ref"] == made["id"] and item["call_id"] == call["id"]
    assert len(model.calls) == 1
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM ask_ingest_settings").fetchone()[0] == 0
        event = c.execute("SELECT detail FROM events WHERE action='task.create' AND target=?",
                          (made["id"],)).fetchone()
        assert '"via": "botops"' in event["detail"]
