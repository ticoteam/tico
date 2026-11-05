"""Make page runs separately with the person’s ordinary rights and publishes a pinned page."""
import json

import pytest

from backend import cards
from backend.store import H, encode
from backend.tests.test_api import api, as_member, assign, get, headers, post, ready, restrict, runner  # noqa: F401
from backend.tests.test_assistant import assistant_turn, say
from backend.tests.test_files import turn
from backend.tests.test_meeting_items import record
from backend.tests.test_meeting_task_links import link, task
from backend.tests.test_pages import HTML


def page(api, turn, **fields):
    data = fields.pop("data", HTML)
    return api.post("/api/v2/pages", params={"name": fields.pop("name", "weekly.html"), **fields},
                    content=data, headers={**headers(turn["token"]), "Content-Type": "text/html"})


def start(api, tid, person="ben-test", key=None, machine=None):
    if machine is None:
        machine = runner(api)
        assign(api, machine, "coo")
        ready(api, machine, ["coo"], capabilities=["make_page"])
    sent = post(api, f"tasks/{tid}/make-page", {"text": "Make the alert flow with three scenario tabs."}, person, key)
    attempt = post(api, "jobs/claim", {"next_run": True}, machine["token"],
                   key="claim:" + sent["message"]["id"])["attempt"]
    assert attempt["message"]["id"] == sent["message"]["id"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "make-page-test"}, machine["token"])
    return machine, attempt, sent["message"]


def status(api, tid, person="ben-test"):
    return get(api, f"tasks/{tid}/make-page", person)["make_page"]


def fill_pins(api, tid, count=cards.PLACE_MAX):
    with api.app.state.store.transaction() as c:
        for i in range(count):
            c.execute("INSERT INTO pins(id,place,item,position,size,made_by,created_at) VALUES(?,?,?,?,?,?,?)",
                      (H.new_id(), "task:" + tid, f"card:placeholder-{i}", i, "medium", "human:ana", H.now()))


def assert_no_page(api):
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM pages").fetchone()
        assert not c.execute("SELECT 1 FROM bot_files").fetchone()
        assert not c.execute("SELECT 1 FROM bot_file_versions").fetchone()


def test_old_runner_never_claims_make_page_and_upgrade_recovers_the_same_request(api):
    from runner_support.state import session_key
    t = task(api, private=False)
    machine, private = assistant_turn(api, "ben-test", text="PRIVATE_MARKER")
    post(api, f"attempts/{private['id']}/started", {"thread_id": "private-provider-session"}, machine["token"])
    post(api, f"attempts/{private['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    sent = post(api, f"tasks/{t['id']}/make-page", {"text": "Make a plan with the previously discussed marker"}, "ben-test")["message"]
    # Rechecking the retryable card preserves the queued request and never creates a provider turn on the old runner.
    for _ in range(2):
        assert post(api, "jobs/claim", {"next_run": True}, machine["token"])["attempt"] is None
        assert status(api, t["id"]) == {"message_id": sent["id"], "conversation_id": sent["conversation_id"],
                                          "state": "update_required"}
    with api.app.state.store.read() as c:
        job = c.execute("SELECT state,held,attempt_id FROM jobs WHERE message_id=?", (sent["id"],)).fetchone()
        assert tuple(job) == ("queued", 0, None)
        assert [r[0] for r in c.execute("SELECT id FROM attempts WHERE bot_id='coo'")] == [private["id"]]
    ready(api, machine, ["coo"], capabilities=[
        "task_lanes_v2", "session_roots_v1", "task_goals_v1", "capacity_continue_v1", "source_continue_v1",
        "model_sign_out_v1", "model_keys_v1", "model_switch_type_v1", "model_remove_v1", "model_switch_to_key_v1",
        "model_connect_v1", "turns_at_once_v1", "private_lanes_v1", "model_subscriptions_v1",
        "model_subscription_activation_v1", "browser_exit_v1", "memory_tidy_packets_v1", "threads_v1",
        "harness_allocation_v1", "make_page", "computer_logins_v1", "screen_v1"])
    claimed = post(api, "jobs/claim", {"next_run": True}, machine["token"])["attempt"]
    assert claimed["message"]["id"] == sent["id"]
    assert claimed["make_page"]["task"]["id"] == t["id"]
    assert [m["id"] for m in claimed["history"]] == [sent["id"]]
    assert "PRIVATE_MARKER" not in json.dumps(claimed)
    assert not claimed.get("continue_thread")
    assert session_key(claimed["config"], claimed) != session_key(private["config"], private)
    assert status(api, t["id"])["state"] == "making"
    post(api, f"attempts/{claimed['id']}/started", {"thread_id": "fresh-page-session"}, machine["token"])
    made = page(api, claimed, task=t["id"])
    assert made.status_code == 200, made.text
    assert status(api, t["id"])["state"] == "made"


def test_make_page_files_and_pins_clean_html_as_the_person_with_authenticated_attempt(api):
    t = task(api, private=False)
    mid = record(api, {"private": False})
    link(api, mid, [t["id"]])
    machine, attempt, message = start(api, t["id"], key="make-once")
    token = attempt["token"]
    assert message["sender_id"] == "human:ben"
    assert {k: message["refs"][k] for k in ("assistant", "make_page", "delivery")} == {
        "assistant": True, "make_page": {"task": t["id"]}, "delivery": "queue"}
    assert attempt["make_page"]["task"]["id"] == t["id"]
    assert attempt["make_page"]["task"]["body"] == t["body"]
    assert [m["id"] for m in attempt["task_context"]["meetings"]] == [mid]
    assert get(api, "me", token)["actor"] == "human:ben"
    assert "credentials" in get(api, "credential-runtime", token)
    assert "company_name" in get(api, "config", token)
    get(api, "meetings/transcript?id=" + mid, token)
    assert status(api, t["id"])["state"] == "making"
    refused = page(api, {"token": machine["token"]}, task=t["id"], bot="coo", attempt=attempt["id"])
    assert refused.status_code == 403, refused.text
    made = page(api, attempt, task=t["id"], attempt="forged-attempt", title="Alert flow")
    assert made.status_code == 200, made.text
    shown = made.json()["page"]
    fid = shown["file_id"]
    assert (shown["bot_id"], shown["updated_by"], shown["task_id"]) == ("coo", "human:ben", t["id"])
    stored = api.get("/api/v2/files/" + fid, headers=headers("cara-test"))
    assert stored.status_code == 200, stored.text
    assert b"<script" not in stored.content and b"42 deals" in stored.content
    with api.app.state.store.read() as c:
        f = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
        v = c.execute("SELECT * FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()
        assert f["scope"] == "task:" + t["id"] and f["bot_id"] == "coo"
        assert v["attempt_id"] == attempt["id"] and v["actor"] == "human:ben"
        assert c.execute("SELECT made_by FROM pins WHERE place=? AND item=?",
                         ("task:" + t["id"], "page:" + fid)).fetchone()[0] == "human:ben"
    assert status(api, t["id"])["state"] == "made"
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM pins WHERE place=? AND item=?", ("task:" + t["id"], "page:" + fid))
        c.execute("UPDATE pages SET archived=1 WHERE file_id=?", (fid,))
    assert status(api, t["id"])["state"] == "made"
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    # A retry creates no second message; a later chat cannot erase this request's result.
    repeated = post(api, f"tasks/{t['id']}/make-page", {"text": "Make the alert flow with three scenario tabs."},
                    "ben-test", "make-once")
    assert repeated["message"]["id"] == message["id"]
    say(api, "What is next?", "ben-test")
    assert status(api, t["id"])["state"] == "made"
    assert status(api, t["id"], "cara-test") is None


def test_make_page_output_uses_page_audience_and_restores_after_clearing_it(api):
    from backend.tests.test_page_audience import edit
    t = task(api, private=False)
    _, attempt, _ = start(api, t["id"])
    made = page(api, attempt, task=t["id"], title="Generated audience plan")
    assert made.status_code == 200, made.text
    fid = made.json()["page"]["file_id"]
    get(api, "pages/" + fid, "cara-test")
    edit(api, fid, {"people": ["ben"]}, token="ana-test")
    for path in ("pages/" + fid, "files/" + fid, "files/" + fid + "/versions/1"):
        get(api, path, "cara-test", expected=404)
    for path in ("pages?task=" + t["id"], "tasks/" + t["id"] + "/context"):
        assert "Generated audience plan" not in json.dumps(get(api, path, "cara-test"))
    assert status(api, t["id"])["state"] == "made"
    edit(api, fid, None, token="ana-test")
    get(api, "pages/" + fid, "cara-test")
    assert api.get("/api/v2/files/" + fid, headers=headers("cara-test")).status_code == 200


def test_forged_make_page_refs_are_forbidden_on_generic_message_and_chat_routes(api):
    t = task(api, private=False)
    room = say(api, "Hello", "ben-test")["message"]["conversation_id"]
    for refs in ({"make_page": {"task": t["id"]}}, {"assistant": True, "make_page": {"task": t["id"]}}):
        for path, body in (("messages", {"to": "assistant", "text": "Make this", "refs": refs}),
                           ("chat/ops", {"text": "Make this", "refs": refs}),
                           (f"threads/{room}/messages", {"text": "Make this", "to": "bot:coo", "refs": refs})):
            post(api, path, body, "ben-test", expected=403)
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM messages WHERE json_type(refs,'$.make_page') IS NOT NULL").fetchone()


def test_identical_html_completes_each_make_page_request_without_a_new_version(api):
    t = task(api, private=False)
    requests = []
    machine = None
    for _ in range(2):
        machine, attempt, message = start(api, t["id"], machine=machine)
        requests.append(attempt["id"])
        made = page(api, attempt, task=t["id"])
        assert made.status_code == 200, made.text
        assert made.json()["page"]["version"] == 1
        assert status(api, t["id"])["message_id"] == message["id"]
        assert status(api, t["id"])["state"] == "made"
        post(api, f"attempts/{attempt['id']}/complete",
             {"outcome": "completed", "text": "The page is published.", "last_seq": 0}, machine["token"])
        assert status(api, t["id"])["state"] == "made"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM bot_file_versions").fetchone()[0] == 1
        assert {r[0] for r in c.execute("SELECT attempt_id FROM bot_file_activity WHERE action IN ('created','published')")} == set(requests)


def test_ordinary_assistant_turn_cannot_publish_even_with_a_supplied_attempt(api):
    t = task(api, private=False)
    _, attempt = assistant_turn(api, "ben-test")
    result = page(api, attempt, task=t["id"], attempt=attempt["id"])
    assert result.status_code == 403, result.text
    assert_no_page(api)


@pytest.mark.parametrize("change", ["sender", "room", "deleted", "target", "via"])
def test_publish_rechecks_the_initiating_message(api, change):
    t = task(api, private=False)
    _, attempt, message = start(api, t["id"])
    with api.app.state.store.transaction() as c:
        if change == "sender":
            c.execute("UPDATE messages SET sender_id='human:cara' WHERE id=?", (message["id"],))
        elif change == "room":
            c.execute("UPDATE conversations SET room_key='other' WHERE id=?", (message["conversation_id"],))
        elif change == "deleted":
            c.execute("UPDATE messages SET deleted_at=? WHERE id=?", (H.now(), message["id"]))
        else:
            refs = {**message["refs"], **({"via": "assistant"} if change == "via" else {"make_page": {"task": "other"}})}
            c.execute("UPDATE messages SET refs=? WHERE id=?", (encode(refs), message["id"]))
    result = page(api, attempt, task=t["id"])
    assert result.status_code == 403, result.text
    assert_no_page(api)


def test_task_access_checks_apply_to_the_route_status_and_publishing(api):
    t = task(api, owner="bot:ops", private=False)
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ben"])
    post(api, f"tasks/{t['id']}/make-page", {"text": "Make a plan"}, "cara-test", expected=404)
    get(api, f"tasks/{t['id']}/make-page", "cara-test", expected=404)
    _, attempt, _ = start(api, t["id"], "ben-test")
    post(api, f"tasks/{t['id']}/make-page", {"text": "Again"}, attempt["token"], expected=403)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1,requester_id='human:cara',owner_id='human:cara' WHERE id=?", (t["id"],))
    refused = page(api, attempt, task=t["id"])
    assert refused.status_code in (403, 404), refused.text


def test_pin_cap_is_visible_at_start_and_rechecked_at_publish_with_atomic_rollback(api):
    t = task(api, private=False)
    fill_pins(api, t["id"])
    refused = post(api, f"tasks/{t['id']}/make-page", {"text": "Make a plan"}, "ben-test", expected=409)
    assert refused["error"]["detail"] == "This task has 60 pins"
    assert status(api, t["id"]) is None
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM pins WHERE place=?", ("task:" + t["id"],))
    _, attempt, _ = start(api, t["id"])
    fill_pins(api, t["id"])
    result = page(api, attempt, task=t["id"])
    assert result.status_code == 409, result.text
    assert result.json()["error"]["detail"] == "This task has 60 pins"
    assert_no_page(api)


def test_failed_request_survives_later_chat_and_ignores_an_unrelated_pin(api):
    t = task(api, private=False)
    machine, attempt, message = start(api, t["id"])
    _, publisher = turn(api, "cpo")
    assert page(api, publisher, task=t["id"]).status_code == 200
    assert status(api, t["id"])["state"] == "making"
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    say(api, "Another question", "ben-test")
    assert status(api, t["id"]) == {"message_id": message["id"], "conversation_id": message["conversation_id"], "state": "failed"}


def test_make_page_is_queued_separately_from_an_already_running_assistant_turn(api):
    t = task(api, private=False)
    machine, attempt = assistant_turn(api, "ben-test")
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "ordinary-chat"}, machine["token"])
    message = post(api, f"tasks/{t['id']}/make-page", {"text": "Make the project page"}, "ben-test")["message"]
    inputs = post(api, f"attempts/{attempt['id']}/inputs", {}, machine["token"])
    assert message["id"] not in {m["id"] for m in inputs["messages"]}
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (message["id"],)).fetchone()[0] == "queued"


def test_mcp_offers_page_publish_to_the_assistant_and_keeps_the_person_and_attempt(api):
    from backend.tests.test_mcp import call, rpc
    t = task(api, private=False)
    _, attempt, _ = start(api, t["id"])
    tools = rpc(api, "tools/list", token=attempt["token"])["result"]["tools"]
    assert "hub_page_publish" in {tool["name"] for tool in tools}
    error, result = call(api, "hub_page_publish", {"name": "alerts.html", "text": HTML.decode(), "task": t["id"]},
                         token=attempt["token"])
    assert not error, result
    assert result["page"]["updated_by"] == "human:ben"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT attempt_id FROM bot_file_versions WHERE file_id=?",
                         (result["page"]["file_id"],)).fetchone()[0] == attempt["id"]


def test_make_page_keeps_private_chat_history_and_other_queued_work_out_of_the_turn(api):
    from runner_support.state import session_key
    t = task(api, private=False)
    earlier = say(api, "Private chat content unrelated to this task", "ben-test")["message"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE message_id=?", (earlier["id"],))
        from backend import delivery
        delivery.defer(c, "coo", "message", earlier["id"], conversation_id=earlier["conversation_id"])
        c.execute("INSERT INTO chat_goals(id,conversation_id,bot,objective,status,set_by,set_at,updated_at) "
                  "VALUES(?,?,'coo','Unrelated chat objective','active','human:ben',?,?)",
                  (H.new_id(), earlier["conversation_id"], H.now(), H.now()))
        other = H.task_create(c, "human:ana", "Private follow-up", "Private details", "bot:coo",
                              next_run=True, private=True, lint=False)
    machine, attempt, message = start(api, t["id"])
    assert [m["id"] for m in attempt["history"]] == [message["id"]]
    assert attempt["next_run"] == [] and attempt["notes"] == []
    assert attempt["deferred"] == "" and attempt["chat_goal"] is None
    key = session_key({"session": "bot"}, attempt)
    assert key == "make-page:" + attempt["id"]
    ordinary = {k: v for k, v in attempt.items() if k != "make_page"}
    assert session_key({"session": "bot"}, ordinary) != key
    say(api, "An unrelated private follow-up", "ben-test")
    assert post(api, f"attempts/{attempt['id']}/inputs", {}, machine["token"])["messages"] == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT carried_by FROM tasks WHERE id=?", (other["id"],)).fetchone()[0] is None
        assert c.execute("SELECT settled_at FROM deferrals WHERE item=?", (earlier["id"],)).fetchone()[0] is None


def test_make_page_cannot_bypass_page_cleaning_with_a_generic_upload(api):
    t = task(api, private=False)
    _, attempt, _ = start(api, t["id"])
    result = api.post("/api/v2/files/uploads", params={"name": "raw.html", "task": t["id"]}, content=HTML,
                      headers=headers(attempt["token"]))
    assert result.status_code == 403, result.text
    assert_no_page(api)



def test_make_page_has_normal_reads_beyond_context_and_no_source_publish_recheck(api):
    as_member(api, "ben@acme.example")
    t = task(api, private=False)
    mid = record(api, {"private": False})
    _, publisher = turn(api, "cpo")
    source = page(api, publisher, task=t["id"])
    assert source.status_code == 200, source.text
    fid = source.json()["page"]["file_id"]
    _, attempt, _ = start(api, t["id"])
    token = attempt["token"]
    # An unlinked source remains readable through ordinary tools, just like in any Assistant turn.
    get(api, "meetings/transcript?id=" + mid, token)
    assert api.get("/api/v2/files/" + fid, headers=headers(token)).status_code == 200
    from backend.tests.test_page_audience import edit
    edit(api, fid, {"people": ["ana"]}, token="ana-test")
    get(api, "pages/" + fid, token, expected=404)
    # Already copied content is accepted; source access does not narrow the made page's default audience.
    made = page(api, attempt, name="copied.html", task=t["id"], data=b"<h1>Copied content</h1>")
    assert made.status_code == 200, made.text
    shown = made.json()["page"]
    assert shown.get("audience") is None
    assert api.get("/api/v2/files/" + shown["file_id"], headers=headers("cara-test")).status_code == 200


def test_make_page_keeps_normal_assistant_learning_and_tools(api):
    from backend.tests.test_learned import learn
    from backend.tests.test_mcp import rpc
    learn(api, "Use a calm page layout", token="ana-test")
    t = task(api, private=False)
    _, attempt, _ = start(api, t["id"], person="ana-test")
    assert "Use a calm page layout" in json.dumps(attempt["learned"])
    tools = rpc(api, "tools/list", token=attempt["token"])["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert {"hub_page_publish", "hub_conversation_show", "hub_task_context"} <= names


def test_make_page_on_a_private_task_keeps_its_normal_task_audience(api):
    t = task(api, owner="human:ben", private=True)
    machine, attempt, request = start(api, t["id"])
    made = page(api, attempt, task=t["id"])
    assert made.status_code == 200, made.text
    fid = made.json()["page"]["file_id"]
    get(api, "pages/" + fid, "ben-test")
    get(api, "pages/" + fid, "cara-test", expected=404)
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0,
         "text": "The private page is made."}, machine["token"])
    messages = get(api, "conversations/" + request["conversation_id"] + "/messages", "ben-test")
    assert any(m["body"] == "The private page is made." for m in messages)
    from backend import task_privacy
    with api.app.state.store.read() as c:
        assert not task_privacy.message_readable(c, "bot:coo", request)
        assert not task_privacy.attempt_readable(c, "bot:coo", attempt["id"])


def test_person_who_makes_a_page_can_unlink_it_from_someone_elses_task(api):
    t = task(api, private=False)
    _, attempt, _ = start(api, t["id"], person="cara-test")
    made = page(api, attempt, task=t["id"])
    assert made.status_code == 200, made.text
    fid = made.json()["page"]["file_id"]
    pins = get(api, "places/task:" + t["id"], "cara-test")["pins"]
    pin = next(p for p in pins if p.get("page", {}).get("file_id") == fid)
    assert pin["can_unpin"]
    removed = api.delete("/api/v2/pins/" + pin["id"], headers=headers("cara-test"))
    assert removed.status_code == 200, removed.text
    assert status(api, t["id"], "cara-test")["state"] == "made"


def test_make_page_does_not_fall_back_to_bot_rights_after_person_loses_sign_in(api):
    t = task(api, private=False)
    machine = runner(api)
    assign(api, machine, "coo")
    ready(api, machine, ["coo"], capabilities=["make_page"])
    request = post(api, f"tasks/{t['id']}/make-page", {"text": "Make the plan"}, "ben-test")["message"]
    post(api, "access/people/ben", {"sign_in": False})
    assert post(api, "jobs/claim", {"next_run": True}, machine["token"])["attempt"] is None
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE j.message_id=?",
                             (request["id"],)).fetchone()


def test_people_sharing_the_assistant_do_not_overwrite_each_others_page(api):
    t = task(api, private=False)
    machine, first, _ = start(api, t["id"], person="ben-test")
    original = page(api, first, task=t["id"], data=b"<h1>Ben's plan</h1>")
    assert original.status_code == 200, original.text
    fid = original.json()["page"]["file_id"]
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    _, second, _ = start(api, t["id"], person="cara-test", machine=machine)
    # The shared publishing bot grants no rights to rename, archive, delete or arrange Ben's page.
    token = second["token"]
    for path, body in (("files/" + fid, {"title": "Cara's title"}),
                       ("pages/" + fid, {"archived": True})):
        refused = api.patch("/api/v2/" + path, json=body, headers=headers(token))
        assert refused.status_code == 403, refused.text
    refused = api.delete("/api/v2/files/" + fid, headers=headers(token))
    assert refused.status_code == 403, refused.text
    pin = get(api, "places/task:" + t["id"], token)["pins"][0]
    refused = api.patch("/api/v2/pins/" + pin["id"], json={"size": "large"}, headers=headers(token))
    assert refused.status_code == 403, refused.text
    refused = api.delete("/api/v2/pins/" + pin["id"], headers=headers(token))
    assert refused.status_code == 403, refused.text
    other = page(api, second, task=t["id"], data=b"<h1>Cara's plan</h1>")
    assert other.status_code == 200, other.text
    assert other.json()["page"]["file_id"] != fid
    assert get(api, "pages/" + fid, "ben-test")["page"]["version"] == 1
    content = api.get("/api/v2/files/" + fid, headers=headers("ben-test"))
    assert b"Ben's plan" in content.content and b"Cara's plan" not in content.content


@pytest.mark.parametrize("person,another_task,expected", [
    ("cara-test", False, 403), ("cara-test", True, 404), ("ben-test", True, 404)])
def test_make_page_replacement_requires_person_management_and_the_same_task(api, person, another_task, expected):
    t = task(api, private=False)
    machine, first, _ = start(api, t["id"])
    original = page(api, first, task=t["id"])
    assert original.status_code == 200, original.text
    fid = original.json()["page"]["file_id"]
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    target = task(api, private=False) if another_task else t
    _, attempt, _ = start(api, target["id"], person=person, machine=machine)
    get(api, "pages/" + fid, attempt["token"])
    refused = page(api, attempt, task=target["id"], name="replacement.html", replaces=fid)
    assert refused.status_code == expected, refused.text
    original = get(api, "pages/" + fid, "ben-test")["page"]
    assert original["replaced_by_id"] is None and original["version"] == 1
    assert [p["page"]["file_id"] for p in get(api, "places/task:" + t["id"], "ben-test")["pins"]] == [fid]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM pages").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM bot_files").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM bot_file_versions").fetchone()[0] == 1


def test_owner_make_page_can_replace_their_own_page_and_records_the_person(api):
    t = task(api, private=True, owner="human:ana")
    _, attempt, _ = start(api, t["id"], person="ana-test")
    original = page(api, attempt, task=t["id"])
    assert original.status_code == 200, original.text
    fid = original.json()["page"]["file_id"]
    made = page(api, attempt, task=t["id"], name="replacement.html", replaces=fid)
    assert made.status_code == 200, made.text
    assert get(api, "pages/" + fid)["page"]["replaced_by_id"] == made.json()["page"]["file_id"]
    activity = get(api, "files/" + fid + "/activity")["activity"]
    assert activity[0]["action"] == "modified" and activity[0]["actor"] == "human:ana"


def test_lost_private_make_page_claim_replays_the_lease_as_the_person_and_rechecks_access(api):
    as_member(api, "ben@acme.example")
    t = task(api, private=True)
    machine, first, _ = start(api, t["id"])
    source = page(api, first, task=t["id"])
    assert source.status_code == 200, source.text
    fid = source.json()["page"]["file_id"]
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    sent = post(api, f"tasks/{t['id']}/make-page", {"text": "Make a revised plan"}, "ben-test")["message"]
    key, body = "claim:" + sent["id"], {"next_run": True}
    claimed = post(api, "jobs/claim", body, machine["token"], key)["attempt"]
    assert [p["id"] for p in claimed["task_context"]["pages"]] == [fid]
    from backend import task_privacy
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM attempts WHERE id=?", (claimed["id"],)).fetchone()[0] == "leased"
        assert not task_privacy.attempt_readable(c, "bot:coo", claimed["id"])
    # Discard the first response: the same key must recover the existing lease, without starting another turn.
    replayed = post(api, "jobs/claim", body, machine["token"], key)["attempt"]
    assert (replayed["id"], replayed["token"]) == (claimed["id"], claimed["token"])
    assert replayed["make_page"] == claimed["make_page"] and replayed["task_context"] == claimed["task_context"]
    post(api, "jobs/claim", body, "cara-test", key, expected=403)
    outsider = runner(api, operator="cara")
    assert post(api, "jobs/claim", body, outsider["token"], key)["attempt"] is None
    archived = api.patch("/api/v2/pages/" + fid, json={"archived": True}, headers=headers())
    assert archived.status_code == 200, archived.text
    refreshed = post(api, "jobs/claim", body, machine["token"], key)["attempt"]
    assert refreshed["id"] == claimed["id"] and refreshed["task_context"]["pages"] == []
    current = get(api, "tasks/" + t["id"])["task"]
    post(api, "tasks/" + t["id"], {"version": current["version"], "owner": "human:cara"})
    post(api, "jobs/claim", body, machine["token"], key, expected=403)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE job_id=?", (claimed["job_id"],)).fetchone()[0] == 1


def test_cached_make_page_claim_requires_the_current_person_and_computer(api):
    t = task(api, private=True)
    machine, attempt, sent = start(api, t["id"])
    key, body = "claim:" + sent["id"], {"next_run": True}
    post(api, "access/people/ben", {"sign_in": False})
    post(api, "jobs/claim", body, machine["token"], key, expected=403)
    post(api, "access/people/ben", {"sign_in": True})
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    other = runner(api)
    assign(api, other, "coo", generation=attempt["generation"])
    post(api, "jobs/claim", body, machine["token"], key, expected=403)
