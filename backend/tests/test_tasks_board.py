"""The tasks board: done and closed, two lanes, labels, links, relations, sub-tasks, comments that wake or wait,
movers, and plain-English titles."""

import json

import pytest

from backend import hubdb as H
from backend import task_relations as TR
from backend.tests.live import live  # noqa: F401
from clients import hubcli
from backend.tests.helpers.tasks_board import api, bot_token, get, headers, pipeline, post, relate


def related(task, kind, direction=None):
    """The ids a task view lists under `kind` (in one `direction` if given)."""
    return [r["id"] for r in (task.get("relations") or {}).get(kind, []) if direction in (None, r["direction"])]


# ----------------------------------------------------------------------------- rank


def test_done_and_closed_tasks_stay_as_their_requester_left_them(api):
    """Done records the stage's work finished; a bot cannot reopen what a person closed; a person reopens with a why."""
    from backend.tests.test_task_moves_routes import as_bot

    bot = as_bot(api, "ops")
    mine = post(api, "tasks", {"owner": "ana", "title": "Decide the content hold", "body": "Keep or change it?"})
    done = post(api, "tasks/" + mine["id"] + "/done", {"note": "Keep the hold."})
    assert done["status"] == "done" and done["done_at"] and done["closed_at"] is None
    task = post(api, "tasks", {"owner": "ops", "title": "Clean up the inbox", "body": "Archive the old threads."})
    version = get(api, "tasks/" + task["id"])["task"]["version"]
    closed = post(api, "tasks/" + task["id"] + "/move", {"to": "closed", "why": "Not needed now", "version": version})
    assert closed["status"] == "closed" and closed["closed_by_id"] == "human:ana"
    late = api.post(
        "/api/v2/tasks/" + task["id"] + "/move",
        headers=headers(bot),
        json={"to": "reopen", "why": "Archived 40, final.", "version": closed["version"]},
    )
    assert late.status_code == 409 and late.json()["error"] == "task_needs_person", late.text
    # What the bot has to add still lands, as a comment, and the person can still reopen it.
    post(api, f"tasks/{task['id']}/comments", {"text": "Archived 40, final."}, token=bot)
    reopened = post(api, "tasks/" + task["id"] + "/move", {"to": "reopen", "why": "One more pass"})
    assert reopened["status"] == "open" and reopened["closed_at"] is None


# ----------------------------------------------------------------------------- lanes


# ----------------------------------------------------------------------------- labels
# ----------------------------------------------------------------------------- blocked by
# ----------------------------------------------------------------------------- links
# ----------------------------------------------------------------------------- comments
def jobs_for(api, bot):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM jobs WHERE bot_id=?", (bot,)).fetchone()[0]


def _a_comment_on_a_task_a_bot_filed_wakes_it_only_when_addressed_to_it(api, token):
    task = post(api, "tasks", {"owner": "ben", "title": "Fix the sign-in check", "body": "x"}, token=token)
    path = f"tasks/{task['id']}/comments"
    queued = jobs_for(api, "ops")
    for teammate in ("ana-test", "priya-test"):  # a mover, then a teammate: neither owns it
        update = post(api, path, {"text": "Testing it on staging for a day."}, token=teammate)
        assert update["woke"] is False and jobs_for(api, "ops") == queued
    assert any(m["body"] == "Testing it on staging for a day." for m in get(api, "tasks/" + task["id"])["comments"])
    assert post(api, path, {"text": "@ops which app is still open?"})["woke"] is True
    # The @mention made ops a follower: the owner's plain reply now reaches it like any post, Steer by default.
    reply = post(api, path, {"text": "I don't know why this task is assigned to me."}, token="ben-test")
    assert reply["woke"] is True and reply["comment"]["recipient_id"] == "bot:ops"
    queued = jobs_for(api, "ops")
    mine = f"{path}/{reply['comment']['id']}"
    assert post(api, mine, {"text": "Why is this mine?"}, token="ben-test")["woke"] is False
    assert post(api, mine + "/delete", {}, token="ben-test")["woke"] is False
    assert jobs_for(api, "ops") == queued, "an edit or delete wakes nobody"


def _a_person_mentioned_in_a_comment_is_reached_and_follows_the_task(api, token):
    task = post(api, "tasks", {"owner": "ana", "title": "Fix the sign-in check", "body": "x"}, token=token)
    queued = jobs_for(api, "ops")
    said = post(api, f"tasks/{task['id']}/comments", {"text": "@ben can you test it on staging?"}, token="priya-test")
    assert said["comment"]["kind"] == "say" and mention_reason(api, said["comment"]["id"], "human:ben") == "mention"
    assert jobs_for(api, "ops") == queued, "the bot that filed it is not woken"
    assert mention_reason(api, said["comment"]["id"], "human:priya") is None, "posting reaches not the author"
    with api.app.state.store.read() as c:
        assert (
            c.execute(
                "SELECT following FROM thread_followers WHERE thread_id=? AND actor='human:ben'",
                (H.task(c, task["id"])["conversation_id"],),
            ).fetchone()[0]
            == 1
        )


def mention_reason(api, message_id, actor):
    """Why a message reached `actor` (its delivery's reason), or None when it did not."""
    with api.app.state.store.read() as c:
        row = c.execute(
            "SELECT reason FROM message_deliveries WHERE message_id=? AND recipient=?", (message_id, actor)
        ).fetchone()
    return row[0] if row else None


def _a_mention_on_a_task_with_no_bot_on_it_is_one_comment(api):
    task = post(api, "tasks", {"owner": "ben", "title": "Book the venue", "body": "x"})
    said = post(api, f"tasks/{task['id']}/comments", {"text": "@ben can you book it by Friday?"})
    # A mention is a comment that reaches whom it names, not a question (question tasks are their own thing).
    assert said["comment"]["kind"] == "say" and mention_reason(api, said["comment"]["id"], "human:ben") == "mention"
    assert [m["body"] for m in get(api, "tasks/" + task["id"])["comments"]] == ["@ben can you book it by Friday?"]


def _mentioning_a_person_on_a_task_a_bot_owns_still_wakes_the_bot(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Send the weekly numbers", "body": "x"})
    queued = jobs_for(api, "ops")
    said = post(api, f"tasks/{task['id']}/comments", {"text": "@ben can you send ops the numbers?"})
    assert said["woke"] is True and said["comment"]["kind"] != "ask"
    assert jobs_for(api, "ops") == queued + 1
    assert mention_reason(api, said["comment"]["id"], "human:ben") == "mention"


def _a_comment_on_a_task_with_no_bot_on_it_is_not_quiet(api):
    task = post(api, "tasks", {"owner": "ben", "title": "Book the caterer", "body": "x"})
    said = post(api, f"tasks/{task['id']}/comments", {"text": "Any update?"})
    assert said["woke"] is False and said["delivered"] is True and not said["comment"].get("refs", {}).get("quiet")


def _the_author_edits_a_comment_and_nobody_wakes(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Draft the newsletter", "body": "x"})
    said = post(api, f"tasks/{task['id']}/comments", {"text": "Use the Agust numbers."})
    assert said["woke"] and said["comment"]["edited_at"] is None
    before, queued = get(api, "tasks/" + task["id"]), jobs_for(api, "ops")
    edited = post(api, f"tasks/{task['id']}/comments/{said['comment']['id']}", {"text": "Use the August numbers."})
    assert edited["woke"] is False and edited["comment"]["edited_at"]
    after = get(api, "tasks/" + task["id"])
    assert [(m["body"], bool(m["edited_at"])) for m in after["comments"]] == [("Use the August numbers.", True)]
    assert len(after["messages"]) == len(before["messages"]) and jobs_for(api, "ops") == queued, (
        "nothing sent, nobody woken"
    )
    assert after["task"]["updated_at"] > before["task"]["updated_at"]
    assert any(e["field"] == "comment" and e["new"] == said["comment"]["id"] for e in after["events"])
    with api.app.state.store.read() as c:
        audit = c.execute(
            "SELECT detail FROM events WHERE action='message.edited' AND target=?", (said["comment"]["id"],)
        ).fetchone()[0]
    assert "Agust" not in audit, "the audit keeps metadata without withdrawn words"


def _only_its_author_edits_or_deletes_a_comment_signed_in_as_themselves(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Draft the press note", "body": "x"})
    said = post(api, f"tasks/{task['id']}/comments", {"text": "Use the September numbers."}, token="ben-test")
    path = f"tasks/{task['id']}/comments/{said['comment']['id']}"
    for token in ("ana-test", "priya-test", "ben-via-botops"):  # Tico's owner, a teammate, BotOps acting for Ben
        post(api, path, {"text": "Use the October numbers."}, token=token, expected=403)
        post(api, path + "/delete", {}, token=token, expected=403)
    assert [m["body"] for m in get(api, "tasks/" + task["id"])["comments"]] == ["Use the September numbers."]
    assert post(api, path, {"text": "Use the October numbers."}, token="ben-test")["comment"]["edited_at"]


def _a_deleted_comment_leaves_the_task_and_never_reaches_the_bot(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Draft the launch email", "body": "x"})
    said = post(api, f"tasks/{task['id']}/comments", {"text": "September, and call me first."})["comment"]
    before = get(api, "tasks/" + task["id"])
    gone = post(api, f"tasks/{task['id']}/comments/{said['id']}/delete", {})
    assert gone["comment"]["deleted_at"] and gone["woke"] is False
    after = get(api, "tasks/" + task["id"])
    assert said["id"] not in {m["id"] for m in gone["comments"] + after["comments"] + after["messages"]}
    assert after["task"]["updated_at"] > before["task"]["updated_at"]
    get(api, "messages/" + said["id"], expected=404)
    post(api, f"tasks/{task['id']}/comments/{said['id']}", {"text": "October"}, expected=404)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (said["id"],)).fetchone()[0] == "canceled"
        assert (
            c.execute("SELECT expires_at FROM task_delegations WHERE message_id=?", (said["id"],)).fetchone()[0]
            <= H.now()
        )


def test_comments_reach_whom_they_name_and_edits_or_deletes_wake_nobody(api, subtests):
    """Comment routing, edits and deletes, one hub: each check files its own task. The checks that need no runner run
    first, as they did on a fresh hub; then ops is leased once and the two checks on a task ops filed share that token."""
    for check in (
        _a_mention_on_a_task_with_no_bot_on_it_is_one_comment,
        _mentioning_a_person_on_a_task_a_bot_owns_still_wakes_the_bot,
        _a_comment_on_a_task_with_no_bot_on_it_is_not_quiet,
        _the_author_edits_a_comment_and_nobody_wakes,
        _only_its_author_edits_or_deletes_a_comment_signed_in_as_themselves,
        _a_deleted_comment_leaves_the_task_and_never_reaches_the_bot,
    ):
        with subtests.test(check.__name__):
            check(api)
    token = bot_token(api, "ops")
    for check in (
        _a_comment_on_a_task_a_bot_filed_wakes_it_only_when_addressed_to_it,
        _a_person_mentioned_in_a_comment_is_reached_and_follows_the_task,
    ):
        with subtests.test(check.__name__):
            check(api, token)


# ----------------------------------------------------------------------------- movers
def test_only_a_mover_changes_lane_or_labels_on_someone_elses_task(api):
    task = post(api, "tasks", {"owner": "cmo", "title": "Draft the newsletter", "body": "x"})
    post(
        api,
        "tasks/" + task["id"],
        {"version": task["version"], "labels": ["newsletter"]},
        token="priya-test",
        expected=403,
    )
    post(api, "tasks/" + task["id"] + "/done", {"version": task["version"]}, token="priya-test", expected=403)
    ok = post(api, "tasks/" + task["id"], {"version": task["version"], "labels": ["newsletter"]}, token="ben-test")
    assert ok["labels"] == ["newsletter"]
    assert get(api, "tasks/" + task["id"], token="priya-test")["mover"] is False
    assert get(api, "tasks/" + task["id"], token="ben-test")["mover"] is True
    # the owner of a task still marks their own done, mover or not
    mine = post(api, "tasks", {"owner": "priya", "title": "Call the lead", "body": "Ask whether they want the demo."})
    done = post(api, "tasks/" + mine["id"] + "/done", {"version": mine["version"]}, token="priya-test")
    assert done["status"] == "done"
    assert api.get("/api/me", headers=headers("priya-test")).json()["mover"] is False
    assert api.get("/api/me", headers=headers("ben-test")).json()["mover"] is True


# ----------------------------------------------------------------------------- titles
# ----------------------------------------------------------------------------- preferences


def test_related_tasks_list_each_other_hide_private_ones_and_survive_a_restore(api):
    api_half = post(api, "tasks", {"owner": "ops", "title": "Add the refund endpoint", "body": "x"})
    ui_half = post(api, "tasks", {"owner": "ben", "title": "Show the refund button", "body": "x"})
    titles = lambda tid, token="ana-test": [
        t["title"] for t in get(api, "tasks/" + tid, token=token)["task"]["relations"].get("related", [])
    ]
    assert related(relate(api, api_half["id"], ui_half["id"], token="ben-test"), "related", "both") == [ui_half["id"]]
    relate(api, ui_half["id"], api_half["id"])  # the same pair from the other end changes nothing
    assert titles(ui_half["id"]) == ["Add the refund endpoint"]
    with api.app.state.store.read() as c:  # stored once, lower id first
        assert c.execute("SELECT count(*) FROM task_relations WHERE kind='related'").fetchone()[0] == 1
    relate(api, api_half["id"], api_half["id"], expected=422)
    # A private task's title never reaches someone who is not one of its two parties.
    secret = post(
        api,
        "tasks",
        {"owner": "priya", "title": "Secret salary review", "body": "x", "private": True},
        token="ben-test",
    )
    relate(api, secret["id"], api_half["id"], token="ben-test")
    assert titles(api_half["id"]) == ["Show the refund button"]
    assert len(titles(api_half["id"], "ben-test")) == 2
    assert secret["id"] not in str(get(api, "tasks?limit=500")["tasks"])
    # Deleting a task takes its relations into the trash, and restoring it puts them back.
    post(api, "tasks/" + ui_half["id"] + "/delete", {})
    assert titles(api_half["id"], "ben-test") == ["Secret salary review"]
    post(api, "tasks/" + ui_half["id"] + "/restore", {})
    assert len(titles(api_half["id"], "ben-test")) == 2
    assert related(relate(api, api_half["id"], ui_half["id"], token="ben-test", remove=True), "related") == [
        secret["id"]
    ]
    assert "related" not in get(api, "tasks/" + ui_half["id"])["task"]["relations"]
    # Directed kinds read from each end; a pair is not both ways round.
    dup = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Add the refund endpoint again",
            "body": "x",
            "relations": [{"task": api_half["id"], "kind": "duplicate_of"}],
        },
    )
    assert related(dup, "duplicate_of", "out") == [api_half["id"]]
    assert related(get(api, "tasks/" + api_half["id"])["task"], "duplicate_of", "in") == [dup["id"]]
    relate(api, api_half["id"], dup["id"], "duplicate_of", expected=422)
    follow = relate(api, ui_half["id"], api_half["id"], "follow_up")
    assert related(follow, "follow_up", "out") == [api_half["id"]]
    with api.app.state.store.read() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        fields = {
            r[0]
            for r in c.execute(
                "SELECT kind AS field FROM history_events WHERE subject_type='task' AND subject_id=?", (api_half["id"],)
            )
        }
        assert {"related", "duplicated_by", "follow_ups"} <= fields


# ----------------------------------------------------------------------------- tags
def test_tag_owner_movers_and_stale_versions(api):
    tag = post(api, "tags", {"key": "release-day", "owner": "priya", "markdown": "- [ ] Smoke checks"})["tag"]
    assert get(api, "tags/" + tag["id"], token="priya-test")["editable"] is True
    changed = post(api, "tags/" + tag["id"], {"version": 1, "markdown": "- [x] Smoke checks"}, token="priya-test")[
        "tag"
    ]
    assert changed["version"] == 2
    post(api, "tags/" + tag["id"], {"version": 1, "metadata": {"date": "2026-10-02"}}, token="priya-test", expected=409)
    assert get(api, "tags/" + tag["id"])["tag"]["metadata"] == {}
    post(api, "tags/" + tag["id"], {"version": 2, "owner": "ana"}, token="ben-test")
    post(api, "tags/" + tag["id"], {"version": 3, "markdown": "Clobber"}, token="priya-test", expected=403)
    post(api, "tags", {"key": "not-mine", "owner": "ana"}, token="priya-test", expected=403)
    mine = post(api, "tags", {"key": "my-checklist"}, token="priya-test")["tag"]
    assert mine["owner"] == "human:priya"
    task = post(api, "tasks", {"owner": "priya", "title": "Review the checklist", "body": "Read it."})
    post(
        api,
        "tasks/" + task["id"],
        {"version": task["version"], "labels": [mine["key"]]},
        token="priya-test",
        expected=403,
    )


# ----------------------------------------------------------------------------- task pipelines


def edit_pipeline_task(api, task, **fields):
    return post(api, 'tasks/' + task['id'], {'version': task['version'], **fields})


def test_three_level_tree_requester_rules_and_sub_task_counts(api):
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Build the feature', 'body': 'x'})
    child = post(
        api,
        'tasks',
        {'owner': 'cpo', 'title': 'Build the service', 'body': 'x', 'parent': parent['id']},
        token='ben-test',
    )
    leaf = post(api, 'tasks', {'owner': 'cmo', 'title': 'Build the page', 'body': 'x', 'parent': child['id']})
    assert child['requester_id'] == 'human:ben' and leaf['requester_id'] == parent['requester_id'] == 'human:ana'
    post(api, 'tasks/' + leaf['id'] + '/links', {'url': 'https://github.com/example/service/pull/1'})
    summary = get(api, 'tasks/' + parent['id'])['task']['children_summary']
    assert summary == {
        'total': 2,
        'open': 2,
        'done': 0,
        'prs_total': 1,
        'prs_merged': 0,
        'direct_total': 1,
        'direct_done': 0,
    }
    tree = get(api, 'tasks/' + parent['id'] + '/tree')
    assert tree[0]['id'] == child['id'] and tree[0]['children'][0]['pr_state'] == 'open'
    assert [f['id'] for f in get(api, 'tasks/' + parent['id'])['task']['filed']] == [child['id']]
    closed = post(api, 'tasks/' + leaf['id'] + '/move', {'to': 'closed', 'why': 'Not needed'})
    assert closed['status'] == 'closed'
    done = post(api, 'tasks/' + child['id'] + '/done', {'note': 'Built.'}, token='ben-test')
    assert done['status'] == 'done'
    assert get(api, 'tasks/' + parent['id'])['task']['children_summary']['done'] == 2
    # A person can still close an open parent.
    assert (
        post(api, 'tasks/' + parent['id'] + '/move', {'to': 'closed', 'why': 'Shipped elsewhere'})['status'] == 'closed'
    )


def test_delegate_cannot_turn_temporary_access_into_ancestor_access(api):
    token = bot_token(api)
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Own the plan', 'body': 'x'})
    unrelated = post(api, 'tasks', {'owner': 'cpo', 'title': 'Review other work', 'body': 'x'})
    with api.app.state.store.transaction() as c:
        c.execute(
            'INSERT INTO task_delegations(task_id,delegate,requested_by,message_id,expires_at) VALUES(?,?,?,?,?)',
            (
                unrelated['id'],
                'bot:ops',
                'human:ana',
                c.execute('SELECT id FROM messages LIMIT 1').fetchone()[0],
                H.shift(H.now(), days=1),
            ),
        )
    get(api, 'tasks/' + unrelated['id'], token=token)
    relate(api, unrelated['id'], parent['id'], 'parent', token=token, expected=403)
    # Direct ownership of the child still does not permit hanging it under a parent it merely reads.
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'human:ana', unrelated['id'], owner='bot:ops', mover=True)
        alien_parent = H.task_create(c, 'human:ana', 'Coordinate unrelated work', 'x', 'bot:cpo', lint=False)
        with pytest.raises(H.Refused):
            TR.relate(c, 'bot:ops', unrelated['id'], alien_parent['id'], 'parent')


def test_hidden_descendants_stay_hidden_in_details_lists_trees_sql_and_counts(api):
    from backend.tests.helpers.api import restrict

    token = bot_token(api)
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Coordinate visible work', 'body': 'x'})
    child = post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': 'Keep private work private',
            'body': 'x',
            'relations': [{'task': parent['id'], 'kind': 'parent'}],
        },
    )
    with api.app.state.store.transaction() as c:
        restrict(c, 'cpo', people=['ana'])
    get(api, 'tasks/' + child['id'], token=token, expected=404)
    assert get(api, 'tasks/' + parent['id'] + '/tree', token=token) == []
    detail = get(api, 'tasks/' + parent['id'], token=token)['task']
    assert detail['children_summary']['total'] == detail['parts']['total'] == 0
    assert child['id'] not in {t['id'] for t in get(api, 'tasks', token=token)['tasks']}
    assert [child['id']] not in post(api, 'sql', {'sql': 'SELECT id FROM tasks'}, token=token)['rows']


def test_a_list_carries_a_links_set_columns_not_its_record_or_write_time(api):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Trim list links', 'body': 'x'})
    with api.app.state.store.transaction() as c:
        tree = H.task_link(c, 'human:ana', task['id'], 'https://example.com/tree', kind='worktree')
        c.execute(
            "UPDATE task_links SET repo='acme/app', path='tasks/t1/acme__app', detail=? WHERE id=?",
            (json.dumps({'size_mb': 40, 'activity_at': '2026-10-24T00:00:00Z'}), tree['id']),
        )
    listed = {l['kind']: l for l in next(t for t in get(api, 'tasks')['tasks'] if t['id'] == task['id'])['links']}
    assert listed['worktree']['repo'] == 'acme/app' and listed['worktree']['path'] == 'tasks/t1/acme__app'
    assert not {'detail', 'updated_at', 'pr_sha', 'number', 'checks'} & set(listed['worktree'])
    # The task's own read keeps the whole record.
    full = {l['kind']: l for l in get(api, 'tasks/' + task['id'])['task']['links']}
    assert json.loads(full['worktree']['detail'])['size_mb'] == 40 and 'updated_at' in full['worktree']


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
    # A session link follows the same rights: any person who sees the task, a bot only with task rights.
    post(api, path, {'url': 'codex resume abc123', 'kind': 'session'}, token=token, expected=403)
    session = post(api, path, {'url': 'codex resume abc123', 'kind': 'session'}, token='priya-test')['links'][-1]
    assert (session['kind'], session['url']) == ('session', 'session:codex resume abc123')
    assert json.loads(session['detail']) == {'primary': False, 'harness': 'codex', 'thread_id': 'abc123'}
    with api.app.state.store.read() as c:  # the thread is stored once, in the url
        assert json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (session['id'],)).fetchone()[0]) == {
            'primary': False,
            'harness': 'codex',
        }
    # The owning bot and an ancestor party retain their task rights.
    parent = post(api, 'tasks', {'owner': 'ops', 'title': 'Track linked work', 'body': 'x'})
    child = post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': 'Build linked work',
            'body': 'x',
            'relations': [{'task': parent['id'], 'kind': 'parent'}],
        },
    )
    post(api, 'tasks/' + parent['id'] + '/links', {'url': 'https://example.com/plan'}, token=token)
    linked = post(api, 'tasks/' + child['id'] + '/links', {'url': 'https://example.com/component'}, token=token)[
        'links'
    ][0]
    post(api, 'tasks/' + child['id'] + '/links', {'remove': linked['id']}, token=token)
