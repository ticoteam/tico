"""What the Assistant did for you: listed with the words it acted on, undone once and only once, and only by you.
Every change here is made through the real route, cited to the person's own message the way their Assistant's turn does."""

import json

import pytest

from backend import hubdb as H
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.api import setup_attempt  # noqa: F401
from backend.tests.helpers.assistant import asked, assistant_turn, turn_message
from backend.tests.helpers.automation_api import make


def task_row(api, task_id):
    with api.app.state.store.read() as c:
        return dict(H.task(c, task_id))


def changes_for(api, obj, token="ben-test"):
    return [c for c in get(api, "assistant/changes?days=30", token)["changes"] if c["object"] == obj]


def restore(api, cid, token="ben-test", expected=200):
    r = api.post(f"/api/v2/assistant/changes/{cid}/restore", json={}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def as_assistant(api, person, work):
    """Run `work(c)` in one transaction as the person's Assistant, cited to nothing: how the route stamps its writes."""
    with api.app.state.store.transaction() as c:
        via, deleg = H.VIA.set("assistant"), H.DELEGATION.set({"for": person, "cited": "", "run": ""})
        try:
            return work(c)
        finally:
            H.VIA.reset(via)
            H.DELEGATION.reset(deleg)


def needs_ids(api, token="ben-test"):
    return {i["task_id"] for i in get(api, "needs-you", token)["items"] if i.get("task_id")}


def close_on_turn(api, task_id, attempt, said, version=None, to="closed", why=None):
    """The turn's close (or other move) of a task, through the task's own route, cited to the person's message."""
    version = task_row(api, task_id)["version"] if version is None else version
    body = {"to": to, "version": version, **({"why": why} if why else {})}
    return asked(api, f"tasks/{task_id}/move", body, attempt["token"], said)


def set_turns_at_once(api, limit, token):
    rev = get(api, "bots/ops", token)["revision"]
    return post(api, "bots/ops/turns-at-once", {"turns_at_once": limit, "expected_revision": rev}, token=token)


def turns_at_once(api, token):
    with api.app.state.store.read() as c:
        from backend import bot_settings, start_places

        row = c.execute(bot_settings.ROW_SQL + " WHERE bc.bot=?", ("ops",)).fetchone()
        return start_places.bot_limit(json.loads(row["config"] or "{}") or {})


def test_a_close_through_the_route_is_listed_and_restored_to_its_exact_prior_state(api):
    task = post(api, "tasks", {"title": "Old release notes", "body": "x", "owner": "human:ben"}, token="ben-test")
    before = task_row(api, task["id"])
    assert task["id"] in needs_ids(api)

    r, attempt = assistant_turn(api, "ben-test", "Close the old release notes")
    said = turn_message(api, attempt)
    close_on_turn(api, task["id"], attempt, said, task["version"])
    assert task_row(api, task["id"])["status"] == "closed"
    assert task["id"] not in needs_ids(api)

    [change] = changes_for(api, f"task:{task['id']}")
    assert change["what"].startswith("Closed") and change["undoable"] is True
    assert change["words"] == "Close the old release notes"

    restored = restore(api, change["id"])
    assert restored["restored"] is True and restored["already"] is False
    after = task_row(api, task["id"])
    assert (after["status"], after["stage_id"], after["closed_by_id"]) == (before["status"], before["stage_id"], None)
    assert task["id"] in needs_ids(api)


def test_restoring_a_change_twice_changes_nothing_and_says_so(api):
    task = post(api, "tasks", {"title": "Twice", "body": "x", "owner": "human:ben"}, token="ben-test")
    r, attempt = assistant_turn(api, "ben-test", "Close it")
    close_on_turn(api, task["id"], attempt, turn_message(api, attempt), task["version"])
    cid = changes_for(api, f"task:{task['id']}")[0]["id"]
    assert restore(api, cid)["already"] is False
    version = task_row(api, task["id"])["version"]
    assert restore(api, cid)["already"] is True
    assert task_row(api, task["id"])["version"] == version


def test_a_bulk_close_through_the_route_is_one_change_per_task(api):
    a = post(api, "tasks", {"title": "Bulk one", "body": "x", "owner": "human:ben"}, token="ben-test")
    b = post(api, "tasks", {"title": "Bulk two", "body": "x", "owner": "human:ben"}, token="ben-test")
    r, attempt = assistant_turn(api, "ben-test", "Close both of these")
    said = turn_message(api, attempt)
    # Many tasks at once is a person's own (task-bulk refuses a turn); the turn closes each through its own route.
    refused = asked(
        api, "task-bulk", {"filter": {"ids": [a["id"], b["id"]]}, "action": "close"}, attempt["token"], said, 403
    )
    assert "Only a person" in refused["error"]["detail"]
    for task in (a, b):
        close_on_turn(api, task["id"], attempt, said, task["version"])
    restore_one_of_two(api, a["id"], b["id"])


def restore_one_of_two(api, a, b):
    per_task = {t: changes_for(api, f"task:{t}") for t in (a, b)}
    assert all(len(rows) == 1 and rows[0]["undoable"] for rows in per_task.values())
    restore(api, per_task[a][0]["id"])
    assert task_row(api, a)["status"] != "closed"
    assert task_row(api, b)["status"] == "closed"


def test_a_setting_the_assistant_changes_through_the_route_is_listed_and_restored(api):
    r, attempt = assistant_turn(api, "ana-test", "Let ops run three turns at once")
    said = turn_message(api, attempt)
    rev = get(api, "bots/ops", "ana-test")["revision"]
    asked(api, "bots/ops/turns-at-once", {"turns_at_once": 3, "expected_revision": rev}, attempt["token"], said)
    [change] = [
        c
        for c in get(api, "assistant/changes?days=30", "ana-test")["changes"]
        if c["object"] == "setting:ops:turns_at_once"
    ]
    assert change["undoable"] is True
    restore(api, change["id"], token="ana-test")
    assert turns_at_once(api, "ana-test") is None


def test_someone_elses_change_is_not_found(api):
    task = post(api, "tasks", {"title": "Ana's", "body": "x", "owner": "human:ben"}, token="ben-test")
    r, attempt = assistant_turn(api, "ben-test", "Close it")
    close_on_turn(api, task["id"], attempt, turn_message(api, attempt), task["version"])
    cid = changes_for(api, f"task:{task['id']}")[0]["id"]
    restore(api, cid, token="cara-test", expected=404)


def test_a_message_citing_the_assistant_is_listed_however_its_refs_are_spaced(api):
    with api.app.state.store.transaction() as c:
        msg = H.say(c, "human:ben", "bot:assistant", "Ask Ops about the reset", kind="say")
        c.execute("UPDATE messages SET refs=? WHERE id=?", ('{"via":"assistant"}', msg["id"]))
    listed = [c for c in get(api, "assistant/changes?days=30", "ben-test")["changes"] if c["id"] == msg["id"]]
    assert listed and listed[0]["undoable"] is False
    restore(api, msg["id"], expected=409)


def test_undoing_an_assistant_reopen_leaves_the_task_closed_again(api):
    task = post(api, "tasks", {"title": "Reopen then undo", "body": "x", "owner": "human:ben"}, token="ben-test")
    post(api, f"tasks/{task['id']}/move", {"to": "closed", "version": task["version"]}, token="ben-test")
    r, attempt = assistant_turn(api, "ben-test", "Reopen the task")
    close_on_turn(api, task["id"], attempt, turn_message(api, attempt), to="reopen", why="Ben asked")
    assert task_row(api, task["id"])["status"] == "open"
    [change] = changes_for(api, f"task:{task['id']}")
    assert change["undoable"] is True
    restore(api, change["id"])
    row = task_row(api, task["id"])
    assert row["status"] == "closed" and row["closed_by_id"] == "human:ben"


def test_a_manager_who_is_not_the_owner_undoes_their_own_assistant_setting(api):
    """A co-owner of the bot made the setting through their Assistant; the list says so and restore does it."""
    post(api, "bots/ops/co-owners", {"add": ["cara"]})
    set_turns_at_once(api, 2, "cara-test")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE settings_changes SET via='assistant' WHERE bot='ops' AND field='turns_at_once'")
    [change] = [
        c
        for c in get(api, "assistant/changes?days=30", "cara-test")["changes"]
        if c["object"] == "setting:ops:turns_at_once"
    ]
    assert change["undoable"] is True
    restore(api, change["id"], token="cara-test")
    assert turns_at_once(api, "cara-test") is None


def test_a_change_to_a_task_the_person_can_no_longer_see_names_no_title(api):
    task = post(api, "tasks", {"title": "Ben's soon-private task", "body": "x", "owner": "human:ben"}, token="ben-test")
    post(api, f"tasks/{task['id']}/owner", {"owner": "human:ana", "version": task["version"]}, token="ben-test")
    as_assistant(api, "human:ben", lambda c: H.task_close(c, "human:ben", task["id"], note=""))
    with api.app.state.store.transaction() as c:  # the owner moved through /owner; who asked for it is a field
        c.execute("UPDATE tasks SET private=1, requester_id='human:ana' WHERE id=?", (task["id"],))
    assert api.get(f"/api/v2/tasks/{task['id']}", headers=headers("ben-test")).status_code == 404
    [change] = changes_for(api, f"task:{task['id']}")
    assert change["what"] == "Closed a task you can no longer see"
    assert change["undoable"] is False and change["why_not"] == "a task you can no longer see"
    assert "soon-private" not in json.dumps(change)
    restore(api, change["id"], expected=409)


def test_an_automation_change_is_not_restored_by_someone_who_no_longer_manages_it(api):
    aid = make(api)["id"]
    with api.app.state.store.transaction() as c:
        now = json.loads(c.execute("SELECT flow FROM automations WHERE id=?", (aid,)).fetchone()["flow"])
        before = dict(now, name="Earlier name")
        after = dict(now, owner="human:ana")
        c.execute(
            "INSERT INTO audit_log (id, created_at, actor, object, before, after, via) VALUES (?,?,?,?,?,?,?)",
            (H.new_id(), H.now(), "human:ben", f"automation:{aid}", json.dumps(before), json.dumps(after), "assistant"),
        )
        c.execute("UPDATE automations SET flow=? WHERE id=?", (json.dumps(after), aid))
    [change] = [
        c for c in get(api, "assistant/changes?days=30", "ben-test")["changes"] if c["object"] == f"automation:{aid}"
    ]
    assert change["undoable"] is False and change["why_not"] == "you no longer manage this automation"
    restore(api, change["id"], token="ben-test", expected=409)


def test_an_owner_change_the_assistant_made_is_restored_to_the_earlier_owner(api):
    task = post(api, "tasks", {"title": "Handover", "body": "x", "owner": "human:ben"}, token="ben-test")
    as_assistant(api, "human:ben", lambda c: H.task_update(c, "human:ben", task["id"], owner="human:cara"))
    [change] = changes_for(api, f"task:{task['id']}")
    assert change["undoable"] is True, change
    restore(api, change["id"])
    assert task_row(api, task["id"])["owner_id"] == "human:ben"


def test_a_note_only_update_right_after_a_close_does_not_reopen_it(api):
    task = post(api, "tasks", {"title": "Close then note", "body": "x", "owner": "human:ben"}, token="ben-test")
    as_assistant(api, "human:ben", lambda c: H.task_close(c, "human:ben", task["id"], note=""))
    as_assistant(api, "human:ben", lambda c: H.task_update(c, "human:ben", task["id"], note="Only a note"))
    # A field edit is not a move: it is not listed, and the close before it stays the one to take back.
    [close] = changes_for(api, f"task:{task['id']}")
    assert close["what"].startswith("Closed") and close["undoable"] is True
    assert task_row(api, task["id"])["status"] == "closed"
    restore(api, close["id"])
    assert task_row(api, task["id"])["status"] != "closed"


def test_five_changes_page_two_at_a_time_with_no_gaps_and_no_early_end(api):
    tasks = [
        post(api, "tasks", {"title": f"Page {n}", "body": "x", "owner": "human:ben"}, token="ben-test")
        for n in range(5)
    ]
    as_assistant(api, "human:ben", lambda c: [H.task_close(c, "human:ben", t["id"], note="") for t in tasks])
    mine = {f"task:{t['id']}" for t in tasks}
    pages, before = [], ""
    for _ in range(5):
        page = get(api, "assistant/changes?days=30&limit=2" + (f"&before={before}" if before else ""), "ben-test")
        pages.append(page)
        before = page["next"]
        if not before:
            break
    assert [len([c for c in p["changes"] if c["object"] in mine]) for p in pages] == [2, 2, 1]
    assert all(p["next"] for p in pages[:-1]) and pages[-1]["next"] is None
    ids = [c["id"] for p in pages for c in p["changes"] if c["object"] in mine]
    assert len(ids) == len(set(ids)) == 5
