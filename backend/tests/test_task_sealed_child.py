"""A private task with no person on it hands a repair to another bot in a sealed private subtask (Dev 632)."""
import json

import pytest

from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_task_guest import dry_run, refused, run_on, subtask, task
from backend.tests.test_tasks_board import bot_token

REPAIR = {"title": "Fix the upload refusal order", "body": "Check the chat setting before writing the upload bytes; "
          "add a test that the store is never called on a refused upload."}
HEAD = "a2944b52a971f509b51b81d40db92c0207cab734"
ROUTE = "ask them with `hub task question`, or have them file the subtask"


@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def repair(parent, owner="finance", **extra):
    return {**subtask(parent, owner), **REPAIR, **extra}


def carry(api, attempt, row):
    """The run also carries `row`, as when its prompt brought that task in."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (attempt["id"], row["id"]))


def run_for(api, bot):
    """A run of `bot` on its own computer, on the oldest work waiting for it (a chat when nothing is)."""
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    with api.app.state.store.read() as c:
        waiting = c.execute("SELECT 1 FROM jobs WHERE bot=? AND state='queued'", (bot,)).fetchone()
    if not waiting:
        post(api, "chat/" + bot, {"text": "Please look at your queue."})
    return claim(api, machine, bot)


def sees(api, token, words):
    r = api.post("/api/v2/sql", json={"sql": "SELECT id,title,body FROM tasks"}, headers=headers(token))
    assert r.status_code == 200, r.text
    return words in r.text


def test_the_owning_bots_run_hands_a_sealed_subtask_to_a_bot_that_reads_only_that(api):
    above = task(api, "bot:cpo", "bot:cpo", "Zip advisory intake")
    row = task(api, "bot:cpo", "bot:ops", "Zip triage", parent=above["id"])
    sibling = task(api, "bot:ops", "bot:ops", "Zip lockfile scan", parent=row["id"])
    attempt = run_on(api)
    body = repair(row["id"])
    assert not [p for p in dry_run(api, body, attempt["token"]) if "rivate" in p]
    child = post(api, "tasks", body, attempt["token"])
    assert child["private"] and child["owner"] == "bot:finance" and child["requester"] == "bot:ops"

    # The Builder reads its subtask, and nothing of the parent: no title, body, id or link.
    finance = bot_token(api, "finance")
    shown = get(api, "tasks/" + child["id"], finance)
    text = json.dumps(shown)
    assert "Zip triage" not in text and row["id"] not in text and "Confidential" not in text
    assert not shown["task"]["relations"].get("parent")
    # The parent, the task above it and its other subtasks are not found for it.
    for hidden in (row, above, sibling):
        assert api.get("/api/v2/tasks/" + hidden["id"], headers=headers(finance)).status_code in (403, 404)
        assert not sees(api, finance, f"Confidential: {hidden['title']}.")

    # The wake notice it is sent says nothing of the parent either.
    with api.app.state.store.read() as c:
        notices = [dict(m) for m in c.execute(
            "SELECT m.body,m.refs FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot='finance'")]
    assert notices and all("Zip triage" not in m["body"] and row["id"] not in (m["body"] + (m["refs"] or ""))
                           for m in notices), notices

    # History: the parent says who it went to; the subtask says who filed it, without the parent's title.
    parent_events = get(api, "tasks/" + row["id"], attempt["token"])["events"]
    assert any(e["field"] == "handed" and e["note"] == "Handed to finance in a private subtask"
               and e["actor"] == "bot:ops" for e in parent_events)
    child_events = get(api, "tasks/" + child["id"], finance)["events"]
    assert any(e["field"] == "sealed" and e["new"] == "bot:ops" for e in child_events)
    assert "Zip triage" not in json.dumps(child_events)

    # The private run itself still writes nothing more to it: a comment could carry the parent's words.
    r = api.post(f"/api/v2/tasks/{child['id']}/comments", json={"text": "Also see the triage notes."},
                 headers=headers(attempt["token"]))
    assert r.status_code == 403, r.text


def test_the_builders_review_subtask_under_the_sealed_one_follows_the_same_rule(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)
    child = post(api, "tasks", repair(row["id"]), attempt["token"])
    built = run_for(api, "finance")
    review = post(api, "tasks", repair(child["id"], "doc-updater", title="Review the upload refusal fix",
                                       body="Review the pull request for the upload order fix."), built["token"])
    assert review["private"] and review["owner"] == "bot:doc-updater"
    reviewer = bot_token(api, "doc-updater")
    assert get(api, "tasks/" + review["id"], reviewer)["task"]["id"] == review["id"]
    for hidden in (row, child):
        assert api.get("/api/v2/tasks/" + hidden["id"], headers=headers(reviewer)).status_code in (403, 404)


def test_a_review_subtask_may_name_the_exact_head_and_its_commit_links(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)
    child = post(api, "tasks", repair(row["id"]), attempt["token"])
    built = run_for(api, "finance")
    body = repair(child["id"], "doc-updater", title="Review the upload order fix at its exact head", body=(
        f"Review PR #1714 at head `{HEAD}`, base {'789aedce94db5d20f7d263f5b4873cb6521d0d4b'}.\n"
        f"https://github.com/acme/acme-services/commit/{HEAD}\n"
        f"https://github.com/acme/acme-services/pull/1714/commits/{HEAD}\n"
        "Branch tico/c0642a6a-private-bot-work-can-hand-a-repair-to-a-builder-in, session "
        "tico-session/3c5c9932-7767-49bb-99a5-fb346c21c624, notes in reports/2026-10-27-pr-1714-review-notes.md.\n"
        f"End with VERDICT: approve PR #1714 head {HEAD}"))
    assert not [p for p in dry_run(api, body, built["token"]) if "secret-shaped" in p]
    assert post(api, "tasks", body, built["token"])["owner"] == "bot:doc-updater"


def test_done_wakes_the_parents_owner_and_no_words_reach_its_requester(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)
    child = post(api, "tasks", repair(row["id"]), attempt["token"])
    finance = bot_token(api, "finance")
    current = get(api, "tasks/" + child["id"], finance)["task"]
    post(api, "tasks/" + child["id"], {"version": current["version"], "status": "done",
                                       "note": "Fixed: checked the setting first, test added."}, finance)
    with api.app.state.store.read() as c:
        jobs = {bot: [json.loads(r[1] or "{}") | {"body": r[0]} for r in c.execute(
            "SELECT m.body,m.refs FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot=?", (bot,))]
            for bot in ("ops", "cpo")}
        assert not H.subtask_flows_up(c, H.task(c, child["id"]), H.task(c, row["id"]))
    assert any(r.get("wake") == "subtask_finished" and r["task"] == row["id"] and r.get("trigger_source") == child["id"]
               for r in jobs["ops"]), jobs["ops"]
    assert not any("checked the setting first" in r["body"] for r in jobs["cpo"]), jobs["cpo"]


@pytest.mark.parametrize("on", ["requester", "guest"])
def test_a_private_task_with_a_person_on_it_names_them_and_the_route(api, on):
    if on == "requester":
        row = task(api, "human:cara", "bot:ops", "Cara's vendor dispute")
    else:
        row = task(api, "bot:cpo", "bot:ops", "Zip triage")
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE tasks SET guest='human:cara' WHERE id=?", (row["id"],))
    attempt = run_on(api)
    body = repair(row["id"])
    words = refused(api, body, attempt["token"])
    assert words == f"Cara is on this private task: {ROUTE}" or words.endswith(ROUTE) and "Cara" in words, words
    assert words in dry_run(api, body, attempt["token"])
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE owner='bot:finance'").fetchone()


def test_a_person_above_the_parent_is_named_only_to_a_filer_that_reads_that_task(api):
    above = task(api, "human:cara", "bot:cpo", "Cara's vendor dispute")
    row = task(api, "bot:cpo", "bot:ops", "Dispute triage", parent=above["id"])
    attempt = run_on(api)
    body = repair(row["id"])
    words = refused(api, body, attempt["token"])
    assert words == "A person is on a private task above this one: ask bot:cpo, who gave you this task, to file the " \
                    "subtask", words
    assert "Cara" not in words and words in dry_run(api, body, attempt["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner='bot:ops' WHERE id=?", (above["id"],))
    words = refused(api, body, attempt["token"])
    assert words == f"Cara is on a private task above this one: {ROUTE}", words
    assert words in dry_run(api, body, attempt["token"])


def test_a_run_also_carrying_a_persons_task_the_bot_cannot_read_is_refused_naming_them(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    theirs = task(api, "human:cara", "bot:ops", "Cara's vendor dispute")
    attempt = run_on(api)
    carry(api, attempt, theirs)
    body = repair(row["id"])
    words = refused(api, body, attempt["token"])
    assert "Cara" in words and words.endswith(ROUTE), words
    assert words in dry_run(api, body, attempt["token"])


def test_a_bot_that_neither_owns_the_parent_nor_manages_its_owner_is_refused_as_before(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_for(api, "cpo")
    carry(api, attempt, row)
    words = refused(api, repair(row["id"]), attempt["token"])
    assert words == "Private task content stays with its requester and current owner", words


def test_the_sealed_subtask_is_private_and_a_company_visible_one_is_refused(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)
    words = refused(api, repair(row["id"], private=False), attempt["token"])
    assert words.startswith("Private task work cannot create company-visible tasks") and "--from-private" in words
    assert post(api, "tasks", repair(row["id"]), attempt["token"])["private"]


@pytest.mark.parametrize("words,check", [
    ("Confidential: Zip triage. The Gateway key for the vendor is stored in the shared vault under vendor ops "
     "and must be rotated", "copies 12 or more words"),
    ("Use token ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8 to push the fix", "secret-shaped"),
    ("Use the vendor key 9f2c4e" + "6a8b0d1f3e5c7a9b2d4f6e8a0c1b3d5f7e9a to push the fix", "secret-shaped"),
    ("Review at head " + HEAD + " with token ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8", "secret-shaped"),
    ("The vendor key is at 9f2c4e6a8b0d" + "1f3e5c7a9b2d4f6e8a0c1b3d5f7e, rotate it", "secret-shaped"),
    ("Review at head " + HEAD + "9f2c4e6a8b0d1f3e5c7a9b2d", "secret-shaped"),
    ("Push with keys/a1B2c3D4e5F6g7H8i9J0" + "k1L2m3N4o5P6q7R8s9/main", "secret-shaped"),
    ("Key wJalrXUtnFEMIxKqZ-" + "2938475610293847561 rotate", "secret-shaped"),
    ("token wJalrXUtnFEMIxKqZkWvBn/" + "7392018465827361", "secret-shaped"),
    ("See https://github.com/acme/" + "9f2c4e6a8b0d1f3e5c7a9b2d4f6e8a0c1b3d5f7e", "secret-shaped"),
    # A long opaque value split by one separator into pieces shorter than 32 is not a workflow reference.
    *[(f"Use the vendor key Aa0Bb1Cc2Dd3Ee4Ff5Gg6Hh7{sep}Ii8Jj9Kk0" + "Ll1Mm2Nn3Oo4Pp5 to investigate the upload refusal.",
       "secret-shaped") for sep in "/_-"],
    ("Push with vendor/9f2c4e6a8b0d-1f3e5c7a" + "9b2d-4f6e8a0c1b3d", "secret-shaped"),
    ("The vendor PIN is 9f2c4e6a8b0d-1234-5678" + "-9012-3456-7890", "secret-shaped"),
])
def test_the_sealed_subtasks_words_may_not_carry_the_private_work(api, words, check):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET body=? WHERE id=?", (
            "Confidential: Zip triage. The Gateway key for the vendor is stored in the shared vault under vendor ops "
            "and must be rotated today.", row["id"]))
    attempt = run_on(api)
    body = repair(row["id"], body=words)
    r = api.post("/api/v2/tasks", json=body, headers=headers(attempt["token"]))
    assert r.status_code == 422 and check in r.text, r.text
    assert "Gateway key" not in r.json()["error"]["detail"] and "ghp_" not in r.json()["error"]["detail"]
    assert any(check in p for p in dry_run(api, body, attempt["token"]))
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM refusals WHERE actor='bot:ops'").fetchone()
        assert not c.execute("SELECT 1 FROM tasks WHERE owner='bot:finance'").fetchone()


def test_a_bot_that_reads_the_parent_gets_a_subtask_kept_from_the_runs_other_person_free_work(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    other = task(api, "bot:ops", "bot:ops", "Ops lockfile scan")
    attempt = run_on(api)
    carry(api, attempt, other)
    child = post(api, "tasks", repair(row["id"], "cpo"), attempt["token"])
    assert child["private"] and child["owner"] == "bot:cpo"
    assert not sees(api, bot_token(api, "cpo"), "Confidential: Ops lockfile scan.")
    events = get(api, "tasks/" + row["id"], attempt["token"])["events"]
    assert any(e["field"] == "handed" and e["new"] == "bot:cpo" for e in events)
