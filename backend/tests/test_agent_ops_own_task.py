"""A task another bot asked Agent Ops for, and a person comments on it: Agent Ops adds its own task's worktree and files its ask
to a person with its own rights; what needs a person's rights becomes that person's Confirm card, never a dead end."""

from fastapi import Request

from backend.tests.helpers.api import assign, claim, get, headers, post, ready  # noqa: F401  (fixture)
from backend.tests.helpers.member_bots import agent_ops, finish  # noqa: F401  (fixture)
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
    """Ops files work for Agent Ops; Agent Ops' first run ends; Ana comments on the task, which starts Agent Ops' next run."""
    ops = claim(api, machine, "ops")
    task = post(
        api,
        "tasks",
        {"owner": "botops", "title": "Align Ops' mail instructions", "body": "Update the docs only."},
        token=ops["token"],
    )
    finish(api, machine, ops)
    finish(api, machine, claim(api, machine, "botops"))
    post(api, f"tasks/{task['id']}/comments", {"text": comment}, token="ana-test")
    attempt = claim(api, machine, "botops")
    with api.app.state.store.read() as c:
        started = c.execute(
            "SELECT m.* FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id WHERE a.id=?",
            (attempt.get("attempt_id") or attempt["id"],),
        ).fetchone()
        assert started["sender_id"] == "human:ana" and H.message_task_id(H.message(c, started["id"])) == task["id"]
        assert H.task(c, task["id"])["requester_id"] == "bot:ops"
    return task, attempt


def _ready(api, machine):
    assign(api, machine, "ops")
    ready(api, machine, ["botops", "ops"])
    post(api, "chat/ops", {"text": "Tidy your mail instructions"}, token="ana-test")


def test_agent_ops_adds_its_own_tasks_worktree_with_its_own_rights(api, agent_ops):
    _ready(api, agent_ops)
    task, attempt = _commented(api, agent_ops)
    for path in (f"/api/v2/tasks/{task['id']}/worktrees", f"/api/v2/tasks/{task['id']}/worktrees/attach"):
        _route(api, path)
        for cite in ({}, {"X-Tico-On-Behalf-Of": "turn"}):
            tried = api.post(path, json={"repo": "acme/site"}, headers={**headers(attempt["token"]), **cite})
            assert tried.status_code == 200, tried.text
            assert tried.json() == {"actor": "bot:botops", "role": "bot"}, (path, cite)


def test_a_worktree_on_someone_elses_task_still_borrows(api, agent_ops):
    """Only Agent Ops' own task is its own work: a worktree on Ops' task goes through the requester's rights as before."""
    _ready(api, agent_ops)
    _, attempt = _commented(api, agent_ops)
    other = post(api, "tasks", {"owner": "ops", "title": "Ops' own work", "body": "x"}, token="ana-test")
    path = f"/api/v2/tasks/{other['id']}/worktrees"
    _route(api, path)
    tried = api.post(path, json={"repo": "acme/site"}, headers=headers(attempt["token"]))
    assert tried.json() == {"actor": "bot:ops", "role": "bot"}


def test_agent_ops_files_a_subtask_for_a_person_on_its_own_task(api, agent_ops):
    _ready(api, agent_ops)
    task, attempt = _commented(api, agent_ops)
    filed = api.post(
        "/api/v2/tasks",
        headers=headers(attempt["token"]),
        json={
            "owner": "ana",
            "title": "Which mailbox should Ops send from?",
            "body": "Which mailbox should Ops send from: support@ or hello@?",
            "parent": task["id"],
        },
    )
    assert filed.status_code == 200, filed.text
    with api.app.state.store.read() as c:
        made = H.task(c, filed.json()["task"]["id"])
        # Agent Ops' own ask: it grants nothing, and it is not recorded as Ana's or Ops' request. A bot's task for a person
        # stays the bot's, with its question in her Needs you (backend/person_asks.py).
        assert (made["owner_id"], made["requester_id"]) == ("bot:botops", "bot:botops")
        assert TR.parent_of(c, made["id"]) == task["id"]
    [item] = [i for i in get(api, "needs-you", "ana-test")["items"] if i.get("task_id") == made["id"]]
    assert item["kind"] == "question"  # its ❓ to her


def test_a_subtask_for_a_bot_or_with_no_parent_is_not_agent_ops_own_ask(api, agent_ops):
    """The own-task rule covers a person's ask under Agent Ops' task only: other new tasks keep the run's requester."""
    _ready(api, agent_ops)
    task, attempt = _commented(api, agent_ops)
    _route(api, "/api/v2/tasks/dry-run")
    for body in (
        {"owner": "ops", "parent": task["id"]},
        {"owner": "ana"},
    ):
        tried = api.post(
            "/api/v2/tasks/dry-run", headers=headers(attempt["token"]), json={"title": "x", "body": "x", **body}
        )
        assert tried.status_code == 200, tried.text
        assert (tried.json()["actor"], tried.json()["role"]) == ("bot:ops", "bot"), body


def test_what_needs_a_persons_rights_becomes_a_confirm_card_for_the_person_who_commented(api, agent_ops):
    """Ana's comment on Ops' task lends nothing. Agent Ops tries with Ops' rights, and what they refuse is Ana's Confirm
    card in her chat with Agent Ops, not a refusal that needs 'someone with task-moving rights'."""
    _ready(api, agent_ops)
    task, attempt = _commented(api, agent_ops)
    before = get(api, "bots/ops/repositories", "ana-test")
    tried = api.put(
        "/api/v2/bots/ops/repositories", json={"mode": "all", "all_access": "write"}, headers=headers(attempt["token"])
    )
    assert tried.status_code == 200, tried.text
    card = tried.json()
    assert card["needs_confirm"] is True and card["action"]["status"] == "pending"
    assert get(api, "bots/ops/repositories", "ana-test") == before
    with api.app.state.store.read() as c:
        row = c.execute(
            "SELECT owner,method,path,proposer FROM assistant_actions WHERE id=?", (card["action"]["id"],)
        ).fetchone()
        assert dict(row) == {
            "owner": "human:ana",
            "method": "PUT",
            "path": "/api/v2/bots/ops/repositories",
            "proposer": "botops",
        }
        said = H.message(c, card["message_id"])
        assert said["recipient_id"] == "human:ana" and task["title"] in said["body"]


def test_a_relayed_comment_still_lends_nothing_and_gets_no_card(api, agent_ops):
    """Words that came through a channel others write in are no one's own: a plain refusal, not a card."""
    _ready(api, agent_ops)
    task, attempt = _commented(api, agent_ops)
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE messages SET refs=json_set(coalesce(refs,'{}'),'$.routing',1) WHERE id=("
            "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?)",
            (attempt.get("attempt_id") or attempt["id"],),
        )
    tried = api.put(
        "/api/v2/bots/ops/repositories", json={"mode": "all", "all_access": "write"}, headers=headers(attempt["token"])
    )
    assert tried.status_code == 403, tried.text
    assert tried.json()["error"]["code"] == "on_behalf_of"


def test_a_worktree_refusal_says_who_can_give_the_access(api, agent_ops):
    _ready(api, agent_ops)
    task, attempt = _commented(api, agent_ops)
    path = f"/api/v2/tasks/{task['id']}/worktrees"
    _route(api, path, refuse="This bot needs write access to this repository")
    tried = api.post(path, json={"repo": "acme/site"}, headers=headers(attempt["token"]))
    assert tried.status_code == 403
    error = tried.json()["error"]
    assert "owner or admin" in error["fix"] and "Settings > General › Repositories" in error["fix"]
    assert error["link"].endswith("#/settings/general/set-repos")


def test_another_persons_comment_on_a_carried_request_lends_only_the_bots_rights(api, agent_ops, monkeypatch):
    """Ops hands Agent Ops Ana's own request, which lends her rights. Ben, who moves tasks, comments on it: his words never
    carry Ana's rights. His run keeps Ops' rights, and what they refuse is Ben's Confirm card, not Ana's."""
    import re
    from backend import agent_ops_act

    monkeypatch.setattr(
        agent_ops_act, "CARRIED", agent_ops_act.CARRIED + [("GET", re.compile("/api/v2/qa-own-identity"))]
    )
    monkeypatch.setattr(H, "can_move", lambda c, actor: actor in ("human:ana", "human:ben"))
    assign(api, agent_ops, "ops")
    ready(api, agent_ops, ["botops", "ops"])
    _route(api, "/api/v2/qa-own-identity", "GET")
    post(api, "chat/ops", {"text": "Give yourself read access to the acme site repository"}, token="ana-test")
    ops = claim(api, agent_ops, "ops")
    task = post(
        api,
        "tasks",
        {"owner": "botops", "title": "Give Ops read access", "body": "Ana asked.", "request_id": "turn"},
        token=ops["token"],
    )
    finish(api, agent_ops, ops)
    first = claim(api, agent_ops, "botops")
    assert get(api, "qa-own-identity", first["token"]) == {"actor": "human:ana", "role": "owner"}
    finish(api, agent_ops, first)
    post(api, f"tasks/{task['id']}/comments", {"text": "Make it write access while you're at it"}, token="ben-test")
    attempt = claim(api, agent_ops, "botops")
    assert get(api, "qa-own-identity", attempt["token"]) == {"actor": "bot:ops", "role": "bot"}
    before = get(api, "bots/ops/repositories", "ana-test")
    tried = api.put(
        "/api/v2/bots/ops/repositories", json={"mode": "all", "all_access": "write"}, headers=headers(attempt["token"])
    )
    assert tried.status_code == 200, tried.text
    assert tried.json()["needs_confirm"] is True
    assert get(api, "bots/ops/repositories", "ana-test") == before
    with api.app.state.store.read() as c:
        card = c.execute("SELECT owner FROM assistant_actions WHERE id=?", (tried.json()["action"]["id"],)).fetchone()
        assert card["owner"] == "human:ben"


def test_agent_ops_own_ask_is_a_plain_ask(api, agent_ops):
    """Roles, goals or other relations on a subtask would reach with Agent Ops' rights for whoever asked: those keep the
    run's requester."""
    _ready(api, agent_ops)
    task, attempt = _commented(api, agent_ops)
    _route(api, "/api/v2/tasks/dry-run")
    for extra in (
        {"roles": {"reviewer": ["ana"]}},
        {"goal_id": "g1"},
        {"relations": [{"task": task["id"], "kind": "related"}]},
    ):
        tried = api.post(
            "/api/v2/tasks/dry-run",
            headers=headers(attempt["token"]),
            json={"title": "x", "body": "x", "owner": "ana", "parent": task["id"], **extra},
        )
        assert (tried.json()["actor"], tried.json()["role"]) == ("bot:ops", "bot"), extra
    tried = api.post(
        "/api/v2/tasks/dry-run",
        headers=headers(attempt["token"]),
        json={"title": "x", "body": "x", "owner": "ana", "parent": task["id"], "due": "2026-11-01T00:00:00Z"},
    )
    assert (tried.json()["actor"], tried.json()["role"]) == ("bot:botops", "bot")


def test_private_subtask_from_bot_requested_run_is_judged_on_by_default(api, agent_ops):
    from backend.tests.helpers.ask_ingest import FakeModel, items, calls

    model = FakeModel(kind="own_work")
    api.app.state.judge = model
    _ready(api, agent_ops)
    ops = claim(api, agent_ops, "ops")
    task = post(
        api,
        "tasks",
        {"owner": "botops", "title": "Align Ops mail instructions", "body": "Update the docs only."},
        token=ops["token"],
    )
    finish(api, agent_ops, ops)
    attempt = claim(api, agent_ops, "botops")
    filed = api.post(
        "/api/v2/tasks",
        headers=headers(attempt["token"]),
        json={
            "owner": "ana",
            "title": "Which mailbox should Ops send from?",
            "body": "Choose support@ or hello@",
            "visibility": "private",
            "parent": task["id"],
        },
    )
    assert filed.status_code == 422 and filed.json()["error"]["code"] == "ask_refused", filed.text
    [item], [call] = items(api), calls(api)
    assert (item["mode"], item["outcome"], item["would"]) == ("on", "sent_back", "sent_back")
    # Agent Ops files a task for a bot that asked it as itself (app.create_task): the ask is Agent Ops' own.
    assert item["asker"] == "bot:botops" and item["ref"] == "" and item["call_id"] == call["id"]
    assert len(model.calls) == 1
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM ask_ingest_settings").fetchone()[0] == 0
        assert not c.execute("SELECT 1 FROM tasks WHERE title='Which mailbox should Ops send from?'").fetchone()
        assert not c.execute("SELECT 1 FROM refusals").fetchone()


# ------------------------------------------------------------------ a bot ends its own task (owner decision 2026-11-23)
THREAD_PAGE = {"from_page": {"label": "Thread", "page": "thread"}}


def _asked_in_its_chat(api, machine, owner="botops", title="Add Raygun access to qa-e2e"):
    """Ana files a task for `owner`; its first run ends; she writes in the task's chat as the web sends it (POST
    /threads/{conversation}/messages, the task page's and the Thread page's chat), which starts its next run. Stored
    as task #35014's reply was: a chat message, no comment, which lends no one's rights."""
    task = post(api, "tasks", {"owner": owner, "title": title, "body": "x"}, token="ana-test")
    finish(api, machine, claim(api, machine, owner))
    said = post(
        api, f"threads/{task['conversation_id']}/messages", {"text": "You can close for now", "refs": THREAD_PAGE}
    )
    return task, said, claim(api, machine, owner)


def _seen(api, task_id, body):
    """`body` with the task's version, as a bot sends a move."""
    with api.app.state.store.read() as c:
        return {**body, "version": H.task(c, task_id)["version"]}


def _ended(api, task_id):
    """(status, the last change, who made it): the last not the engine's own (its move on to Done)."""
    with api.app.state.store.read() as c:
        row = H.task(c, task_id)
        acts = c.execute(
            "SELECT kind, actor_id FROM history_events WHERE subject_type='task' AND subject_id=? AND actor_id!=? "
            "ORDER BY rowid DESC LIMIT 1",
            (task_id, H.TICO_ACTOR),
        ).fetchone()
        return row["status"], acts["kind"], acts["actor_id"]


ENDS = [
    ("done", {"note": "Closed for now"}, "done"),
    ("move", {"to": "done", "why": "Finished"}, "done"),
    ("move", {"to": "closed", "why": "Chris said to close it for now"}, "closed"),
    ("snooze", {"until": "2099-01-01", "why": "Later"}, "open"),
]


def test_agent_ops_ends_its_own_task_with_its_own_rights_when_nothing_lends(api, agent_ops):
    """Task #35014: Chris's reply in the task's chat lent nothing, and every done, close or snooze was refused ("A person's
    words inside a task are not a request to Agent Ops"). Ending its own task needs no one's rights, and its owner closes
    it even though a person asked for it (owner decision, 2026-11-23: they can reopen it)."""
    for op, body, after in ENDS:
        task, _, attempt = _asked_in_its_chat(api, agent_ops, title=f"{op} {body}")
        ended = api.post(
            f"/api/v2/tasks/{task['id']}/{op}", json=_seen(api, task["id"], body), headers=headers(attempt["token"])
        )
        assert ended.status_code == 200, (op, body, ended.text)
        status, kind, actor = _ended(api, task["id"])
        assert (status, actor) == (after, "bot:botops"), (op, body)
        assert op != "snooze" or kind == "snooze", (op, body, kind)
        finish(api, agent_ops, attempt)


def test_a_refused_lend_still_reaches_nothing_else(api, agent_ops):
    """Only ending Agent Ops' own task falls back to its own rights: someone else's task, another move or anything
    beyond the task is refused as before, and never made with the person's rights."""
    assign(api, agent_ops, "ops")
    ready(api, agent_ops, ["botops", "ops"])
    other = post(api, "tasks", {"owner": "ops", "title": "Ops' own work", "body": "x"}, token="ana-test")
    task, _, attempt = _asked_in_its_chat(api, agent_ops)
    for path, body in (
        (f"tasks/{other['id']}/done", {"note": "x"}),
        (f"tasks/{other['id']}/move", {"to": "closed", "why": "x"}),
        (f"tasks/{task['id']}/move", {"to": "next", "why": "x"}),
        (f"tasks/{task['id']}/owner", {"owner": "ops", "why": "x"}),
    ):
        tried = api.post(
            "/api/v2/" + path, json=_seen(api, path.split("/")[1], body), headers=headers(attempt["token"])
        )
        assert tried.status_code == 403 and tried.json()["error"]["code"] == "on_behalf_of", (path, tried.text)
    tried = api.put(
        "/api/v2/bots/ops/repositories", json={"mode": "all", "all_access": "write"}, headers=headers(attempt["token"])
    )
    assert tried.status_code == 403 and tried.json()["error"]["code"] == "on_behalf_of", tried.text
    with api.app.state.store.read() as c:
        assert H.task(c, other["id"])["status"] == "open" and H.task(c, task["id"])["status"] == "open"


def test_an_ordinary_bot_ends_its_own_task(api, agent_ops):
    """Only Agent Ops borrows a requester's rights; any other bot works its own task with its own. Told "You can close
    for now" in a plain message in the task's chat, it closes its own task, though a person asked for it."""
    assign(api, agent_ops, "ops")
    ready(api, agent_ops, ["botops", "ops"])
    for op, body, after in ENDS:
        task, _, attempt = _asked_in_its_chat(api, agent_ops, owner="ops", title=f"{op} {body}")
        ended = api.post(
            f"/api/v2/tasks/{task['id']}/{op}", json=_seen(api, task["id"], body), headers=headers(attempt["token"])
        )
        assert ended.status_code == 200, (op, body, ended.text)
        assert _ended(api, task["id"])[::2] == (after, "bot:ops"), (op, body)
        finish(api, agent_ops, attempt)


def test_a_body_or_header_citation_of_the_turn_ends_its_own_task_too(api, agent_ops):
    """The citation is weighed once (agent_ops_act `act_for_requester`): `on_behalf_of: turn` in the body, or the
    header, falls back to its own rights as no citation does; the route never refuses it again."""
    for n, (body, cite) in enumerate(
        (({"on_behalf_of": "turn"}, {}), ({}, {"X-Tico-On-Behalf-Of": "turn"}), ({"on_behalf_of": "turn"}, {}))
    ):
        task, _, attempt = _asked_in_its_chat(api, agent_ops, title=f"cite {n}")
        op, more = ("move", {"to": "closed", "why": "x"}) if n == 2 else ("done", {"note": "x"})
        ended = api.post(
            f"/api/v2/tasks/{task['id']}/{op}",
            json=_seen(api, task["id"], {**more, **body}),
            headers={**headers(attempt["token"]), **cite},
        )
        assert ended.status_code == 200, (body, cite, ended.text)
        assert _ended(api, task["id"])[2] == "bot:botops"
        finish(api, agent_ops, attempt)


def test_words_that_lend_nothing_on_one_task_never_end_another(api, agent_ops):
    """Only the run's own task falls back to Agent Ops' own rights: another task it owns stays refused."""
    other = post(api, "tasks", {"owner": "botops", "title": "Another task for Agent Ops", "body": "x"})
    finish(api, agent_ops, claim(api, agent_ops, "botops"))
    _, _, attempt = _asked_in_its_chat(api, agent_ops)
    tried = api.post(
        f"/api/v2/tasks/{other['id']}/done",
        json=_seen(api, other["id"], {"note": "x"}),
        headers=headers(attempt["token"]),
    )
    assert tried.status_code == 403 and tried.json()["error"]["code"] == "on_behalf_of", tried.text
    with api.app.state.store.read() as c:
        assert H.task(c, other["id"])["status"] == "open"


def _lent(api, task_id, bot):
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT count(*) FROM task_delegations WHERE task_id=? AND delegate=? AND expires_at>?",
            (task_id, bot, H.now()),
        ).fetchone()[0]


def test_a_comment_deleted_from_the_chat_takes_back_the_rights_it_lent(api, agent_ops):
    """Ana's comment @mentions Ops: Ops works the task with her party rights. She deletes it from the task's chat
    (DELETE /messages/{id}, as the web does), and they end with it, as through DELETE /tasks/{id}/comments/{id}
    (H.expire_delegations)."""
    assign(api, agent_ops, "ops")
    ready(api, agent_ops, ["botops", "ops"])
    task = post(api, "tasks", {"owner": "botops", "title": "Tidy the launch note", "body": "x"})
    said = post(api, f"tasks/{task['id']}/comments", {"text": "@ops can you check the numbers?"})["comment"]
    assert _lent(api, task["id"], "bot:ops") == 1
    gone = api.delete(f"/api/v2/messages/{said['id']}", headers=headers("ana-test"))
    assert gone.status_code == 200, gone.text
    assert _lent(api, task["id"], "bot:ops") == 0


def test_a_held_close_of_its_own_task_is_sent_with_its_own_rights(api, agent_ops):
    """A rule holds Agent Ops' close that cited the turn in its body. The citation lent nothing and was weighed once,
    so the stored write has none: Send anyway closes the task with Agent Ops' own rights, not the refused lend again."""
    from backend.tests.helpers.org_rule_check import COLOR, Judge
    from backend.tests.helpers.org_rules import rule

    api.app.state.judge = Judge(api.app.state.store)
    rule(
        api,
        {"kind": "internal", "text": COLOR, "who": {"bots": ["botops"]}, "if_broken": "escalate", "reviewer": "ben"},
    )
    task, _, attempt = _asked_in_its_chat(api, agent_ops)
    body = _seen(api, task["id"], {"to": "closed", "why": "Our favorite color is gold", "on_behalf_of": "turn"})
    held = api.post(f"/api/v2/tasks/{task['id']}/move", json=body, headers=headers(attempt["token"]))
    assert held.status_code == 200 and held.json().get("held"), held.text
    hold = held.json()["held"]
    view = get(api, f"rule-holds/{hold['id']}", token="ben-test")
    post(api, f"rule-holds/{hold['id']}/send", {"nonce": view["nonce"]}, token="ben-test")
    assert _ended(api, task["id"])[::2] == ("closed", "bot:botops")
