"""A refusal loop holds its task/chat; unrelated work of the same bot keeps running."""
import pytest

from backend import hubdb as H
from backend.tests.test_api import api, claim, get, headers, post, ready, settled  # noqa: F401 (fixture)
from backend.tests.test_execution_review import job_state
from backend.tests.test_held_runs import botops
from backend.tests.test_task_lanes import new_task, task_bot


def started(api, machine):
    attempt = claim(api, machine)
    assert attempt is not None
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["lane"]}, machine["token"])
    return attempt


def refusals(api, attempt, task, count=H.QUARANTINE_AT + 1):
    for _ in range(count):
        row = get(api, "tasks/" + task["id"])["task"]
        result = post(api, "tasks/" + task["id"], {"version": row["version"], "number": 993},
                      attempt["token"], expected=403)
        assert result["error"]["code"] == "identity"


def holds(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM quarantine_holds WHERE released_at IS NULL")]


@pytest.fixture
def external_work(api):
    from backend.tests.test_agents import credential, hermes_bot

    hermes_bot(api)
    token = credential(api)["token"]
    task = new_task(api, "Check the export", owner="scout")
    other = new_task(api, "Check the archive", owner="scout")
    return token, task, other


def hold_external(api, task):
    from backend import task_quarantine as Q

    with api.app.state.store.transaction() as c:
        Q.hold(c, H.actor_id(task["owner_id"]), "task:" + task["id"], "Repeated refusals", {"rule": "identity"})


@pytest.mark.parametrize("mutation", ["request", "consume", "supersede"])
def test_external_approvals_fence_their_task_and_the_stored_task_of_a_replaced_approval(api, external_work, mutation):
    token, task, other = external_work
    approvals = [post(api, "approvals", {"kind": "merge", "task_id": row["id"],
                                       "payload": {"repo": "acme/widgets", "pr": number}}, token)
                 for number, row in enumerate((task, other), 41)]
    if mutation == "consume":
        for approval in approvals:
            post(api, "approvals/" + approval["id"], {"decision": "approved"})
    hold_external(api, task)

    def write(row, approval, expected=200):
        if mutation == "consume":
            return post(api, f"approvals/{approval['id']}/consume", {"payload_hash": approval["payload_hash"]},
                        token, expected=expected)
        body = {"kind": "merge", "task_id": row["id"],
                "payload": {"repo": "acme/widgets", "pr": approval["payload"]["pr"] + 10}}
        if mutation == "supersede":
            # An unheld request must not withdraw an approval whose own stored task is held.
            body.update(task_id=other["id"], supersedes_approval=approval["id"])
        return post(api, "approvals", body, token, expected=expected)

    with api.app.state.store.read() as c:
        before = [dict(r) for r in c.execute("SELECT * FROM approvals ORDER BY id")]
        messages = c.execute("SELECT count(*) FROM messages").fetchone()[0]
    assert write(task, approvals[0], expected=403)["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert [dict(r) for r in c.execute("SELECT * FROM approvals ORDER BY id")] == before
        assert c.execute("SELECT count(*) FROM messages").fetchone()[0] == messages
        assert H.bot(c, H.actor_id(task["owner_id"]))["state"] == "active"
    write(other, approvals[1])
    post(api, "bots/scout/quarantine/clear", {})
    write(task, approvals[0])


@pytest.mark.parametrize("route", ["messages", "conversations", "threads"])
def test_external_message_refs_cannot_mask_the_native_threads_held_task(api, external_work, route):
    token, task, other = external_work
    hold_external(api, task)

    def write(row, expected=200):
        body = {"text": "Checked the report.", "refs": {"task": other["id"]}}
        if route == "messages":
            path = "messages"
            body.update(to="ana", conversation_id=row["conversation_id"])
        else:
            path = f"{route}/{row['conversation_id']}/messages"
        return post(api, path, body, token, expected=expected)

    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM messages").fetchone()[0]
    assert write(task, expected=403)["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages").fetchone()[0] == before
    write(other)
    # People may still talk on the held task.
    post(api, f"conversations/{task['conversation_id']}/messages", {"text": "I will review the hold."})
    post(api, "bots/scout/quarantine/clear", {})
    write(task)


@pytest.mark.parametrize("mutation", ["ack", "answer", "edit", "delete", "react", "unreact"])
def test_external_message_mutations_check_the_stored_message_and_native_task(api, external_work, mutation):
    token, task, other = external_work
    messages = []
    for row in (task, other):
        sender, target = ("human:ana", task["owner_id"]) if mutation in ("ack", "answer") else (task["owner_id"], "human:ana")
        with api.app.state.store.transaction() as c:
            messages.append(H.say(c, sender, target, "Which format?" if mutation == "answer" else "Checking the report.",
                                  conversation_id=row["conversation_id"], kind="ask" if mutation == "answer" else "say",
                                  refs={"task": other["id"]}))
        if mutation == "unreact":
            assert api.put(f"/api/v2/messages/{messages[-1]['id']}/reactions/check", headers=headers(token)).status_code == 200
    hold_external(api, task)

    def write(msg):
        path, body, method = f"/api/v2/messages/{msg['id']}", {}, "post"
        if mutation in ("ack", "answer"):
            path += "/" + mutation
            body = {"text": "Markdown."} if mutation == "answer" else {}
        elif mutation in ("react", "unreact"):
            path += "/reactions/check"
            method = "put" if mutation == "react" else "delete"
        else:
            method = "patch" if mutation == "edit" else "delete"
            body = {"text": "Checked the report."} if mutation == "edit" else {}
        return api.request(method, path, json=body, headers=headers(token))

    with api.app.state.store.read() as c:
        before = dict(c.execute("SELECT * FROM messages WHERE id=?", (messages[0]["id"],)).fetchone())
        reactions = [tuple(r) for r in c.execute("SELECT * FROM message_reactions ORDER BY id")]
    refused = write(messages[0])
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert dict(c.execute("SELECT * FROM messages WHERE id=?", (messages[0]["id"],)).fetchone()) == before
        assert [tuple(r) for r in c.execute("SELECT * FROM message_reactions ORDER BY id")] == reactions
    result = write(messages[1])
    assert result.status_code == 200, result.text
    post(api, "bots/scout/quarantine/clear", {})
    result = write(messages[0])
    assert result.status_code == 200, result.text


@pytest.mark.parametrize("mutation", ["create", "subthread", "rename", "read", "read-all", "follow", "unfollow", "pin", "unpin"])
def test_external_thread_mutations_check_the_stored_thread_task(api, external_work, mutation):
    token, task, other = external_work
    # A bot may manage a thread it started, including one associated with a task.
    rooms = [post(api, "threads", {"title": row["title"], "task_id": row["id"], "members": ["ana"]}, token)
             for row in (task, other)]
    task, other = ({**row, "conversation_id": room["id"]} for row, room in zip((task, other), rooms))
    comments = [post(api, f"threads/{row['conversation_id']}/messages", {"text": "Review this report."})
                for row in (task, other)]
    for row in (task, other):
        if mutation == "unpin":
            assert api.put(f"/api/v2/threads/{row['conversation_id']}/pin", headers=headers(token)).status_code == 200
    hold_external(api, task)

    def write(row, comment):
        path, body, method = f"/api/v2/threads/{row['conversation_id']}", {}, "post"
        if mutation in ("create", "subthread"):
            path = "/api/v2/threads"
            body = {"audience": "task", "task_id": row["id"]} if mutation == "create" else {"parent_message_id": comment["id"]}
        elif mutation == "rename":
            method, body = "patch", {"title": "Reviewed report"}
        else:
            path += "/" + mutation
            if mutation == "read":
                body = {"seq": 100}
            elif mutation == "follow":
                method = "put"
            elif mutation in ("unfollow", "unpin"):
                method, path = "delete", path.replace("/unfollow", "/follow").replace("/unpin", "/pin")
            elif mutation == "pin":
                method = "put"
        return api.request(method, path, json=body, headers=headers(token))

    with api.app.state.store.read() as c:
        before = {table: [tuple(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY id")]
                  for table in ("conversations", "thread_followers", "thread_pins")}
    refused = write(task, comments[0])
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        for table, rows in before.items():
            assert [tuple(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY id")] == rows
    result = write(other, comments[1])
    assert result.status_code == 200, result.text
    post(api, "bots/scout/quarantine/clear", {})
    result = write(task, comments[0])
    assert result.status_code == 200, result.text


def test_external_status_fences_the_task_it_keeps_without_blocking_a_switch_to_other_work(api, external_work):
    token, task, other = external_work
    path = "bots/scout/status"
    post(api, path, {"state": "running", "focus": "Checking the export", "task_id": task["id"]}, token)
    hold_external(api, task)
    with api.app.state.store.read() as c:
        before = dict(c.execute("SELECT * FROM bot_status WHERE bot=?", (H.actor_id(task["owner_id"]),)).fetchone())
    for body in ({"focus": "Still trying"}, {"focus": "Still trying", "task_id": task["id"]}):
        assert post(api, path, body, token, expected=403)["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert dict(c.execute("SELECT * FROM bot_status WHERE bot=?", (H.actor_id(task["owner_id"]),)).fetchone()) == before
    post(api, path, {"state": "running", "task_id": other["id"]}, token)
    post(api, "bots/scout/quarantine/clear", {})
    post(api, path, {"state": "running", "task_id": task["id"]}, token)


def test_external_direct_file_reviews_check_both_the_review_task_and_the_files_stored_task(api, external_work):
    from backend.tests.test_agents import credential, hermes_bot

    token, task, other = external_work
    hermes_bot(api, slug="publisher")
    publisher = credential(api, "publisher")["token"]
    files = [post(api, "files/uploads", {"task": row["id"], "name": "report.txt", "text": "Checked."}, publisher)["file"]
             for row in (task, other)]
    hold_external(api, task)
    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM bot_file_activity WHERE action='reviewed'").fetchone()[0]
    for row, file in ((task, files[1]), (other, files[0])):
        assert post(api, "file-reviews", {"task": row["id"], "file": file["id"], "verdict": "approve"},
                    token, expected=403)["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM bot_file_activity WHERE action='reviewed'").fetchone()[0] == before
    post(api, "file-reviews", {"task": other["id"], "file": files[1]["id"], "verdict": "approve"}, token)
    post(api, "bots/scout/quarantine/clear", {})
    post(api, "file-reviews", {"task": other["id"], "file": files[0]["id"], "verdict": "approve"}, token)


def test_external_structured_review_answers_check_the_files_stored_task(api, external_work):
    from backend.tests.test_agents import credential, hermes_bot
    from backend.tests.test_task_review import ask

    token, task, other = external_work
    hermes_bot(api, slug="publisher")
    publisher = credential(api, "publisher")["token"]
    questions = []
    for row in (task, other):
        file = post(api, "files/uploads", {"task": row["id"], "name": "report.txt", "text": "Checked."}, publisher)["file"]
        question = post(api, f"tasks/{other['id']}/comments", {"text": "Review this version.", "ask": ask("scout")})["comment"]
        with api.app.state.store.transaction() as c:
            version = c.execute("SELECT * FROM bot_file_versions WHERE file_id=?", (file["id"],)).fetchone()
            refs = {**H.message(c, question["id"])["refs"], "review": {"files": [
                {"file": file["id"], "version": version["version"], "digest": version["digest"]}]}}
            c.execute("UPDATE messages SET refs=? WHERE id=?", (H._dump(refs), question["id"]))
        questions.append(question)
    hold_external(api, task)
    body = lambda question: {"target": {"comment": question["id"]}, "answers": {"verdict": ["Approve"]}}
    assert post(api, f"tasks/{other['id']}/answers", body(questions[0]), token,
                expected=403)["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM bot_file_activity WHERE action='reviewed'").fetchone()
        assert H.review_answers(c, questions[0]["id"], actor=task["owner_id"]) == []
    post(api, f"tasks/{other['id']}/answers", body(questions[1]), token)
    post(api, "bots/scout/quarantine/clear", {})
    post(api, f"tasks/{other['id']}/answers", body(questions[0]), token)


@pytest.mark.parametrize("mutation", ["edit", "replace"])
def test_external_page_changes_check_the_pages_stored_task(api, external_work, mutation):
    token, task, other = external_work
    pages = [post(api, "pages", {"name": "report.html", "task": row["id"], "text": "<h1>Report</h1>"}, token)["page"]
             for row in (task, other)]
    hold_external(api, task)

    def write(page, suffix):
        if mutation == "edit":
            return api.patch(f"/api/v2/pages/{page['file_id']}", json={"archived": True}, headers=headers(token))
        return api.post("/api/v2/pages", json={"name": "replacement-" + suffix + ".html", "scope": "bot",
                                              "replaces": page["file_id"], "text": "<h1>Updated report</h1>"},
                        headers=headers(token))

    with api.app.state.store.read() as c:
        before = [tuple(r) for r in c.execute("SELECT * FROM pages ORDER BY file_id")]
        versions = c.execute("SELECT count(*) FROM bot_file_versions").fetchone()[0]
    result = write(pages[0], "held")
    assert result.status_code == 403, result.text
    assert result.json()["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert [tuple(r) for r in c.execute("SELECT * FROM pages ORDER BY file_id")] == before
        assert c.execute("SELECT count(*) FROM bot_file_versions").fetchone()[0] == versions
    result = write(pages[1], "other")
    assert result.status_code == 200, result.text
    post(api, "bots/scout/quarantine/clear", {})
    result = write(pages[0], "held")
    assert result.status_code == 200, result.text


@pytest.mark.parametrize("mutation", ["add", "add_page", "edit", "remove", "order", "card_edit", "card_delete", "card_add"])
def test_external_task_pins_and_cards_check_their_stored_place(api, external_work, mutation):
    token, task, other = external_work
    cards = [post(api, "cards", {"template": "tasks_by_status",
                                **({} if mutation == "add" else {"place": "task:" + row["id"]})}, token)
             for row in (task, other)]
    page = post(api, "pages", {"name": "report.html", "scope": "bot", "text": "<h1>Report</h1>"}, token)["page"] \
        if mutation == "add_page" else None
    hold_external(api, task)

    def write(row, card):
        place = "task:" + row["id"]
        cid, pid = card["card"]["id"], card.get("pin")
        if mutation in ("add", "add_page"):
            item = "page:" + page["file_id"] if mutation == "add_page" else "card:" + cid
            path, body, method = "pins", {"place": place, "item": item}, "post"
        elif mutation in ("edit", "remove"):
            path, body, method = "pins/" + pid, {"size": "large"} if mutation == "edit" else {}, "patch" if mutation == "edit" else "delete"
        elif mutation == "order":
            path, body, method = "places/" + place + "/order", {"ids": [pid]}, "post"
        elif mutation in ("card_edit", "card_delete"):
            path, body, method = "cards/" + cid, {"title": "Updated"} if mutation == "card_edit" else {}, "patch" if mutation == "card_edit" else "delete"
        else:
            path, body, method = "cards", {"template": "done_per_week", "place": place}, "post"
        return api.request(method, "/api/v2/" + path, json=body, headers=headers(token))

    with api.app.state.store.read() as c:
        before = {table: [tuple(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY id")] for table in ("cards", "pins")}
    result = write(task, cards[0])
    assert result.status_code == 403, result.text
    assert result.json()["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        for table, rows in before.items():
            assert [tuple(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY id")] == rows
    result = write(other, cards[1])
    assert result.status_code == 200, result.text
    post(api, "bots/scout/quarantine/clear", {})
    result = write(task, cards[0])
    assert result.status_code == 200, result.text


def test_external_requester_add_and_message_backed_note_cancel_respect_the_task_hold(api, external_work):
    token, task, other = external_work
    notes = []
    for row in (task, other):
        msg = post(api, f"tasks/{row['id']}/comments", {"text": "Review this report."}, token)["comment"]
        with api.app.state.store.transaction() as c:
            note = H.note_create(c, task["owner_id"], "ops", "Review this report.")
            c.execute("UPDATE notes SET message_id=? WHERE id=?", (msg["id"], note["id"]))
            notes.append(note)
    hold_external(api, task)
    assert post(api, f"tasks/{task['id']}/requesters", {"actor": task["owner_id"]}, token,
                expected=403)["error"]["code"] == "quarantined"
    assert post(api, f"notes/{notes[0]['id']}/cancel", {}, token, expected=403)["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert H.note(c, notes[0]["id"])["canceled_at"] is None
        assert not c.execute("SELECT 1 FROM task_requesters WHERE task_id=? AND actor=?",
                             (task["id"], task["owner_id"])).fetchone()
    post(api, f"tasks/{other['id']}/requesters", {"actor": task["owner_id"]}, token)
    post(api, f"notes/{notes[1]['id']}/cancel", {}, token)
    post(api, "bots/scout/quarantine/clear", {})
    post(api, f"tasks/{task['id']}/requesters", {"actor": task["owner_id"]}, token)
    post(api, f"notes/{notes[0]['id']}/cancel", {}, token)


def test_external_page_pins_check_the_files_task_even_at_an_unheld_place(api, external_work):
    from backend.tests.test_cards import page

    token, task, other = external_work
    fid = page(api, H.actor_id(task["owner_id"]), task=task["id"])
    body = {"place": "task:" + other["id"], "item": "page:" + fid}
    hold_external(api, task)
    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM bot_file_activity WHERE file_id=?", (fid,)).fetchone()[0]
    assert post(api, "pins", body, token, expected=403)["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM pins WHERE item=?", ("page:" + fid,)).fetchone()
        assert c.execute("SELECT count(*) FROM bot_file_activity WHERE file_id=?", (fid,)).fetchone()[0] == before
    post(api, "bots/scout/quarantine/clear", {})
    post(api, "pins", body, token)


@pytest.mark.parametrize("mutation", ["keep", "withdraw", "appeal"])
def test_external_rule_hold_mutations_check_the_stored_review_task(api, external_work, mutation):
    token, task, other = external_work
    ids = [H.new_id(), H.new_id()]
    with api.app.state.store.transaction() as c:
        for ident, row in zip(ids, (task, other)):
            c.execute("INSERT INTO org_rule_holds(id,operation,payload,author,destinations,rule_ids,verdict,nonce,"
                      "state,reviewer,task_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (ident, "message", H._dump({"text": "Review this report.", "replay": {}}), task["owner_id"],
                       "[]", "[]", "{}", H.new_id(), "held", "human:ana", row["id"], H.now()))
    hold_external(api, task)
    body = {"why": "Please review this report."} if mutation == "appeal" else {}
    with api.app.state.store.read() as c:
        before = dict(c.execute("SELECT * FROM org_rule_holds WHERE id=?", (ids[0],)).fetchone())
        status = H.task(c, task["id"])["status"]
    assert post(api, f"rule-holds/{ids[0]}/{mutation}", body, token, expected=403)["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert dict(c.execute("SELECT * FROM org_rule_holds WHERE id=?", (ids[0],)).fetchone()) == before
        assert H.task(c, task["id"])["status"] == status
    post(api, f"rule-holds/{ids[1]}/{mutation}", body, token)
    post(api, "bots/scout/quarantine/clear", {})
    post(api, f"rule-holds/{ids[0]}/{mutation}", body, token)


def test_one_tasks_refusals_stop_only_its_turn_and_other_work_claims_and_runs(api):
    botops(api)
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    bad = started(api, machine)
    other = new_task(api, "Check the archive")
    good = started(api, machine)
    waiting = new_task(api, "Check the summary")
    refusals(api, bad, task)
    [hold] = holds(api)
    assert hold["task_id"] == task["id"] and hold["conversation_id"] is None
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "active"
        assert H.task(c, task["id"])["status"] == bad["task"]["status"]
        assert c.execute("SELECT owner_id FROM tasks WHERE title=?", ("Review ops's refused writes",)).fetchone()[0] == H.bot_actor(H.FLEET_MAINTAINER)
    stopped = post(api, f"attempts/{bad['id']}/renew", {}, machine["token"])
    assert stopped["stop"] and stopped["stop_reason"].startswith("Quarantined: 10 repeated refusals today")
    assert not post(api, f"attempts/{good['id']}/renew", {}, machine["token"]).get("stop")
    post(api, f"tasks/{task['id']}/comments", {"text": "Please check it again."})
    assert post(api, f"attempts/{bad['id']}/inputs", {}, machine["token"])["messages"] == []
    refused = post(api, "messages", {"to": "finance", "text": "Keep trying."}, bad["token"], expected=403)
    assert refused["error"]["code"] == "quarantined"
    post(api, f"attempts/{bad['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, machine["token"])
    assert job_state(api, bad["job_id"]) == "queued"
    next_run = started(api, machine)
    assert next_run["task"]["id"] == waiting["id"]
    for attempt in (good, next_run):
        post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Checked."}, machine["token"])
    assert claim(api, machine) is None
    assert any("Held for" in m["body"] for m in get(api, f"conversations/{task['conversation_id']}/messages"))
    assert other["id"] != task["id"]


@pytest.mark.parametrize("lift", ["cooldown", "clear", "person", "botops"])
def test_task_hold_lifts_and_its_count_restarts(api, time_machine, lift):
    time_machine.move_to("2026-11-04T12:00:00Z", tick=False)
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    attempt = started(api, machine)
    refusals(api, attempt, task)
    post(api, f"attempts/{attempt['id']}/renew", {}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, machine["token"])
    assert claim(api, machine) is None
    if lift == "cooldown":
        with api.app.state.store.transaction() as c:
            assert H.lift_cooled_quarantines(c) == []
        time_machine.shift(H.QUARANTINE_COOLDOWN_S + 1)
        with api.app.state.store.transaction() as c:
            assert H.lift_cooled_quarantines(c) == ["ops"]
    elif lift == "clear":
        post(api, "bots/ops/quarantine/clear", {}, "cara-test", expected=403)
        assert holds(api)
        time_machine.shift(1)
        post(api, "bots/ops/quarantine/clear", {})
    else:
        time_machine.shift(1)
        botops(api)
        with api.app.state.store.transaction() as c:
            actor = H.bot_actor(H.FLEET_MAINTAINER) if lift == "botops" else "human:ana"
            H.status_set(c, actor, "ops", state="active", reason="Reviewed")
    assert holds(api) == []
    ready(api, machine, ["ops"])
    settled(api, machine)
    resumed = started(api, machine)
    assert resumed["job_id"] == attempt["job_id"]
    assert "by a quarantine" in resumed["message"]["body"]
    refusals(api, resumed, task, count=2)
    assert holds(api) == []


def test_refusals_from_several_tasks_still_stop_every_turn(api):
    machine = task_bot(api, lanes=True)
    first = new_task(api, "Check the export")
    one = started(api, machine)
    second = new_task(api, "Check the archive")
    two = started(api, machine)
    refusals(api, one, first, count=6)
    refusals(api, two, second, count=5)
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "quarantined"
        assert not H.quarantine_is_escape(c, "ops")
    assert holds(api) == []
    for attempt in (one, two):
        assert post(api, f"attempts/{attempt['id']}/renew", {}, machine["token"])["stop"]


def test_a_held_tasks_refusals_do_not_count_toward_another_tasks_hold(api):
    machine = task_bot(api, lanes=True)
    first = new_task(api, "Check the export")
    one = started(api, machine)
    second = new_task(api, "Check the archive")
    two = started(api, machine)
    refusals(api, one, first)
    refusals(api, two, second, count=2)
    assert [row["task_id"] for row in holds(api)] == [first["id"]]
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "active"
    refusals(api, two, second, count=H.QUARANTINE_AT - 1)
    assert {row["task_id"] for row in holds(api)} == {first["id"], second["id"]}
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "active"


@pytest.mark.parametrize("rule", ["reach", "target_inactive"])
def test_held_scopes_are_also_contained_at_the_total_refusal_backstop(api, rule):
    first = new_task(api, "Check the export")
    second = new_task(api, "Check the archive")
    with api.app.state.store.transaction() as c:
        for task in (first, second):
            for i in range(H.QUARANTINE_TOTAL_AT):
                with pytest.raises(H.Refused):
                    H.refuse(c, "bot:ops", rule, f"Refused target {i}", task=task["id"])
                assert H.bot(c, "ops")["state"] == "active"
                held = c.execute("SELECT task_id FROM quarantine_holds WHERE released_at IS NULL").fetchall()
                assert (task["id"] in {row[0] for row in held}) == (i == H.QUARANTINE_TOTAL_AT - 1)
    assert {row["task_id"] for row in holds(api)} == {first["id"], second["id"]}


def test_unattributed_refusals_after_a_task_hold_still_quarantine_the_bot(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    refusals(api, started(api, machine), task)
    with api.app.state.store.transaction() as c:
        for i in range(H.QUARANTINE_AT + 1):
            with pytest.raises(H.Refused):
                H.refuse(c, "bot:ops", "reach", "No source for this refused write")
            assert H.bot(c, "ops")["state"] == ("quarantined" if i == H.QUARANTINE_AT else "active")
    assert [row["task_id"] for row in holds(api)] == [task["id"]]


def test_an_external_agent_cannot_write_a_held_task_but_can_write_another_over_mcp(api):
    from backend.tests.test_agents import credential, hermes_bot
    from backend.tests.test_mcp import call
    hermes_bot(api)
    token = credential(api)["token"]
    first = new_task(api, "Check the export", owner="scout")
    second = new_task(api, "Check the archive", owner="scout")
    for _ in range(H.QUARANTINE_AT + 1):
        row = get(api, "tasks/" + first["id"])["task"]
        result = post(api, "tasks/" + first["id"], {"version": row["version"], "number": 993}, token, expected=403)
        assert result["error"]["code"] == "identity"
    assert [row["task_id"] for row in holds(api)] == [first["id"]]
    assert call(api, "hub_task_update", {"id": first["id"], "status": "doing"}, token=token)[0]
    assert call(api, "hub_task_comment", {"id": first["id"], "text": "Keep trying."}, token=token)[0]
    assert not call(api, "hub_task_update", {"id": second["id"], "status": "doing"}, token=token)[0]
    assert not call(api, "hub_task_comment", {"id": second["id"], "text": "Checked the archive."}, token=token)[0]
    with api.app.state.store.read() as c:
        assert H.task(c, first["id"])["status"] == first["status"]
        assert H.task(c, second["id"])["status"] == "doing"
    post(api, "bots/scout/quarantine/clear", {})
    assert not call(api, "hub_task_update", {"id": first["id"], "status": "doing"}, token=token)[0]


@pytest.mark.parametrize("mutation", ["snooze", "relations"])
def test_external_validation_refusals_keep_the_task_scope_through_rollback(api, mutation):
    from backend.tests.test_agents import credential, hermes_bot

    hermes_bot(api)
    token = credential(api)["token"]
    task = new_task(api, "Check the export", owner="scout")
    other = new_task(api, "Check the archive", owner="scout")
    actor = task["owner_id"]
    if mutation == "snooze":
        body, rule = {"until": H.shift(H.now(), days=-1)}, "date"
    else:
        body, rule = {"task": task["id"], "kind": "related"}, "kind"
    for _ in range(H.QUARANTINE_AT + 1):
        result = post(api, f"tasks/{task['id']}/{mutation}", body, token, expected=422)
        assert result["error"]["code"] == rule
    assert [row["task_id"] for row in holds(api)] == [task["id"]]
    with api.app.state.store.read() as c:
        assert H.bot(c, H.actor_id(actor))["state"] == "active"
        rows = c.execute("SELECT detail FROM refusals WHERE actor=? AND rule=?", (actor, rule)).fetchall()
        assert len(rows) == H.QUARANTINE_AT + 1
        assert {H._json(row["detail"], {}).get("scope") for row in rows} == {"task:" + task["id"]}
        assert H.task(c, task["id"])["snoozed_until"] is None
    post(api, f"tasks/{other['id']}/snooze", {"until": H.shift(H.now(), days=2)}, token)


@pytest.mark.parametrize("mutation", ["thread", "upload", "import", "link"])
def test_external_native_posts_and_file_publishing_fence_the_resolved_task(api, mutation):
    from backend import task_quarantine as Q
    from backend.tests.test_agents import credential, hermes_bot

    hermes_bot(api)
    token = credential(api)["token"]
    task = new_task(api, "Check the export", owner="scout")
    other = new_task(api, "Check the archive", owner="scout")
    bot = H.actor_id(task["owner_id"])

    def write(row, expected=200):
        if mutation == "thread":
            return post(api, f"threads/{row['conversation_id']}/messages", {"text": "Checked the report."},
                        token, expected=expected)
        if mutation == "link":
            return post(api, "files/links", {"task": row["id"], "url": "https://acme.example/report"},
                        token, expected=expected)
        body = {"task": row["id"], "name": "report.txt", "text": "Checked the report."}
        if mutation == "import":
            body.update(source="s3://acme-reports/report.txt", etag="v1")
        return post(api, "files/" + ("imports" if mutation == "import" else "uploads"), body,
                    token, expected=expected)

    with api.app.state.store.transaction() as c:
        Q.hold(c, bot, "task:" + task["id"], "Repeated refusals", {"rule": "identity"})
        before = c.execute("SELECT count(*) FROM messages").fetchone()[0]
    assert write(task, expected=403)["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages").fetchone()[0] == before
        assert c.execute("SELECT count(*) FROM bot_files").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM bot_file_versions").fetchone()[0] == 0
        assert H.bot(c, bot)["state"] == "active"
    write(other)
    post(api, "bots/scout/quarantine/clear", {})
    write(task)


@pytest.mark.parametrize("mutation", ["upload", "failed_sync", "link"])
def test_file_publishing_checks_the_stored_task_when_the_request_omits_it(api, mutation):
    from backend import task_quarantine as Q
    from backend.tests.test_agents import credential, hermes_bot

    hermes_bot(api)
    token = credential(api)["token"]
    task = new_task(api, "Check the export", owner="scout")
    bot = H.actor_id(task["owner_id"])
    if mutation == "link":
        path, body = "files/links", {"scope": "bot", "url": "https://acme.example/report"}
        other = {**body, "url": "https://acme.example/archive"}
    else:
        path, body = "files/uploads", {"scope": "bot", "name": "report.txt", "text": "Checked the report."}
        other = {**body, "name": "archive.txt"}
    made = post(api, path, {**body, "task": task["id"]}, token)["file"]
    body["title"] = "Rechecked report"
    if mutation == "failed_sync":
        body["sync"] = "failed"
    elif mutation == "upload":
        body["text"] = "Rechecked the report."
    with api.app.state.store.transaction() as c:
        Q.hold(c, bot, "task:" + task["id"], "Repeated refusals", {"rule": "identity"})
        before = dict(c.execute("SELECT * FROM bot_files WHERE id=?", (made["id"],)).fetchone())
        versions = c.execute("SELECT count(*) FROM bot_file_versions").fetchone()[0]
    assert post(api, path, body, token, expected=403)["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert dict(c.execute("SELECT * FROM bot_files WHERE id=?", (made["id"],)).fetchone()) == before
        assert c.execute("SELECT count(*) FROM bot_file_versions").fetchone()[0] == versions
    post(api, path, other, token)
    post(api, "bots/scout/quarantine/clear", {})
    post(api, path, body, token)


@pytest.mark.parametrize("scope", ["task", "chat"])
def test_runner_file_publishing_checks_the_supplied_attempt_hold(api, scope):
    from backend import task_quarantine as Q

    machine = task_bot(api, lanes=True)
    if scope == "task":
        task = new_task(api, "Check the export")
        held_scope = "task:" + task["id"]
    else:
        message = post(api, "chat/ops", {"text": "Check the export."})
        held_scope = "chat:" + message["conversation_id"]
    attempt = started(api, machine)
    body = {"bot": "ops", "attempt": attempt["id"], "scope": "bot", "name": "report.txt", "text": "Checked."}
    with api.app.state.store.transaction() as c:
        Q.hold(c, "ops", held_scope, "Repeated refusals", {"rule": "identity"})
    result = post(api, "files/uploads", body, machine["token"], expected=403)
    assert result["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM bot_files").fetchone()[0] == 0
    post(api, "bots/ops/quarantine/clear", {})
    post(api, "files/uploads", body, machine["token"])


def test_claim_fence_uses_both_partial_quarantine_indexes(api):
    from backend import execution
    machine = task_bot(api, lanes=True)
    new_task(api, "Check the export")
    post(api, "chat/ops", {"text": "Check the archive."})
    with api.app.state.store.read() as c:
        # The claim's candidate joins and fences, including task and chat probes, use both partial indexes.
        plan = [row["detail"] for row in c.execute(
            "EXPLAIN QUERY PLAN SELECT j.id FROM jobs j JOIN assignments a ON a.bot=j.bot_id "
            "JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
            f"LEFT JOIN tasks qt ON qt.id={H.MESSAGE_TASK_SQL} "
            "WHERE j.state='queued' AND j.held=0 AND j.bot_id=? AND a.runner_id=? "
            f"AND {execution.unfenced_sql()} AND {execution.scoped_sql()}", ("ops", machine["runner_id"]))]
    assert "SEARCH qh USING INDEX quarantine_holds_task (bot_id=? AND task_id=?)" in plan, plan
    assert "SEARCH qh USING INDEX quarantine_holds_chat (bot_id=? AND conversation_id=?)" in plan, plan
    assert not any("SCAN qh" in step for step in plan), plan


def test_the_running_task_is_held_instead_of_the_target_of_its_refused_write(api):
    machine = task_bot(api, lanes=True)
    source = new_task(api, "Check the export")
    attempt = started(api, machine)
    target = new_task(api, "Check the archive")
    refusals(api, attempt, target)
    [hold] = holds(api)
    assert hold["task_id"] == source["id"]
    assert started(api, machine)["task"]["id"] == target["id"]


def test_an_escape_over_a_task_hold_stays_bot_wide_and_needs_a_person(api, time_machine):
    time_machine.move_to("2026-11-04T12:00:00Z", tick=False)
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    one = started(api, machine)
    new_task(api, "Check the archive")
    two = started(api, machine)
    refusals(api, one, task)
    with api.app.state.store.transaction() as c:
        H.quarantine(c, "ops", "escape: the task note reaches outside the hub: secrets/mail.env",
                     context={"what": "task note", "found": "secrets/", "task": task["id"]})
    for attempt in (one, two):
        stop = post(api, f"attempts/{attempt['id']}/renew", {}, machine["token"])
        assert stop["stop"] and "until a person clears it" in stop["stop_reason"]
    time_machine.shift(H.QUARANTINE_COOLDOWN_S + 1)
    with api.app.state.store.transaction() as c:
        H.lift_cooled_quarantines(c)
        assert H.bot(c, "ops")["state"] == "quarantined" and H.quarantine_is_escape(c, "ops")
    post(api, "bots/ops/quarantine/clear", {})
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "active"


def test_a_chat_loop_holds_its_chat_while_tasks_still_run(api):
    machine = task_bot(api, lanes=True)
    post(api, "chat/ops", {"text": "Check the export."})
    chat = started(api, machine)
    target = new_task(api, "Check the archive")
    refusals(api, chat, target)
    [hold] = holds(api)
    assert hold["task_id"] is None and hold["conversation_id"] == chat["message"]["conversation_id"]
    post(api, f"attempts/{chat['id']}/renew", {}, machine["token"])
    post(api, f"attempts/{chat['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, machine["token"])
    post(api, "chat/ops", {"text": "Try again."})
    assert started(api, machine)["task"]["id"] == target["id"]
    assert claim(api, machine) is None
    post(api, "bots/ops/quarantine/clear", {})
    assert claim(api, machine)["job_id"] == chat["job_id"]


def test_unattributed_refusals_keep_the_bot_wide_stop(api):
    with api.app.state.store.transaction() as c:
        for _ in range(H.QUARANTINE_AT + 1):
            with pytest.raises(H.Refused):
                H.refuse(c, "bot:ops", "identity", "Only a person may do this")
        assert H.bot(c, "ops")["state"] == "quarantined"
    assert holds(api) == []


def test_one_unattributed_refusal_keeps_a_later_task_loop_bot_wide(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    attempt = started(api, machine)
    with api.app.state.store.transaction() as c:
        with pytest.raises(H.Refused):
            H.refuse(c, "bot:ops", "reach", "No source for this refused write")
    refusals(api, attempt, task)
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "quarantined"
    assert holds(api) == []


def test_a_scoped_stop_keeps_the_named_outside_action_for_review_after_clear(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    attempt = started(api, machine)
    post(api, f"attempts/{attempt['id']}/events", {"events": [
        {"seq": 1, "kind": "status", "payload": {"state": "active", "tool_events": True}},
        {"seq": 2, "kind": "tool", "payload": {"name": "Bash", "status": "started", "summary": "gh pr merge 42 --squash"}}]}, machine["token"])
    refusals(api, attempt, task)
    post(api, f"attempts/{attempt['id']}/renew", {}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "last_seq": 2}, machine["token"])
    assert job_state(api, attempt["job_id"]) == "uncertain"
    post(api, "bots/ops/quarantine/clear", {})
    assert claim(api, machine) is None
    assert job_state(api, attempt["job_id"]) == "uncertain"


def test_a_task_hold_keeps_other_task_holds_and_clear_does_not_resume_a_paused_bot(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    attempt = started(api, machine)
    with api.app.state.store.transaction() as c:
        from backend import task_holds
        task_holds.put(c, task["id"], "snoozed", H.now(), until_at=H.shift(H.now(), days=2), reason="Waiting for data")
    refusals(api, attempt, task)
    with api.app.state.store.transaction() as c:
        H.status_set(c, "human:ana", "ops", state="paused", reason="Maintenance")
    post(api, "bots/ops/quarantine/clear", {})
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "paused"
        assert H.task(c, task["id"])["snoozed_note"] == "Waiting for data"
    assert holds(api) == []


def test_a_held_task_cannot_ride_along_with_another_tasks_next_run(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    attempt = started(api, machine)
    refusals(api, attempt, task)
    post(api, f"attempts/{attempt['id']}/renew", {}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, machine["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET next_run=1,status='open' WHERE id=?", (task["id"],))
    other = new_task(api, "Check the archive")
    attempt = post(api, "jobs/claim", {"next_run": True, "start_places": True}, machine["token"])["attempt"]
    assert attempt["task"]["id"] == other["id"] and attempt["next_run"] == []


@pytest.mark.parametrize("failure", ["archived", "chat_notice", "task_notice", "release"])
def test_a_bad_release_preserves_other_cooldowns_and_the_rest_of_the_scheduler_tick(api, time_machine, monkeypatch, failure):
    from backend import task_quarantine as Q
    from backend.scheduler import Scheduler

    time_machine.move_to("2026-11-04T12:00:00Z", tick=False)
    chat = post(api, "chat/ops", {"text": "Check the export."})
    bad = new_task(api, "Check the export") if failure == "task_notice" else None
    good = new_task(api, "Check the archive", owner="finance")
    bad_scope = "task:" + bad["id"] if bad else "chat:" + chat["conversation_id"]
    with api.app.state.store.transaction() as c:
        Q.hold(c, "ops", bad_scope, "Repeated refusals", {"rule": "identity"})
        Q.hold(c, "finance", "task:" + good["id"], "Repeated refusals", {"rule": "identity"})
    if failure == "archived":
        revision = next(b for b in get(api, "bots") if b["id"] == "ops")["revision"]
        post(api, "bots/ops/archive", {"expected_revision": revision})
    elif failure in ("chat_notice", "task_notice"):
        original = Q._release_notice

        def broken_notice(c, row):
            original(c, row)
            if row["bot_id"] == "ops":
                raise RuntimeError("Notice delivery failed after writing")

        monkeypatch.setattr(Q, "_release_notice", broken_notice)
    else:
        original = H.event

        def broken_release(c, actor, action, target="", detail=None):
            if action == "quarantine.released" and target == "bot:ops":
                raise RuntimeError("This release cannot be recorded")
            return original(c, actor, action, target, detail)

        monkeypatch.setattr(H, "event", broken_release)
    following = []
    catch_up = H.notes_settle_catch_up

    def after_release(c, limit):
        following.append(True)
        return catch_up(c, limit)

    monkeypatch.setattr(H, "notes_settle_catch_up", after_release)
    time_machine.shift(H.QUARANTINE_COOLDOWN_S + 1)
    Scheduler(api.app.state.store, api.app.state.execution).tick()
    assert following, "the step after cooldowns must run"
    assert [r["bot_id"] for r in holds(api)] == (["ops"] if failure == "release" else [])
    with api.app.state.store.read() as c:
        released = {r[0] for r in c.execute("SELECT target FROM events WHERE action='quarantine.released'")}
        assert released == ({"bot:finance"} if failure == "release" else {"bot:ops", "bot:finance"})
        assert any("has lifted" in m["body"] for m in H.task_comments(c, good["id"]))
        messages = H.task_comments(c, bad["id"]) if bad else H.messages(c, chat["conversation_id"])
        assert not any("has lifted" in m["body"] for m in messages), "failed or skipped notices leave no partial write"


@pytest.mark.parametrize("mutation", ["snooze", "unsnooze", "check-back", "later", "defer", "relations",
                                     "reverse_relation", "wait_task", "wait_pr", "links", "unlink", "files",
                                     "edit", "delete_comment", "resolve", "answers", "ask", "update"])
def test_external_task_mutations_fence_the_explicit_task_and_allow_an_unheld_task(api, mutation):
    from backend import task_quarantine as Q
    from backend.tests.test_agents import credential, hermes_bot
    from backend.tests.test_task_review import ask

    hermes_bot(api)
    token = credential(api)["token"]
    tasks = [new_task(api, title, owner="scout") for title in ("Check the export", "Check the archive", "Check the summary")]
    other = tasks[2]
    requests = []
    for task in tasks[:2]:
        tid, body, suffix = task["id"], {}, mutation
        if mutation == "snooze":
            body = {"until": H.shift(H.now(), days=2)}
        elif mutation == "unsnooze":
            post(api, f"tasks/{tid}/snooze", {"until": H.shift(H.now(), days=2)})
        elif mutation == "check-back":
            body = {"at": H.shift(H.now(), days=2)}
        elif mutation in ("later", "defer"):
            with api.app.state.store.transaction() as c:
                c.execute("UPDATE tasks SET next_run=1 WHERE id=?", (tid,))
            body = {"next_run": True}
        elif mutation in ("relations", "reverse_relation"):
            suffix, body = "relations", {"task": other["id"], "kind": "related"}
            if mutation == "reverse_relation":
                tid, body["task"] = other["id"], tid
        elif mutation in ("wait_task", "wait_pr"):
            suffix = "waits"
            body = {"on": "task:" + other["id"] if mutation == "wait_task" else "pr:https://github.com/acme/widgets/pull/42"}
        elif mutation == "links":
            body = {"url": "https://acme.example/export"}
        elif mutation == "unlink":
            link = post(api, f"tasks/{tid}/links", {"url": "https://acme.example/export"})["links"][0]
            suffix, body = "links", {"remove": link["id"]}
        elif mutation == "files":
            body = {"name": "report.txt", "text": "Checked the report."}
        elif mutation in ("edit", "delete_comment"):
            comment = post(api, f"tasks/{tid}/comments", {"text": "Checking the report."}, token)["comment"]
            suffix = "comments/" + comment["id"] + ("/delete" if mutation == "delete_comment" else "")
            body = {} if mutation == "delete_comment" else {"text": "Checked the report."}
        elif mutation == "resolve":
            comment = post(api, f"tasks/{tid}/ask", {"text": "Which report should I check?"}, token)
            suffix = "comments/" + comment["id"] + "/resolve"
        elif mutation == "answers":
            comment = post(api, f"tasks/{tid}/comments", {"text": "Is the report ready?", "ask": ask("scout")})["comment"]
            body = {"target": {"comment": comment["id"]}, "answers": {"verdict": ["Approve"]}}
        elif mutation == "ask":
            body = {"text": "Which report should I check?"}
        elif mutation == "update":
            suffix = ""
            body = {"version": get(api, "tasks/" + tid)["task"]["version"], "status": "doing"}
        requests.append((f"tasks/{tid}" + ("/" + suffix if suffix else ""), body))
    with api.app.state.store.transaction() as c:
        Q.hold(c, H.actor_id(tasks[0]["owner_id"]), "task:" + tasks[0]["id"], "Repeated refusals", {"rule": "identity"})
        before = dict(c.execute("SELECT * FROM tasks WHERE id=?", (tasks[0]["id"],)).fetchone())
    path, body = requests[0]
    result = post(api, path, body, token, expected=403)
    assert result["error"]["code"] == "quarantined"
    with api.app.state.store.read() as c:
        assert dict(c.execute("SELECT * FROM tasks WHERE id=?", (tasks[0]["id"],)).fetchone()) == before
    path, body = requests[1]
    post(api, path, body, token)
    post(api, "bots/scout/quarantine/clear", {})
    path, body = requests[0]
    post(api, path, body, token)


@pytest.mark.parametrize("source", ["task", "task_padded", "task_id", "task_room", "chat", "chat_blank_task"])
def test_notes_from_held_sources_stay_pending_and_unread_until_release(api, source, monkeypatch):
    from backend import bot_to_bot as B, task_quarantine as Q
    from backend import threads

    monkeypatch.setattr(threads, "NOTES_READ", 1)
    machine = task_bot(api, lanes=True)
    with api.app.state.store.transaction() as c:
        if source.startswith("chat"):
            message = H.say(c, "bot:finance", "bot:ops", "The export is ready to check.")
            scope = "chat:" + message["conversation_id"]
            if source == "chat_blank_task":
                c.execute("UPDATE messages SET refs=? WHERE id=?", (H._dump({"task": " \t\u2003"}), message["id"]))
        else:
            task = H.task_create(c, "bot:finance", "Check the export", "Check the report.", "bot:ops")
            message = H.task_comment(c, "bot:finance", task["id"], "The export is ready to check.")
            scope = "task:" + task["id"]
            if source != "task":
                refs = {k: v for k, v in message["refs"].items() if k != "task"}
                if source == "task_id":
                    refs["task_id"] = task["id"]
                elif source == "task_padded":
                    refs["task"] = " \t\u2003" + task["id"] + "\n\u00a0"
                c.execute("UPDATE messages SET refs=? WHERE id=?", (H._dump(refs), message["id"]))
        Q.hold(c, "ops", scope, "Repeated refusals", {"rule": "identity"})
        pending = H._one(c, "SELECT * FROM notes WHERE message_id=?", (message["id"],))
        free = H.say(c, "bot:cpo", "bot:ops", "The archive is ready to check.")
    other = new_task(api, "Check the archive")
    attempt = post(api, "jobs/claim", {"next_run": True, "start_places": True}, machine["token"])["attempt"]
    assert attempt["task"]["id"] == other["id"]
    assert [n["message"] for n in attempt["notes"] if n.get("message")] == [free["id"]]
    with api.app.state.store.transaction() as c:
        note = H.note(c, pending["id"])
        assert H.note_waiting(c, note) and H.notes_waiting_counts(c, ["ops"])["ops"] == 1
        assert all(note[key] is None for key in ("carried_by", "settled_at", "canceled_at"))
        assert H.message(c, message["id"])["read_at"] is None
        assert B.continue_backlog(c, "ops") is None, "a held note must not start a backlog loop"
    if not source.startswith("chat"):
        row = get(api, "tasks/" + task["id"])["task"]
        result = post(api, "tasks/" + task["id"], {"version": row["version"], "status": "doing"}, attempt["token"], expected=403)
        assert result["error"]["code"] == "quarantined"
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["lane"]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    post(api, "bots/ops/quarantine/clear", {})
    if source.startswith("chat"):
        new_task(api, "Check the summary")
    resumed = post(api, "jobs/claim", {"next_run": True, "start_places": True}, machine["token"])["attempt"]
    assert [n["message"] for n in resumed["notes"] if n.get("message")] == [message["id"]]
    with api.app.state.store.read() as c:
        assert H.note(c, pending["id"])["carried_by"] == resumed["id"]
        assert H.message(c, message["id"])["read_at"] is not None


def test_a_held_task_does_not_use_the_bots_only_automatic_task_place(api):
    from backend.tests.test_auto_tasks import set_cap
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    attempt = started(api, machine)
    set_cap(api, 1)
    other = new_task(api, "Check the archive")
    assert claim(api, machine) is None
    refusals(api, attempt, task)
    post(api, f"attempts/{attempt['id']}/renew", {}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, machine["token"])
    assert get(api, "bots/ops")["task_flow"]["going"] == 0
    assert started(api, machine)["task"]["id"] == other["id"]
