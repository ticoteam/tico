"""A person's task reaches BotOps with their rights only when they gave it to BotOps themselves. A bot that reassigns a
person's task to BotOps lends only its own rights: what they refuse goes to the person as a Confirm card."""
from backend.tests.test_api import api, assign, claim, get, headers, post, ready  # noqa: F401  (fixture)
from backend.tests.test_member_bots import botops, finish  # noqa: F401  (fixture)
from backend.store import H


def _reassign(api, task_id, token):
    version = get(api, f"tasks/{task_id}", "ana-test")["task"]["version"]
    post(api, f"tasks/{task_id}", {"owner": "botops", "version": version}, token=token)


def _write_everywhere(api, token):
    return api.put("/api/v2/bots/ops/repositories", json={"mode": "all", "all_access": "write"}, headers=headers(token))


def test_a_bot_reassigning_a_persons_task_to_botops_lends_only_its_own_rights(api, botops):
    assign(api, botops, "ops")
    ready(api, botops, ["botops", "ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Write the release notes", "body": "For 0.4"}, token="ana-test")
    ops = claim(api, botops, "ops")
    _reassign(api, task["id"], ops["token"])
    finish(api, botops, ops)
    attempt = claim(api, botops, "botops")
    before = get(api, "bots/ops/repositories", "ana-test")
    tried = _write_everywhere(api, attempt["token"])
    assert tried.status_code == 200, tried.text
    assert tried.json()["needs_confirm"] is True
    assert get(api, "bots/ops/repositories", "ana-test") == before
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["requester_id"] == "human:ana"
        card = c.execute("SELECT owner FROM assistant_actions WHERE id=?", (tried.json()["action"]["id"],)).fetchone()
        assert card["owner"] == "human:ana"
    # Citing the task as a follow-through lends nothing either.
    cited = api.put("/api/v2/bots/ops/repositories", headers=headers(attempt["token"]),
                    json={"mode": "all", "all_access": "write", "on_behalf_of": task["id"]})
    assert cited.status_code == 403, cited.text
    assert get(api, "bots/ops/repositories", "ana-test") == before


def test_a_person_who_gives_their_own_task_to_botops_lends_their_rights(api, botops):
    assign(api, botops, "ops")
    ready(api, botops, ["botops", "ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Give Ops write access", "body": "x"}, token="ana-test")
    _reassign(api, task["id"], "ana-test")
    attempt = claim(api, botops, "botops")
    tried = _write_everywhere(api, attempt["token"])
    assert tried.status_code == 200, tried.text
    assert "needs_confirm" not in tried.json()
    assert get(api, "bots/ops/repositories", "ana-test")["all_access"] == "write"


def test_botops_moving_a_persons_task_to_itself_with_lent_rights_lends_nothing_later(api, botops):
    """BotOps acts with Ana's rights on one request and reassigns another of her tasks to itself. That reassignment is
    BotOps', not Ana's: the moved task's own run never acts as her."""
    assign(api, botops, "ops")
    ready(api, botops, ["botops", "ops"])
    other = post(api, "tasks", {"owner": "ops", "title": "Tidy the backlog", "body": "x"}, token="ana-test")
    post(api, "tasks", {"owner": "botops", "title": "Rename the Ops bot", "body": "x"}, token="ana-test")
    first = claim(api, botops, "botops")
    _reassign(api, other["id"], first["token"])
    with api.app.state.store.read() as c:
        moved = c.execute("SELECT actor FROM task_events WHERE task_id=? AND field='owner' ORDER BY created_at DESC LIMIT 1",
                          (other["id"],)).fetchone()
        assert moved["actor"] == "human:ana"         # made with her rights, through BotOps
    finish(api, botops, first)
    later = claim(api, botops, "botops")
    with api.app.state.store.read() as c:
        started = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                            (later.get("attempt_id") or later["id"],)).fetchone()
        assert H.message_task_id(H.message(c, started["message_id"])) == other["id"]
    before = get(api, "bots/ops/repositories", "ana-test")
    tried = _write_everywhere(api, later["token"])
    assert tried.status_code == 403, tried.text
    assert get(api, "bots/ops/repositories", "ana-test") == before


def test_a_later_wake_run_citing_a_task_a_bot_moved_to_botops_lends_nothing(api, botops):
    """A keeper wake (the daily update) starts a run with no requester; citing the task Ops moved must not lend Ana's
    rights as a follow-through on her request would."""
    assign(api, botops, "ops")
    ready(api, botops, ["botops", "ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Write the release notes", "body": "For 0.4"}, token="ana-test")
    ops = claim(api, botops, "ops")
    _reassign(api, task["id"], ops["token"])
    finish(api, botops, ops)
    finish(api, botops, claim(api, botops, "botops"))           # the moved task's own notice
    with api.app.state.store.transaction() as c:
        H.say(c, H.KEEPER, "bot:botops", "Time for your daily update.", kind="notice", refs={"wake": "update"})
    later = claim(api, botops, "botops")
    before = get(api, "bots/ops/repositories", "ana-test")
    cited = api.put("/api/v2/bots/ops/repositories", json={"mode": "all", "all_access": "write"},
                    headers={**headers(later["token"]), "X-Tico-On-Behalf-Of": task["id"]})
    assert cited.status_code == 403, cited.text
    assert "gave it to BotOps" in cited.json()["error"]["detail"]
    assert get(api, "bots/ops/repositories", "ana-test") == before


def test_the_persons_comment_on_a_task_a_bot_moved_says_why_it_lends_nothing(api, botops):
    assign(api, botops, "ops")
    ready(api, botops, ["botops", "ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Write the release notes", "body": "For 0.4"}, token="ana-test")
    ops = claim(api, botops, "ops")
    _reassign(api, task["id"], ops["token"])
    finish(api, botops, ops)
    finish(api, botops, claim(api, botops, "botops"))           # the moved task's own notice
    post(api, f"tasks/{task['id']}/comments", {"text": "Go ahead and give Ops write everywhere"}, token="ana-test")
    later = claim(api, botops, "botops")
    tried = _write_everywhere(api, later["token"])
    assert tried.status_code == 403, tried.text
    assert "A bot gave this task to BotOps" in tried.json()["error"]["detail"]
