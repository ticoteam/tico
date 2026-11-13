"""Who can see a private task: its reader list (people, bots, groups) through the API, the CLI and MCP, and the one
person a private task with no person on it reaches through its owning bot's hand-off (Dev 172)."""

import json

import pytest

from backend import task_relations as TR
from backend.store import H
from backend.tests.helpers.api import get, headers, post, put
from backend.tests.helpers.tasks_board import bot_token
from backend.tests.helpers.task_readers import dry_run, refused, run_on, subtask, task


@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.helpers.api import unrelated_task_managers

    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def readers(api, tid):
    return {
        person
        for person, token in (("ana", "ana-test"), ("ben", "ben-test"), ("cara", "cara-test"))
        if api.get("/api/v2/tasks/" + tid, headers=headers(token)).status_code == 200
    }


def seen(api, row, attempt):
    """The version of the task the run was shown: a bot's write carries it."""
    return get(api, "tasks/" + row["id"], attempt["token"])["task"]["version"]


def sql_sees(api, token, words):
    r = api.post("/api/v2/sql", json={"sql": "SELECT id,title,body FROM tasks"}, headers=headers(token))
    assert r.status_code == 200, r.text
    return words in r.text


@pytest.mark.parametrize("requester", ["bot:cpo", "bot:ops"])  # another bot's task, and a bot's task to itself
def test_private_bot_task_reaches_one_named_person_and_nobody_else(api, requester):
    above = task(api, "bot:cpo", "bot:cpo", "Zip advisory intake")
    row = task(api, requester, "bot:ops", "Zip triage", parent=above["id"])
    sibling = task(api, "bot:ops", "bot:ops", "Zip lockfile scan", parent=row["id"])
    attempt = run_on(api)
    assert readers(api, row["id"]) == set()
    body = subtask(row["id"], "ben")

    # Dry-run says what the create does: no privacy refusal, then the create goes through.
    assert not [p for p in dry_run(api, body, attempt["token"]) if "rivate" in p]
    child = post(api, "tasks", body, attempt["token"])
    assert child["private"] and child["owner_id"] == "human:ben" and child["requester_id"] == "bot:ops"

    # Ben, and only Ben, now reads the task and the subtask; its history says who it reached.
    assert get(api, "tasks/" + row["id"], "ben-test")["task"]["readers"] == ["human:ben"]
    assert readers(api, row["id"]) == {"ben"} and readers(api, child["id"]) == {"ben"}
    assert row["id"] in {t["id"] for t in get(api, "tasks", "ben-test")["tasks"]}
    assert row["id"] not in {t["id"] for t in get(api, "tasks", "cara-test")["tasks"]}
    assert sql_sees(api, "ben-test", "Confidential: Zip triage.")
    assert not sql_sees(api, "cara-test", "Confidential: Zip triage.")
    assert not sql_sees(api, "ana-test", "Confidential: Zip triage.")
    events = get(api, "tasks/" + row["id"], "ben-test")["events"]
    assert any(e["field"] == "reader" and e["new"] == "human:ben" and e["actor"] == "bot:ops" for e in events)

    # Ben reads that one task: not the task above it, nor its other subtasks.
    assert readers(api, above["id"]) == set() and readers(api, sibling["id"]) == set()
    assert not sql_sees(api, "ben-test", "Confidential: Zip advisory intake.")
    assert not sql_sees(api, "ben-test", "Confidential: Zip lockfile scan.")

    # A second, different person is refused, by the create and its dry-run in the same words.
    other = subtask(row["id"], "cara", title="Approve the zip triage plan again")
    words = refused(api, other, attempt["token"])
    assert "already reached Ben" in words
    assert words in dry_run(api, other, attempt["token"])
    assert readers(api, row["id"]) == {"ben"}


def test_the_readers_answer_comes_back_to_the_task_and_nowhere_wider(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)
    child = post(api, "tasks", subtask(row["id"], "ben"), attempt["token"])
    done = post(
        api,
        "tasks/" + child["id"] + "/done",
        {"version": child["version"], "note": "Approved: go with the plan."},
        "ben-test",
    )
    assert (done.get("task") or done)["status"] == "done"
    # Ben's answer wakes the bot that owns the task, which can act on its task with it: its turn on the task again.
    with api.app.state.store.read() as c:
        queued = c.execute(
            "SELECT m.refs FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='ops' AND j.state='queued'"
        ).fetchall()
    assert any(
        json.loads(r[0] or "{}").get("task") == row["id"] and json.loads(r[0] or "{}").get("wake") == "task_turn"
        for r in queued
    ), queued
    answer = run_on(api)
    post(api, f"tasks/{row['id']}/comments", {"text": "Ben approved; going ahead."}, answer["token"])
    # ...but not past the task's own readers.
    r = api.post(
        "/api/v2/messages",
        json={"to": "cara", "text": "Ben approved.", "refs": {"task": row["id"]}},
        headers=headers(answer["token"]),
    )
    assert r.status_code == 403, r.text


def test_refused_hand_offs_match_their_dry_run(api):
    # A person's private task above: its work cannot reach someone who cannot read it.
    above = task(api, "human:cara", "bot:cpo", "Cara's vendor dispute")
    row = task(api, "bot:cpo", "bot:ops", "Dispute triage", parent=above["id"])
    attempt = run_on(api)
    body = subtask(row["id"], "ben")
    words = refused(api, body, attempt["token"])
    assert words in dry_run(api, body, attempt["token"])
    assert readers(api, row["id"]) == set()
    with api.app.state.store.read() as c:
        assert not H.task_readers(H.task(c, row["id"]))


def test_only_the_owning_bot_brings_a_person_in(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    child = post(api, "tasks", subtask(row["id"], "ben"), bot_token(api, "cpo"))
    assert child["owner_id"] == "human:ben"
    # The requester bot's hand-off goes on as before (the subtask alone), and makes nobody a reader.
    assert readers(api, row["id"]) == set()


def test_an_ask_reaches_only_a_reader_and_reassignment_keeps_the_readers(api):
    """An ask on a private task makes nobody a reader (task_engine.not_shared): the owning bot's sub-task brings the
    one person in (Dev 172), an ask then reaches them, and an ask of anyone else is refused with the fix."""
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)

    def ask(to):
        return api.post(
            f"/api/v2/tasks/{row['id']}/asks",
            json={"to": to, "line": "Approve the plan?", "choices": ["Yes", "No"], "version": seen(api, row, attempt)},
            headers=headers(attempt["token"]),
        )

    r = ask("ben")
    assert r.status_code == 422 and r.json()["error"]["code"] == "private" and "Ben can't see" in r.text, r.text
    assert "Ask " in r.text and "Who can see" not in r.text and readers(api, row["id"]) == set()
    card = post(api, "tasks", subtask(row["id"], "ben"), attempt["token"])
    assert card["private"] and card["requester_id"] == "bot:ops"
    assert readers(api, row["id"]) == {"ben"} and readers(api, card["id"]) == {"ben"}
    r = ask("ben")
    assert r.status_code == 200, r.text
    r = ask("cara")
    assert r.status_code == 422 and r.json()["error"]["code"] == "private" and "Cara can't see" in r.text, r.text
    assert readers(api, row["id"]) == {"ben"}
    with api.app.state.store.transaction() as c:
        H.task_reassign(c, "bot:cpo", row["id"], "bot:cpo", "Cpo takes it back", H.task(c, row["id"])["version"])
    assert readers(api, row["id"]) == {"ben"}


def test_a_reader_who_cannot_read_the_bots_opens_that_task_and_no_other_work_of_theirs(api):
    for slug in ("cpo", "ops"):
        current = get(api, f"bots/{slug}/access", "ana-test")
        put(
            api,
            f"bots/{slug}/access",
            {
                "see": {"everyone": True},
                "read": {"people": ["ana", "ben"]},
                "write": {"everyone": True},
                "revision": current["revision"],
            },
        )
    other = post(api, "tasks", {"owner": "ops", "title": "Ops weekly sweep", "body": "Routine."})
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)
    post(api, "tasks", subtask(row["id"], "cara"), attempt["token"])
    # Cara opens the task she was brought into, as its list and SQL already show it to her...
    assert readers(api, row["id"]) == {"cara"} and sql_sees(api, "cara-test", "Confidential: Zip triage.")
    assert api.get(f"/api/v2/tasks/{row['id']}/comments", headers=headers("cara-test")).status_code == 200
    # ...and nothing else of the bots she may not read.
    assert api.get("/api/v2/tasks/" + other["id"], headers=headers("cara-test")).status_code == 403


def test_the_readers_answer_clears_the_wait(api):
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)
    card = post(api, "tasks", subtask(row["id"], "ben"), attempt["token"])
    from backend.waiting_on import waiting_on

    with api.app.state.store.read() as c:
        assert [(w.state, w.who) for w in waiting_on(c, row["id"])][:1] == [("tasks", card["id"])]
    post(api, "tasks/" + card["id"] + "/done", {"version": card["version"], "note": "Approved."}, "ben-test")
    with api.app.state.store.read() as c:
        assert card["id"] not in {w.who for w in waiting_on(c, row["id"])}
        assert H.task(c, row["id"])["status"] == "open"


def test_a_run_carrying_work_the_person_cannot_read_does_not_reach_them(api):
    from backend.tests.helpers.task_sealed_child import carry

    own = task(api, "bot:ops", "bot:ops", "Zip triage")
    theirs = task(api, "human:cara", "bot:ops", "Cara's vendor dispute")
    attempt = run_on(api)
    carry(api, attempt, theirs)
    body = subtask(own["id"], "ben", body="Ben, approve the vendor dispute reply? If yes, I will send it.")
    r = api.post("/api/v2/tasks", json=body, headers=headers(attempt["token"]))
    assert r.status_code == 403, r.text
    r = api.post(
        f"/api/v2/tasks/{own['id']}/asks",
        json={"to": "ben", "line": "Send the reply?", "choices": ["Yes", "No"], "version": seen(api, own, attempt)},
        headers=headers(attempt["token"]),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "private", r.text
    # Nothing reached Ben: no reader, no task of his, and no ask on the task.
    with api.app.state.store.read() as c:
        assert not H.task_readers(H.task(c, own["id"]))
        assert not c.execute(
            "SELECT 1 FROM requirements r JOIN visits v ON v.id=r.visit_id WHERE v.task_id=? AND r.person_id='human:ben'",
            (own["id"],),
        ).fetchone()
    assert readers(api, own["id"]) == set() and not get(api, "tasks", "ben-test")["tasks"]
    # Once Ben reads every task the run carries, the same sub-task reaches him.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET requester_id='human:ben' WHERE id=?", (theirs["id"],))
    post(api, "tasks", body, attempt["token"])
    assert readers(api, own["id"]) == {"ben"}


def test_a_question_reaches_one_reader(api):
    """A question is an ask on the task: it goes only to someone who can read it already, and makes nobody a reader."""
    row = task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)

    def ask(to):
        return api.post(
            f"/api/v2/tasks/{row['id']}/asks",
            json={
                "to": to,
                "line": "Ship the zip fix in the next release?",
                "choices": ["Yes", "No"],
                "version": seen(api, row, attempt),
            },
            headers=headers(attempt["token"]),
        )

    r = ask("ben")
    assert r.status_code == 422 and r.json()["error"]["code"] == "private" and "Ben can't see" in r.text, r.text
    assert readers(api, row["id"]) == set() and not get(api, "tasks", "ben-test")["tasks"]
    assert change(api, row["id"], {"add": ["ben"]}, attempt["token"]).status_code == 200
    asked = ask("ben")
    assert asked.status_code == 200, asked.text
    assert readers(api, row["id"]) == {"ben"}
    r = ask("cara")
    assert r.status_code == 422 and r.json()["error"]["code"] == "private" and "Cara can't see" in r.text, r.text
    assert readers(api, row["id"]) == {"ben"}


def who_sees(api, tid, token="ana-test"):
    return get(api, f"tasks/{tid}/readers", token)


def change(api, tid, body, token="ana-test", method="post"):
    return getattr(api, method)(f"/api/v2/tasks/{tid}/readers", json=body, headers=headers(token))


def test_who_can_see_add_remove_replace_and_history(api):
    row = task(api, "human:ana", "bot:ops", "Payroll review")
    assert who_sees(api, row["id"]) == {
        "private": True,
        "requester_id": "human:ana",
        "owner_id": "bot:ops",
        "readers": [],
    }
    assert readers(api, row["id"]) == {"ana"}
    r = change(api, row["id"], {"add": ["ben"]})
    assert r.status_code == 200 and r.json()["readers"] == ["human:ben"], r.text
    assert readers(api, row["id"]) == {"ana", "ben"} and sql_sees(api, "ben-test", "Confidential: Payroll review.")
    r = change(api, row["id"], {"add": ["cara", "ana"], "remove": ["ben"]})
    assert r.json()["readers"] == ["human:cara"], r.text  # the requester is not listed: she reads it already
    assert readers(api, row["id"]) == {"ana", "cara"} and not sql_sees(api, "ben-test", "Confidential: Payroll")
    r = change(api, row["id"], {"readers": ["ben", "cara"]}, method="put")
    assert r.json()["readers"] == ["human:ben", "human:cara"], r.text
    assert who_sees(api, row["id"], "cara-test")["readers"] == ["human:ben", "human:cara"]
    events = [
        (e["old"], e["new"], e["actor"]) for e in get(api, "tasks/" + row["id"])["events"] if e["field"] == "reader"
    ]
    assert events == [
        (None, "human:ben", "human:ana"),
        (None, "human:cara", "human:ana"),
        ("human:ben", None, "human:ana"),
        (None, "human:ben", "human:ana"),
    ] or sorted(events, key=str) == sorted(
        [
            (None, "human:ben", "human:ana"),
            ("human:ben", None, "human:ana"),
            (None, "human:cara", "human:ana"),
            (None, "human:ben", "human:ana"),
        ],
        key=str,
    ), events
    # A reader who may not change the task does not change who sees it; nobody adds a name that is not on the team.
    r = change(api, row["id"], {"remove": ["ben"]}, "cara-test")
    assert r.status_code == 403, r.text
    r = change(api, row["id"], {"add": ["nobody-here"]})
    assert r.status_code in (404, 422) and "not a person, bot or group" in r.text, r.text
    assert who_sees(api, row["id"])["readers"] == ["human:ben", "human:cara"]
    # Somebody who cannot see it cannot read or change its list.
    with api.app.state.store.transaction() as c:
        H.set_task_readers(c, "human:ana", row["id"], replace=[])
    assert api.get(f"/api/v2/tasks/{row['id']}/readers", headers=headers("ben-test")).status_code in (403, 404)
    assert change(api, row["id"], {"add": ["ben"]}, "ben-test").status_code in (403, 404)


def test_a_company_task_is_public_and_has_no_reader_list(api):
    open_task = post(api, "tasks", {"owner": "ops", "title": "Ops weekly sweep", "body": "Routine."})
    assert who_sees(api, open_task["id"]) == {
        "private": False,
        "requester_id": "human:ana",
        "owner_id": "bot:ops",
        "readers": [],
    }
    r = change(api, open_task["id"], {"add": ["ben"]})
    assert r.status_code == 422 and "make it private to choose who sees it" in r.text, r.text


def test_a_group_reads_through_whoever_is_in_it_now(api):
    gid = post(api, "groups", {"name": "Payroll desk", "add": {"people": ["cara"]}})["id"]
    row = task(api, "human:ana", "bot:ops", "Payroll review")
    assert change(api, row["id"], {"add": ["group:" + gid]}).json()["readers"] == ["group:" + gid]
    assert readers(api, row["id"]) == {"ana", "cara"} and sql_sees(api, "cara-test", "Confidential: Payroll review.")
    r = api.patch(
        "/api/v2/groups/" + gid, json={"add": {"people": ["ben"]}, "remove": {"people": ["cara"]}}, headers=headers()
    )
    assert r.status_code == 200, r.text
    assert readers(api, row["id"]) == {"ana", "ben"}
    assert not sql_sees(api, "cara-test", "Confidential: Payroll review.")
    assert change(api, row["id"], {"add": ["group:no-such-group"]}).status_code in (404, 422)


def test_create_a_private_task_with_readers(api):
    made = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Draft the offer letter",
            "body": "For the new hire.",
            "private": True,
            "readers": ["ben"],
        },
    )
    assert made["private"] and made["readers"] == ["human:ben"]
    assert readers(api, made["id"]) == {"ana", "ben"}
    assert any(e["field"] == "reader" and e["new"] == "human:ben" for e in get(api, "tasks/" + made["id"])["events"])
    r = api.post(
        "/api/v2/tasks",
        json={
            "owner": "ops",
            "title": "Draft the offer letter again",
            "body": "Again.",
            "private": False,
            "readers": ["ben"],
        },
        headers=headers(),
    )
    assert r.status_code == 422 and "make it private" in r.text, r.text


def test_a_bot_adds_readers_within_its_reach_and_never_a_group(api):
    gid = post(api, "groups", {"name": "Payroll desk", "add": {"people": ["cara"]}})["id"]
    row = task(api, "human:ana", "bot:ops", "Payroll review")
    token = bot_token(api)
    r = change(api, row["id"], {"add": ["group:" + gid]}, token)
    assert r.status_code == 403 and "Only a person adds a group to Who can see" in r.text, r.text
    r = api.put("/api/v2/reach/settings/bot:ops", json={"mode": "closed"}, headers=headers())
    assert r.status_code == 200, r.text
    r = change(api, row["id"], {"add": ["cara"]}, token)
    assert r.status_code == 409 and r.json()["error"]["code"] == "reach_closed", r.text
    r = change(api, row["id"], {"readers": ["cara"]}, token, method="put")
    assert r.status_code == 409, r.text
    # A person adds both; the bot may still take a reader off.
    assert change(api, row["id"], {"add": ["cara", "group:" + gid]}).status_code == 200
    r = change(api, row["id"], {"remove": ["cara"]}, token)
    assert r.status_code == 200 and r.json()["readers"] == ["group:" + gid], r.text


def test_a_private_run_adds_only_readers_who_read_what_it_carries(api):
    own = task(api, "bot:ops", "bot:ops", "Zip triage")
    theirs = task(api, "human:cara", "bot:ops", "Cara's vendor dispute")
    attempt = run_on(api)
    from backend.tests.helpers.task_sealed_child import carry

    carry(api, attempt, theirs)
    r = change(api, own["id"], {"add": ["ben"]}, attempt["token"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "privacy", r.text
    words = r.json()["error"]["detail"]
    problems = dry_run(
        api,
        {"owner": "ops", "title": "Zip triage follow-up", "body": "Next step.", "private": True, "readers": ["ben"]},
        attempt["token"],
    )
    assert words in problems, problems
    with api.app.state.store.read() as c:
        assert not H.task_readers(H.task(c, own["id"]))
    # Once Ben reads every task the run carries, the same add goes through.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET requester_id='human:ben' WHERE id=?", (theirs["id"],))
    r = change(api, own["id"], {"add": ["ben"]}, attempt["token"])
    assert r.status_code == 200 and r.json()["readers"] == ["human:ben"], r.text


def test_a_task_made_public_then_private_again_has_no_readers(api):
    row = task(api, "human:ana", "bot:ops", "Payroll review")
    change(api, row["id"], {"add": ["ben", "cara"]})
    current = get(api, "tasks/" + row["id"])["task"]
    post(api, "tasks/" + row["id"], {"version": current["version"], "private": False})
    assert who_sees(api, row["id"])["readers"] == [] and get(api, "tasks/" + row["id"])["task"]["readers"] == []
    removed = {
        e["old"] for e in get(api, "tasks/" + row["id"])["events"] if e["field"] == "reader" and e["new"] is None
    }
    assert removed == {"human:ben", "human:cara"}
    current = get(api, "tasks/" + row["id"])["task"]
    post(api, "tasks/" + row["id"], {"version": current["version"], "private": True})
    assert who_sees(api, row["id"])["readers"] == [] and readers(api, row["id"]) == {"ana"}
    # A company task lists nobody, even one with an old reader list.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=0, readers=json_array('human:ben') WHERE id=?", (row["id"],))
    assert get(api, "tasks/" + row["id"])["task"]["readers"] == [] and who_sees(api, row["id"])["readers"] == []


def test_a_reader_who_becomes_owner_stays_listed(api):
    row = task(api, "human:ana", "bot:ops", "Payroll review")
    change(api, row["id"], {"add": ["ben"]})
    post(api, f"tasks/{row['id']}/owner", {"owner": "ben", "why": "Ben does the review"})
    assert change(api, row["id"], {"add": ["cara"]}).json()["readers"] == ["human:ben", "human:cara"]
    assert change(api, row["id"], {"readers": ["ben"]}, method="put").json()["readers"] == ["human:ben"]
    post(api, f"tasks/{row['id']}/owner", {"owner": "ops", "why": "Ops takes it back"})
    assert readers(api, row["id"]) == {"ana", "ben"}
