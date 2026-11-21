"""The person's own Assistant acts as them on any write they could make, citing the message that started its turn
(backend/assistant.py `admits_on_turn`, backend/app.py request_guard). The NEVER list, the 30-minute window, the member's
ceiling, the owner's switch, and approvals that only the person decides stay as they were."""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import assistant
from backend import bot_settings as BS
from backend import hubdb as H
from backend.store import Problem
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import as_member, get, headers, post, setup_attempt  # noqa: F401
from backend.tests.helpers.assistant import assistant_turn, say
from backend.tests.helpers.automation_access import flow
from clients import hubtools


def set_rule(api, **rules):
    assert api.put("/api/v2/access/rules", json=rules, headers=headers()).status_code == 200


def test_the_turn_closes_a_task_the_person_owns_as_them_on_the_cite(api):
    """A task a bot asked the person to do: the person's Assistant settles it on their own cited words."""
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Send the invoice", "Monday.", "human:ben")["id"]
    r, attempt = assistant_turn(api, "ben-test")
    token = attempt["token"]
    current = get(api, "tasks/" + task, token)
    current = current.get("task", current)
    post(api, f"tasks/{task}/done", {"version": current["version"]}, token=token)
    with api.app.state.store.read() as c:
        assert H.task(c, task)["status"] == "done"
        assert (
            c.execute(
                "SELECT via FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='move' "
                "ORDER BY created_at DESC LIMIT 1",
                (task,),
            ).fetchone()[0]
            == "assistant"
        )


def test_a_bulk_change_on_the_turn_records_each_task_as_the_person_via_assistant(api):
    with api.app.state.store.transaction() as c:
        ids = [H.task_create(c, "bot:ops", f"Follow up {n}", "Pending.", "human:ana")["id"] for n in range(2)]
    r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    # Many tasks at once is the person's own sign-in; the turn closes each through its own route, with its version.
    bulk = {"filter": {"owner": "human:ana", "status": "open"}, "action": "close"}
    post(api, "task-bulk", bulk, token=token, expected=403)
    for task_id in ids:
        current = get(api, "tasks/" + task_id, token)
        current = current.get("task", current)
        post(api, f"tasks/{task_id}/move", {"to": "closed", "version": current["version"]}, token=token)
    with api.app.state.store.read() as c:
        latest = {}  # newest first: keep the first row seen for each task
        for task_id, via in c.execute(
            "SELECT subject_id, via FROM history_events WHERE subject_type='task' AND subject_id IN (?,?) "
            "AND kind='move' ORDER BY created_at DESC",
            ids,
        ).fetchall():
            latest.setdefault(task_id, via)
    assert latest == {ids[0]: "assistant", ids[1]: "assistant"}, latest


def test_an_automation_edit_and_a_team_rule_run_on_the_turn_as_the_person(api):
    aid = post(api, "automations", flow(owner="person:ana"))["id"]
    r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    renamed = api.patch(f"/api/v2/automations/{aid}", json={"name": "Weekly digest"}, headers=headers(token))
    assert renamed.status_code == 200, renamed.text
    rule = {"kind": "external", "text": "Keep a consent record for five years.", "who": {"bots": ["ops"]}}
    # A rule needs its source and quote: the route's own check decides, so a refusal is never the gate's card.
    refused = api.post("/api/v2/rules", json=rule, headers=headers(token))
    assert refused.status_code == 200 or refused.json()["error"]["code"] != "confirm_required", refused.text


def test_the_never_list_stays_the_persons_own_tap(api):
    r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    never = [
        ("POST", "/api/v2/me/tokens", {"name": "Chat token"}),
        ("POST", "/api/v2/service-keys", {"name": "Key"}),
        ("POST", "/api/v2/enrollments", {}),
        ("POST", "/api/v2/access/owner", {"person": "human:ben"}),
        ("POST", "/api/v2/sql", {"query": "SELECT body FROM messages"}),
        ("POST", "/api/v2/support/tickets", {"subject": "Help", "body": "Please"}),
        ("DELETE", "/api/v2/humans/ben", None),
        ("DELETE", "/api/v2/company/server-connection", None),
        ("PUT", "/api/v2/assistant/settings", {"enabled": False}),
    ]
    for method, path, body in never:
        res = api.request(method, path, json=body, headers=headers(token))
        assert res.status_code == 403, (method, path, res.text)
        assert res.json()["error"]["code"] == "confirm_required", (method, path)


def test_a_write_that_carries_a_secret_is_never_admitted_from_a_chat(api):
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Send the invoice", "Monday.", "human:ben")["id"]
    r, attempt = assistant_turn(api, "ben-test")
    token = attempt["token"]
    # A status change is gated; a body that names a secret key is never admitted from a chat, whatever the words.
    leaked = {
        "status": "done",
        "version": get(api, "tasks/" + task, token).get("task", {}).get("version", 1),
        "api_key": "not-for-a-chat",
    }
    res = api.post("/api/v2/tasks/" + task, json=leaked, headers=headers(token))
    assert res.status_code == 403 and res.json()["error"]["code"] == "confirm_required", res.text


def test_a_message_older_than_thirty_minutes_no_longer_lends_its_words(api):
    r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE messages SET created_at=? WHERE id=(SELECT message_id FROM jobs ORDER BY created_at DESC LIMIT 1)",
            (H.shift(H.now(), seconds=-(assistant.INSTRUCTION_TTL_S + 60)),),
        )
    # Archiving a bot is gated (not a task create, which is exempt), so the stale citation leaves only a card.
    revision = post(api, "bots/ops/archive", {"expected_revision": 1}, token=token, expected=403)
    assert revision["error"]["code"] == "confirm_required"


def test_a_run_that_is_not_the_persons_own_chat_lends_nothing(api):
    """The routine or Slack run (not the person's Assistant room) never acts as the person."""
    with api.app.state.store.read() as c:
        who = SimpleNamespace(via="assistant", confirmed=False, attempt_id=None, actor="human:ana")
        with pytest.raises(Problem) as refused:
            assistant.instruction(c, who, "turn")
        assert refused.value.code == "on_behalf_of"


def test_two_peoples_private_work_stays_apart_and_a_private_handoff_is_filed_as_the_person(api):
    """One Assistant serves everyone and acts with the rights of the person its run is for. Ana's own chat neither reads
    nor writes Ben's private task, and her private handoff is filed as Ana: the shared Assistant becomes no reader or
    owner of it, and only Ana's runs read it, as she does."""
    from backend.tests.helpers.api import unrelated_task_managers

    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)  # no org-chart line lets Ana read Ben's work
    ben = post(api, "tasks", {"owner": "ops", "title": "Ben's raise", "body": "Salary.", "private": True}, "ben-test")
    post(api, "tasks", {"owner": "ops", "title": "Ana's offer", "body": "Offer.", "private": True})
    r, attempt = assistant_turn(api, "ana-test", text="Hand the payroll check to Ops, privately")
    token = attempt["token"]
    get(api, "tasks/" + ben["id"], token, expected=404)
    post(api, f"tasks/{ben['id']}/comments", {"text": "Seen it"}, token=token, expected=404)
    assert "Ben's raise" not in json.dumps(get(api, "tasks", token))
    handed = post(
        api, "tasks", {"owner": "ops", "title": "Check payroll", "body": "Mine.", "private": True}, token=token
    )
    assert handed["requester_id"] == "human:ana" and handed["owner_id"] == "bot:ops" and handed["readers"] == []
    with api.app.state.store.read() as c:
        row = H.task(c, handed["id"])
        assert not H.task_private_readable(c, "bot:assistant", row) and H.task_private_readable(c, "human:ana", row)
    get(api, "tasks/" + handed["id"], token)  # her run reads what she reads, by id and in her lists
    assert handed["id"] in json.dumps(get(api, "tasks", token))
    get(api, "tasks/" + handed["id"], "ben-test", expected=404)
    # In her own chat it reads what she reads, by her own rule: a private task Cara shared with her too.
    shared = post(
        api,
        "tasks",
        {"owner": "ops", "title": "Desk offer", "body": "Ours.", "private": True, "readers": ["human:ana"]},
        token="cara-test",
    )
    company = post(api, "tasks", {"owner": "ops", "title": "Tidy the wiki", "body": "For all.", "private": False})
    get(api, "tasks/" + shared["id"], token)
    assert shared["id"] in json.dumps(get(api, "tasks", token))
    # Ben's own chat does not, and once Ben may not sign in, his run reads nothing at all, company work included.
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "thread-ana"}, token=r["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "text": "Done", "last_seq": 0},
        token=r["token"],
    )
    say(api, "What's on my plate?", "ben-test")
    bens = post(api, "jobs/claim", {}, token=r["token"])["attempt"]
    get(api, "tasks/" + shared["id"], bens["token"], expected=404)
    get(api, "tasks/" + company["id"], bens["token"])
    with api.app.state.store.transaction() as c:
        doc = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for person in doc["people"]:
            if person.get("id") == "ben":
                person["sign_in"] = False
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(doc),))
    get(api, "tasks/" + company["id"], bens["token"], expected=404)
    assert get(api, "tasks", bens["token"])["tasks"] == []


def test_a_task_read_in_the_persons_own_chat_shows_what_they_read_under_it(api):
    """The task's own view reads what it names (its subtasks, what it waits on) as the request reads: in Ana's own chat,
    by her rights, so her private subtask shows; Ben's chat sees "a private task" no more than he does."""
    parent = post(api, "tasks", {"owner": "ops", "title": "Launch", "body": "Plan.", "private": False})
    with api.app.state.store.transaction() as c:
        child = H.task_create(c, "human:ana", "Launch budget", "Mine.", "bot:ops", parent_id=parent["id"], private=True)
        handed = H.task_create(c, "human:ana", "Launch venue", "Mine.", "bot:ops", private=True)
        c.execute(  # the launch handed this on (task_calls: the tasks it filed)
            "INSERT INTO links(id,from_task_id,to_task_id,kind,created_at) VALUES(?,?,?,'handoff',?)",
            (H.new_id(), parent["id"], handed["id"], H.now()),
        )
    r, attempt = assistant_turn(api, "ana-test", text="What's under the launch?")
    shown = get(api, "tasks/" + parent["id"], attempt["token"])
    assert child["id"] in json.dumps(shown["children"]), shown["children"]
    assert handed["id"] in json.dumps(shown["task"]["filed"]), shown["task"]["filed"]
    bens = get(api, "tasks/" + parent["id"], "ben-test")
    assert child["id"] not in json.dumps(bens["children"]) and handed["id"] not in json.dumps(bens["task"]["filed"])


def test_only_the_persons_own_assistant_chat_reads_with_their_rights(api):
    """Person-wide reads are for the person's own chat with the Assistant: a room of theirs with another bot is not."""
    from backend import rooms

    r, attempt = assistant_turn(api, "ana-test", text="What's mine?")
    with api.app.state.store.transaction() as c:
        assert rooms._run_scope(c, attempt["id"]) == ("person", "human:ana")
        c.execute("UPDATE conversations SET room_key='docs' WHERE id=?", (attempt["conversation"]["id"],))
        assert rooms._run_scope(c, attempt["id"]) == ("", "")


def test_an_install_with_its_own_assistant_slug_scopes_that_bots_runs(api, monkeypatch):
    """The shared Assistant is whichever bot the install names (its settings' assistant_bot), not the word "assistant":
    a run of it reads its own routine turn and no other private task it holds, though the bot itself still holds both."""
    from backend import task_privacy as privacy
    from backend.auth import Identity
    from backend.tests.helpers.api import assign, claim, ready, runner

    with api.app.state.store.transaction() as c:
        turns = [
            H.task_create(c, f"human:{p}", "Turn", "Mine.", "bot:ops", private=True)["id"] for p in ("ana", "cara")
        ]
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    attempt = claim(api, machine, "ops")
    mine, other = turns if attempt["task"]["id"] == turns[0] else turns[::-1]
    monkeypatch.setattr(api.app.state.store.settings, "assistant_bot", "ops")
    with api.app.state.store.read() as c:
        principal = privacy.actor(Identity("bot:ops", "bot", attempt_id=attempt["id"]))
        assert all(H.task_private_readable(c, "bot:ops", H.task(c, t)) for t in turns)  # the bot, outside a run
        assert H.task_private_readable(c, principal, H.task(c, mine))
        assert not H.task_private_readable(c, principal, H.task(c, other))


def test_an_ok_asked_of_the_person_reads_as_theirs_in_their_own_chat(api):
    """A bound OK's words are its parties': in Ana's own chat her Assistant reads the OK asked of her, as she does;
    Cara, no party, reads that it waits on a person's OK and no more."""
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Ship the fix", "Soon.", "bot:ops")
        asked = X.ok_ask(c, task["id"], "bot:ops", "human:ana", "OK to ship the fix Friday?")
        c.execute(  # bound to what it lets go (task_asks.ask_ok): its words are its parties'
            "INSERT INTO ok_grants(requirement_id,asked_by_id,asked_of_id,subject,content_hash,content,filed,created_at) "
            "VALUES(?,'bot:ops','human:ana','budget_raise:fix','x','{}',0,?)",
            (asked["requirement_id"], H.now()),
        )
    r, attempt = assistant_turn(api, "ana-test", text="What waits on me?")

    def lines(token):
        return [entry.get("line") for entry in get(api, "tasks/" + task["id"], token)["task"]["to_leave"]]

    assert "OK to ship the fix Friday?" in lines(attempt["token"])
    assert "OK to ship the fix Friday?" not in lines("cara-test")


def sign_in_off(api, person):
    with api.app.state.store.transaction() as c:
        doc = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for entry in doc["people"]:
            if entry.get("id") == person:
                entry["sign_in"] = False
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(doc),))


def test_a_chat_of_someone_who_may_no_longer_sign_in_is_never_handed_out_nor_its_receipts_replayed(api):
    """A run for a person who may no longer sign in reads nothing: their next chat turn is never handed out, and an
    earlier run's acknowledgement receipt is not replayed either."""
    from backend.tests.helpers.api import claim

    r, attempt = assistant_turn(api, "ben-test", text="What's on my plate?")
    ack = headers(r["token"], key="ack-1")
    assert (
        api.post(f"/api/v2/attempts/{attempt['id']}/started", json={"thread_id": "t"}, headers=ack).status_code == 200
    )
    done = {"outcome": "completed", "text": "Done", "last_seq": 0}
    post(api, f"attempts/{attempt['id']}/complete", done, token=r["token"])
    say(api, "Still there?", "ben-test")
    sign_in_off(api, "ben")
    replayed = api.post(f"/api/v2/attempts/{attempt['id']}/started", json={"thread_id": "t"}, headers=ack)
    assert replayed.status_code == 403, replayed.text
    assert claim(api, r) is None


def test_a_page_read_in_the_persons_own_chat_is_checked_against_its_audience_as_them(api):
    """A page's audience is read as the run's person in their own chat (files.version_refusal, as file_visible), and as
    nobody once that person may no longer sign in, never as the Assistant itself."""
    from backend import files
    from backend.auth import Identity

    task = post(api, "tasks", {"owner": "ops", "title": "Pricing", "body": "Draft.", "private": False})
    with api.app.state.store.transaction() as c:
        fid = X.shown(c, "Pricing page", "ops", task["id"])[0].split(":", 1)[1]
        c.execute(
            "INSERT INTO pages(file_id,audience,created_at,updated_at) VALUES(?,?,?,?)",
            (fid, json.dumps({"everyone": False, "people": ["ana"]}), H.now(), H.now()),
        )
    with api.app.state.store.transaction() as c:  # and one with no audience of its own
        open_id = X.shown(c, "Pricing notes", "ops", task["id"])[0].split(":", 1)[1]
        open_page = dict(c.execute("SELECT * FROM bot_files WHERE id=?", (open_id,)).fetchone())
    r, attempt = assistant_turn(api, "ana-test", text="Show me the pricing page")
    auth = api.app.state.auth

    def refusal():
        with api.app.state.store.read() as c:
            who = auth.authenticate({"authorization": "Bearer " + attempt["token"]}, "/api/v2/files/" + fid, "GET")
            row = dict(c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone())
            return files.version_refusal(c, auth, who, row, 1, files.stored_version(c, fid, 1))

    assert refusal() != "audience"
    sign_in_off(api, "ana")
    with api.app.state.store.read() as c:
        run = auth.run_viewer(c, Identity("bot:assistant", "bot", attempt_id=attempt["id"]))
        assert run.actor == "" and run.role == "none"
        assert not files.page_audience_visible(c, auth, run, dict(open_page), {})  # not even a page open to all
    assert refusal() == "audience"


def test_a_private_task_is_never_the_shared_assistants_as_owner_or_reader(api):
    """One Assistant serves everyone, so a private task naming it would be read by its runs for all: refused as owner or
    reader, on create and on a later change, from the person's own chat and from the person directly."""

    def refused(path, body, token="ana-test"):
        answer = api.post("/api/v2/" + path, json=body, headers=headers(token))
        assert answer.status_code >= 400 and "shared Assistant" in answer.json()["error"]["detail"], answer.text

    r, attempt = assistant_turn(api, "ana-test", text="Keep this to yourself")
    for token in (attempt["token"], "ana-test"):
        mine = {"title": "Payroll", "body": "Mine.", "private": True}
        refused("tasks", {**mine, "owner": "assistant"}, token)
        refused("tasks", {**mine, "owner": "ops", "readers": ["bot:assistant"]}, token)
    task = post(api, "tasks", {"owner": "ops", "title": "Payroll", "body": "Mine.", "private": True})
    refused(f"tasks/{task['id']}/readers", {"add": ["assistant"]})
    refused(
        f"tasks/{task['id']}/owner",
        {"owner": "assistant", "version": get(api, "tasks/" + task["id"])["task"]["version"]},
    )
    public = post(api, "tasks", {"owner": "assistant", "title": "Tidy notes", "body": "For all.", "private": False})
    refused("tasks/" + public["id"], {"private": True, "version": get(api, "tasks/" + public["id"])["task"]["version"]})


def test_making_a_task_private_never_carries_down_to_a_task_the_shared_assistant_owns(api):
    """A task's subtasks go private with it; one the shared Assistant owns refuses the whole change, which writes nothing."""
    parent = post(api, "tasks", {"owner": "ops", "title": "Launch", "body": "Plan.", "private": False})
    child = post(
        api,
        "tasks",
        {"owner": "assistant", "title": "Launch notes", "body": "Tidy.", "private": False, "parent": parent["id"]},
    )
    version = get(api, "tasks/" + parent["id"])["task"]["version"]
    answer = api.post("/api/v2/tasks/" + parent["id"], json={"private": True, "version": version}, headers=headers())
    assert answer.status_code >= 400 and "shared Assistant" in answer.json()["error"]["detail"], answer.text
    with api.app.state.store.read() as c:
        assert not H.task_private(c, H.task(c, parent["id"])) and not H.task_private(c, H.task(c, child["id"]))


def test_the_shared_assistant_is_no_requester_or_assignee_and_reads_no_private_task_through_a_group_or_report(api):
    """Refused as a private task's requester or assignee (on create, as it goes private, and on the engine's own writes).
    A group it is in, or a bot that reports to it, lends it no read: those private tasks are filed, and it reads none."""
    from backend import rooms
    from backend import task_engine as E

    def refused(write):
        with pytest.raises(H.Refused, match="shared Assistant"):
            with api.app.state.store.transaction() as c:
                write(c)

    refused(lambda c: H.task_create(c, "bot:assistant", "Pay run", "Mine.", "bot:ops", private=True, lint=False))
    with api.app.state.store.transaction() as c:
        asked = H.task_create(c, "bot:assistant", "Pay run", "Company.", "bot:ops", private=False, lint=False)["id"]
        mine = H.task_create(c, "human:ana", "Offer", "Mine.", "bot:ops", private=True, lint=False)["id"]
    refused(lambda c: H._made_private(c, "human:ana", asked))
    refused(lambda c: E._write(c, mine, assignee_id="bot:assistant"))
    # Built-in bots stay outside groups in the app; one placed there by its configuration reads nothing through it.
    desk = post(api, "groups", {"name": "Desk", "add": {"bots": ["ops"]}})["id"]
    with api.app.state.store.transaction() as c:
        BS.update(c, "assistant", {"team": desk})
        c.execute("UPDATE bot_config SET reports_to='assistant' WHERE bot='finance'")
    grouped = post(
        api,
        "tasks",
        {"owner": "ops", "title": "Desk offer", "body": "Ours.", "private": True, "readers": ["group:" + desk]},
    )
    with api.app.state.store.transaction() as c:
        books = H.task_create(c, "human:ana", "Books", "Mine.", "bot:finance", private=True, lint=False)
    with api.app.state.store.read() as c:
        for row in (H.task(c, grouped["id"]), books):
            assert not H.task_private_readable(c, "bot:assistant", row)
            assert H.task_private_readable(c, "bot:finance" if row["id"] == books["id"] else "bot:ops", row)
        seen = c.execute(
            f"SELECT id FROM tasks WHERE id IN (?,?) AND {api.app.state.auth.task_sql(c, bot_identity('bot:assistant'))}",
            (grouped["id"], books["id"]),
        ).fetchall()
        assert seen == []
        # Its company tasks open a room of their own, never a person's chat with it.
        assert rooms.task_conversation_id(c, api.app.state.auth, "bot:assistant", "human:ana") is None


def bot_identity(actor):
    from backend.auth import Identity

    return Identity(actor, "bot")


def test_archiving_an_agent_never_makes_the_shared_assistant_a_private_tasks_requester(api):
    """Its open tasks' requests go to its successor; a private one, with the shared Assistant as successor, goes to the
    agent's human owner instead (the default person when it has none)."""
    from backend import settings_admin

    with api.app.state.store.transaction() as c:
        mine = H.task_create(c, "bot:finance", "Books", "Private.", "bot:ops", private=True, lint=False)["id"]
        ours = H.task_create(c, "bot:finance", "Notes", "Company.", "bot:ops", private=False, lint=False)["id"]
        settings_admin.archive_bot(c, "human:ana", "finance", successor="assistant")
        assert H.is_human(H.task(c, mine)["requester_id"])
        assert H.task(c, ours)["requester_id"] == "bot:assistant"


def test_a_batch_commit_never_oks_on_an_assistant_walk(api):
    with api.app.state.store.transaction() as c:
        made = H.task_create(c, "human:ana", "Run the LinkedIn ads", "Q4.", "bot:finance", lint=False)
        asked = H.task_ask(
            c,
            "bot:finance",
            made["id"],
            "human:ana",
            "Spend $1,200?",
            kind="ok",
            shows=X.shown(c),
            seen_version=made["version"],
        )
    tid, rid = made["id"], asked["requirement_id"]

    def found():
        with api.app.state.store.read() as c:
            view = H.task_view(c, tid, "human:ana")
        return next(r for r in view["to_leave"] if r["requirement_id"] == rid)

    bid = post(api, "batch", {})["id"]
    r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    post(
        api,
        f"batch/{bid}/respond",
        {"kind": "decide", "decision": "ok", "text": "Go ahead", "heard": "go ahead"},
        token=token,
    )
    done = post(api, f"batch/{bid}/commit", {}, token=token)
    assert done["errors"] and "own" in str(done["errors"]), done["errors"]
    assert not found()["met"] and found()["answer"] is None
    # A send back is the Assistant's to make on a walk: a fresh batch (the committed one is closed).
    bid = post(api, "batch", {}, token=token)["id"]
    post(api, f"batch/{bid}/respond", {"kind": "decide", "decision": "send_back", "text": "No"}, token=token)
    post(api, f"batch/{bid}/commit", {}, token=token)
    assert (found()["met"], found()["answer"]["value"]) == (False, "no")


def test_admits_on_turn_refuses_what_the_person_alone_decides():
    from backend.assistant import admits_on_turn

    assert admits_on_turn("POST", "/api/v2/tasks", {"title": "x"})
    assert admits_on_turn("PATCH", "/api/v2/automations/a1", {"name": "x"})
    assert admits_on_turn("POST", "/api/v2/bots/ops/archive", {"expected_revision": 1})
    assert not admits_on_turn("POST", "/api/v2/me/tokens", {})
    assert not admits_on_turn("POST", "/api/v2/sql", {"query": "select 1"})
    assert not admits_on_turn("DELETE", "/api/v2/humans/ben", None)
    assert not admits_on_turn("PUT", "/api/v2/access/rules", {"assistant_direct": False})
    assert not admits_on_turn("GET", "/api/v2/tasks", None)
    assert not admits_on_turn("POST", "/api/v2/tasks/t1", {"status": "done", "api_key": "x"})


def test_an_outward_step_is_a_card_on_create_and_on_change_but_a_name_is_not(api):
    """A webhook, reply or forward in an automation's steps is a card whether a turn creates the automation or adds the
    step to one that exists. The name of an automation sends nothing, so a rename to "Forward ..." is not a card."""
    hook = {"id": "hook", "type": "action", "do": "call_webhook", "args": {"webhook": "wh1"}}
    store = api.app.state.store
    assert assistant.automation_sends_outside(store, "POST", "/api/v2/automations", {"name": "Hook", "steps": [hook]})
    aid = post(api, "automations", flow(owner="person:ana"))["id"]
    assert assistant.automation_sends_outside(store, "PATCH", f"/api/v2/automations/{aid}", {"steps": [hook]})
    assert not assistant.automation_sends_outside(
        store, "PATCH", f"/api/v2/automations/{aid}", {"name": "Forward invoices"}
    )
    assert not assistant.automation_sends_outside(
        store, "POST", "/api/v2/automations", {**flow(owner="person:ana"), "name": "Forward invoices"}
    )
    token = assistant_turn(api, "ana-test")[1]["token"]
    created = api.post("/api/v2/automations", json=flow(owner="person:ana", steps=[hook]), headers=headers(token))
    assert created.status_code == 403 and created.json()["error"]["code"] == "confirm_required", created.text
    changed = api.patch(f"/api/v2/automations/{aid}", json={"steps": [hook]}, headers=headers(token))
    assert changed.status_code == 403 and changed.json()["error"]["code"] == "confirm_required", changed.text


def api_room(api, token):
    return get(api, "assistant", token)["room_id"]


def card(api, token, path, body, method="POST", summary="Archive the Ops bot", for_person=None, expected=200):
    sent = {"summary": summary, "method": method, "path": path, "body": body}
    if for_person:
        sent["for"] = for_person
    return post(api, "assistant/actions", sent, token=token, expected=expected)


def test_the_assistants_background_run_puts_one_card_in_the_persons_own_chat(api):
    coo = setup_attempt(api, "assistant")[2]["token"]  # the Assistant bot's own identity (its daily sweep)
    made = card(api, coo, "/api/v2/bots/ops/archive", {"expected_revision": 1}, for_person="human:ana")["action"]
    assert made["owner"] == "human:ana" and made["proposer"] == "assistant" and made["status"] == "pending"
    assert made["conversation_id"] == api_room(api, "ana-test")
    # Another bot may not put a card in anyone's chat, and a person may not for someone else.
    other = setup_attempt(api, "ops")[2]["token"]
    card(api, other, "/api/v2/bots/ops/archive", {"expected_revision": 1}, for_person="human:ana", expected=403)
    card(api, "ben-test", "/api/v2/bots/ops/archive", {"expected_revision": 1}, for_person="human:ana", expected=403)


def test_a_background_card_never_approves_and_is_capped_per_person_per_day(api):
    coo = setup_attempt(api, "assistant")[2]["token"]
    # An OK is the person's own tap on its preview: never a card, alone or as a bulk step.
    with api.app.state.store.transaction() as c:
        ads = H.task_create(c, "human:ana", "Run the ads", "Q4.", "bot:ops")
        ok = H.task_ask(
            c,
            "bot:ops",
            ads["id"],
            "human:ana",
            "Spend $500 on ads?",
            kind="ok",
            shows=X.shown(c),
            seen_version=ads["version"],
        )
    give = f"/api/v2/tasks/{ads['id']}/asks/{ok['requirement_id']}/ok"
    for path, body in (("/api/v2/assistant/batch", {"actions": [{"path": give, "body": {}}]}), (give, {})):
        sent = {"summary": "OK the spend", "method": "POST", "path": path, "body": body, "for": "human:ana"}
        refused = api.post("/api/v2/assistant/actions", json=sent, headers=headers(coo))
        assert refused.status_code in (403, 422), refused.text
    for n in range(5):
        card(api, coo, "/api/v2/bots/ops/archive", {"expected_revision": n + 1}, for_person="human:ana")
    card(api, coo, "/api/v2/bots/ops/archive", {"expected_revision": 9}, for_person="human:ana", expected=429)


def test_one_card_holds_closes_an_automation_change_and_a_rule_stop(api):
    aid = post(api, "automations", flow(owner="person:ana"))["id"]
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Send the invoice", "Monday.", "human:ana")["id"]
    token = assistant_turn(api, "ana-test")[1]["token"]
    batch = {
        "actions": [
            {"path": f"/api/v2/tasks/{task}/move", "body": {"to": "done", "version": 1}},
            {"method": "PATCH", "path": "/api/v2/automations/" + aid, "body": {"name": "Weekly digest"}},
        ]
    }
    made = card(api, token, "/api/v2/assistant/batch", batch, summary="Close it and rename the automation")["action"]
    assert [s["method"] for s in made["body"]["actions"]] == ["POST", "PATCH"]
    # A step that is never admitted (the Assistant's own switches) is refused on the card as well.
    switch = {
        "actions": [
            {"method": "POST", "path": "/api/v2/rules", "body": {"kind": "external", "assistant_direct": False}}
        ]
    }
    card(api, token, "/api/v2/assistant/batch", switch, summary="Switch the Assistant off", expected=422)


def test_a_background_card_is_only_for_a_real_active_person(api):
    coo = setup_attempt(api, "assistant")[2]["token"]
    card(api, coo, "/api/v2/bots/ops/archive", {"expected_revision": 1}, for_person="human:ghost", expected=404)
    with api.app.state.store.read() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM conversations WHERE owner_id='human:ghost' OR "
                "id IN (SELECT conversation_id FROM assistant_actions WHERE owner='human:ghost')"
            ).fetchone()[0]
            == 0
        )


def test_a_routine_run_of_the_assistant_bot_is_never_the_persons_turn(api):
    """The daily sweep is a routine of the assistant bot: its attempt is not a turn the person started in their Assistant room,
    so the Assistant principal is None and the run keeps the bot's own identity (via is never "assistant")."""
    _, _, attempt = setup_attempt(api, "assistant")
    auth = api.app.state.auth
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
        assert auth.assistant_principal(c, row) is None
    assert get(api, "me", attempt["token"])["actor"] == "bot:assistant"


def test_the_providers_keys_are_never_a_chat_write(api):
    assert not assistant.admits_on_turn("PUT", "/api/v2/providers", {"enabled": []})
    token = assistant_turn(api, "ana-test")[1]["token"]
    res = api.put("/api/v2/providers", json={"enabled": [], "expected_revision": 0}, headers=headers(token))
    assert res.status_code == 403 and res.json()["error"]["code"] == "confirm_required", res.text


def morning(api, token, for_person="human:ana", body="Close the invoice first.", expected=200):
    r = api.post("/api/v2/assistant/morning", json={"for": for_person, "body": body}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def test_the_morning_day_is_the_company_day_in_los_angeles(monkeypatch):
    """The cap counts company days (updates.ZONE), not UTC days: 22:00 on Nov 5 in Los Angeles is already Nov 6 in UTC,
    and midnight in Los Angeles starts the next day."""

    def day_at(ts):
        monkeypatch.setattr(H, "now", lambda: ts)
        return assistant.morning_day()

    assert day_at("2026-11-05T23:30:00.000000Z") == ("2026-11-05", "2026-11-06T08:00:00.000000Z")
    assert day_at("2026-11-06T06:00:00.000000Z") == ("2026-11-05", "2026-11-06T08:00:00.000000Z")
    assert day_at("2026-11-06T07:59:59.000000Z")[0] == "2026-11-05"
    assert day_at("2026-11-06T08:00:00.000000Z") == ("2026-11-06", "2026-11-07T08:00:00.000000Z")


def test_the_morning_message_lands_in_the_persons_assistant_chat(api, monkeypatch):
    monkeypatch.setattr(H, "now", lambda: "2026-11-06T09:00:00.000000Z")  # 01:00 on Nov 6 in Los Angeles
    coo = setup_attempt(api, "assistant")[2]["token"]  # the Assistant bot's own identity (its background run)
    made = morning(api, coo, body="Close the invoice first.")
    assert made["day"] == "2026-11-06" and made["next_at"] == "2026-11-07T08:00:00.000000Z", made
    assert made["conversation_id"] == api_room(api, "ana-test") and made["conversation_id"] != api_room(api, "ben-test")
    with api.app.state.store.read() as c:
        sender, recipient, kind, body, refs = c.execute(
            "SELECT sender_id, recipient_id, kind, body, refs FROM messages WHERE id=?", (made["message_id"],)
        ).fetchone()
        audit = c.execute(
            "SELECT actor FROM events WHERE action='assistant.morning' AND target=?", (made["message_id"],)
        ).fetchone()
    assert (sender, recipient, kind, body) == ("bot:assistant", "human:ana", "say", "Close the invoice first.")
    assert json.loads(refs) == {"assistant": True, "morning": "2026-11-06"}, refs
    assert audit is not None and audit[0] == "human:ana"


def test_a_second_morning_message_the_same_company_day_is_refused_with_the_next_time(api, monkeypatch):
    # The run is claimed at 23:59:30 UTC (15:59 in Los Angeles), so its lease covers both calls. The second call is past
    # UTC midnight and still the same company day: it is refused, and says when the next one can go.
    monkeypatch.setattr(H, "now", lambda: "2026-11-05T23:59:30.000000Z")
    coo = setup_attempt(api, "assistant")[2]["token"]
    assert morning(api, coo)["day"] == "2026-11-05"
    monkeypatch.setattr(H, "now", lambda: "2026-11-06T00:00:45.000000Z")
    refused = api.post("/api/v2/assistant/morning", json={"for": "human:ana", "body": "Again."}, headers=headers(coo))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "already_sent_today", refused.text
    assert refused.json()["error"]["next_at"] == "2026-11-06T08:00:00.000000Z", refused.text


def test_a_morning_that_names_the_same_items_as_the_last_one_is_not_sent(api):
    """Quiet mornings: the items are the ids the Assistant names (`items`), deduped and in any order; the same set as the
    person's last morning is 409 same_as_last and writes nothing. Other items go, a message with no items is never held
    back (nor one after it), and what the Markdown says has no part in it."""
    coo = setup_attempt(api, "assistant")[2]["token"]

    def next_day():
        # The day's cap aside (one lease cannot span days): the morning already sent becomes yesterday's.
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE messages SET refs=json_set(refs,'$.morning','2000-01-01') WHERE refs LIKE '%morning%'")

    def send(body, items=None, expected=200):
        sent = {"for": "human:ana", "body": body, **({"items": items} if items is not None else {})}
        r = api.post("/api/v2/assistant/morning", json=sent, headers=headers(coo))
        assert r.status_code == expected, r.text
        if expected == 409:
            assert r.json()["error"]["code"] == "same_as_last", r.text
        next_day()

    send("Close [the invoice](#/task/t-1) to move [Q4 cash](#/goals/g-1).", ["t-1", "g-1"])
    odd = "~~~\n[x](#/task/t-9)\n~~~ [[nested] link](/a/) `[y](#/task/t-8)` [z](</b (c)>)"
    send(odd, ["g-1", "t-1", "t-1"], expected=409)
    with api.app.state.store.read() as c:
        sent = c.execute("SELECT COUNT(*) FROM messages WHERE json_extract(refs,'$.morning') IS NOT NULL").fetchone()[0]
    assert sent == 1
    send(odd, ["t-1", "g-2"])
    send("A quiet one.")
    send("A quiet one.")
    send("Back to it.", ["t-1", "g-2"])


def test_only_the_assistants_background_run_sends_the_morning_message(api):
    coo = setup_attempt(api, "assistant")[2]["token"]
    other_bot = setup_attempt(api, "ops")[2]["token"]
    morning(api, other_bot, expected=403)
    morning(api, "ana-test", expected=403)  # a person, even the one it is for
    morning(api, coo, for_person="bot:ops", expected=403)  # only a person gets one


def test_the_assistants_chat_turn_never_sends_the_morning_message(api):
    """The chat turn is the person's own request in their Assistant room, not the background run that sends this."""
    turn = assistant_turn(api, "ana-test")[1]["token"]
    morning(api, turn, expected=403)


def test_a_morning_message_is_only_for_an_active_person(api):
    coo = setup_attempt(api, "assistant")[2]["token"]
    morning(api, coo, for_person="human:ghost", expected=404)
    post(api, "access/humans/cara", {"sign_in": False})
    morning(api, coo, for_person="human:cara", expected=404)


def test_a_morning_message_is_at_most_two_thousand_characters(api):
    coo = setup_attempt(api, "assistant")[2]["token"]
    r = api.post("/api/v2/assistant/morning", json={"for": "human:ana", "body": "x" * 2001}, headers=headers(coo))
    assert r.status_code == 422, r.text


def test_the_morning_tool_is_offered_to_the_assistant_and_its_bot_and_to_no_one_else():
    offered = {
        kind: {t["name"] for t in hubtools.listing(local=True, kind=kind)}
        for kind in ("assistant", "bot", "owner", "admin", "member", "botops", "agent")
    }
    assert "tico_assistant_morning" in offered["assistant"] and "tico_assistant_morning" in offered["bot"]
    assert not any(
        "tico_assistant_morning" in names for kind, names in offered.items() if kind not in ("assistant", "bot")
    )


def _turn_message_id(api, attempt):
    """The person's message that started this turn (the `turn` a run cites)."""
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (attempt["id"],)
        ).fetchone()["message_id"]


def test_a_turn_never_edits_deletes_or_redacts_a_message_the_person_wrote(api):
    """A "No" the person wrote cannot become a "yes" on a turn: the edit, the delete and the redaction are the person's own
    tap, so the gate holds them (NEVER), and the message keeps its words."""
    _, attempt = assistant_turn(api, "ana-test", "No")
    mid = _turn_message_id(api, attempt)
    cited = {**headers(attempt["token"]), "X-Tico-On-Behalf-Of": "turn"}
    assert not assistant.admits_on_turn("PATCH", f"/api/v2/messages/{mid}", {"body": "yes"})
    assert not assistant.admits_on_turn("DELETE", f"/api/v2/messages/{mid}")
    assert not assistant.admits_on_turn("POST", f"/api/v2/messages/{mid}/redact", {})
    for method, path, body in (
        ("PATCH", f"/api/v2/messages/{mid}", {"body": "yes"}),
        ("DELETE", f"/api/v2/messages/{mid}", None),
        ("POST", f"/api/v2/messages/{mid}/redact", {}),
    ):
        raw = api.request(method, path, json=body, headers=cited)
        assert raw.status_code == 403 and raw.json()["error"]["code"] == "confirm_required", (method, raw.text)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT body FROM messages WHERE id=?", (mid,)).fetchone()["body"] == "No"


def test_a_message_edited_after_it_was_posted_lends_no_words(api):
    """`instruction` refuses a cited message that was edited, or that has any revision row: the words are not what the
    person posted (backend/assistant.py `edited`)."""
    _, attempt = assistant_turn(api, "ana-test", "Archive the Ops bot")
    mid = _turn_message_id(api, attempt)
    cited = {**headers(attempt["token"]), "X-Tico-On-Behalf-Of": "turn"}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET edited_at=? WHERE id=?", (H.now(), mid))
    raw = api.post("/api/v2/bots/ops/archive", json={"expected_revision": 1}, headers=cited)
    assert raw.status_code == 403 and raw.json()["error"]["code"] == "on_behalf_of" and "edited" in raw.text, raw.text
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET edited_at=NULL WHERE id=?", (mid,))
        assert not assistant.edited(c, {"id": mid, "edited_at": None, "revision": 0})
        c.execute(
            "INSERT INTO message_revisions(id,message_id,origin_id,revision,body,deleted,actor,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("rev-1", mid, "origin-1", 1, "yes", 0, "human:ana", H.now()),
        )
        assert assistant.edited(c, {"id": mid, "edited_at": None, "revision": 0})
        assert assistant.edited(c, {"id": mid, "edited_at": None, "revision": 1})


# The walk's keyword match reads a handler's source, so a word like "provider" or "login" can match a route that writes no
# credential. Each such route is reviewed here with the reason; a new route that the walk flags and this list does not
# name fails the test. A route that does touch a credential, a sign-in, an invite, a subscription or a signing key is on
# NEVER instead (backend/assistant.py), and a route here must never be one of those.
REVIEWED = {
    "POST /api/v2/finance/ad-imports": "turns Finance's ad spend import on or off and picks, by id, which saved keys it reads with; reads or writes no secret",
    "POST /api/v2/humans/{pid}": "a profile edit; 'login' is a last-login field",
    "POST /api/v2/humans/{pid}": "a profile edit; 'login' is a last-login field",
    "POST /api/v2/runners/heartbeat": "the runner's own heartbeat; 'provider' is a field name",
    "POST /api/v2/bots/{bot}/go-live": "switches a bot live on credentials the person already granted; writes none",
    "POST /api/v2/grokbot/sync": "syncs a bot's state from its provider; writes no credential",
    "POST /api/v2/bots/{bot}/model": "a bot's model choice; a computer's sign-in is NEVER",
    "POST /api/v2/bots/{bot}/limit/retry": "retries a bot's limit; writes no sign-in",
    "POST /api/v2/lenses/{lens_id}/runs": "runs a lens on the model provider the company already set",
    "POST /api/v2/files/links": "a file link; 'provider' names the file's host",
    "POST /api/v2/calendar/appointments": "books a calendar event; its invites go out through the calendar (see the report)",
    "POST /api/v2/imports/transcripts": "imports a transcript; 'provider' is its source field",
    "POST /api/v2/credential-requests": "asks the person for a credential; they enter it in Settings, which is NEVER",
    "POST /api/v2/credential-requests/{rid}/cancel": "cancels that ask; writes no credential",
    "POST /api/v2/decisions": "asks the LLM judge; 'provider' is the model provider",
    "POST /api/v2/decisions": "asks the LLM judge; 'provider' is the model provider",
    "POST /api/v2/rules/read": "reads a rules document with the company's model provider; writes nothing",
    "POST /api/v2/market/ask": "reads the market graph and asks the company's already-set model provider; writes no credential or provider setting",
    "POST /api/v2/bots/{bot}/tools": "registers a bot's tool manifest; clients/access_entry refuses any credential in it",
    "POST /api/v2/bots/{bot}/copy": "copies a bot the person may copy; no secret is shown in the chat, and the route checks the person's right",
    "POST /api/v2/settings/catalog/{key}/plan": "plans a setting and names the run it would make; the run is checked by the gate when it is made",
    "PUT /api/v2/access/rules": "refused by the keys on its body (credential and Assistant rules, in admits_on_turn); the walk sends no body",
    "POST /api/v2/api-tools/{tool_id}/check": "one authenticated GET per stored key; it reads, records the result and returns no value",
    "PUT /api/v2/usage/limits": "a spend cap, not a credential; the person sets it on their own word",
    "POST /api/v2/assistant/actions": "the Assistant's own card route: a proposal the person confirms",
    "POST /api/v2/api-tools/{tool_id}/check": "Check now: one authenticated GET to the vendor per key; reveals and changes nothing",
    "POST /api/v2/tool-proxy/{tool_id}/{path:path}": "a proxied tool call: no Tico identity, only a run's tool token; the key stays on the server",
    "PUT /api/v2/tool-proxy/{tool_id}/{path:path}": "a proxied tool call: no Tico identity, only a run's tool token; the key stays on the server",
    "PATCH /api/v2/tool-proxy/{tool_id}/{path:path}": "a proxied tool call: no Tico identity, only a run's tool token; the key stays on the server",
    "DELETE /api/v2/tool-proxy/{tool_id}/{path:path}": "a proxied tool call: no Tico identity, only a run's tool token; the key stays on the server",
    "POST /api/v2/computers/{rid}/revoke": "removes a computer the person may remove (its operator or an admin; a Confirm card in BotOps); the extra key it deletes holds the computer's first name, not a credential",
    "POST /api/v2/computers/{rid}/revoke": "removes a computer the person may remove (its operator or an admin; a Confirm card in BotOps); the extra key it deletes holds the computer's first name, not a credential",
}


def _template_tools():
    """Every command the Assistant template tells it to run, as the tool it runs: the hubcli command tree walked for each
    `hub ...` span (clients/remotecli.tool_name names the tool), not a pattern."""
    import argparse

    from clients import hubcli, remotecli

    def fn_of(words):
        node, fn = hubcli.parser(), None
        for word in words:
            sub = next((a for a in node._actions if isinstance(a, argparse._SubParsersAction)), None)
            if not sub or word not in sub.choices:
                break
            node = sub.choices[word]
            fn = node._defaults.get("fn", fn)
        return fn

    template = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "assistant"
    tools = set()
    for path in template.rglob("*.md"):
        for span in re.findall(r"\b(?:tico|hub) ([^\n`]+)", path.read_text(encoding="utf-8")):
            words = []
            for token in span.split():
                if not re.fullmatch(r"[a-z][a-z-]*", token):
                    break
                words.append(token)
            fn = fn_of(words)
            if fn:
                name = remotecli.tool_name(fn)
                # The CLI's aliases dispatch to these tools, not to a tool named after the command's spelling.
                tools.update(hubtools.CLI_TOOL_ALIASES.get(name, (name,)))
    return tools


def test_the_assistant_turn_is_offered_the_tools_its_template_runs_and_never_the_hidden_ones():
    """The Assistant's MCP listing is its short allowlist (ASSISTANT_TOOLS): every tool its template runs, none of the
    NEVER tools, and nothing else (93 tools, 85,364 bytes at this commit, down from 285)."""
    from clients.commands._access import ASSISTANT_TOOLS, ASSISTANT_UNLISTED

    everything = {t["name"] for t in hubtools.TOOLS}
    offered = hubtools.listing(local=True, kind="assistant")
    names = {t["name"] for t in offered}
    template = _template_tools()
    assert len(template) >= 40, sorted(template)
    assert template - names == set(), sorted(template - names)
    assert names == ASSISTANT_TOOLS, sorted(names ^ ASSISTANT_TOOLS)
    # The trim shrinks the listing only: the Needs you batch walk is still a call the turn may make.
    assert all("assistant" in hubtools.offered_to(hubtools.BY_NAME[n]) for n in ASSISTANT_UNLISTED)
    assert not set(hubtools.ASSISTANT_HIDDEN) & names and set(hubtools.ASSISTANT_HIDDEN) <= everything
    assert len(json.dumps(offered)) <= 100_000, len(json.dumps(offered))
    assert "tico_assistant_propose" in {t["name"] for t in hubtools.listing(local=True, kind="bot")}
