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
from backend.tests.test_mcp import call


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
    post(api, "tasks/" + task["id"], {"version": task["version"], "close": True}, expected=422)
    done = post(api, "tasks/" + task["id"], {"version": task["version"], "status": "done",
        "note": "Keep the hold; no publication is approved."})
    assert done["status"] == "done" and done["note"] == "Keep the hold; no publication is approved."


@pytest.mark.parametrize('owner', ['ana', 'ops'])
@pytest.mark.parametrize('completion', [{'status': 'done'}, {'step': 'Done'}])
def test_self_requested_completion_stays_done_until_explicit_close(api, owner, completion):
    token = 'ana-test' if owner == 'ana' else bot_token(api)
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


def test_a_question_an_answer_or_a_chat_line_is_not_a_comment_to_edit(api):
    # A person's task has a thread of its own: Ben asks, Ana answers.
    task = post(api, "tasks", {"owner": "ben", "title": "Draft the newsletter", "body": "Draft it."})
    ask = post(api, f"tasks/{task['id']}/ask", {"text": "Which month?"}, token="ben-test")
    answer = post(api, f"messages/{ask['id']}/answer", {"text": "September"})
    # A bot's task lives in its chat room, where a chat line that mentions the task is not a comment.
    work = post(api, "tasks", {"owner": "ops", "title": "Draft the update", "body": "x"})
    with api.app.state.store.transaction() as c:
        chat = H.say(c, "human:ana", "bot:ops", "Also the footer.", conversation_id=work["conversation_id"],
                     refs={"task": work["id"]})
    for tid, mid in ((task["id"], ask["id"]), (task["id"], answer["id"]), (work["id"], chat["id"])):
        refused = post(api, f"tasks/{tid}/comments/{mid}", {"text": "October"}, expected=403)
        assert "not a question, an answer" in refused["error"]["detail"]
        post(api, f"tasks/{tid}/comments/{mid}/delete", {}, expected=403)


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


def test_task_labels_and_links_with_an_attachment(api):
    import json
    response = api.post("/api/v2/uploads/tasks", headers=headers(), data={
        "owner": "ana", "title": "Review the release", "body": "Check the attached notes.",
        "labels": json.dumps(["release"]), "links": json.dumps(["https://example.com/release"]),
        "acceptance_criteria": json.dumps(["Read the notes"])},
        files={"files": ("notes.md", b"Release notes", "text/markdown")})
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["labels"] == ["release"]
    assert task["links"][0]["url"] == "https://example.com/release"
    assert task["attachments"][0]["name"] == "notes.md"


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
@pytest.mark.parametrize("cloud", [False, True])
def test_tag_migration_backfills_once_and_preserves_legacy_column(api, cloud):
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


def test_legacy_label_keys_write_tags_and_filter_by_join(api):
    task = post(api, "tasks", {"owner": "cmo", "title": "Fix the settings", "body": "x", "labels": ["Bug", "settings"]})
    assert task["labels"] == ["bug", "settings"]
    assert [(tag["key"], tag["label"], tag["metadata"]) for tag in task["tags"]] == [
        ("bug", "bug", {}), ("settings", "settings", {})]
    with api.app.state.store.transaction() as c:
        assert c.execute("SELECT labels_json FROM tasks WHERE id=?", (task["id"],)).fetchone()[0] == "[]"
        c.execute("UPDATE tasks SET labels_json='[\"obsolete\"]' WHERE id=?", (task["id"],))
    assert get(api, "tasks?label=bug")["tasks"][0]["id"] == task["id"]
    assert get(api, "tasks?label=obsolete")["tasks"] == []
    assert get(api, "tasks/labels")["labels"] == ["bug", "settings"]
    updated = post(api, "tasks/" + task["id"], {"version": task["version"], "labels": ["settings", "new-key"]})
    assert updated["labels"] == ["settings", "new-key"]
    data = get(api, "tasks/" + task["id"])
    assert data["task"]["labels"] == updated["labels"]
    assert any(event["field"] == "labels" and event["new"] == '["settings", "new-key"]' for event in data["events"])
    with api.app.state.store.read() as c:
        assert {row[0] for row in c.execute("SELECT action FROM events WHERE action LIKE 'tag.%'")} >= {
            "tag.create", "tag.attach", "tag.detach"}


def test_template_instances_copy_defaults_and_cannot_attach_templates(api):
    template = post(api, "tags", {"key": "release-checklist", "label": "release", "is_template": True,
        "metadata": {"date": "2026-10-02", "channel": "stable"}, "markdown": "- [ ] Smoke checks"})["tag"]
    instance = post(api, "tags/" + template["id"] + "/instances", {"key": "release-2026-10-02", "metadata": {"date": "2026-10-03"}})["tag"]
    assert instance["template_id"] == template["id"] and not instance["is_template"]
    assert instance["label"] == "release" and instance["markdown"] == template["markdown"]
    assert instance["metadata"] == {"date": "2026-10-03", "channel": "stable"}
    post(api, "tags/" + template["id"], {"version": 1, "markdown": "Changed", "metadata": {} })
    assert get(api, "tags/" + instance["key"])["tag"]["markdown"] == "- [ ] Smoke checks"
    assert get(api, "tags?is_template=true")["tags"][0]["id"] == template["id"]
    task = post(api, "tasks", {"owner": "cmo", "title": "Ship the release", "body": "x", "labels": [instance["key"]]})
    assert task["tags"][0]["metadata"] == instance["metadata"]
    assert get(api, "tags/" + instance["key"])["tasks"][0]["id"] == task["id"]
    post(api, "tasks/" + task["id"], {"version": task["version"], "labels": ["would-be-new", template["key"]]}, expected=422)
    assert get(api, "tags/" + instance["key"])["tasks"][0]["id"] == task["id"]
    assert not any(tag["key"] == "would-be-new" for tag in get(api, "tags")["tags"])
    post(api, "tasks", {"owner": "cmo", "title": "Invalid template task", "body": "x", "labels": [template["key"]]}, expected=422)


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


def test_bot_tag_owner_edits_and_tag_tasks_respect_visibility(api):
    token = bot_token(api, "ops")
    tag = post(api, "tags", {"key": "release-bot", "markdown": "- [ ] Smoke checks"}, token=token)["tag"]
    post(api, "tags/" + tag["id"], {"version": 1, "markdown": "- [x] Smoke checks"}, token=token)
    mine = post(api, "tasks", {"owner": "ops", "title": "Run smoke checks", "body": "x", "labels": [tag["key"]]})
    other = post(api, "tasks", {"owner": "cmo", "title": "Write release notes", "body": "x", "labels": [tag["key"]]})
    assert {task["id"] for task in get(api, "tags/" + tag["id"], token=token)["tasks"]} == {mine["id"], other["id"]}
    assert {task["id"] for task in get(api, "tags/" + tag["id"])["tasks"]} == {mine["id"], other["id"]}
    sql = post(api, "sql", {"sql": "SELECT task_id FROM task_tags JOIN tags ON tags.id=task_tags.tag_id WHERE tags.key='release-bot'"}, token=token)
    assert {row[0] for row in sql["rows"]} == {mine["id"], other["id"]}
    assert get(api, "tasks/labels", token=token)["tags"][0]["key"] == tag["key"]


def test_task_label_strings_resolve_only_by_key_even_if_one_is_a_tag_id(api):
    tag = post(api, "tags", {"key": "release-checklist"})["tag"]
    task = post(api, "tasks", {"owner": "cmo", "title": "Review a key collision", "body": "x", "labels": [tag["id"]]})
    assert task["labels"] == [tag["id"]]
    assert task["tags"][0]["key"] == tag["id"]
    assert task["tags"][0]["id"] != tag["id"]
    assert get(api, "tasks/labels")["tags"][0]["key"] == tag["id"]


def test_needs_you_includes_tag_keys_and_metadata(api):
    tag = post(api, "tags", {"key": "release-2026-10-02", "label": "release", "metadata": {"date": "2026-10-02"}})["tag"]
    task = post(api, "tasks", {"owner": "ana", "title": "Review the release", "body": "Read the notes.", "labels": [tag["key"]]})
    items = get(api, "needs-you")["items"]
    mine = next(item for item in items if item["id"] == task["id"])
    assert mine["labels"] == [tag["key"]]
    assert mine["tags"][0]["metadata"] == {"date": "2026-10-02"}

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


def test_template_tags_and_pipeline_moves_preserve_each_other(api):
    typ = pipeline(api)
    template = post(api, 'tags', {'key': 'release-checklist', 'label': 'release',
        'is_template': True, 'markdown': '- [ ] Smoke checks'})['tag']
    tag = post(api, 'tags/' + template['id'] + '/instances', {
        'key': 'release-2026-10-02', 'metadata': {'date': '2026-10-02'}})['tag']
    task = post(api, 'tasks', {'owner': 'cmo', 'title': 'Review the release checklist',
        'body': 'Please.', 'type': typ['id'], 'step': 'Draft', 'labels': [tag['key']]})
    task = edit_pipeline_task(api, task, step='Copy review', labels=[tag['key'], 'launch'])
    assert task['status'] == 'review' and task['step']['name'] == 'Copy review'
    assert task['labels'] == [tag['key'], 'launch']
    assert task['tags'][0]['metadata'] == tag['metadata']
    step_id = task['step_id']
    task = edit_pipeline_task(api, task, labels=[tag['key']])
    assert task['type_id'] == typ['id'] and task['step_id'] == step_id
    task = edit_pipeline_task(api, task, status='doing')
    assert task['step'] is None and task['labels'] == [tag['key']]
    post(api, 'tags/' + tag['id'], {'version': tag['version'], 'markdown': '- [x] Smoke checks'})
    listed = get(api, 'tasks?label=' + tag['key'])['tasks'][0]
    related = get(api, 'tags/' + tag['id'])['tasks'][0]
    for view in (listed, related):
        assert view['type'] == {'id': typ['id'], 'name': 'Marketing'}
        assert view['status'] == 'doing' and view['step'] is None
        assert view['tags'][0]['markdown'] == '- [x] Smoke checks'
    task = edit_pipeline_task(api, task, step='Complete')
    task = edit_pipeline_task(api, task, close=True)
    assert task['step']['name'] == 'Archive' and task['labels'] == [tag['key']]
    detail = get(api, 'tasks/' + task['id'])
    assert {'labels', 'step', 'status'} <= {event['field'] for event in detail['events']}
    with api.app.state.store.read() as c:
        assert c.execute('SELECT labels_json FROM tasks WHERE id=?', (task['id'],)).fetchone()[0] == '[]'


def test_a_custom_types_task_is_a_ticket_not_an_ask(api):
    typ = pipeline(api)
    ticket = {"owner": "priya", "title": "(B/F) Account page: the copy", "body": "Details. " * 150}
    refused = api.post("/api/v2/tasks", json=ticket, headers=headers())
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "lint"
    assert not post(api, "tasks/dry-run", ticket)["ok"]
    ticket["type"] = typ["name"]
    assert post(api, "tasks/dry-run", ticket)["ok"]
    task = post(api, "tasks", ticket)
    assert task["title"] == ticket["title"] and task["type"]["id"] == typ["id"]
    assert task["body"] == ticket["body"].strip()


def test_bot_ticket_titles_and_long_descriptions_stay_exempt_on_custom_types(api, monkeypatch):
    monkeypatch.setattr(H, "TITLE_LINT", "refuse")
    typ = pipeline(api)
    token = bot_token(api)
    ticket = {"owner": "ana", "title": "#18945 (B/F) ACCOUNT COPY", "body": "Details. " * 150}
    assert not post(api, "tasks/dry-run", ticket, token=token)["ok"]
    ticket["type"] = typ["id"]
    assert post(api, "tasks/dry-run", ticket, token=token)["ok"]
    made = post(api, "tasks", ticket, token=token)
    changed = post(api, "tasks/" + made["id"], {"version": made["version"], "title": "#18945 COPY UPDATE"}, token=token)
    assert changed["title"] == "#18945 COPY UPDATE" and changed["body"] == ticket["body"].strip()


def test_a_renamed_task_is_checked_like_a_new_one_and_keeps_its_history(api):
    ask = post(api, "tasks", {"owner": "priya", "title": "Approve the launch copy", "body": "Yes or no?"})
    refused = api.post("/api/v2/tasks/" + ask["id"], json={"version": ask["version"], "title": "The launch copy"},
                       headers=headers())
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "lint"
    renamed = post(api, "tasks/" + ask["id"], {"version": ask["version"], "title": "Approve the final copy"},
                   token="priya-test")
    assert renamed["title"] == "Approve the final copy"
    assert any(e["field"] == "title" and (e["old"], e["new"]) == ("Approve the launch copy", "Approve the final copy")
               for e in get(api, "tasks/" + ask["id"])["events"])
    with api.app.state.store.read() as c:
        subject = c.execute("SELECT subject FROM conversations WHERE id=?", (renamed["conversation_id"],)).fetchone()[0]
    assert subject == "Approve the final copy"
    typ = pipeline(api)
    post(api, "task-types/" + typ["id"], {"numbered": True})
    ticket = post(api, "tasks", {"owner": "priya", "title": "(B) Account page", "body": "x", "type": typ["id"]})
    number, step = ticket["number"], ticket["step_id"]
    error, result = call(api, "hub_task_update", {"id": "#" + str(number), "title": "(B/F) Account page: the copy"})
    assert not error, result
    ticket = result["task"]
    assert ticket["title"] == "(B/F) Account page: the copy" and ticket["number"] == number and ticket["step_id"] == step
    since = ticket["updated"]
    for change in ({"title": " "}, {"title": "Changed", "close": True}, {"title": "The other page", "type": "General"}):
        post(api, "tasks/" + ticket["id"], {"version": ticket["version"], **change}, expected=422)
    duplicate = post(api, "tasks", {"owner": "priya", "title": "(B) Another page", "body": "x", "type": typ["id"]})
    post(api, "tasks/" + ticket["id"], {"version": ticket["version"], "title": duplicate["title"]}, expected=422)
    after = get(api, "tasks/" + ticket["id"])["task"]
    assert (after["title"], after["version"], after["updated"]) == (ticket["title"], ticket["version"], since)
    other = post(api, "tasks", {"owner": "cmo", "title": "Draft the launch copy", "body": "x"})
    post(api, "tasks/" + other["id"], {"version": other["version"], "title": "Draft it"}, token="priya-test", expected=403)


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


def test_editing_an_occupied_step_status_requires_explicit_task_moves(api):
    typ = pipeline(api)
    task = post(api, 'tasks', {'owner': 'cmo', 'title': 'Review the launch', 'body': 'Please.', 'type': typ['id']})
    before = get(api, 'tasks/' + task['id'])
    steps = [{k: s[k] for k in ('id', 'name', 'position', 'status')} for s in typ['steps']]
    steps[0]['status'] = 'done'
    post(api, 'task-types/' + typ['id'], {'steps': steps}, expected=422)
    assert get(api, 'tasks/' + task['id']) == before


def test_same_status_step_move_does_not_repeat_a_result_or_completion(api):
    typ = post(api, 'task-types', {'name': 'Decisions', 'steps': [
        {'name': 'Recorded', 'status': 'done'}, {'name': 'Verified', 'status': 'done'},
        {'name': 'Archived', 'status': 'closed'}, {'name': 'Filed', 'status': 'closed'}]})['type']
    token = bot_token(api)
    task = post(api, 'tasks', {'owner': 'ana', 'title': 'Decide the campaign', 'body': 'Review it.',
        'type': typ['id']}, token=token)
    task = edit_pipeline_task(api, task, step='Recorded', note='Proceed with the draft.')
    done_at = task['done_at']
    task = edit_pipeline_task(api, task, step='Verified')
    assert task['status'] == 'done' and task['done_at'] == done_at
    task = edit_pipeline_task(api, task, step='Archived')
    closed_at = task['closed_at']
    task = edit_pipeline_task(api, task, step='Filed')
    assert task['status'] == 'closed' and task['closed_at'] == closed_at
    task = edit_pipeline_task(api, task, close=True)
    assert task['closed_at'] == closed_at


def test_stranded_auto_reopen_maps_to_the_types_open_step(api):
    typ = post(api, 'task-types', {'name': 'Follow-up', 'steps': [
        {'name': 'Next', 'status': 'open'}, {'name': 'Waiting for input', 'status': 'waiting'}]})['type']
    task = post(api, 'tasks', {'owner': 'cmo', 'title': 'Review the draft', 'body': 'Please.', 'type': typ['id']})
    task = edit_pipeline_task(api, task, step='Waiting for input')
    with api.app.state.store.transaction() as c:
        future = H.shift(H.now(), days=2)
        assert (task['id'], 'open') in H.sweep_stranded(c, at=future)
        after = H.task(c, task['id'])
        assert after['status'] == 'open' and after['step_id'] == typ['steps'][0]['id']


def test_a_numbered_type_numbers_its_tasks_once_and_keeps_an_imported_number(api):
    typ = post(api, 'task-types', {'name': 'Dev ticket', 'numbered': True, 'steps': [
        {'name': 'Backlog', 'status': 'open'}, {'name': 'Shipped', 'status': 'done'}]})['type']
    assert typ['numbered'] is True
    first = post(api, 'tasks', {'owner': 'cmo', 'title': 'Fix the account page', 'body': 'x', 'type': typ['id']})
    imported = post(api, 'tasks', {'owner': 'cmo', 'title': 'Fix the signup page', 'body': 'x', 'type': typ['id'],
                                   'number': 18945})
    assert (first['number'], imported['number']) == (1, 18945)
    post(api, 'tasks', {'owner': 'cmo', 'title': 'Fix the login page', 'body': 'x', 'number': 18945}, expected=422)
    post(api, 'tasks', {'owner': 'priya', 'title': 'Fix the help page', 'body': 'x', 'number': 7},
         token='priya-test', expected=403)
    plain = post(api, 'tasks', {'owner': 'cmo', 'title': 'Write the brief', 'body': 'x'})
    assert plain['number'] is None
    moved = edit_pipeline_task(api, plain, type=typ['id'])
    assert moved['number'] == 18946
    assert edit_pipeline_task(api, moved, type='General')['number'] == 18946
    assert get(api, 'tasks/%2318945')['task']['id'] == imported['id']
    get(api, 'tasks/18945', expected=404)           # bare digits may be a cut-short id
    assert call(api, 'hub_task_show', {'id': '#18945'})[1]['task']['id'] == imported['id']
    assert [t['id'] for t in get(api, 'tasks?number=18945')['tasks']] == [imported['id']]



def test_number_exhaustion_does_not_create_an_unreadable_ticket(api):
    typ = post(api, 'task-types', {'name': 'Dev ticket', 'numbered': True})['type']
    task = post(api, 'tasks', {'owner': 'cmo', 'title': 'Fix the last page', 'body': 'x',
                             'type': typ['id'], 'number': 999999999})
    post(api, 'tasks', {'owner': 'cmo', 'title': 'Fix another page', 'body': 'x',
                       'type': typ['id']}, expected=422)
    assert [t['id'] for t in get(api, 'tasks?type=' + typ['id'])['tasks']] == [task['id']]
    assert get(api, 'tasks/%23999999999')['task']['id'] == task['id']
    post(api, 'tasks/' + task['id'], {'version': task['version'], 'number': 2}, expected=422)


def test_tickets_on_a_numbered_type_stay_out_of_needs_you_unless_they_ask_the_person(api):
    tickets = post(api, 'task-types', {'name': 'Dev ticket', 'numbered': True, 'steps': [
        {'name': 'Backlog', 'status': 'open'}, {'name': "Can't replicate", 'status': 'declined'}]})['type']
    actions = post(api, 'task-types', {'name': 'Client action', 'steps': [{'name': 'To do', 'status': 'open'}]})['type']
    mine = [post(api, 'tasks', {'owner': 'priya', 'title': title, 'body': 'Please.', **extra}) for title, extra in (
        ('Approve the launch copy', {}), ('Call the client', {'type': actions['id']}),
        ('Fix the account page', {'type': tickets['id']}))]
    needs = lambda: {item['id']: item for item in get(api, 'needs-you', token='priya-test')['items']}  # noqa: E731
    assert set(needs()) == {mine[0]['id'], mine[1]['id']}
    assert get(api, 'needs-you?count=true', token='priya-test')['count'] == 2
    # A ticket comes in once it asks her something: the bot she filed it for asks back (hub task ask).
    asked = post(api, 'tasks', {'owner': 'ops', 'title': 'Fix the signup page', 'body': 'Please.',
                                'type': tickets['id']}, token='priya-test')
    post(api, 'tasks/' + asked['id'] + '/ask', {'text': 'Which browser was it?'}, token=bot_token(api, 'ops'))
    assert needs()[asked['id']]['kind'] == 'question'
    # A declined ticket is not hers to deal with today; a declined General task still is.
    declined = [post(api, 'tasks', {'owner': 'cmo', 'title': title, 'body': 'Please.', **extra}, token='priya-test')
                for title, extra in (('Fix the pricing page', {'type': tickets['id']}), ('Draft the newsletter', {}))]
    edit_pipeline_task(api, declined[0], step="Can't replicate")
    edit_pipeline_task(api, declined[1], status='declined')
    assert declined[1]['id'] in needs() and declined[0]['id'] not in needs()

def test_a_task_has_a_place_in_its_step_and_a_board_lists_in_that_order(api):
    typ = post(api, 'task-types', {'name': 'Dev ticket', 'steps': [
        {'name': 'Backlog', 'status': 'open'}, {'name': 'On deck', 'status': 'open'}]})['type']
    a, b = (post(api, 'tasks', {'owner': 'cmo', 'title': 'Fix the ' + page, 'body': 'x', 'type': typ['id']})
            for page in ('account page', 'signup page'))
    top = post(api, 'tasks', {'owner': 'cmo', 'title': 'Fix the help page', 'body': 'x', 'type': typ['id'], 'top': True})
    assert top['step_rank'] < a['step_rank'] < b['step_rank']
    b = edit_pipeline_task(api, b, step='On deck')
    a = edit_pipeline_task(api, a, step='On deck')
    assert a['step_rank'] > b['step_rank']                      # entering a step joins its end
    a = post(api, 'tasks/' + a['id'], {'version': a['version'], 'step_rank': b['step_rank'] - 1}, token='ben-test')
    board = get(api, 'tasks?type=Dev ticket&sort=step')['tasks']
    assert [t['id'] for t in board] == [top['id'], a['id'], b['id']]
    assert [t['id'] for t in get(api, 'tasks?type=' + typ['id'] + '&step=On deck&sort=step')['tasks']] == [a['id'], b['id']]
    post(api, 'tasks/' + a['id'], {'version': a['version'], 'step_rank': 0}, token='priya-test', expected=403)


def test_a_board_polls_only_what_changed_since_it_last_looked(api):
    old = post(api, 'tasks', {'owner': 'cmo', 'title': 'Write the brief', 'body': 'x'})
    changed = post(api, 'tasks', {'owner': 'cmo', 'title': 'Write the plan', 'body': 'x'})
    since = get(api, 'tasks/' + changed['id'])['task']['updated']
    post(api, 'tasks/' + changed['id'] + '/comments', {'text': 'Shorter, please.'})
    assert [t['id'] for t in get(api, 'tasks?updated_since=' + since.replace('Z', '%2B00:00'))['tasks']] == [changed['id']]
    get(api, 'tasks?updated_since=2026-01-01T00:00:00', expected=422)
    post(api, 'tasks/' + old['id'] + '/files', {'name': 'notes.md', 'text': 'The notes.'})
    assert {t['id'] for t in get(api, 'tasks?updated_since=' + since)['tasks']} == {old['id'], changed['id']}


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


def test_a_brief_list_leaves_out_what_a_board_does_not_show(api):
    post(api, 'tasks', {'owner': 'cmo', 'title': 'Write the brief', 'body': 'A long body.', 'acceptance_criteria': ['Short']})
    task = get(api, 'tasks?brief=true')['tasks'][0]
    assert task['title'] == 'Write the brief' and not {'body', 'acceptance_criteria', 'acceptance_json'} & set(task)
    from backend.tests.test_openapi_v2 import conforms
    from backend.openapi_v2 import generate
    document = generate()
    assert conforms(task, document['components']['schemas']['Task'], document) is None


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
    token = bot_token(api)
    post(api, 'tasks/' + parent['id'], {'version': parent['version'], 'status': 'done'}, token=token, expected=422)
    post(api, 'tasks/' + child['id'], {'version': child['version'], 'status': 'done'}, token=token, expected=422)
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


def test_ordinary_chat_keeps_subject_and_assistant_room_name(api):
    conversation = post(api, 'conversations', {'participants': ['ops'], 'kind': 'chat'})
    assert conversation['subject'] == 'Chat with ops'
    named = post(api, 'conversations', {'participants': ['ops'], 'kind': 'chat', 'subject': 'Feature planning'})
    assert named['id'] == conversation['id'] and named['subject'] == 'Feature planning'
    assert post(api, 'conversations', {'participants': ['ops'], 'kind': 'chat'})['subject'] == 'Feature planning'

    # A named personal room is never renamed by another create call.
    assert post(api, 'conversations', {'participants': ['ops'], 'kind': 'chat', 'subject': 'Another name'})['subject'] == 'Feature planning'
    shared = post(api, 'conversations', {'participants': ['cpo'], 'kind': 'chat'})
    assert shared['scope'] == 'shared'
    assert post(api, 'conversations', {'participants': ['cpo'], 'kind': 'chat', 'subject': 'Another name'})['subject'] == shared['subject']
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,state) VALUES('coo','Assistant','active')")
        c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES('coo',?, 'ana')", (encode({'name': 'coo', 'runtime': 'fake', 'status': 'active'}),))
    assistant = post(api, 'conversations', {'participants': ['coo'], 'kind': 'chat'})
    assert assistant['subject'] == 'Private ' + api.app.state.store.settings.assistant_name + ' control room'


def test_nested_task_mcp_and_cli_use_shared_routes(api, monkeypatch):
    from backend.tests.test_mcp import call
    from clients import hubcli, remotecli
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Coordinate the release', 'body': 'x'})
    error, created = call(api, 'hub_task_child_create', {'parent': parent['short_id'], 'owner': 'cpo',
        'title': 'Build a component', 'body': 'x'})
    assert not error and created['task']['requester'] == parent['requester']
    child = created['task']
    error, tree = call(api, 'hub_task_tree', {'id': parent['short_id']})
    assert not error and tree['result'][0]['id'] == child['id']
    error, moved = call(api, 'hub_task_relate', {'id': child['short_id'], 'task': parent['short_id'],
                                                 'kind': 'parent', 'remove': True})
    assert not error, moved
    assert related(moved['task'], 'parent') == []
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        def get(self, path, **query):
            return get(api, path)
        def post(self, path, body, key=None):
            response = api.post('/api/v2/' + path, json=body, headers=headers())
            assert response.status_code == 200, response.text
            return response.json()
    monkeypatch.setattr(remotecli, 'Client', Client)
    monkeypatch.setenv('HUB_API_URL', 'http://testserver')
    args = hubcli.parser().parse_args(['task', 'relate', child['short_id'], parent['short_id'], '--kind', 'parent'])
    assert related(remotecli.run(args), 'parent') == [parent['id']]
    args = hubcli.parser().parse_args(['task', 'tree', parent['short_id']])
    assert remotecli.run(args)[0]['id'] == child['id']
    args = hubcli.parser().parse_args(['task', 'child', parent['short_id'], '--owner', 'cmo', '--title', 'Build another component'])
    assert related(remotecli.run(args)['task'], 'parent') == [parent['id']]
    args = hubcli.parser().parse_args(['task', 'create', '--owner', 'cmo', '--title', 'Build a third component',
                                       '--body', 'x', '--parent', parent['short_id']])
    assert related(remotecli.run(args), 'parent') == [parent['id']]


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
    post(api, 'tasks/' + parent['id'], {'version': parent['version'], 'status': 'done'}, token=token, expected=422)
    moved = relate(api, child['short_id'], parent['id'], 'parent', token=token, remove=True)
    assert related(moved, 'parent') == []


def test_bot_created_children_keep_bot_requester_and_notify_and_close_for_the_manager(api):
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Manage delivery', 'body': 'x'})
    token = bot_token(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET request_id='human-request' WHERE id=?", (parent['id'],))
    child = post(api, 'tasks', {'owner': 'cpo', 'title': 'Implement delivery', 'body': 'x', 'relations': [{'task': parent['id'], 'kind': 'parent'}]}, token=token)
    assert child['requester'] == 'bot:ops' and child['request_id'] is None
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'bot:cpo', child['id'], status='done')
        notices = H._rows(c.execute("SELECT * FROM messages WHERE json_extract(refs_json,'$.task')=? AND body LIKE 'Finished:%'", (child['id'],)))
        assert notices and {m['to_actor'] for m in notices} == {'bot:ops'}
    child = get(api, 'tasks/' + child['id'], token=token)['task']
    assert post(api, 'tasks/' + child['id'], {'version': child['version'], 'close': True}, token=token)['status'] == 'closed'


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


def test_closed_middle_task_stops_open_rollup_and_reparenting_wakes_previous_parent(api):
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Coordinate nested work', 'body': 'x'})
    child = post(api, 'tasks', {'owner': 'cpo', 'title': 'Coordinate service work', 'body': 'x', 'relations': [{'task': parent['id'], 'kind': 'parent'}]})
    post(api, 'tasks', {'owner': 'cmo', 'title': 'Finish nested work', 'body': 'x', 'relations': [{'task': child['id'], 'kind': 'parent'}]})
    child = post(api, 'tasks/' + child['id'], {'version': child['version'], 'close': True})
    summary = get(api, 'tasks/' + parent['id'])['task']['children_summary']
    assert summary['total'] == 2 and summary['open'] == 0 and summary['direct_done'] == 1
    token = bot_token(api)
    parent = post(api, 'tasks/' + parent['id'], {'version': parent['version'], 'status': 'done'}, token=token)
    # Humans can mark Done even when a child is open.
    next_parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Track another piece', 'body': 'x'})
    open_child = post(api, 'tasks', {'owner': 'cpo', 'title': 'Finish another piece', 'body': 'x', 'relations': [{'task': next_parent['id'], 'kind': 'parent'}]})
    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM messages WHERE body='All subtasks done' AND to_actor='bot:ops'").fetchone()[0]
    relate(api, open_child['id'], next_parent['id'], 'parent', remove=True)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE body='All subtasks done' AND to_actor='bot:ops'").fetchone()[0] == before + 1
    another = post(api, 'tasks', {'owner': 'cpo', 'title': 'Finish remaining piece', 'body': 'x', 'relations': [{'task': next_parent['id'], 'kind': 'parent'}]})
    post(api, 'tasks/' + next_parent['id'], {'version': next_parent['version'], 'status': 'done'})
    # An omitted other task is refused by MCP.
    from backend.tests.test_mcp import call
    error, result = call(api, 'hub_task_relate', {'id': another['id'], 'kind': 'parent'})
    assert error and result['error'] == 'usage'
    assert api.delete('/api/v2/tasks/' + parent['id'] + '/links/' + 'x' * 201, headers=headers()).status_code == 422
    with api.app.state.store.transaction() as c:
        self_parent = H.task_create(c, 'bot:ops', 'Manage remaining work', 'x', 'bot:ops', lint=False)
        self_child = H.task_create(c, 'bot:ops', 'Cancel remaining work', 'x', 'bot:cpo', parent_id=self_parent['id'], lint=False)
        before = c.execute("SELECT count(*) FROM messages WHERE body='All subtasks done'").fetchone()[0]
        H.task_close(c, 'bot:ops', self_child['id'])
        assert c.execute("SELECT count(*) FROM messages WHERE body='All subtasks done'").fetchone()[0] == before



def test_tree_visibility_and_link_reads_are_batched(api, monkeypatch):
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Track a large plan', 'body': 'x'})
    with api.app.state.store.transaction() as c:
        for n in range(25):
            child = H.task_create(c, 'human:ana', 'Implement component ' + str(n), 'x', 'bot:cpo', parent_id=parent['id'], lint=False)
            H.task_link(c, 'human:ana', child['id'], 'https://github.com/example/service/pull/' + str(n + 1))
    auth = api.app.state.auth
    real = auth.bot_accesses
    calls = []
    def accesses(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)
    monkeypatch.setattr(auth, 'bot_accesses', accesses)
    tree = get(api, 'tasks/' + parent['id'] + '/tree', token='ben-test')
    assert len(tree) == 25 and all(node['pr_state'] == 'open' for node in tree)
    assert len(calls) <= 6
    with api.app.state.store.read() as c:
        queries = []
        c.set_trace_callback(queries.append)
        assert len(H.task_tree(c, parent['id'])) == 25
        assert len([q for q in queries if q.startswith('SELECT') or q.startswith('WITH')]) == 2


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


def test_child_owner_cannot_detach_or_move_away_from_uncontrolled_parent(api):
    token = bot_token(api, 'ops')
    parent = post(api, 'tasks', {'owner': 'cpo', 'title': 'Keep manager plan', 'body': 'x'})
    child = post(api, 'tasks', {'owner': 'ops', 'title': 'Implement manager plan', 'body': 'x', 'relations': [{'task': parent['id'], 'kind': 'parent'}]})
    destination = post(api, 'tasks', {'owner': 'ops', 'title': 'Track own plan', 'body': 'x'})
    relate(api, child['id'], parent['id'], 'parent', token=token, expected=403, remove=True)
    relate(api, child['id'], destination['id'], 'parent', token=token, expected=403)
    assert related(get(api, 'tasks/' + child['id'])['task'], 'parent') == [parent['id']]
    # The parent requester can detach; a mover can move the whole subtree.
    relate(api, child['id'], parent['id'], 'parent', remove=True)
    assert related(relate(api, child['id'], destination['id'], 'parent', token='ben-test'), 'parent') == [destination['id']]


def test_person_filing_under_bot_request_is_the_subtask_requester(api):
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, 'bot:ops', 'Coordinate bot request', 'x', 'bot:cpo', lint=False)
    child = post(api, 'tasks', {'owner': 'cpo', 'title': 'Follow up on bot request', 'body': 'x', 'relations': [{'task': parent['id'], 'kind': 'parent'}]})
    assert child['requester'] == 'human:ana'
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'bot:cpo', child['id'], status='done')
        notices = c.execute("SELECT to_actor FROM messages WHERE json_extract(refs_json,'$.task')=? AND body LIKE 'Finished:%'",
                            (child['id'],)).fetchall()
        assert notices and {r[0] for r in notices} == {'human:ana'}


def test_cancelled_parent_does_not_wake_when_last_child_finishes(api):
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Cancel a plan', 'body': 'x'})
    child = post(api, 'tasks', {'owner': 'cpo', 'title': 'Finish cancelled work', 'body': 'x', 'relations': [{'task': parent['id'], 'kind': 'parent'}]})
    post(api, 'tasks/' + parent['id'], {'version': parent['version'], 'close': True})
    post(api, 'tasks/' + child['id'], {'version': child['version'], 'status': 'done'})
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM messages WHERE json_extract(refs_json,'$.task')=? AND body LIKE 'All subtasks done%'",
                             (parent['id'],)).fetchone()


def test_tree_visibility_query_only_reads_descendant_ids(api, monkeypatch):
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Read one task tree', 'body': 'x'})
    child = post(api, 'tasks', {'owner': 'cpo', 'title': 'Read one child', 'body': 'x', 'relations': [{'task': parent['id'], 'kind': 'parent'}]})
    unrelated = post(api, 'tasks', {'owner': 'cpo', 'title': 'Keep other task outside', 'body': 'x'})
    store = api.app.state.store
    connect = store.connect
    queries = []
    def traced():
        c = connect()
        c.set_trace_callback(queries.append)
        return c
    monkeypatch.setattr(store, 'connect', traced)
    assert get(api, 'tasks/' + parent['id'] + '/tree')[0]['id'] == child['id']
    visibility = [q for q in queries if q.startswith('SELECT id FROM tasks WHERE')]
    assert visibility and all('id IN (' in q and child['id'] in q and unrelated['id'] not in q for q in visibility)


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
    todo = get(api, 'tasks/' + todo['id'])['task']
    post(api, 'tasks/' + todo['id'], {'version': todo['version'], 'status': 'doing'}, token=token, expected=403)

    post(api, 'task-types/' + typ['id'], {'bots': 'anyone'}, expected=422)
    assert post(api, 'task-types/' + typ['id'], {'bots': 'parties'})['type']['bots'] is None
    assert get(api, 'tasks/' + ticket['id'], token=token)['task']['id'] == ticket['id']
    assert ticket['id'] in listed()
    post(api, 'tasks/' + ticket['id'], {'version': moved['version'], 'status': 'doing'}, token=token, expected=403)
