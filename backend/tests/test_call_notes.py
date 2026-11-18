"""Per-bot call briefs and the tasks that fill them."""

from backend import call_notes, events
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, headers, post, ready, runner
from backend.tests.helpers.automation_cli import Client
from backend.tests.helpers.calls import join, meet, start_call, thread  # noqa: F401
from backend.tests.helpers.live_meetings import attach, chunk, live  # noqa: F401
from backend.tests.helpers.mcp import call
from backend.tests.helpers.openapi_v2 import conforms
from clients import cli_spec, hubcli


def test_cold_prep_queues_once_per_bot_and_call(meet):
    cid = thread(meet)
    original = start_call(meet, cid, title="Acme launch", bots=["ops"])
    cid2 = thread(meet, "Acme review")
    second = start_call(meet, cid2, title="Acme review", bots=["ops"])
    call_id = original["id"]
    join(meet, call_id, "ben-test")
    route = f"calls/{call_id}/bots"
    post(meet, route, {"bots": ["ops", "finance"]}, token="ben-test")
    post(meet, route, {"bots": ["finance"]}, token="ben-test")
    assert meet.request("DELETE", f"/api/v2/{route}/finance", json={}, headers=headers()).status_code == 200
    post(meet, route, {"bots": ["finance"]}, token="ben-test")

    with meet.app.state.store.read() as c:
        tasks = c.execute(
            "SELECT id,title,owner_id,requester_id FROM tasks WHERE title LIKE 'Prepare for %' ORDER BY title,owner_id"
        ).fetchall()
        assert [(t["title"], t["owner_id"], t["requester_id"]) for t in tasks] == [
            ("Prepare for Acme launch", "bot:finance", "human:ben"),
            ("Prepare for Acme launch", "bot:ops", "human:ana"),
            ("Prepare for Acme review", "bot:ops", "human:ana"),
        ]
        for task, expected in zip(tasks, [call_id, call_id, second["id"]], strict=True):
            words = H.task_words(c, task["id"])
            assert expected in words and "already live" in words and "skills/meeting-prep/SKILL.md" in words


def test_the_call_people_and_each_attached_bot_get_only_their_briefs(meet):
    call_id = start_call(meet, thread(meet), bots=["ops", "finance"])["id"]
    route = f"calls/{call_id}/bot-notes"
    assert get(meet, route, token="ops-live") == {"text": ""}
    post(meet, route, {"text": "Human note"}, expected=403)
    post(meet, route, {"text": "   "}, token="ops-live", expected=422)
    spec = hubcli.parser().parse_args(["call", "note", "--call", call_id, "Rollout reached 45%."])
    assert cli_spec.of(spec).call(Client(meet, "ops-live"), spec) == {"ok": True}
    assert call(meet, "tico_call_note", {"call_id": call_id, "text": "Spend reached $300."}, "finance-live") == (
        False,
        {"ok": True},
    )
    assert call(meet, "tico_call_notes", {"call_id": call_id, "bot": "ops"}, "ben-test") == (
        False,
        {"text": "Rollout reached 45%."},
    )
    assert get(meet, route, token="finance-live") == {"text": "Spend reached $300."}
    get(meet, route, expected=422)
    get(meet, route + "?bot=finance", token="ops-live", expected=404)
    get(meet, route + "?bot=ops", token="cara-test", expected=404)
    assert meet.request("DELETE", f"/api/v2/calls/{call_id}/bots/ops", json={}, headers=headers()).status_code == 200
    get(meet, route, token="ops-live", expected=404)
    post(meet, route, {"text": "After detach"}, token="ops-live", expected=404)
    assert get(meet, route + "?bot=ops", token="ben-test") == {"text": "Rollout reached 45%."}


def test_a_call_reply_starts_ahead_of_queued_prep(meet):
    call_view = start_call(meet, thread(meet), speak_replies=False)
    meeting_id = call_view["meeting_id"]
    # The live-meeting route can also add a bot to a call.
    assert attach(meet, meeting_id, "ops")["added"] == ["ops"]

    computer = runner(meet)
    assign(meet, computer, "ops")
    ready(meet, computer, ["ops"], capabilities=["private_lanes_v1"])
    chunk(meet, meeting_id, 1, 0, 45_000, "Can we cover reliability next?")
    reply = claim(meet, computer, "ops")
    assert reply["message"]["refs"]["live_meeting"]["meeting_id"] == meeting_id
    post(meet, f"attempts/{reply['id']}/started", {"thread_id": "synthetic-call"}, token=computer["token"])
    post(
        meet,
        f"attempts/{reply['id']}/complete",
        {"outcome": "completed", "text": "Yes, let's check reliability.", "last_seq": 0},
        token=computer["token"],
    )

    prep = claim(meet, computer, "ops")
    assert prep["task"]["title"] == "Prepare for Acme launch call"
    assert prep["message"]["refs"]["call_prep"] == call_view["id"]


def test_a_brief_keeps_the_newest_utf8_text_and_changes_send_no_text(meet):
    cid = thread(meet)
    call_id = start_call(meet, cid, bots=["ops"])["id"]
    route = f"calls/{call_id}/bot-notes"
    long = "é" * 4_000
    assert post(meet, route, {"text": long}, token="ops-live", key="prep") == {"ok": True}
    assert post(meet, route, {"text": long}, token="ops-live", key="prep") == {"ok": True}
    with meet.app.state.store.transaction() as c:
        call_notes.append(c, call_id, "ops", "123")
    expected = {"text": "é" * 3_997 + "\n\n123"}
    assert get(meet, route + "?bot=ops") == expected
    auth = meet.app.state.auth
    with meet.app.state.store.read() as c:
        assert call_notes.text(c, call_id, "ops") == expected["text"]
        assert call_notes.text(c, call_id, "finance") == ""
        for actor, count in [("human:ben", 2), ("human:cara", 0)]:
            who = auth.identity_for_actor(c, actor)
            sent, _, _, _ = events.read(c, auth, who, 0, topics=("threads",))
            assert [
                {key: value for key, value in data.items() if key not in ("seq", "at")}
                for _, _, data in sent
                if data.get("kind") == "call.notes"
            ] == [{"kind": "call.notes", "call_id": call_id, "thread_id": cid, "actor": "bot:ops"}] * count

    document = get(meet, "openapi.json")
    contract = document["paths"]["/api/v2/calls/{call_id}/bot-notes"]
    for method, result in [("get", expected), ("post", {"ok": True})]:
        schema = contract[method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert conforms(result, schema, document) is None
