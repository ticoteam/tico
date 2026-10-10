"""The tasks board: queues in rank order, two lanes,
labels, blocked-by, links, comments that wake or wait, movers, and plain-English titles."""

import uuid

import pytest
from fastapi.testclient import TestClient

from backend import hubdb as H
from backend import task_relations as TR
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import encode


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "TITLE_LINT", "warn")
    app = create_app(Settings(db_path=tmp_path / "hub.db", test_identities={
        "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
        "ben-test": Identity("human:ben", "human", "ben@acme.example"),
        "priya-test": Identity("human:priya", "human", "priya@acme.example"),
        # BotOps acting for Ben (backend/botops_act.py): his rights, not Ben signed in.
        "ben-via-botops": Identity("human:ben", "human", "ben@acme.example", via="botops", confirmed=True),
    }))
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            bots = {slug: {"name": slug, "runtime": "fake", "status": "active"}
                    for slug in ("ops", "cpo", "cmo")}
            H.sync_registry(c, bots, {"people": [
                {"id": "ana", "email": "ana@acme.example", "team": "leadership"},
                {"id": "ben", "email": "ben@acme.example", "team": "product"},
                {"id": "priya", "email": "priya@acme.example", "team": "sales"}]})
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({"people": [
                {"id": "ana", "email": "ana@acme.example", "team": "leadership", "primary_for": ["*"]},
                {"id": "ben", "email": "ben@acme.example", "team": "product"},
                {"id": "priya", "email": "priya@acme.example", "team": "sales"}]}),))
            for slug, config in bots.items():
                c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES(?,?,?,?)",
                          (slug, encode(config), "product" if slug == "cpo" else "marketing" if slug == "cmo" else None, "ana"))
            c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)",
                      (encode({"completed": "2026-01-01T00:00:00Z"}),))
        yield client


def headers(token="ana-test"):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}


def post(api, path, body, token="ana-test", expected=200):
    r = api.post("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    data = r.json()
    return data["task"] if isinstance(data, dict) and set(data) == {"task"} else data


def relate(api, tid, other, kind="related", token="ana-test", expected=200, remove=False):
    """POST /tasks/{tid}/relations: `tid kind other`, or take it off with `remove`."""
    return post(api, "tasks/" + tid + "/relations", {"task": other, "kind": kind, "remove": remove},
                token=token, expected=expected)


def related(task, kind, direction=None):
    """The ids a task view lists under `kind` (in one `direction` if given)."""
    return [r["id"] for r in (task.get("relations") or {}).get(kind, []) if direction in (None, r["direction"])]


def get(api, path, token="ana-test", expected=200):
    r = api.get("/api/v2/" + path, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def bot_token(api, slug="ops"):
    """A leased bot identity: enroll a runner, assign the bot, send it a message, claim the turn."""
    code = post(api, "enrollments", {"operator": "ana"})["code"]
    r = post(api, "runners/enroll", {"code": code, "label": "Mac", "platform": "test"})
    post(api, "bots/" + slug + "/assignment", {"runner_id": r["runner_id"], "expected_generation": 0})
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {slug: True}}, token=r["token"])
    post(api, "chat/" + slug, {"text": "Please look at your queue."})
    return post(api, "jobs/claim", {"bot": slug}, token=r["token"])["attempt"]["token"]


# ----------------------------------------------------------------------------- rank


def test_completing_a_bot_requested_human_task_needs_a_result(api):
    token = bot_token(api, "ops")
    task = post(api, "tasks", {"owner": "ana", "title": "Decide the content hold",
        "body": "Keep or change the hold?"}, token=token)
    post(api, "tasks/" + task["id"], {"version": task["version"], "status": "done"}, expected=422)
    done = post(api, "tasks/" + task["id"], {"version": task["version"], "status": "done",
        "note": "Keep the hold; no publication is approved."})
    assert done["status"] == "done" and done["note"] == "Keep the hold; no publication is approved."


def test_self_requested_completion_stays_done_until_explicit_close(api):
    owner, completion, token = 'ana', {'status': 'done'}, 'ana-test'
    task = post(api, 'tasks', {'owner': owner, 'title': 'Check the receipt', 'body': 'Check the receipt totals.'}, token=token)
    assert task['requester'] == task['owner']
    dry = post(api, 'tasks/dry-run', {'owner': owner, 'title': task['title'], 'body': task['body']}, token=token)
    assert any('already asks' in problem for problem in dry['problems'])
    with api.app.state.store.transaction() as c:
        with pytest.raises(H.Refused, match='already asks'):
            H.task_create(c, task['requester'], task['title'], task['body'], task['owner'])
    done = post(api, 'tasks/' + task['id'], {'version': task['version'], **completion, 'note': 'Receipt totals match.'}, token=token)
    assert done['status'] == 'done' and done['done_at']
    assert done['closed_at'] is None and done['closed_by'] is None
    history = get(api, 'tasks/' + task['id'], token=token)['events']
    assert not any(e['field'] == 'status' and e['new'] == 'closed' for e in history)
    dry = post(api, 'tasks/dry-run', {'owner': owner, 'title': task['title'], 'body': task['body']}, token=token)
    assert not any('already asks' in problem for problem in dry['problems'])
    # Repeating completed work does not require closing its history first; unfinished duplicates remain refused.
    fresh = post(api, 'tasks', {'owner': owner, 'title': task['title'], 'body': task['body']}, token=token)
    assert fresh['id'] != task['id'] and fresh['status'] == 'open'
    assert get(api, 'tasks/' + task['id'], token=token)['task']['status'] == 'done'
    closed = post(api, 'tasks/' + task['id'], {'version': done['version'], 'close': True}, token=token)
    assert closed['status'] == 'closed' and closed['closed_at'] and closed['closed_by'] == task['requester']
    assert closed['done_at'] == done['done_at']


# ----------------------------------------------------------------------------- lanes


# ----------------------------------------------------------------------------- labels
# ----------------------------------------------------------------------------- blocked by
def test_finishing_the_blocker_clears_blocked_by_and_wakes_the_bot_owner(api):
    blocker = post(api, "tasks", {"owner": "cmo", "title": "Write the copy", "body": "x"})
    blocked = post(api, "tasks", {"owner": "ops", "title": "Publish the page", "body": "x"})
    other = post(api, "tasks", {"owner": "cmo", "title": "Pick the photo", "body": "x"})
    blocked = relate(api, blocked["id"], blocker["id"], "blocked_by")
    entry = blocked["relations"]["blocks"][0]
    assert {k: entry[k] for k in ("id", "title", "status", "owner", "direction")} == {
        "id": blocker["id"], "title": "Write the copy", "status": "open", "owner": "bot:cmo", "direction": "in"}
    relate(api, blocked["id"], blocked["id"], "blocked_by", expected=422)
    relate(api, blocker["id"], blocked["id"], "blocked_by", expected=422)      # no loops
    assert related(relate(api, other["id"], blocked["id"], "blocks"), "blocks", "out") == [blocked["id"]]  # a second
    blocked = get(api, "tasks/" + blocked["id"])["task"]
    assert related(blocked, "blocks", "in") == [blocker["id"], other["id"]]
    assert related(get(api, "tasks/" + blocker["id"])["task"], "blocks", "out") == [blocked["id"]]
    post(api, "tasks/" + blocker["id"], {"version": blocker["version"], "status": "done", "note": "Done."})
    after = get(api, "tasks/" + blocked["id"])
    assert related(after["task"], "blocks") == [other["id"]]
    assert not any("Unblocked" in m["body"] for m in after["messages"])      # the photo still blocks it
    post(api, "tasks/" + other["id"], {"version": other["version"], "status": "done", "note": "Done."})
    after = get(api, "tasks/" + blocked["id"])
    assert "blocks" not in after["task"]["relations"]
    assert any(e["field"] == "blocked_by" and e["new"] is None for e in after["events"])
    assert any("Unblocked" in m["body"] for m in after["messages"])


# ----------------------------------------------------------------------------- links
# ----------------------------------------------------------------------------- comments
def jobs_for(api, bot):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM jobs WHERE bot=?", (bot,)).fetchone()[0]


def test_the_author_edits_a_comment_and_nobody_wakes(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Draft the newsletter", "body": "x"})
    said = post(api, f"tasks/{task['id']}/comments", {"text": "Use the Agust numbers."})
    assert said["woke"] and said["comment"]["edited_at"] is None
    before, queued = get(api, "tasks/" + task["id"]), jobs_for(api, "ops")
    edited = post(api, f"tasks/{task['id']}/comments/{said['comment']['id']}", {"text": "Use the August numbers."})
    assert edited["woke"] is False and edited["comment"]["edited_at"]
    after = get(api, "tasks/" + task["id"])
    assert [(m["body"], bool(m["edited_at"])) for m in after["comments"]] == [("Use the August numbers.", True)]
    assert len(after["messages"]) == len(before["messages"]) and jobs_for(api, "ops") == queued, "nothing sent, nobody woken"
    assert after["task"]["updated"] > before["task"]["updated"]
    assert any(e["field"] == "comment" and e["new"] == said["comment"]["id"] for e in after["events"])
    with api.app.state.store.read() as c:
        audit = c.execute("SELECT detail_json FROM events WHERE action='message.edited' AND target=?",
                          (said["comment"]["id"],)).fetchone()[0]
    assert "Agust" not in audit, "the audit keeps metadata without withdrawn words"


def test_only_its_author_edits_or_deletes_a_comment_signed_in_as_themselves(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Draft the newsletter", "body": "x"})
    said = post(api, f"tasks/{task['id']}/comments", {"text": "Use the September numbers."}, token="ben-test")
    path = f"tasks/{task['id']}/comments/{said['comment']['id']}"
    for token in ("ana-test", "priya-test", "ben-via-botops"):      # Tico's owner, a teammate, BotOps acting for Ben
        post(api, path, {"text": "Use the October numbers."}, token=token, expected=403)
        post(api, path + "/delete", {}, token=token, expected=403)
    assert [m["body"] for m in get(api, "tasks/" + task["id"])["comments"]] == ["Use the September numbers."]
    assert post(api, path, {"text": "Use the October numbers."}, token="ben-test")["comment"]["edited_at"]


def test_a_deleted_comment_leaves_the_task_and_never_reaches_the_bot(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Draft the newsletter", "body": "x"})
    with api.app.state.store.transaction() as c:
        ask = H.task_ask(c, "bot:ops", task["id"], "Which month?")
    said = post(api, f"tasks/{task['id']}/comments", {"text": "September, and call me first."})["comment"]
    before = get(api, "tasks/" + task["id"])
    assert before["task"]["ask"] is None, "writing back answered the question"
    gone = post(api, f"tasks/{task['id']}/comments/{said['id']}/delete", {})
    assert gone["comment"]["deleted_at"] and gone["woke"] is False
    after = get(api, "tasks/" + task["id"])
    assert said["id"] not in {m["id"] for m in gone["comments"] + after["comments"] + after["messages"]}
    assert after["task"]["updated"] > before["task"]["updated"]
    assert after["task"]["ask"]["id"] == ask["id"], "the question it answered is open again"
    get(api, "messages/" + said["id"], expected=404)
    post(api, f"tasks/{task['id']}/comments/{said['id']}", {"text": "October"}, expected=404)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (said["id"],)).fetchone()[0] == "cancelled"
        assert c.execute("SELECT expires FROM task_delegations WHERE message_id=?", (said["id"],)).fetchone()[0] <= H.now()


# ----------------------------------------------------------------------------- movers
def test_only_a_mover_changes_lane_labels_or_blocked_by_on_someone_elses_task(api):
    task = post(api, "tasks", {"owner": "cmo", "title": "Draft the newsletter", "body": "x"})
    post(api, "tasks/" + task["id"], {"version": task["version"], "labels": ["newsletter"]}, token="priya-test", expected=403)
    post(api, "tasks/" + task["id"], {"version": task["version"], "status": "done"}, token="priya-test", expected=403)
    ok = post(api, "tasks/" + task["id"], {"version": task["version"], "labels": ["newsletter"]}, token="ben-test")
    assert ok["labels"] == ["newsletter"]
    assert get(api, "tasks/" + task["id"], token="priya-test")["mover"] is False
    assert get(api, "tasks/" + task["id"], token="ben-test")["mover"] is True
    # the owner of a task still marks their own done, mover or not
    mine = post(api, "tasks", {"owner": "priya", "title": "Call the lead", "body": "Ask whether they want the demo."})
    done = post(api, "tasks/" + mine["id"], {"version": mine["version"], "status": "done"}, token="priya-test")
    assert done["status"] == "done"
    assert api.get("/api/me", headers=headers("priya-test")).json()["mover"] is False
    assert api.get("/api/me", headers=headers("ben-test")).json()["mover"] is True


# ----------------------------------------------------------------------------- titles
# ----------------------------------------------------------------------------- preferences


def test_related_tasks_list_each_other_hide_private_ones_and_survive_a_restore(api):
    api_half = post(api, "tasks", {"owner": "ops", "title": "Add the refund endpoint", "body": "x"})
    ui_half = post(api, "tasks", {"owner": "ben", "title": "Show the refund button", "body": "x"})
    titles = lambda tid, token="ana-test": [t["title"] for t in get(api, "tasks/" + tid, token=token)["task"]["relations"].get("related", [])]
    assert related(relate(api, api_half["id"], ui_half["id"], token="ben-test"), "related", "both") == [ui_half["id"]]
    relate(api, ui_half["id"], api_half["id"])        # the same pair from the other end changes nothing
    assert titles(ui_half["id"]) == ["Add the refund endpoint"]
    with api.app.state.store.read() as c:            # stored once, lower id first
        assert c.execute("SELECT count(*) FROM task_relations WHERE kind='related'").fetchone()[0] == 1
    relate(api, api_half["id"], api_half["id"], expected=422)
    # A private task's title never reaches someone who is not one of its two parties.
    secret = post(api, "tasks", {"owner": "priya", "title": "Secret salary review", "body": "x", "private": True},
                  token="ben-test")
    relate(api, secret["id"], api_half["id"], token="ben-test")
    assert titles(api_half["id"]) == ["Show the refund button"]
    assert len(titles(api_half["id"], "ben-test")) == 2
    assert secret["id"] not in str(get(api, "tasks?limit=500")["tasks"])
    # Deleting a task takes its relations into the trash, and restoring it puts them back.
    post(api, "tasks/" + ui_half["id"] + "/delete", {})
    assert titles(api_half["id"], "ben-test") == ["Secret salary review"]
    post(api, "tasks/" + ui_half["id"] + "/restore", {})
    assert len(titles(api_half["id"], "ben-test")) == 2
    assert related(relate(api, api_half["id"], ui_half["id"], token="ben-test", remove=True), "related") == [secret["id"]]
    assert "related" not in get(api, "tasks/" + ui_half["id"])["task"]["relations"]
    # Directed kinds read from each end; a pair is not both ways round.
    dup = post(api, "tasks", {"owner": "ops", "title": "Add the refund endpoint again", "body": "x",
                              "relations": [{"task": api_half["id"], "kind": "duplicate_of"}]})
    assert related(dup, "duplicate_of", "out") == [api_half["id"]]
    assert related(get(api, "tasks/" + api_half["id"])["task"], "duplicate_of", "in") == [dup["id"]]
    relate(api, api_half["id"], dup["id"], "duplicate_of", expected=422)
    follow = relate(api, ui_half["id"], api_half["id"], "follow_up")
    assert related(follow, "follow_up", "out") == [api_half["id"]]
    with api.app.state.store.read() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        fields = {r[0] for r in c.execute("SELECT field FROM task_events WHERE task_id=?", (api_half["id"],))}
        assert {"related", "duplicated_by", "follow_ups"} <= fields


# ----------------------------------------------------------------------------- tags
def test_tag_migration_backfills_once_and_preserves_legacy_column(api):
    cloud = False
    typ = pipeline(api)
    task = post(api, "tasks", {"owner": "cmo", "title": "Review the migration", "body": "x",
                              "type": typ["id"], "step": "Copy review"})
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE tasks SET labels_json=? WHERE id=?", ('["release","bug","bug"]', task["id"]))
        c.execute("DROP TABLE task_tags")
        c.execute("DROP TABLE tags")
        c.execute("PRAGMA user_version=13")
        c.execute("DELETE FROM cloud_migrations WHERE version=47")
    if cloud:
        store.initialize(seed_market=False)
    else:
        c = H.connect(store.settings.db_path)
        c.close()
    with store.read() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == len(H.MIGRATIONS)
        assert H.task(c, task["id"])["type_id"] == typ["id"]
        assert H.task(c, task["id"])["step_id"] == task["step_id"]
        assert {tag["key"] for tag in H.tags(c)} == {"release", "bug"}
        assert set(H.task_labels(H.task(c, task["id"]))) == {"release", "bug"}
        assert c.execute("SELECT COUNT(*) FROM task_tags").fetchone()[0] == 2
        if cloud:
            assert c.execute("SELECT 1 FROM cloud_migrations WHERE version=47").fetchone()
    changed = post(api, "tasks/" + task["id"], {"version": task["version"], "labels": ["bug"]})
    assert changed["labels"] == ["bug"]
    store.initialize(seed_market=False)
    with store.read() as c:
        assert H.task_labels(H.task(c, task["id"])) == ["bug"]
        assert c.execute("SELECT labels_json FROM tasks WHERE id=?", (task["id"],)).fetchone()[0] == '["release","bug","bug"]'


def test_tag_owner_movers_and_stale_versions(api):
    tag = post(api, "tags", {"key": "release-day", "owner": "priya", "markdown": "- [ ] Smoke checks"})["tag"]
    assert get(api, "tags/" + tag["id"], token="priya-test")["editable"] is True
    changed = post(api, "tags/" + tag["id"], {"version": 1, "markdown": "- [x] Smoke checks"}, token="priya-test")["tag"]
    assert changed["version"] == 2
    post(api, "tags/" + tag["id"], {"version": 1, "metadata": {"date": "2026-10-02"}}, token="priya-test", expected=409)
    assert get(api, "tags/" + tag["id"])["tag"]["metadata"] == {}
    post(api, "tags/" + tag["id"], {"version": 2, "owner": "ana"}, token="ben-test")
    post(api, "tags/" + tag["id"], {"version": 3, "markdown": "Clobber"}, token="priya-test", expected=403)
    post(api, "tags", {"key": "not-mine", "owner": "ana"}, token="priya-test", expected=403)
    mine = post(api, "tags", {"key": "my-checklist"}, token="priya-test")["tag"]
    assert mine["owner"] == "human:priya"
    task = post(api, "tasks", {"owner": "priya", "title": "Review the checklist", "body": "Read it."})
    post(api, "tasks/" + task["id"], {"version": task["version"], "labels": [mine["key"]]}, token="priya-test", expected=403)


# ----------------------------------------------------------------------------- task pipelines

def pipeline(api, token='ana-test'):
    return post(api, 'task-types', {'name': 'Marketing', 'steps': [
        {'name': 'Draft', 'status': 'open'},
        {'name': 'Legal review', 'status': 'review'},
        {'name': 'Copy review', 'status': 'review'},
        {'name': 'Complete', 'status': 'done'},
        {'name': 'Archive', 'status': 'closed'},
    ]}, token=token)['type']


def edit_pipeline_task(api, task, **fields):
    return post(api, 'tasks/' + task['id'], {'version': task['version'], **fields})


def test_steps_keep_status_contract_stay_first_match_and_clear(api):
    typ = pipeline(api)
    task = post(api, 'tasks', {'owner': 'cmo', 'title': 'Draft the campaign', 'body': 'Please.', 'type': typ['id']})
    assert task['type'] == {'id': typ['id'], 'name': 'Marketing'}
    assert task['step']['name'] == 'Draft' and task['status'] == 'open'
    task = edit_pipeline_task(api, task, step='Copy review')
    copy_id = task['step_id']
    assert task['status'] == 'review'
    # A status-only call from an old client stays on the second review step.
    task = edit_pipeline_task(api, task, status='review')
    assert task['step_id'] == copy_id
    task = edit_pipeline_task(api, task, status='doing')
    assert task['step'] is None and task['step_id'] is None and task['status'] == 'doing'
    task = edit_pipeline_task(api, task, status='review')
    assert task['step']['name'] == 'Legal review'
    # No step order: move straight to any step, then back to the first.
    task = edit_pipeline_task(api, task, step='Complete')
    assert task['status'] == 'done' and task['done_at']
    task = edit_pipeline_task(api, task, step='Draft')
    assert task['status'] == 'open' and task['done_at'] is None
    task = edit_pipeline_task(api, task, step='Archive')
    assert task['status'] == 'closed' and task['closed_at'] and task['step']['name'] == 'Archive'
    task = edit_pipeline_task(api, task, status='open')
    assert task['closed_at'] is None and task['step']['name'] == 'Draft'
    task = edit_pipeline_task(api, task, close=True)
    assert task['status'] == 'closed' and task['step']['name'] == 'Archive'
    history = get(api, 'tasks/' + task['id'])['events']
    assert any(e['field'] == 'step' and e['new'] == copy_id for e in history)
    listed = next(t for t in get(api, 'tasks?status=closed')['tasks'] if t['id'] == task['id'])
    assert listed['type'] == task['type'] and listed['step'] == task['step']


def test_type_and_step_management_permissions_and_references(api):
    post(api, 'task-types', {'name': 'Sales'}, token='priya-test', expected=403)
    typ = pipeline(api, token='ben-test')
    task = post(api, 'tasks', {'owner': 'cmo', 'title': 'Write the ad', 'body': 'Please.',
        'type': 'Marketing', 'step': 'Copy review'})
    assert task['status'] == 'review'
    clean = lambda steps: [{k: s[k] for k in ('id', 'name', 'position', 'status')} for s in steps]
    post(api, 'task-types/' + typ['id'], {'steps': clean(typ['steps'][:2])}, expected=422)
    post(api, 'task-types/' + typ['id'] + '/delete', {}, expected=422)
    renamed = clean(typ['steps'])
    renamed[1]['name'], renamed[2]['name'] = renamed[2]['name'], renamed[1]['name']
    post(api, 'task-types/' + typ['id'], {'steps': renamed})
    assert get(api, 'tasks/' + task['id'])['task']['step_id'] == task['step_id']
    task = edit_pipeline_task(api, task, type='General')
    assert task['type_id'] == 'general' and task['step_id'] == 'general-review'
    # Empty removes a step assignment without changing its status.
    task = edit_pipeline_task(api, task, step='')
    assert task['status'] == 'review' and task['step'] is None
    other = post(api, 'task-types', {'name': 'Design', 'steps': [{'name': 'Draft', 'status': 'open'}]})['type']
    post(api, 'tasks/' + task['id'], {'version': task['version'], 'step': other['steps'][0]['id']}, expected=422)
    post(api, 'task-types/' + typ['id'], {'steps': []})
    post(api, 'task-types/' + typ['id'] + '/delete', {})
    post(api, 'task-types/general/delete', {}, expected=422)
    with api.app.state.store.read() as c:
        assert c.execute('SELECT 1 FROM cloud_migrations WHERE version=48').fetchone()
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []


def test_step_moves_enforce_existing_bot_status_permissions(api):
    typ = post(api, 'task-types', {'name': 'Engineering', 'steps': [
        {'name': 'Merged', 'status': 'ready'}, {'name': 'Archive', 'status': 'closed'}]})['type']
    task = post(api, 'tasks', {'owner': 'ops', 'title': 'Build the screen', 'body': 'Please.', 'type': typ['id']})
    token = bot_token(api)
    for step in typ['steps']:
        post(api, 'tasks/' + task['id'], {'version': task['version'], 'step': step['id']}, token=token, expected=403)
    post(api, 'tasks', {'owner': 'cmo', 'title': 'Build another screen', 'body': 'Please.',
         'type': typ['id'], 'step': typ['steps'][0]['id']}, token=token, expected=403)
    assert get(api, 'tasks/' + task['id'])['task']['status'] == 'open'


def test_number_migration_preserves_shipped_schemas_and_existing_step_order(tmp_path):
    import sqlite3
    c = sqlite3.connect(tmp_path / 'old.db', isolation_level=None)
    c.row_factory = sqlite3.Row
    for schema in H.MIGRATIONS[:22]:
        H._apply(c, schema)
    c.execute('PRAGMA user_version=22')
    stamp = H.now()
    c.execute("INSERT INTO task_types(id,name,created,updated) VALUES('dev','Dev ticket',?,?)", (stamp, stamp))
    c.execute("INSERT INTO task_steps(id,type_id,name,position,status) VALUES('todo','dev','To do',0,'open')")
    for ident in ('first', 'second'):
        c.execute("INSERT INTO tasks(id,title,body,requester,owner,status,created,updated,type_id,step_id) "
                  "VALUES(?,?,'x','human:ana','bot:ops','open',?,?,'dev','todo')", (ident, ident, stamp, stamp))
    H.migrate(c)
    ranks = [tuple(row) for row in c.execute('SELECT id,step_rank,number FROM tasks ORDER BY step_rank')]
    assert [row[0] for row in ranks] == ['first', 'second'] and all(row[2] is None for row in ranks)
    assert H.MIGRATIONS[19:23] == [H.STORAGE_SCHEMA, H.TASK_REVIEW_SCHEMA, H.MEETING_REVIEW_SCHEMA, H.NUMBERS_SCHEMA]
    H._apply(c, H.NUMBERS_SCHEMA)
    H.migrate(c)
    assert ranks == [tuple(row) for row in c.execute('SELECT id,step_rank,number FROM tasks ORDER BY step_rank')]
    assert c.execute('PRAGMA user_version').fetchone()[0] == len(H.MIGRATIONS)
    c.close()


def test_three_level_tree_requester_rules_cycles_and_last_child_wake(api):
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Build the feature', 'body': 'x'})
    child = post(api, 'tasks', {'owner': 'cpo', 'title': 'Build the service', 'body': 'x', 'relations': [{'task': parent['id'], 'kind': 'parent'}]}, token='ben-test')
    leaf = post(api, 'tasks', {'owner': 'cmo', 'title': 'Build the page', 'body': 'x', 'relations': [{'task': child['id'], 'kind': 'parent'}]})
    assert child['requester'] == leaf['requester'] == parent['requester']
    post(api, 'tasks/' + leaf['id'] + '/links', {'url': 'https://github.com/example/service/pull/1'})
    summary = get(api, 'tasks/' + parent['id'])['task']['children_summary']
    assert summary == {'total': 2, 'open': 2, 'done': 0, 'prs_total': 1, 'prs_merged': 0, 'direct_total': 1, 'direct_done': 0}
    tree = get(api, 'tasks/' + parent['id'] + '/tree')
    assert tree[0]['id'] == child['id'] and tree[0]['children'][0]['pr_state'] == 'open'
    relate(api, parent['id'], leaf['id'], 'parent', expected=422)
    leaf = post(api, 'tasks/' + leaf['id'], {'version': leaf['version'], 'close': True})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE body='All subtasks done'").fetchone()[0] == 1
    child = post(api, 'tasks/' + child['id'], {'version': child['version'], 'status': 'done'})
    assert get(api, 'tasks/' + parent['id'])['task']['children_summary']['done'] == 2
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE body='All subtasks done'").fetchone()[0] == 2
    other = post(api, 'tasks', {'owner': 'ops', 'title': 'Build the next feature', 'body': 'x'})
    child = relate(api, child['id'], other['id'], 'parent')
    assert get(api, 'tasks/' + parent['id'] + '/tree') == []
    assert get(api, 'tasks/' + other['id'] + '/tree')[0]['children'][0]['id'] == leaf['id']
    # A human can still cancel an open parent.
    other = post(api, 'tasks/' + other['id'], {'version': other['version'], 'close': True})
    assert other['status'] == 'closed'


def test_parent_owner_bot_can_track_and_reparent_inherited_subtasks(api):
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Manage the feature', 'body': 'x'})
    token = bot_token(api)
    child = post(api, 'tasks', {'owner': 'cpo', 'title': 'Implement the feature', 'body': 'x', 'relations': [{'task': parent['id'], 'kind': 'parent'}]}, token=token)
    assert child['requester'] == 'bot:ops'
    assert get(api, 'tasks/' + child['short_id'], token=token)['task']['id'] == child['id']
    assert get(api, 'tasks/' + parent['short_id'] + '/tree', token=token)[0]['id'] == child['id']
    assert child['id'] in {row['id'] for row in get(api, 'tasks', token=token)['tasks']}
    rows = post(api, 'sql', {'sql': 'SELECT id FROM tasks'}, token=token)['rows']
    assert [child['id']] in rows
    unrelated = post(api, 'tasks', {'owner': 'cpo', 'title': 'Do unrelated work', 'body': 'x'})
    assert get(api, 'tasks/' + unrelated['id'], token=token)['task']['id'] == unrelated['id']
    # Company transparency grants reads; it does not grant unrelated task mutations.
    post(api, 'tasks/' + unrelated['id'], {'version': unrelated['version'], 'title': 'Change it'},
         token=token, expected=403)
    moved = relate(api, child['short_id'], parent['id'], 'parent', token=token, remove=True)
    assert related(moved, 'parent') == []


def test_delegate_cannot_turn_temporary_access_into_ancestor_access(api):
    token = bot_token(api)
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Own the plan', 'body': 'x'})
    unrelated = post(api, 'tasks', {'owner': 'cpo', 'title': 'Review other work', 'body': 'x'})
    with api.app.state.store.transaction() as c:
        c.execute('INSERT INTO task_delegations(task_id,delegate,requested_by,message_id,expires) VALUES(?,?,?,?,?)',
                  (unrelated['id'], 'bot:ops', 'human:ana', c.execute('SELECT id FROM messages LIMIT 1').fetchone()[0], H.shift(H.now(), days=1)))
    get(api, 'tasks/' + unrelated['id'], token=token)
    relate(api, unrelated['id'], parent['id'], 'parent', token=token, expected=403)
    # Direct ownership of the child still does not permit hanging it under a parent it merely reads.
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'human:ana', unrelated['id'], owner='bot:ops', mover=True)
        alien_parent = H.task_create(c, 'human:ana', 'Coordinate unrelated work', 'x', 'bot:cpo', lint=False)
        with pytest.raises(H.Refused):
            TR.relate(c, 'bot:ops', unrelated['id'], alien_parent['id'], 'parent')


def test_hidden_descendants_stay_hidden_in_details_lists_trees_sql_and_counts(api):
    from backend.tests.test_api import restrict
    token = bot_token(api)
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Coordinate visible work', 'body': 'x'})
    child = post(api, 'tasks', {'owner': 'cpo', 'title': 'Keep private work private', 'body': 'x', 'relations': [{'task': parent['id'], 'kind': 'parent'}]})
    with api.app.state.store.transaction() as c:
        restrict(c, 'cpo', people=['ana'])
    get(api, 'tasks/' + child['id'], token=token, expected=404)
    assert get(api, 'tasks/' + parent['id'] + '/tree', token=token) == []
    detail = get(api, 'tasks/' + parent['id'], token=token)['task']
    assert detail['children_summary']['total'] == detail['parts']['total'] == 0
    assert child['id'] not in {t['id'] for t in get(api, 'tasks', token=token)['tasks']}
    assert [child['id']] not in post(api, 'sql', {'sql': 'SELECT id FROM tasks'}, token=token)['rows']


def test_people_can_change_visible_task_links_but_bots_need_task_rights(api):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Attach task links', 'body': 'x'})
    path = 'tasks/' + task['id'] + '/links'
    get(api, 'tasks/' + task['id'], token='priya-test')
    link = post(api, path, {'url': 'https://example.com/work'}, token='priya-test')['links'][0]
    post(api, path, {'remove': link['id']}, token='priya-test')
    link = post(api, path, {'url': 'https://example.com/work'})['links'][0]
    assert api.delete('/api/v2/' + path + '/' + link['id'], headers=headers('priya-test')).status_code == 200
    # Worktree links keep the task-control requirement for people.
    with api.app.state.store.transaction() as c:
        worktree = H.task_link(c, 'human:ana', task['id'], 'https://example.com/tree', kind='worktree')
    post(api, path, {'remove': worktree['id']}, token='priya-test', expected=403)
    token = bot_token(api, 'ops')
    post(api, path, {'url': 'https://example.com/plan'}, token=token, expected=403)
    link = post(api, path, {'url': 'https://example.com/work'})['links'][-1]
    post(api, path, {'remove': link['id']}, token=token, expected=403)
    # The owning bot and an ancestor party retain their task rights.
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Track linked work', 'body': 'x'})
    child = post(api, 'tasks', {'owner': 'cpo', 'title': 'Build linked work', 'body': 'x', 'relations': [{'task': parent['id'], 'kind': 'parent'}]})
    post(api, 'tasks/' + parent['id'] + '/links', {'url': 'https://example.com/plan'}, token=token)
    linked = post(api, 'tasks/' + child['id'] + '/links', {'url': 'https://example.com/component'}, token=token)['links'][0]
    post(api, 'tasks/' + child['id'] + '/links', {'remove': linked['id']}, token=token)


def test_a_type_grants_extra_bot_permissions_while_ordinary_tasks_are_readable(api):
    """Ordinary reads are transparent; type settings add comments/subtasks or work, with human controls."""
    typ = post(api, 'task-types', {'name': 'Dev ticket', 'steps': [
        {'name': 'On Deck', 'status': 'open'}, {'name': 'PR Review', 'status': 'review'},
        {'name': 'Shipped', 'status': 'ready'}, {'name': 'Done', 'status': 'closed'}]})['type']
    assert typ['bots'] is None
    ticket = post(api, 'tasks', {'owner': 'ben', 'title': 'Fix the guest message times',
                                 'body': 'Please.', 'type': typ['id'], 'step': 'On Deck'})
    todo = post(api, 'tasks', {'owner': 'ben', 'title': 'Book the offsite', 'body': 'Please.'})
    token = bot_token(api, 'ops')
    listed = lambda: {t['id'] for t in get(api, 'tasks?limit=500', token=token)['tasks']}
    sql = lambda: {r[0] if isinstance(r, list) else r['id'] for r in post(
        api, 'sql', {'sql': 'SELECT id FROM tasks'}, token=token)['rows']}

    assert get(api, 'tasks/' + ticket['id'], token=token)['task']['id'] == ticket['id']
    assert ticket['id'] in listed() and ticket['id'] in sql()
    post(api, 'tasks/' + ticket['id'] + '/comments', {'text': 'Unrelated writer.'}, token=token, expected=403)

    post(api, 'task-types/' + typ['id'], {'bots': 'read'}, token='priya-test', expected=403)
    assert post(api, 'task-types/' + typ['id'], {'bots': 'read'})['type']['bots'] == 'read'
    assert get(api, 'tasks/' + ticket['id'], token=token)['task']['id'] == ticket['id']
    assert ticket['id'] in listed() and ticket['id'] in sql()
    assert todo['id'] in listed() and todo['id'] in sql()
    assert get(api, 'tasks/' + todo['id'], token=token)['task']['id'] == todo['id']
    post(api, 'tasks/' + ticket['id'] + '/comments', {'text': 'The same gap shows on two more threads.'}, token=token)
    child = post(api, 'tasks', {'owner': 'ops', 'title': 'Build the fix', 'body': 'Please.',
                                'relations': [{'task': ticket['id'], 'kind': 'parent'}]}, token=token)
    assert related(child, 'parent') == [ticket['id']]
    ticket = get(api, 'tasks/' + ticket['id'])['task']
    post(api, 'tasks/' + ticket['id'], {'version': ticket['version'], 'step': 'PR Review'}, token=token, expected=403)
    post(api, 'tasks/' + ticket['id'] + '/links', {'url': 'https://github.com/acme/app/pull/7'}, token=token, expected=403)

    post(api, 'task-types/' + typ['id'], {'bots': 'work'})
    moved = post(api, 'tasks/' + ticket['id'], {'version': ticket['version'], 'step': 'PR Review',
                                               'owner': 'priya'}, token=token)
    assert moved['status'] == 'review' and moved['owner'] == 'human:priya'
    post(api, 'tasks/' + ticket['id'] + '/links', {'url': 'https://github.com/acme/app/pull/7'}, token=token)
    for refused in ({'step': 'Shipped'}, {'step': 'Done'}, {'close': True}, {'labels': ['bug']}):
        r = api.post('/api/v2/tasks/' + ticket['id'], json={'version': moved['version'], **refused},
                     headers=headers(token))
        assert r.status_code in (403, 422), (refused, r.text)
    shipped = post(api, 'tasks/' + ticket['id'], {'version': moved['version'], 'step': 'Shipped', 'owner': 'ben'})
    child = get(api, 'tasks/' + child['id'])['task']
    post(api, 'tasks/' + child['id'], {'version': child['version'], 'close': True})
    closed = post(api, 'tasks/' + ticket['id'], {'version': shipped['version'], 'step': 'Done'}, token=token)
    assert closed['status'] == 'closed'
    todo = get(api, 'tasks/' + todo['id'])['task']
    post(api, 'tasks/' + todo['id'], {'version': todo['version'], 'status': 'doing'}, token=token, expected=403)

    post(api, 'task-types/' + typ['id'], {'bots': 'anyone'}, expected=422)
    assert post(api, 'task-types/' + typ['id'], {'bots': 'parties'})['type']['bots'] is None
    assert get(api, 'tasks/' + ticket['id'], token=token)['task']['id'] == ticket['id']
    assert ticket['id'] in listed()
    post(api, 'tasks/' + ticket['id'], {'version': closed['version'], 'status': 'doing'}, token=token, expected=403)


# ----------------------------------------------------------------------------- stats
def test_task_flow_counts_status_changes_by_day_and_filters_by_owner(api):
    """The Tasks page's stats read task_events: what entered each status, by day and by owner."""
    ops = post(api, "tasks", {"owner": "ops", "title": "Check the invoices", "body": "x"})
    post(api, "tasks", {"owner": "ana", "title": "Approve the budget", "body": "x"})
    post(api, "tasks/" + ops["id"], {"version": ops["version"], "status": "doing"})
    flow = get(api, "tasks/flow?days=7")
    assert flow["totals"] == {"created": 2, "doing": 1}
    assert flow["days"][-1]["created"] == 2 and flow["stages"]["open"]["n"] == 1
    assert get(api, "tasks/flow?owner_kind=bot")["totals"] == {"created": 1, "doing": 1}
    assert [(p["actor"], p["created"]) for p in get(api, "tasks/flow?owner=ana")["people"]] == [("human:ana", 1)]
    steps = get(api, "tasks/flow?type=" + H.GENERAL_TYPE)
    assert steps["steps"] and any(k.startswith("step:") for k in steps["totals"])
    get(api, "tasks/flow?days=0", expected=422)
