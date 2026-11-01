"""Tico Meet in a thread, end to end over the API with LiveKit and the Gateway faked: the call's transcript is one
message in the thread, read by the thread's people and by nobody else. Synthetic names only (Acme, acme.example)."""
from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.tests.test_calls import PCM_EIGHT_SECONDS, SYNTHETIC_SPEECH, join, meet, start_call, thread  # noqa: F401
from backend.tests.test_live_meetings import live, settle  # noqa: F401


def test_the_call_transcript_is_read_by_the_thread_members_and_by_no_one_else(meet):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    join(meet, call_id, "ana-test")
    for index in range(2):
        meet.app.state.calls.ingest(call_id, "human:ana", PCM_EIGHT_SECONDS, index * 8000)
    settle(meet)
    post(meet, f"calls/{call_id}/end", {}, token="ana-test")

    member = meet.get(f"/api/v2/conversations/{cid}/messages", headers=headers("ben-test"))
    assert member.status_code == 200, member.text
    transcripts = [m["body"] for m in member.json()["messages"] if m["body"].startswith("Call transcript")]
    assert len(transcripts) == 1 and SYNTHETIC_SPEECH in transcripts[0]

    outsider = meet.get(f"/api/v2/conversations/{cid}/messages", headers=headers("cara-test"))
    assert outsider.status_code in (403, 404), outsider.text[:200]
    assert SYNTHETIC_SPEECH not in outsider.text
