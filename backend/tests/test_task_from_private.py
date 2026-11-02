"""A private task's owning bot files a company-visible task from that work, in its own words (Dev 637)."""
import json

import pytest

from backend import task_privacy as privacy
from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_task_guest import dry_run, refused, run_on, task
from backend.tests.test_task_sealed_child import ROUTE, carry, run_for, sees
from backend.tests.test_tasks_board import bot_token

REPAIR = {"title": "Check the chat setting before an upload is stored",
          "body": "An upload refused by the chat setting still writes its bytes first. Check the setting before the "
                  "write, and add a test that the store is never called for a refused upload.",
          "acceptance_criteria": ["A refused upload stores nothing"]}
PRIVATE_BODY = ("Confidential: Zip triage. The vendor's intake form posts every upload to the shared bucket before the "
                "chat setting is read, so a refused file is still kept for a day.")
FILE = "file-" + "0a1b2c3d4e5f6a7b8c9d0e1f"


@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def filed(parent=None, owner="finance", **extra):
    return {"owner": owner, **REPAIR, "from_private": True,
            **({"relations": [{"task": parent, "kind": "parent"}]} if parent else {}), **extra}


def private_work(api, title="Zip triage", owner="bot:ops"):
    row = task(api, "bot:cpo", owner, title)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET body=? WHERE id=?", (PRIVATE_BODY, row["id"]))
    return row


def correction(api, body, token, check):
    r = api.post("/api/v2/tasks", json=body, headers=headers(token))
    assert r.status_code == 422 and check in r.json()["error"]["detail"], r.text
    assert "vendor" not in r.json()["error"]["detail"] and "ghp_" not in r.json()["error"]["detail"]
    assert any(check in p for p in dry_run(api, body, token))
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM refusals WHERE actor='bot:ops'").fetchone()
        assert not c.execute("SELECT 1 FROM tasks WHERE owner='bot:finance'").fetchone()


@pytest.mark.parametrize("under", [True, False])
def test_the_owning_bots_run_files_a_company_visible_task_the_builder_reads_and_the_private_task_stays_sealed(api, under):
    row = private_work(api)
    attempt = run_on(api)
    body = filed(row["id"] if under else None)
    assert not dry_run(api, body, attempt["token"])
    made = post(api, "tasks", body, attempt["token"])
    assert not made["private"] and made["owner"] == "bot:finance" and made["requester"] == "bot:ops"
    with api.app.state.store.read() as c:
        assert H.task_private(c, H.task(c, row["id"]))

    finance = bot_token(api, "finance")
    shown = get(api, "tasks/" + made["id"], finance)
    assert shown["task"]["acceptance_criteria"] == REPAIR["acceptance_criteria"]
    assert api.get("/api/v2/tasks/" + row["id"], headers=headers(finance)).status_code in (403, 404)
    assert not sees(api, finance, "Confidential: Zip triage.")
    # Any other bot reads it too: it is the company's work now.
    assert get(api, "tasks/" + made["id"], bot_token(api, "doc-updater"))["task"]["id"] == made["id"]
    text = json.dumps(shown)
    assert "Zip triage" not in text and "Confidential" not in text
    with api.app.state.store.read() as c:
        notices = [m["body"] for m in c.execute(
            "SELECT m.body FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot='finance'")]
    assert notices and not any("Zip triage" in n for n in notices), notices
    # The private run writes nothing more to it: a comment would skip the words check.
    r = api.post(f"/api/v2/tasks/{made['id']}/comments", json={"text": "Also see the triage notes."},
                 headers=headers(attempt["token"]))
    assert r.status_code == 403, r.text


def test_a_reader_of_the_new_task_sees_its_parent_only_as_a_private_task(api):
    row = private_work(api)
    attempt = run_on(api)
    made = post(api, "tasks", filed(row["id"]), attempt["token"])
    for bot in ("finance", "doc-updater"):
        token = bot_token(api, bot)
        shown = get(api, "tasks/" + made["id"], token)["task"]
        assert shown["relations"]["parent"] == [{"id": None, "title": "a private task", "status": None, "owner": None,
                                                 "direction": "out", "private": True}]
        assert row["id"] not in json.dumps(shown["relations"])
        listed = get(api, "tasks?owner=bot:finance", token)
        rows = listed.get("tasks") or listed.get("items") or []
        assert rows and all(r["relations"].get("parent", [{}])[0].get("title") == "a private task"
                            for r in rows if r["id"] == made["id"]), listed
    # The private task's own readers see the real parent.
    mine = get(api, "tasks/" + made["id"], attempt["token"])["task"]
    assert mine["relations"]["parent"][0]["id"] == row["id"]


def test_history_says_on_both_tasks_what_was_filed_without_the_private_title(api):
    row = private_work(api)
    attempt = run_on(api)
    made = post(api, "tasks", filed(row["id"]), attempt["token"])
    parent_events = get(api, "tasks/" + row["id"], attempt["token"])["events"]
    assert any(e["field"] == "filed_from_private" and e["actor"] == "bot:ops" and e["new"] == made["id"]
               and e["note"] == f"Filed a company-visible task from this private task: {REPAIR['title']}"
               for e in parent_events), parent_events
    events = get(api, "tasks/" + made["id"], bot_token(api, "finance"))["events"]
    assert any(e["field"] == "from_private" and e["note"] == "Filed from a private task" for e in events), events
    assert "Zip triage" not in json.dumps(events)


def test_done_tells_the_private_tasks_owner_and_its_wait_clears(api):
    row = private_work(api)
    attempt = run_on(api)
    made = post(api, "tasks", filed(row["id"]), attempt["token"])
    with api.app.state.store.read() as c:
        assert (H.wait_detail(c, H.task(c, row["id"])) or {}).get("on") == "bot:finance"
    finance = bot_token(api, "finance")
    current = get(api, "tasks/" + made["id"], finance)["task"]
    post(api, "tasks/" + made["id"], {"version": current["version"], "status": "done", "note": "Fixed, test added."},
         finance)
    with api.app.state.store.read() as c:
        assert H.wait_detail(c, H.task(c, row["id"])) is None
        jobs = [json.loads(r[0] or "{}") for r in c.execute(
            "SELECT m.refs FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot='ops'")]
    # The company task's result reaches the private task's owner on it, as any awaited subtask's does.
    assert any(r.get("rule") == "awaited_subtask" and r["task"] == row["id"] and r.get("subtask") == made["id"]
               for r in jobs), jobs


@pytest.mark.parametrize("under", [True, False])
@pytest.mark.parametrize("words", [
    "Background: the vendor's intake form posts every upload to the shared bucket before the chat setting is read.",
    "Comment",
    "Criteria",
])
def test_words_copied_from_the_private_work_are_a_correction_naming_the_copy_check(api, under, words):
    row = private_work(api)
    with api.app.state.store.transaction() as c:
        H.task_comment(c, "bot:cpo", row["id"], "Note for ops: the bucket lifecycle rule deletes refused uploads only "
                                                 "after twenty four hours have passed.", wake=False)
        c.execute("UPDATE tasks SET acceptance=? WHERE id=?", (json.dumps(
            ["The quarantine report lists every refused upload with its owner and the hour it was deleted"]), row["id"]))
    if words == "Comment":
        words = "The bucket lifecycle rule deletes refused uploads only after twenty four hours have passed, so check first."
    elif words == "Criteria":
        words = "Done when the quarantine report lists every refused upload with its owner and the hour it was deleted."
    attempt = run_on(api)
    correction(api, filed(row["id"] if under else None, body=words), attempt["token"], "copies 12 or more words")


@pytest.mark.parametrize("under", [True, False])
def test_a_secret_shaped_value_is_a_correction_naming_that_check(api, under):
    row = private_work(api)
    attempt = run_on(api)
    body = filed(row["id"] if under else None, body="Push the fix with token ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8")
    correction(api, body, attempt["token"], "secret-shaped")


@pytest.mark.parametrize("under", [True, False])
@pytest.mark.parametrize("carried", ["file", "attachment", "link"])
def test_a_file_or_link_from_the_private_task_is_a_correction_naming_that_check(api, under, carried):
    row = private_work(api)
    url = "https://docs.acme.example/vendor-intake-review"
    with api.app.state.store.transaction() as c:
        blob = H.new_id()
        c.execute("INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) "
                  "VALUES(?,'bot:ops','d',1,'intake.pdf','application/pdf','2026')", (blob,))
        c.execute("INSERT INTO task_assets(task_id,blob_id) VALUES(?,?)", (row["id"], blob))
        c.execute("INSERT INTO bot_files(id,bot,scope,identity,title,kind,locator,first_activity_at,last_activity_at,"
                  "task_id) VALUES(?,'ops',?,'intake','Intake notes','document','intake','2026','2026',?)",
                  (FILE, "task:" + row["id"], row["id"]))
        H.task_link(c, "bot:ops", row["id"], url)
    attempt = run_on(api)
    extra = {"file": {"body": f"See {FILE} for the failing request."},
             "attachment": {"body": f"The attachment {blob} shows the failing request."},
             "link": {"links": [url]}}[carried]
    correction(api, filed(row["id"] if under else None, **extra), attempt["token"], "file or link from a private task")


def test_a_bot_that_only_reads_the_private_task_is_refused(api):
    row = private_work(api)
    attempt = run_for(api, "cpo")              # cpo asked for it: it reads it, and neither owns it nor manages ops
    carry(api, attempt, row)
    for parent in (row["id"], None):
        words = refused(api, filed(parent), attempt["token"])
        assert words == "Only a private task's owning bot, or its manager, files a task from its work", words
        assert words in dry_run(api, filed(parent), attempt["token"])
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE owner='bot:finance'").fetchone()


def test_a_person_on_the_private_task_keeps_todays_refusal_naming_them_and_the_route(api):
    row = task(api, "human:cara", "bot:ops", "Cara's vendor dispute")
    attempt = run_on(api)
    words = refused(api, filed(row["id"]), attempt["token"])
    assert words == f"Cara is on this private task: {ROUTE}", words


def test_without_the_route_a_company_visible_create_is_refused_naming_the_route(api):
    row = private_work(api)
    attempt = run_on(api)
    body = {k: v for k, v in filed(row["id"], private=False).items() if k != "from_private"}
    words = refused(api, body, attempt["token"])
    assert words.startswith("Private task work cannot create company-visible tasks") and "--from-private" in words


def test_with_the_route_switched_off_it_is_refused_as_before(api, monkeypatch):
    monkeypatch.setattr(privacy, "FROM_PRIVATE", False)
    row = private_work(api)
    attempt = run_on(api)
    for parent in (row["id"], None):
        assert refused(api, filed(parent), attempt["token"]) == privacy.COMPANY_REFUSAL
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE owner='bot:finance'").fetchone()


def test_outside_a_private_run_the_route_is_refused(api):
    finance = bot_token(api, "finance")
    words = refused(api, filed(None, owner="doc-updater"), finance)
    assert words == "Only a bot's own run on private work files a task from it", words


@pytest.mark.parametrize("change,status,words", [
    ({"private": True}, 422, "A task from private work is company-visible: leave out --private"),
    ({"owner": "human:cara"}, 403, "A task from private work is for a bot: ask a person with `hub task question`"),
    ("company parent", 403, "File a task from private work under the private task it comes from, or with no parent"),
    ("related", 403, "A task from private work is related to no private task but its parent"),
])
def test_the_route_refuses_a_private_task_a_person_a_company_parent_and_a_private_relation(api, change, status, words):
    row = private_work(api)
    other = private_work(api, "Ops lockfile scan")
    attempt = run_on(api)
    if change == "company parent":
        with api.app.state.store.transaction() as c:
            shared = H.task_create(c, "bot:cpo", "Upload fixes", "Company work.", "bot:ops", private=False, lint=False)
        body = filed(shared["id"])
    elif change == "related":
        body = filed(row["id"], relations=[{"task": row["id"], "kind": "parent"}, {"task": other["id"], "kind": "related"}])
    else:
        body = filed(row["id"], **change)
    r = api.post("/api/v2/tasks", json=body, headers=headers(attempt["token"]))
    assert r.status_code == status and r.json()["error"]["detail"] == words, r.text
    assert words in dry_run(api, body, attempt["token"])
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE requester='bot:ops' AND title=?", (REPAIR["title"],)).fetchone()


SECRET = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
COPIED = "the vendor's intake form posts every upload to the shared bucket before the chat setting is read"


@pytest.mark.parametrize("extra,check", [
    ({"acceptance_criteria": ["Done when " + COPIED]}, "copies 12 or more words"),
    ({"labels": [SECRET.lower()]}, "secret-shaped"),
    ({"links": ["https://ci.acme.example/run?token=" + SECRET]}, "secret-shaped"),
    ({"change_paths": ["backend/" + COPIED.replace(" ", "_") + ".py"]}, "copies 12 or more words"),
    ({"blocker": {"repo": "acme/web", "status": 403, "endpoint": SECRET}}, "secret-shaped"),
    ({"public_title": "Uploads: " + SECRET}, "secret-shaped"),
    ({"public_summary": "Background: " + COPIED}, "copies 12 or more words"),
])
def test_every_text_the_task_keeps_gets_the_words_checks(api, extra, check):
    row = private_work(api)
    attempt = run_on(api)
    correction(api, filed(row["id"], **extra), attempt["token"], check)


def test_the_private_run_attaches_no_file_to_the_new_task(api):
    row = private_work(api)
    attempt = run_on(api)
    made = post(api, "tasks", filed(row["id"]), attempt["token"])
    r = api.post(f"/api/v2/tasks/{made['id']}/files", json={"name": "intake.md", "text": "Intake notes."},
                 headers=headers(attempt["token"]))
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("field", ["title", "body", "acceptance_criteria", "labels", "public_title", "public_summary"])
def test_a_private_tasks_title_anywhere_in_the_text_is_a_correction(api, field):
    row = private_work(api)
    carried = private_work(api, "Ops Lockfile_Scan")
    attempt = run_on(api)
    carry(api, attempt, carried)
    for name in ("ZIP TRIAGE", "ops lockfile scan"):
        value = f"Follow up on {name} first."
        extra = {field: [value] if field in ("acceptance_criteria", "labels") else value}
        correction(api, filed(row["id"], **extra), attempt["token"], "names a private task by its title")
    # A word of a title on its own is not the title.
    assert post(api, "tasks", filed(row["id"], body=REPAIR["body"] + " Triage it first."), attempt["token"])


def _rows(api):
    with api.app.state.store.read() as c:
        return [c.execute(sql).fetchone()[0] for sql in (
            "SELECT count(*) FROM tasks", "SELECT count(*) FROM task_roles", "SELECT count(*) FROM tags",
            "SELECT count(*) FROM events WHERE action IN ('task.create','task.roles')",
            "SELECT count(*) FROM refusals")]


# A role's name is checked as sent and as stored (lower-cased): each of these secret shapes is caught by one side only.
@pytest.mark.parametrize("under", [True, False])
@pytest.mark.parametrize("name,check", [
    ("ZIP_Triage", "names a private task by its title"),
    (" Ops-Lockfile_scan ", "names a private task by its title"),
    ("XOXB-1234567890-ABCDEFABCDEF", "secret-shaped"),      # secret-shaped once lower-cased
    ("AKIAABCDEFGHIJ123456", "secret-shaped"),              # secret-shaped as sent
])
def test_a_role_name_gets_the_words_checks_as_sent_and_as_stored(api, under, name, check):
    row = private_work(api)
    carried = private_work(api, "Ops Lockfile_Scan")
    attempt = run_on(api)
    carry(api, attempt, carried)
    before = _rows(api)
    correction(api, filed(row["id"] if under else None, roles={name: ["doc-updater"]}), attempt["token"], check)
    assert _rows(api) == before


@pytest.mark.parametrize("under", [True, False])
def test_a_plain_role_still_files(api, under):
    row = private_work(api)
    attempt = run_on(api)
    body = filed(row["id"] if under else None, roles={"Reviewer": ["doc-updater"]})
    assert not dry_run(api, body, attempt["token"])
    made = post(api, "tasks", body, attempt["token"])
    assert get(api, "tasks/" + made["id"], bot_token(api, "finance"))["task"]["roles"] == {"reviewer": ["bot:doc-updater"]}
