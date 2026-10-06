"""Documented MCP text responses, including Granola's deliberately non-XML markdown."""
import json
from datetime import datetime, timezone

import httpx
import pytest

from backend.granola_mcp import GranolaError, GranolaMCP
from backend.tests.test_granola_mcp import BASE, Provider, api, headers  # noqa: F401


GET_MEETINGS = '''<meetings_data from="Feb 4, 2026" to="Feb 4, 2026" count="1">
<meeting id="0dba4400-..." title="Team sync" date="Feb 4, 2026 7:30 PM">
  <known_participants>
  John Doe (note creator) from Acme <john@acme.com>
  Jane Smith from Acme <jane@acme.com>
  </known_participants>
  <summary>
## Key Decisions
- Budget < 15% & roadmap approved
</summary>
</meeting>
</meetings_data>'''
LIST_MEETINGS = GET_MEETINGS.replace('  <summary>\n## Key Decisions\n- Budget < 15% & roadmap approved\n</summary>\n', '')
FREE_TRANSCRIPT = {"content": [{"type": "text", "text": "Transcripts are only available to paid Granola tiers"}], "isError": True}
PAID_TRANSCRIPT = '<transcript meeting_id="0dba4400-...">[00:00:15] John: ...</transcript>'
SUMMARY = "## Key Decisions\n- Budget < 15% & roadmap approved"


def text_result(raw):
    return {"content": [{"type": "text", "text": raw}]}


def test_documented_meeting_text_and_private_notes():
    listed = GranolaMCP.content(text_result(LIST_MEETINGS))["meetings"][0]
    assert "summary" not in listed
    assert listed["attendees"] == [{"name": "John Doe", "email": "john@acme.com"},
                                   {"name": "Jane Smith", "email": "jane@acme.com"}]
    raw = GET_MEETINGS.replace('</meeting>', '<private_notes><summary>Never import</summary></private_notes></meeting>')
    note = GranolaMCP.content(text_result(raw))["meetings"][0]
    item = GranolaMCP.item(note)
    assert item.notes == SUMMARY and item.title == "Team sync"
    assert item.started_at == "2026-02-04T19:30:00+00:00"
    assert "Never import" not in item.model_dump_json()
    assert GranolaMCP.content(text_result(PAID_TRANSCRIPT))["transcript"] == "[00:00:15] John: ..."


@pytest.mark.parametrize("value,expected", [
    ("2026-02-04T19:30:00-08:00", "2026-02-04T19:30:00-08:00"), ("Feb 31, 2026", None),
])
def test_provider_dates_are_utc_and_unknown_dates_keep_meetings(value, expected):
    item = GranolaMCP.item({"id": "meeting", "summary": "Shared notes", "date": value})
    assert item.started_at == expected


def test_summary_markup_is_preserved_and_nested_private_fields_are_ignored():
    summary = 'Budget < 15% & example <notes>shared</notes> <meeting id="example">markup</meeting>'
    raw = '<meetings><meeting id="shared"><private_notes><summary>Private</summary><meeting id="secret"/></private_notes>'
    raw += '<unknown><summary>Also private</summary></unknown><summary>' + summary + '</summary></meeting></meetings>'
    value = GranolaMCP.xml_content(raw)
    assert len(value["meetings"]) == 1
    assert GranolaMCP.item(value["meetings"][0]).notes == summary


@pytest.mark.parametrize("declaration", ['<!ENTITY x "secret">'])
def test_xml_declarations_are_refused(declaration):
    with pytest.raises(GranolaError, match="bad_response"):
        GranolaMCP.content(text_result(declaration + GET_MEETINGS))


@pytest.mark.parametrize("paid", [False])
def test_documented_free_and_paid_sync_with_broken_meeting(api, paid):
    provider = Provider(api)
    provider.now = datetime(2026, 2, 5, tzinfo=timezone.utc).timestamp()
    provider.paid = True  # Free accounts can advertise the transcript tool too.
    assert provider.connect()["plan_hint"] == "free"
    previous = provider.handle
    transcript_calls = []

    def handle(request):
        if request.url.path == "/mcp":
            name = json.loads(request.content).get("params", {}).get("name")
            if name == "list_meetings":
                raw = LIST_MEETINGS.replace('<meeting id=', '<meeting title="Broken">\n<meeting id=', 1)
                raw = raw.replace('</meetings_data>', '<meeting id="outside-window" date="Jan 1, 2026"/></meetings_data>')
                return httpx.Response(200, json={"result": text_result(raw)})
            if name == "get_meetings":
                assert "outside-window" not in json.loads(request.content)["params"]["arguments"]["meeting_ids"]
                # Several notes ensure a free-tier denial stops after the first request.
                raw = GET_MEETINGS.replace('</meetings_data>', '<meeting id="second"><summary>Shared notes</summary></meeting></meetings_data>')
                return httpx.Response(200, json={"result": text_result(raw)})
            if name == "get_meeting_transcript":
                transcript_calls.append(request)
                return httpx.Response(200, json={"result": text_result(PAID_TRANSCRIPT) if paid else FREE_TRANSCRIPT})
        return previous(request)

    provider.service.transport = httpx.MockTransport(handle)
    provider.sync()
    status = api.get(BASE, headers=headers("ana-test")).json()
    assert status["last_sync"] and status["imported_count"] == 2 and status["skipped"] == 1
    assert status["plan_hint"] == ("paid" if paid else "free")
    assert status["last_error"] == "bad_response: list_meetings"
    assert len(transcript_calls) == (2 if paid else 1)
    assert provider.service.load("human:ana")[1]["transcripts_unavailable"] is (not paid)
    with api.app.state.store.read() as c:
        row = c.execute("SELECT m.notes,m.metadata_json FROM meetings m JOIN recording_source_refs r ON r.meeting_id=m.id "
                        "WHERE r.source='granola' AND r.external_id='human:ana:0dba4400-...'").fetchone()
        meeting = json.loads(row["metadata_json"])
        assert row["notes"] == SUMMARY and meeting["started"].startswith("2026-02-04T19:30:00")
        assert [(p["name"], p["email"]) for p in meeting["participants"]] == [
            ("John Doe", "john@acme.com"), ("Jane Smith", "jane@acme.com")]
        assert bool(meeting.get("turns")) is paid
    provider.sync()
    assert len(transcript_calls) == (4 if paid else 2)  # Recheck access after an upgrade.


@pytest.mark.parametrize("step", ["refresh_token"])
def test_failures_name_the_step_and_log_only_a_fixed_code(api, caplog, step):
    provider = Provider(api)
    provider.connect()
    previous = provider.handle

    def handle(request):
        if request.url.path == "/oauth2/token" and step == "refresh_token":
            return httpx.Response(200, text="fake-refresh-sensitive")
        if request.url.path == "/mcp":
            body = json.loads(request.content)
            name = body.get("params", {}).get("name") or body["method"]
            if name == step:
                return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": "fake-access-sensitive"}]}}
                                      if step in ("list_meetings", "get_meetings") else {"result": []})
        return previous(request)

    provider.service.transport = httpx.MockTransport(handle)
    if step == "refresh_token":
        provider.now += 3600
    provider.sync()
    status = api.get(BASE, headers=headers("ana-test")).json()
    assert status["last_error"] == "bad_response: " + step
    messages = [record.getMessage() for record in caplog.records if record.name == "backend.granola_mcp"]
    assert messages == ["bad_response: " + step]
    assert "fake-access-sensitive" not in caplog.text and "fake-refresh-sensitive" not in caplog.text
