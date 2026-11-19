"""Normal task follow-ups from a live Tico Meet call."""

import json
import threading

import httpx

from backend import call_media, call_notes, hubdb as H, run_failures, task_privacy
from backend.tests.helpers.api import assign, get, post, runner, unrelated_task_managers
from backend.tests.helpers.calls import (
    PCM_ONE_SECOND,
    SILENCE_ONE_SECOND,
    join,
    meet,  # noqa: F401 - installs the external call media and Gateway doubles
    private_lanes_ready,
    speech_response,
    start_call,
    thread,
)
from backend.tests.helpers.credentials import setup as credential_setup
from backend.tests.helpers.live_meetings import live  # noqa: F401 - the meet fixture needs the live API
from backend.tests.helpers.model_subscriptions import add, company, give, machine, on
from backend.tests.helpers.run_failures import at
from backend.tests.threads_e2e import add_member, create_thread, ok, remove_member


def _machine(api):
    """A runner for ops with a Claude subscription: voice call turns run only on a plan."""
    credential_setup(api)
    company(api)
    default = get(api, "providers")["default"]
    on(api, "ops", default["runtime"], default["model"])
    worker = machine(api, signed_in=True, bot="ops", caps=("model_subscriptions_v1", "private_lanes_v1"))
    give(api, worker, "claude", add(api)["id"])
    return worker


def _claim(api, machine):
    attempt = post(api, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"]
    assert attempt
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "run-" + attempt["id"]}, token=machine["token"])
    return attempt


def _prepared_call(api, thread_id, machine):
    call = start_call(api, thread_id, bots=["ops"])
    join(api, call["id"])
    prep = _claim(api, machine)
    assert prep["message"]["refs"]["call_prep"] == call["id"]
    post(api, f"tasks/{prep['task']['id']}/done", {"version": prep["task"]["version"]}, prep["token"])
    post(api, f"attempts/{prep['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    return call


def _follow_up(api, call, ask, token="ana-test", **kwargs):
    return post(api, f"calls/{call['id']}/follow-ups", {"ask": ask}, token=token, **kwargs)


def _messages(api, thread_id, task_id, token="ben-test"):
    return [m for m in get(api, f"conversations/{thread_id}/messages", token) if m["refs"].get("task") == task_id]


def test_an_attached_bot_hands_its_question_to_a_normal_task_only_the_thread_can_read(meet):
    with meet.app.state.store.transaction() as c:
        unrelated_task_managers(c, "ops")
    machine = _machine(meet)
    thread_id = thread(meet)
    call = _prepared_call(meet, thread_id, machine)
    assert _follow_up(meet, call, "Find the ship date", "ben-test", expected=403)["error"]["code"] == "forbidden"
    assert _follow_up(meet, call, "Find the ship date", "cara-test", expected=404)["error"]["code"] == "not_found"
    calls = meet.app.state.calls
    for i in range(7):
        post(
            meet,
            f"live-meetings/{call['meeting_id']}/chunks",
            {
                "chunks": [
                    {
                        "seq": i + 1,
                        "speaker": "Ana",
                        "start_ms": i * 1000,
                        "end_ms": (i + 1) * 1000,
                        "text": f"Calendar context {i}",
                    }
                ]
            },
        )
    post(meet, f"live-meetings/{call['meeting_id']}/chat", {"text": "Ops, can you look up the ship date?"})
    attempt = _claim(meet, machine)
    assert _follow_up(meet, call, "Find the ship date", machine["token"], expected=403)["error"]["code"] == "identity"
    task_id = _follow_up(meet, call, "Find the ship date", attempt["token"], key="follow-up")["task_id"]
    assert _follow_up(meet, call, "Find the ship date", attempt["token"], key="follow-up") == {"task_id": task_id}
    next_task = _follow_up(meet, call, "Check the freight window", attempt["token"])["task_id"]
    shown = get(meet, f"tasks/{task_id}", "ben-test")
    assert (shown["task"]["title"], shown["task"]["owner_id"]) == ("Find the ship date", "bot:ops")
    [request] = [m["body"] for m in shown["messages"] if m["body"].startswith("Find the ship date")]
    assert request.splitlines() == [
        "Find the ship date",
        "",
        f"[Call](/#/thread/{thread_id}?call={call['id']})",
        "",
        "Recent call transcript:",
        "Ana: Calendar context 2",
        "Ana: Calendar context 3",
        "Ana: Calendar context 4",
        "Ana: Calendar context 5",
        "Ana: Calendar context 6",
    ]
    get(meet, f"tasks/{task_id}", "cara-test", expected=404)

    post(
        meet,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Let me check."},
        machine["token"],
    )
    assert calls.settle()
    worker = _claim(meet, machine)
    assert (worker["task"]["id"], worker["lane"], worker.get("live_meeting")) == (task_id, "task:" + task_id, None)
    post(
        meet,
        f"attempts/{worker['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "The shipping date is still pending."},
        machine["token"],
    )
    next_run = _claim(meet, machine)
    assert next_run["task"]["id"] == next_task
    with meet.app.state.store.read() as c:
        assert task_privacy.attempt_tasks(c, next_run["id"]) == {next_task}


def test_a_task_answer_waits_for_speech_and_an_open_question_then_returns_once(meet, monkeypatch):
    machine = _machine(meet)
    thread_id = thread(meet)
    call = _prepared_call(meet, thread_id, machine)
    call_id = call["id"]
    task_id = _follow_up(meet, call, "Find the launch date")["task_id"]
    calls, media = meet.app.state.calls, meet.app.state.call_media
    synthesized = []
    answer_text = "Back on Find the launch date: The rollout starts on Friday."
    requested, release = threading.Event(), threading.Event()

    def speech(request):
        text = json.loads(request.content)["input"]
        synthesized.append(text)
        if text == answer_text and not requested.is_set():
            requested.set()
            assert release.wait(10), "the speaker never resumed during follow-up synthesis"
        return speech_response()

    monkeypatch.setattr(call_media, "SPEECH_TRANSPORT", httpx.MockTransport(speech))
    worker = _claim(meet, machine)
    assert worker["task"]["id"] == task_id
    post(meet, f"live-meetings/{call['meeting_id']}/chat", {"text": "Ops, is the agenda ready?"})
    post(
        meet,
        f"tasks/{task_id}/done",
        {"note": "The rollout starts on Friday.", "version": worker["task"]["version"]},
        worker["token"],
    )
    calls.sweep()
    [answer] = _messages(meet, thread_id, task_id)
    assert answer["sender_id"] == "bot:ops"
    assert answer["refs"]["turn_id"] == worker["id"]
    assert answer["body"] == f"{answer_text}\n\n[Open task](#/task/{task_id})"
    post(
        meet,
        f"attempts/{worker['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "I checked the calendar."},
        machine["token"],
    )
    calls.sweep()
    [settled_answer] = _messages(meet, thread_id, task_id)
    assert settled_answer["id"] == answer["id"]
    answer = settled_answer
    calls.voice.tick()
    assert synthesized == []  # a question is still open
    turn = _claim(meet, machine)
    post(
        meet,
        f"attempts/{turn['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Yes, the agenda is ready."},
        machine["token"],
    )
    assert calls.settle()
    assert synthesized == ["Yes, the agenda is ready."]
    calls.ingest(call_id, "human:ana", PCM_ONE_SECOND, 0)
    calls.voice.tick()
    assert len(media.said) == 1
    calls.ingest(call_id, "human:ana", SILENCE_ONE_SECOND, 1000)
    assert calls.settle()
    calls.voice.tick()
    try:
        assert requested.wait(10), "the follow-up never requested speech at the pause"
        calls.ingest(call_id, "human:ana", PCM_ONE_SECOND, 2000)
    finally:
        release.set()
    assert calls.settle()
    assert len(media.said) == 1
    calls.ingest(call_id, "human:ana", SILENCE_ONE_SECOND, 3000)
    assert calls.settle()
    calls.voice.tick()
    assert calls.settle()
    assert synthesized == ["Yes, the agenda is ready.", answer_text, answer_text]
    assert [one[0] for one in media.said] == [call_id, call_id]
    calls.sweep()
    calls.voice.tick()
    assert _messages(meet, thread_id, task_id) == [answer]
    assert len(media.said) == 2


def test_an_answer_after_end_is_in_the_thread_and_notes_but_not_spoken(meet):
    machine = _machine(meet)
    thread_id = thread(meet)
    call = _prepared_call(meet, thread_id, machine)
    call_id = call["id"]
    task_id = _follow_up(meet, call, "Check the next booking")["task_id"]
    worker = _claim(meet, machine)
    post(meet, f"calls/{call_id}/end", {})
    calls = meet.app.state.calls
    calls.sweep()
    post(meet, f"tasks/{task_id}/done", {"version": worker["task"]["version"]}, worker["token"])
    post(
        meet,
        f"attempts/{worker['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "The next booking is Tuesday."},
        machine["token"],
    )
    calls.sweep()
    calls.voice.tick()
    assert calls.settle()
    [answer] = _messages(meet, thread_id, task_id)
    assert answer["sender_id"] == "bot:ops"
    assert answer["body"].splitlines()[0] == "Back on Check the next booking: The next booking is Tuesday."
    assert meet.app.state.call_media.said == []
    assert get(meet, f"calls/{call_id}/notes", "ben-test")["follow_ups"] == [
        {
            "task_id": task_id,
            "title": "Check the next booking",
            "status": "done",
            "text": answer["body"].splitlines()[0],
        }
    ]


def test_closed_work_reports_no_answer_without_speaking(meet):
    machine = _machine(meet)
    thread_id = thread(meet)
    call = _prepared_call(meet, thread_id, machine)
    task_id = _follow_up(meet, call, "Check the menu")["task_id"]
    worker = _claim(meet, machine)
    post(meet, f"tasks/{task_id}/move", {"to": "closed", "why": "Plans changed."})
    calls = meet.app.state.calls
    calls.sweep()
    assert _messages(meet, thread_id, task_id) == []
    post(
        meet,
        f"attempts/{worker['id']}/complete",
        {"outcome": "interrupted", "last_seq": 0},
        machine["token"],
    )
    calls.sweep()
    calls.voice.tick()
    assert calls.settle()
    [answer] = _messages(meet, thread_id, task_id)
    assert answer["sender_id"] == "bot:ops"
    assert answer["body"].splitlines()[0] == "The follow-up on Check the menu ended without an answer."
    assert meet.app.state.call_media.said == []
    calls.sweep()
    assert _messages(meet, thread_id, task_id) == [answer]


def test_recovery_waits_for_a_retry_and_reports_a_final_disposition_once(meet, monkeypatch):
    machine = _machine(meet)
    thread_id = thread(meet)
    call = _prepared_call(meet, thread_id, machine)
    task_id = _follow_up(meet, call, "Verify the estimate")["task_id"]
    worker = _claim(meet, machine)
    post(
        meet,
        f"attempts/{worker['id']}/complete",
        {"outcome": "failed", "last_seq": 0, "text": "The outside service returned 503."},
        machine["token"],
    )
    calls = meet.app.state.calls
    calls.sweep()
    assert _messages(meet, thread_id, task_id) == []
    resumed = post(
        meet,
        f"jobs/{worker['job_id']}/reconcile",
        {
            "run_id": worker["id"],
            "decision": "resume",
            "note": "The service is available again. Try the estimate once more.",
            "acknowledge_uncertain_effects": True,
        },
    )
    assert resumed["state"] == "queued"
    calls.sweep()
    assert _messages(meet, thread_id, task_id) == []
    with meet.app.state.store.read() as c:
        until = run_failures.get(c, "ops")["until_at"]
    at(monkeypatch, meet, H.shift(until, seconds=1))
    private_lanes_ready(meet, machine, "ops")
    retry = _claim(meet, machine)
    assert retry["job_id"] == worker["job_id"]
    post(
        meet,
        f"attempts/{retry['id']}/complete",
        {"outcome": "failed", "last_seq": 0, "text": "The outside service returned 503 again."},
        machine["token"],
    )
    calls.sweep()
    assert _messages(meet, thread_id, task_id) == []
    post(
        meet,
        f"jobs/{retry['job_id']}/reconcile",
        {
            "run_id": retry["id"],
            "decision": "dismiss",
            "note": "The service is still unavailable. Stop looking up the estimate.",
            "acknowledge_uncertain_effects": True,
        },
    )
    calls.sweep()
    calls.voice.tick()
    assert calls.settle()
    [answer] = _messages(meet, thread_id, task_id)
    assert answer["body"].splitlines()[0] == "The follow-up on Verify the estimate failed before it could answer."
    assert meet.app.state.call_media.said == []
    calls.sweep()
    assert _messages(meet, thread_id, task_id) == [answer]


def test_requester_note_survives_recorder_recovery_with_its_author(meet):
    machine = _machine(meet)
    thread_id = thread(meet)
    call = _prepared_call(meet, thread_id, machine)
    call_id = call["id"]
    task_id = _follow_up(meet, call, "Check the rollout date")["task_id"]
    worker = _claim(meet, machine)
    post(meet, f"tasks/{task_id}/done", {"note": "Friday", "version": worker["task"]["version"]})
    calls, media = meet.app.state.calls, meet.app.state.call_media
    media.stop_recorder(call_id)
    media.down = True
    calls.sweep()
    [answer] = _messages(meet, thread_id, task_id)
    assert answer["sender_id"] == "human:ana"
    assert answer["body"].splitlines()[0] == "Back on Check the rollout date: Friday"
    assert "turn_id" not in answer["refs"]
    with meet.app.state.store.read() as c:
        assert (
            c.execute("SELECT notes FROM answers WHERE id=?", (answer["refs"]["answer_id"],)).fetchone()[0] == "Friday"
        )
        assert call_notes.text(c, call_id, "ops").endswith("Back on Check the rollout date: Friday")
    post(meet, f"attempts/{worker['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    calls.voice.tick()
    assert media.said == []

    media.down = False
    calls.sweep()
    calls.voice.forget(call_id)
    calls.sweep()
    calls.voice.tick()
    assert calls.settle()
    assert [item[0] for item in media.said] == [call_id]
    calls.voice.forget(call_id)
    calls.sweep()
    calls.voice.tick()
    assert calls.settle()
    assert _messages(meet, thread_id, task_id) == [answer]
    assert len(media.said) == 1


def test_requester_note_posts_while_an_unrelated_job_is_uncertain(meet):
    thread_id = thread(meet)
    call = start_call(meet, thread_id, bots=["ops"])
    join(meet, call["id"])
    task_id = _follow_up(meet, call, "Check the delivery date")["task_id"]
    store = meet.app.state.store
    with store.transaction() as c:
        task = H.task(c, task_id)
        room = H.conversation(c, task["conversation_id"])
        msg = H._write_message(c, "human:ana", None, "A separate finance question.", room, "say", {}, None, None)
        job_id = H.new_id()
        c.execute(
            "INSERT INTO jobs(id,message_id,bot_id,state,created_at) VALUES (?,?,?,'uncertain',?)",
            (job_id, msg["id"], "finance", H.now()),
        )
    post(meet, f"tasks/{task_id}/done", {"note": "Friday", "version": task["version"]})

    meet.app.state.calls.sweep()
    meet.app.state.calls.sweep()
    [answer] = _messages(meet, thread_id, task_id)
    assert (answer["sender_id"], answer["body"].splitlines()[0]) == (
        "human:ana",
        "Back on Check the delivery date: Friday",
    )
    with store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "uncertain"


def test_follow_up_access_follows_thread_membership(meet):
    with meet.app.state.store.transaction() as c:
        unrelated_task_managers(c, "ops", "finance")
    thread_id = create_thread(meet, title="Private launch")["id"]
    call = start_call(meet, thread_id, bots=["ops", "finance"])
    path = f"calls/{call['id']}/follow-ups"
    ask = "Check the release window"
    assert post(meet, path, {"ask": ask}, expected=422)["error"]["code"] == "follow_up_bot"
    task_id = post(meet, path, {"ask": ask, "bot": "ops"})["task_id"]
    get(meet, f"tasks/{task_id}", "cara-test", expected=404)
    ok(add_member(meet, "ana-test", thread_id, "human:cara"))
    with meet.app.state.store.read() as c:
        assert H.task_private_readable(
            c, "human:cara", {"id": task_id, "private": 1, "owner_id": "bot:ops", "requester_id": "human:ana"}
        )
    assert get(meet, f"tasks/{task_id}", "cara-test")["task"]["title"] == ask
    ok(remove_member(meet, "ana-test", thread_id, "human:cara"))
    get(meet, f"tasks/{task_id}", "cara-test", expected=404)


def test_restoring_a_follow_up_restores_its_call_link_and_audience(meet):
    with meet.app.state.store.transaction() as c:
        unrelated_task_managers(c, "ops")
    thread_id = thread(meet)
    call = start_call(meet, thread_id, bots=["ops"])
    task_id = _follow_up(meet, call, "Check the rehearsal time")['task_id']

    post(meet, f"tasks/{task_id}/delete", {})
    get(meet, f"tasks/{task_id}", "ben-test", expected=404)
    post(meet, f"tasks/{task_id}/restore", {})
    assert get(meet, f"tasks/{task_id}", "ben-test")["task"]["id"] == task_id
    get(meet, f"tasks/{task_id}", "cara-test", expected=404)
    assert [row["task_id"] for row in get(meet, f"calls/{call['id']}/notes", "ben-test")["follow_ups"]] == [task_id]
