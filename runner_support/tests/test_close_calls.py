"""Close transcript sync uses provider text only; fixtures contain no audio or credential."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clients.tico import APIError
from runner import close_calls as CC
from runner.state import State

NOW = datetime(2026, 9, 19, 20, 0, tzinfo=timezone.utc)
CREATED = "2026-09-19T18:00:00+00:00"


def call(cid="acti_call", **changes):
    row = {"id": cid, "lead_id": "lead_1", "contact_id": "cont_1", "user_id": "user_1",
           "direction": "outbound", "duration": 120, "status": "completed",
           "date_created": CREATED, "date_updated": "2026-09-19T18:03:00+00:00",
           "activity_at": CREATED, "remote_phone": "+16195550123",
           "recording_url": "https://api.close.com/secret-recording"}
    row.update(changes)
    return row


def meeting(cid="acti_meeting", **changes):
    row = {"id": cid, "lead_id": "lead_1", "user_id": "user_1", "title": "Pricing demo",
           "starts_at": CREATED, "ends_at": "2026-09-19T18:30:00+00:00",
           "status": "completed", "date_created": CREATED, "activity_at": CREATED,
           "date_updated": "2026-09-19T18:35:00+00:00",
           "provider_calendar_event_id": "calendar-event-1", "attached_call_ids": ["acti_call"]}
    row.update(changes)
    return row


def transcript(words="Please send pricing."):
    return {"utterances": [{"speaker_label": "Dana Reyes", "speaker_side": "contact",
                            "start": 1.2, "end": 3.4, "text": words}],
            "summary_text": "Pricing requested."}


OWNER = "ana@acme.example"


class Hub:
    def __init__(self, refuse_owner=False):
        self.posts = []
        self.refuse_owner = refuse_owner
        self.hashes = set()

    def get(self, path):
        assert path == "config"
        return {"owner_email": OWNER}

    def post(self, path, body):
        self.posts.append((path, body))
        if path == "imports/transcripts":
            if self.refuse_owner and body["owner_email"] != OWNER:
                raise APIError("not_found", "Owner is not on the roster", 404)
            key = (body["resource_type"], body["external_id"], str(body["turns"]))
            changed = key not in self.hashes
            self.hashes.add(key)
            return {"id": "tico-1", "status": "done", "changed": changed}
        return {"ok": True}

    def imports(self):
        return [b for path, b in self.posts if path == "imports/transcripts"]


class Close:
    def __init__(self, calls=(), meetings=(), *, call_transcript=None, meeting_transcript=None):
        self.calls, self.meetings = list(calls), list(meetings)
        self.call_transcript = call_transcript
        self.meeting_transcript = meeting_transcript
        self.seen = []

    def __call__(self, url, params=None):
        self.seen.append((url, params))
        if url == "activity/call/":
            rows = [r for r in self.calls if params["date_created__gte"] <= r["date_created"]
                    <= params["date_created__lte"]]
            return {"data": rows if not params["_skip"] else [], "has_more": False}
        if url == "activity/meeting/":
            rows = [r for r in self.meetings if params["date_created__gte"] <= r["date_created"]
                    <= params["date_created__lte"]]
            return {"data": rows if not params["_skip"] else [], "has_more": False}
        if url.startswith("activity/call/"):
            return {**self.calls[0], "recording_transcript": self.call_transcript}
        if url.startswith("activity/meeting/"):
            return {**self.meetings[0], "transcripts": [self.meeting_transcript] if self.meeting_transcript else []}
        if url.startswith("lead/"):
            return {"display_name": "Dana Reyes", "url": "/lead/lead_1/"}
        if url.startswith("user/"):
            return {"email": "ben@acme.example"}
        if url.startswith("contact/"):
            return {"name": "Dana Reyes"}
        raise AssertionError(url)


@pytest.fixture
def setup():
    with tempfile.TemporaryDirectory() as directory:
        def build(close, hub=None):
            client = hub or Hub()
            worker = CC.CloseCallImporter(
                {"url": "https://tico.test", "token": "runner-token", "projects_dir": directory},
                client=client, transport=close, state=State(Path(directory) / "state"),
                now=lambda: NOW)
            return worker, client
        yield build


def test_late_transcript_is_retried_and_revisions_are_idempotent(setup):
    close = Close([call()], call_transcript=None)
    worker, hub = setup(close)
    assert worker.tick() == 0
    assert worker.state.close_pending()[0]["external_id"] == "acti_call"
    assert hub.imports() == []
    close.call_transcript = transcript()
    assert worker.tick() == 1
    assert worker.state.close_pending() == []
    assert worker.tick() == 0
    close.call_transcript = transcript("Please send the revised pricing.")
    assert worker.tick() == 1
    assert len(hub.imports()) == 3  # repeat poll is a safe no-op at the hub
    assert hub.imports()[-1]["turns"][0]["text"] == "Please send the revised pricing."


def test_a_pending_activity_close_deleted_leaves_the_queue_and_the_rest_are_still_checked(setup):
    # 2026-09-28 and 2026-10-02: one deleted activity (HTTP 404) failed every pull for hours.
    close = Close([call()], call_transcript=None)
    worker, hub = setup(close)
    worker.tick()
    worker.state.close_pending("call", "acti_gone", created="2026-09-18T18:00:00+00:00", checked="2026-09-18T18:00:00+00:00")
    plain = close.__call__

    def deleted(url, params=None):
        if url == "activity/call/acti_gone/":
            raise CC.CloseError("Close returned HTTP 404", 404)
        return plain(url, params)
    worker.transport = deleted
    worker.now = lambda: NOW.replace(hour=21)
    close.call_transcript = transcript()
    assert worker.tick() == 1                       # the call behind it came in
    assert [p["external_id"] for p in worker.state.close_pending()] == []
    assert hub.posts[-1] == ("imports/sources/close/status", {"state": "ok", "pending": 0, "imported": 1})
