"""Make page publishes only the person's request and currently shared, logged sources."""
import json

import pytest

from backend import cards
from backend.store import H, encode
from backend.tests.test_api import api, assign, get, headers, post, ready, restrict, runner  # noqa: F401
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
    assert set(get(api, "config", token)) == {"features", "upload_max_bytes"}
    get(api, "meetings/transcript?id=" + mid, token)
    assert status(api, t["id"])["state"] == "making"
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
        assert c.execute("SELECT 1 FROM pins WHERE place=? AND item=?", ("task:" + t["id"], "page:" + fid)).fetchone()
        assert c.execute("SELECT 1 FROM attempt_reads WHERE attempt_id=? AND kind='meeting' AND ref=?",
                         (attempt["id"], mid)).fetchone()
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


def test_make_page_reads_only_the_list_including_direct_meeting_and_version_routes(api):
    t = task(api, private=False)
    included, outside = record(api, {"private": False}), record(api, {"private": False})
    link(api, included, [t["id"]])
    _, attempt, _ = start(api, t["id"])
    for suffix in ("", "/versions"):
        r = api.get(f"/api/meetings/{outside}{suffix}", headers=headers(attempt["token"]))
        assert r.status_code == 404, r.text
    get(api, "meetings/transcript?id=" + outside, attempt["token"], expected=404)
    assert api.get("/api/meetings/" + included, headers=headers(attempt["token"])).status_code == 200


def test_make_page_cannot_read_the_eleventh_linked_meeting(api):
    t = task(api, private=False)
    mids = [record(api, {"private": False}) for _ in range(11)]
    link(api, mids[0], [t["id"]])
    for mid in mids[1:]:
        link(api, mid, [t["id"]])
    _, attempt, _ = start(api, t["id"])
    listed = {m["id"] for m in attempt["task_context"]["meetings"]}
    assert len(listed) == 10
    outside = (set(mids) - listed).pop()
    assert api.get(f"/api/meetings/{outside}/versions", headers=headers(attempt["token"])).status_code == 404


def test_logged_meeting_made_participants_only_blocks_publish_without_storing_a_page(api):
    t = task(api, private=False)
    mid = record(api, {"private": False})
    link(api, mid, [t["id"]])
    _, attempt, _ = start(api, t["id"])
    get(api, "meetings/transcript?id=" + mid, attempt["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE meetings SET metadata=json_set(metadata,'$.private',json('true')) WHERE id=?", (mid,))
    refused = page(api, attempt, task=t["id"])
    assert refused.status_code == 403, refused.text
    assert_no_page(api)


def test_an_unlinked_logged_file_blocks_publish(api):
    t = task(api, private=False)
    _, publisher = turn(api, "cpo")
    made = page(api, publisher, task=t["id"])
    assert made.status_code == 200, made.text
    fid = made.json()["page"]["file_id"]
    _, attempt, _ = start(api, t["id"])
    assert api.get("/api/v2/files/" + fid, headers=headers(attempt["token"])).status_code == 200
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_files SET scope='bot',task_id=NULL WHERE id=?", (fid,))
    refused = page(api, attempt, task=t["id"])
    assert refused.status_code == 403, refused.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM pages").fetchone()[0] == 1


def test_logged_page_audience_must_still_cover_the_task_at_publication(api):
    from backend.tests.test_page_audience import edit
    t = task(api, private=False)
    _, publisher = turn(api, "cpo")
    source = page(api, publisher, task=t["id"])
    assert source.status_code == 200, source.text
    fid = source.json()["page"]["file_id"]
    _, attempt, _ = start(api, t["id"])
    assert api.get("/api/v2/files/" + fid, headers=headers(attempt["token"])).status_code == 200
    edit(api, fid, {"people": ["ben"]}, token="ana-test")
    # The requester can still read it; the shared task's other readers cannot.
    get(api, "pages/" + fid, "ben-test")
    refused = page(api, attempt, task=t["id"], name="made.html")
    assert refused.status_code == 403, refused.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM pages").fetchone()[0] == 1


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


def test_assistant_publish_cannot_change_task_scope_or_claim_another_bot(api):
    t, other = task(api, private=False), task(api, title="Another project", private=False)
    _, attempt, _ = start(api, t["id"])
    for fields in ({}, {"task": other["id"]}, {"task": t["id"], "scope": "bot"},
                   {"task": t["id"], "source": "a-meeting"}):
        refused = page(api, attempt, **fields)
        assert refused.status_code == 403, refused.text
    result = page(api, attempt, task=t["id"], bot="ops")
    assert result.status_code == 200, result.text
    assert result.json()["page"]["bot_id"] == "coo"


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


def test_every_task_publish_honors_the_pin_cap_but_republishing_an_existing_pin_works(api):
    t = task(api, private=False)
    _, attempt = turn(api)
    fid = page(api, attempt, task=t["id"]).json()["page"]["file_id"]
    fill_pins(api, t["id"], cards.PLACE_MAX - 1)
    assert page(api, attempt, task=t["id"], data=HTML + b"<p>v2</p>").status_code == 200
    refused = page(api, attempt, task=t["id"], name="second.html")
    assert refused.status_code == 409, refused.text
    with api.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT file_id FROM pages")] == [fid]


def test_failed_request_survives_later_chat_and_ignores_an_unrelated_pin(api):
    t = task(api, private=False)
    machine, attempt, message = start(api, t["id"])
    _, publisher = turn(api, "cpo")
    assert page(api, publisher, task=t["id"]).status_code == 200
    assert status(api, t["id"])["state"] == "making"
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    say(api, "Another question", "ben-test")
    assert status(api, t["id"]) == {"message_id": message["id"], "conversation_id": message["conversation_id"], "state": "failed"}


def test_participants_only_attempt_provenance_blocks_publish(api):
    t = task(api, private=False)
    mid = post(api, "live-meetings", {"title": "Restricted planning", "visibility": "private"}, "ben-test")["id"]
    _, attempt, message = start(api, t["id"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET refs=? WHERE id=?",
                  (encode({**message["refs"], "live_meeting": {"meeting_id": mid}}), message["id"]))
    result = page(api, attempt, task=t["id"])
    assert result.status_code == 403, result.text
    assert_no_page(api)


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
        other = H.task_create(c, "human:ana", "Private follow-up", "Private details", "bot:coo",
                              next_run=True, private=True, lint=False)
    machine, attempt, message = start(api, t["id"])
    assert [m["id"] for m in attempt["history"]] == [message["id"]]
    assert attempt["next_run"] == [] and attempt["notes"] == []
    key = session_key({"session": "bot"}, attempt)
    assert key == "make-page:" + attempt["id"]
    ordinary = {k: v for k, v in attempt.items() if k != "make_page"}
    assert session_key({"session": "bot"}, ordinary) != key
    say(api, "An unrelated private follow-up", "ben-test")
    assert post(api, f"attempts/{attempt['id']}/inputs", {}, machine["token"])["messages"] == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT carried_by FROM tasks WHERE id=?", (other["id"],)).fetchone()[0] is None


def test_private_messages_and_both_learned_layers_never_reach_make_page(api):
    from backend.tests.test_learned import learn
    from backend.tests.test_strategy_bot_chat import strategy_bot
    from backend.tests.test_mcp import call, rpc
    strategy_bot(api)
    marker = "PRIVATE-ASSISTANT-LEARNING-MARKER"
    learn(api, marker, token="ana-test")
    learn(api, marker, layer="company", token="ana-test")
    earlier = say(api, marker, "ana-test")["message"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE message_id=?", (earlier["id"],))
    t = task(api, private=False)
    machine, attempt, initiating = start(api, t["id"], person="ana-test")
    assert attempt["learned"] is None
    assert marker not in json.dumps(attempt)
    key = "claim:" + initiating["id"]
    with api.app.state.store.transaction() as c:
        saved = c.execute("SELECT response FROM idempotency WHERE key=?", (key,)).fetchone()
        payload = json.loads(saved["response"])
        payload["attempt"].update(learned={"personal": [marker], "company": {"vision": marker}},
                                  history=[earlier, initiating], notes=[{"text": marker}])
        c.execute("UPDATE idempotency SET response=? WHERE key=?", (encode(payload), key))
    replay = post(api, "jobs/claim", {"next_run": True}, machine["token"], key=key)
    assert marker not in json.dumps(replay)
    assert replay["attempt"]["learned"] is None
    assert [m["id"] for m in replay["attempt"]["history"]] == [initiating["id"]]
    token, cid = attempt["token"], earlier["conversation_id"]
    for path in (f"messages/{earlier['id']}", f"conversations/{cid}/messages", f"conversations/{cid}/snapshot",
                 f"threads/{cid}", f"threads/{cid}/timeline", "threads/search?q=" + marker,
                 "assistant/learned", "conversations", "inbox", "notes", "bots/coo/memory"):
        refused = get(api, path, token, expected=404)
        assert marker not in json.dumps(refused)
    tools = rpc(api, "tools/list", token=token)["result"]["tools"]
    assert {tool["name"] for tool in tools} == {
        "hub_task_context", "hub_file_read", "hub_meeting_read", "hub_page_show", "hub_page_publish"}
    for name, args in (("hub_conversation_show", {"id": cid}), ("hub_assistant_learned", {}),
                       ("hub_message_list", {})):
        error, result = call(api, name, args, token=token)
        assert error and marker not in json.dumps(result)
    made = page(api, attempt, task=t["id"])
    assert made.status_code == 200, made.text
    published = api.get("/api/v2/files/" + made.json()["page"]["file_id"], headers=headers("cara-test"))
    assert published.status_code == 200 and marker.encode() not in published.content


def test_make_page_cannot_bypass_page_cleaning_with_a_generic_upload(api):
    t = task(api, private=False)
    _, attempt, _ = start(api, t["id"])
    result = api.post("/api/v2/files/uploads", params={"name": "raw.html", "task": t["id"]}, content=HTML,
                      headers=headers(attempt["token"]))
    assert result.status_code == 403, result.text
    assert_no_page(api)


def test_make_page_does_not_inherit_ordinary_turn_writes(api):
    t = task(api, private=False)
    _, attempt, _ = start(api, t["id"])
    for path, body in (("messages", {"to": "human:ana", "text": "Unreviewed output"}),
                       ("assistant/actions", {"method": "POST", "path": "/api/v2/tasks", "body": {}})):
        post(api, path, body, attempt["token"], expected=403)
    assert_no_page(api)


def test_publishing_after_reading_ten_context_pages_does_not_fail_its_own_read_flush(api):
    t = task(api, private=False)
    _, publisher = turn(api, "cpo")
    for i in range(10):
        result = page(api, publisher, name=f"source-{i}.html", task=t["id"], data=HTML + str(i).encode())
        assert result.status_code == 200, result.text
    _, attempt, _ = start(api, t["id"])
    sources = attempt["task_context"]["pages"]
    assert len(sources) == 10
    assert api.get("/api/v2/files/" + sources[-1]["id"], headers=headers(attempt["token"])).status_code == 200
    result = page(api, attempt, name="combined.html", task=t["id"])
    assert result.status_code == 200, result.text
    assert status(api, t["id"])["state"] == "made"
