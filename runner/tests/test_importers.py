"""Meeting importers, offline: recorded provider responses in, hub import bodies out.

The fixtures follow the providers' published schemas (Granola v1 OpenAPI,
Zoom Meetings API, Google Meet REST v2). Nothing here touches a network, and no credential is real.
"""

import base64
import json
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from backend.imports import MeetingImport
from clients.tico import APIError
from runner.importers import REGISTRY, google_meet, granola, zoom
from runner.importers.base import ProviderError
from runner.importers.service import ImporterService
from runner.state import State

NOW = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)
OWNER = "ana@acme.example"
KEY = "sk-live-SECRET-KEY-123"


class Hub:
    """Just enough of the hub: the import door keyed like the real one, and the status door."""

    def __init__(self, roster=(OWNER, "ben@acme.example")):
        self.roster, self.meetings, self.posts, self.statuses = set(roster), {}, [], []

    def get(self, path, **query):
        if path == "config":
            return {"owner_email": OWNER}
        if path == "runners/importers":
            return {"importers": [{"source": s} for s in getattr(self, "enabled", [])]}
        raise AssertionError(path)

    def post(self, path, body=None, key=None):
        if path.startswith("imports/sources/"):
            self.statuses.append((path.split("/")[2], body))
            return {"ok": True}
        assert path == "meetings/import"
        self.posts.append(body)
        MeetingImport.model_validate(body)              # the real contract accepts every body
        if body["owner_email"] not in self.roster:
            raise APIError("not_found", "owner_email must name a person on the Tico roster", 404)
        key = (body["source"], body["owner_email"], body["external_id"])
        existing = key in self.meetings
        changed = self.meetings.get(key) != body
        self.meetings[key] = body
        return {"id": "m" + str(len(self.meetings)), "existing": existing, "changed": changed, "status": "done"}


def make(cls, tmp_path, transport, env, **extra):
    hub = extra.pop("hub", None) or Hub()
    state = State(tmp_path / "state")
    config = {"projects_dir": str(tmp_path / "projects"), "url": "https://hub.test", "token": "t"}
    return cls(config, state, hub, env=env, transport=transport, now=lambda: NOW, **extra), hub, state


class Recorder:
    """A transport that answers from a routing function and remembers every call."""

    def __init__(self, route):
        self.route, self.calls = route, []

    def __call__(self, method, url, headers=None, body=None, form=None, raw=False):
        parsed = urllib.parse.urlsplit(url)
        call = SimpleNamespace(method=method, url=url, path=parsed.path, host=parsed.netloc, headers=headers or {},
                               query=dict(urllib.parse.parse_qsl(parsed.query)), body=body, form=form)
        self.calls.append(call)
        return self.route(call)


# ---------------------------------------------------------------- Granola

def gr_note(ident="not_1d3tmYTlCICgjy", **changes):
    row = {"id": ident, "object": "note", "title": "Quarterly review", "owner": {"name": "Ana Cruz", "email": OWNER},
           "created_at": (NOW - timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
           "updated_at": (NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z")}
    row.update(changes)
    return row


def gr_detail(row, transcript, **changes):
    detail = {**row, "web_url": "https://notes.granola.ai/d/abc", "calendar_event": {
        "event_title": "Quarterly review", "invitees": [{"email": "dana@example.com"}], "organiser": OWNER,
        "calendar_event_id": "evt1", "scheduled_start_time": "2026-09-28T17:00:00Z", "scheduled_end_time": "2026-09-28T18:00:00Z"},
        "attendees": [{"name": "Ana Cruz", "email": OWNER}, {"name": "Dana Reyes", "email": "dana@example.com"}],
        "folder_membership": [], "summary_text": "Plain", "summary_markdown": "## Decisions\nHold price.",
        "private_notes_text": "SECRET SCRATCH", "private_notes_markdown": "SECRET SCRATCH", "transcript": transcript}
    detail.update(changes)
    return detail


T0 = "2026-09-28T17:00:05Z"
TRANSCRIPT = [
    {"speaker": {"source": "speaker", "attribution": "them", "name": "Dana Reyes"}, "text": "Can you send pricing?",
     "start_time": T0, "end_time": "2026-09-28T17:00:08Z"},
    {"speaker": {"source": "microphone", "attribution": "me"}, "text": "Ten percent.",
     "start_time": "2026-09-28T17:01:15Z", "end_time": "2026-09-28T17:01:17Z"},
    {"speaker": {"source": "speaker", "attribution": "them"}, "text": "Great.", "start_time": "2026-09-28T17:02:00Z",
     "end_time": "2026-09-28T17:02:01Z"}]


def gr_route(pages, details, transcripts=None, too_large=()):
    def route(call):
        parts = call.path.split("/")
        if call.path == "/v1/notes":
            return pages[int(call.query.get("cursor") or 0)]
        note = parts[3]
        if len(parts) == 5:
            data = transcripts[note]
            start = int(call.query.get("cursor") or 0)
            size = int(call.query["page_size"])
            chunk = data[start:start + size]
            more = start + size < len(data)
            return {"transcript": chunk, "hasMore": more, "cursor": str(start + size) if more else None}
        if note in too_large and call.query.get("include"):
            raise ProviderError("provider_error", "Granola returned HTTP 413", 413)
        return details[note]
    return route


def test_granola_uses_the_official_api_and_keeps_private_notes_out(tmp_path):
    row = gr_note()
    detail = gr_detail(row, TRANSCRIPT)
    transport = Recorder(gr_route([{"notes": [row], "hasMore": False, "cursor": None}], {row["id"]: detail}))
    importer, hub, _ = make(granola.Granola, tmp_path, transport, {"GRANOLA_API_KEY": "grn_x"})
    assert importer.tick() == 1
    body = hub.posts[0]
    assert (body["source"], body["external_id"], body["owner_email"]) == ("granola", row["id"], OWNER)
    assert body["private"] is True                                 # personal notes stay personal by default
    assert body["transcript"] == [
        {"speaker": "Dana Reyes", "start_ms": 0, "end_ms": 3000, "text": "Can you send pricing?"},
        {"speaker": "Ana Cruz", "start_ms": 70000, "end_ms": 72000, "text": "Ten percent."},
        {"speaker": "Others", "start_ms": 115000, "end_ms": 116000, "text": "Great."}]
    assert body["notes"] == "## Decisions\nHold price." and "SECRET SCRATCH" not in json.dumps(body)
    assert body["media_url"] == "https://notes.granola.ai/d/abc" and body["started_at"] == "2026-09-28T17:00:00+00:00"
    assert {"name": "Dana Reyes", "email": "dana@example.com"} in body["participants"]
    assert all(c.headers["Authorization"] == "Bearer grn_x" and c.host == "public-api.granola.ai" for c in transport.calls)
    listing = transport.calls[0]
    assert listing.query["created_after"] == "2026-08-29T20:00:00Z" and int(listing.query["page_size"]) <= 30


def test_granola_company_visibility_is_opt_in(tmp_path):
    row = gr_note()
    route = gr_route([{"notes": [row], "hasMore": False, "cursor": None}], {row["id"]: gr_detail(row, TRANSCRIPT)})
    importer, hub, _ = make(granola.Granola, tmp_path, Recorder(route), {"GRANOLA_API_KEY": "k", "GRANOLA_PRIVATE": "0"})
    importer.tick()
    assert "private" not in hub.posts[0]


def test_granola_paginates_notes_and_large_transcripts_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setattr(granola, "TRANSCRIPT_PAGE", 2)
    rows = [gr_note("not_" + str(i) * 14) for i in range(3)]
    pages = {0: {"notes": rows[:2], "hasMore": True, "cursor": "1"}, 1: {"notes": rows[2:], "hasMore": False, "cursor": None}}
    details = {r["id"]: gr_detail(r, None) for r in rows}
    long = {r["id"]: TRANSCRIPT for r in rows}
    transport = Recorder(gr_route(pages, details, long, too_large={r["id"] for r in rows}))
    importer, hub, _ = make(granola.Granola, tmp_path, transport, {"GRANOLA_API_KEY": "k"})
    assert importer.tick() == 3 and len(hub.meetings) == 3
    assert all(len(p["transcript"]) == 3 for p in hub.posts)
    pages_read = [c for c in transport.calls if c.path.endswith("/transcript")]
    assert len(pages_read) == 6                                # two pages per note
    before = len(hub.posts)
    assert importer.tick() == 0 and len(hub.posts) == before   # updated_at unchanged
    changed = {**pages[1], "notes": [gr_note(rows[2]["id"], updated_at="2026-09-28T19:59:00Z")]}
    pages[1] = changed
    importer.tick()
    assert len(hub.posts) == before + 1 and len(hub.meetings) == 3


# ---------------------------------------------------------------- Zoom

ZOOM_ENV = {"ZOOM_ACCOUNT_ID": "acct", "ZOOM_CLIENT_ID": "cid", "ZOOM_CLIENT_SECRET": "csecret"}
VTT = "WEBVTT\n\n1\n00:00:05.000 --> 00:00:08.000\nDana Reyes: Can you send pricing?\n\n2\n00:01:10.000 --> 00:01:12.000\nAna: Ten percent.\n"


def zm_meeting(uuid="abc+/def==", start="2026-09-28T17:00:00Z", **changes):
    row = {"uuid": uuid, "id": 88320100042, "topic": "Pricing call", "start_time": start, "duration": 45,
           "host_id": "u1", "share_url": "https://acme.zoom.us/rec/share/xyz",
           "recording_files": [{"id": "f1", "file_type": "MP4", "status": "completed", "download_url": "https://acme.zoom.us/rec/download/v"},
                               {"id": "f2", "file_type": "TRANSCRIPT", "file_extension": "VTT", "status": "completed",
                                "recording_type": "audio_transcript", "download_url": "https://acme.zoom.us/rec/download/t"}]}
    row.update(changes)
    return row


def zm_route(recordings, participants=None, users=None, vtt=VTT):
    users = users if users is not None else [{"id": "u1", "email": "Ana@Acme.example"}]

    def route(call):
        if call.host == "zoom.us":
            assert call.form == {"grant_type": "account_credentials", "account_id": "acct"}
            assert call.headers["Authorization"] == "Basic " + base64.b64encode(b"cid:csecret").decode()
            return {"access_token": "tok", "expires_in": 3600}
        assert call.headers["Authorization"] == "Bearer tok"
        if call.path == "/v2/users":
            return {"users": users, "next_page_token": ""}
        if call.path.endswith("/recordings"):
            token = call.query.get("next_page_token") or ""
            page = recordings(call, token)
            return page
        if "/participants" in call.path:
            if participants is None:
                raise ProviderError("forbidden", "denied", 403)
            return {"participants": participants, "next_page_token": ""}
        if "/rec/download/" in call.path:
            return vtt.encode()
        raise AssertionError(call.url)
    return route


def test_zoom_downloads_the_vtt_transcript_and_files_it_for_the_host(tmp_path):
    seen = {}
    def recordings(call, token):
        seen.update(call.query)
        return {"meetings": [zm_meeting()], "next_page_token": ""}
    parts = [{"name": "Dana Reyes", "user_email": "dana@example.com"}, {"name": "Ana", "user_email": ""}]
    transport = Recorder(zm_route(recordings, parts))
    importer, hub, _ = make(zoom.Zoom, tmp_path, transport, ZOOM_ENV)
    assert importer.tick() == 1
    body = hub.posts[0]
    assert (body["source"], body["owner_email"], body["format"]) == ("zoom", "ana@acme.example", "vtt")
    assert body["external_id"] == "abc-/def" and body["transcript"] == VTT and body["duration_seconds"] == 2700
    assert body["media_url"] == "https://acme.zoom.us/rec/share/xyz" and body["context"] == {"zoom_meeting_id": "88320100042"}
    assert {"name": "Dana Reyes", "email": "dana@example.com"} in body["participants"]
    assert "Ana" in body["participants"] and "ana@acme.example" in body["participants"]
    assert set(seen) >= {"from", "to", "page_size"} and int(seen["page_size"]) <= 300
    assert any("/past_meetings/abc%2B%2Fdef%3D%3D/participants" in c.url for c in transport.calls)
    downloads = [c for c in transport.calls if "/rec/download/" in c.path]
    assert len(downloads) == 1 and downloads[0].url.endswith("/rec/download/t")      # audio and video are never fetched


def test_zoom_refuses_a_download_outside_zoom_and_never_follows_redirects_elsewhere(tmp_path):
    bad = zm_meeting(recording_files=[{"id": "f2", "file_type": "TRANSCRIPT", "file_extension": "VTT", "status": "completed",
                                       "download_url": "https://evil.example/rec/download/t"}])
    importer, hub, _ = make(zoom.Zoom, tmp_path, Recorder(zm_route(lambda c, t: {"meetings": [bad], "next_page_token": ""}, [])), ZOOM_ENV)
    with pytest.raises(ProviderError) as caught:
        importer.tick()
    assert caught.value.code == "bad_response"
    assert zoom.zoom_host("https://us02web.zoom.us/x") and not zoom.zoom_host("https://zoom.us.evil.example/x")
    assert not zoom.zoom_host("http://zoom.us/x")


# ---------------------------------------------------------------- Google Meet

def service_account(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    path = tmp_path / "projects" / "secrets" / "sa.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "service_account", "client_email": "tico@proj.iam.gserviceaccount.com",
                                "private_key": pem, "token_uri": "https://evil.example/token"}))
    return key.public_key(), str(path)


def gm_env(path, users="Ana@acme.example"):
    return {"GOOGLE_MEET_USERS": users, "GOOGLE_SERVICE_ACCOUNT_FILE": path}


REC = "conferenceRecords/conf1"
TR = REC + "/transcripts/tr1"


def gm_route(public=None, records=None, transcripts=None, entries=None, seen=None):
    seen = seen if seen is not None else {}
    def route(call):
        if call.host == "oauth2.googleapis.com":
            assertion = call.form["assertion"]
            head, claims, sig = assertion.split(".")
            pad = lambda s: s + "=" * (-len(s) % 4)
            data = json.loads(base64.urlsafe_b64decode(pad(claims)))
            seen["claims"] = data
            if public:
                public.verify(base64.urlsafe_b64decode(pad(sig)), (head + "." + claims).encode(), padding.PKCS1v15(), hashes.SHA256())
            assert call.form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
            return {"access_token": "tok-" + data["sub"], "expires_in": 3600}
        assert call.headers["Authorization"].startswith("Bearer tok-")
        seen.setdefault("users", set()).add(call.headers["Authorization"])
        path = call.path[len("/v2/"):]
        if path == "conferenceRecords":
            seen["filter"] = call.query.get("filter")
            return records(call)
        if path == REC + "/transcripts":
            return transcripts
        if path == REC + "/participants":
            return {"participants": [{"name": REC + "/participants/p1", "signedinUser": {"user": "users/1", "displayName": "Ana Cruz"}},
                                     {"name": REC + "/participants/p2", "anonymousUser": {"displayName": "Dana Reyes"}}]}
        if path == TR + "/entries":
            return entries(call)
        if path == "spaces/sp1":
            return {"name": "spaces/sp1", "meetingCode": "abc-mnop-xyz", "meetingUri": "https://meet.google.com/abc-mnop-xyz"}
        raise AssertionError(path)
    return route


def gm_record(**changes):
    row = {"name": REC, "startTime": "2026-09-28T17:00:00Z", "endTime": "2026-09-28T18:00:00Z", "space": "spaces/sp1"}
    row.update(changes)
    return row


def entry(i, who, words, at):
    end = (datetime.fromisoformat(at.replace("Z", "+00:00")) + timedelta(seconds=3)).isoformat().replace("+00:00", "Z")
    return {"name": TR + "/entries/e" + str(i), "participant": REC + "/participants/" + who, "text": words,
            "languageCode": "en-US", "startTime": at, "endTime": end}


def test_google_meet_signs_a_delegated_assertion_and_maps_entries(tmp_path):
    public, path = service_account(tmp_path)
    seen = {}
    pages = {"": {"transcriptEntries": [entry(1, "p2", "Can you send pricing?", "2026-09-28T17:00:05Z"),
                                        entry(2, "p1", "Ten percent.", "2026-09-28T17:01:10Z")], "nextPageToken": "n2"},
             "n2": {"transcriptEntries": [entry(3, "p1", "Sending it.", "2026-09-28T17:01:20Z")]}}
    transcripts = {"transcripts": [{"name": TR, "state": "FILE_GENERATED", "startTime": "2026-09-28T17:00:00Z",
                                    "endTime": "2026-09-28T18:00:00Z",
                                    "docsDestination": {"document": "d1", "exportUri": "https://docs.google.com/document/d/d1/view"}}]}
    transport = Recorder(gm_route(public, lambda c: {"conferenceRecords": [gm_record()]}, transcripts,
                                  lambda c: pages[c.query.get("pageToken", "")], seen))
    importer, hub, _ = make(google_meet.GoogleMeet, tmp_path, transport, gm_env(path))
    assert importer.tick() == 1
    assert seen["claims"]["sub"] == "ana@acme.example" and seen["claims"]["iss"] == "tico@proj.iam.gserviceaccount.com"
    assert seen["claims"]["scope"] == "https://www.googleapis.com/auth/meetings.space.readonly"
    assert seen["claims"]["aud"] == "https://oauth2.googleapis.com/token"          # never the key file's own token_uri
    assert all(c.host in ("oauth2.googleapis.com", "meet.googleapis.com") for c in transport.calls)
    body = hub.posts[0]
    assert (body["source"], body["external_id"], body["owner_email"]) == ("google-meet", "conf1", "ana@acme.example")
    assert body["title"] == "Google Meet abc-mnop-xyz" and body["duration_seconds"] == 3600
    assert body["transcript"] == [
        {"speaker": "Dana Reyes", "start_ms": 5000, "end_ms": 8000, "text": "Can you send pricing?"},
        {"speaker": "Ana Cruz", "start_ms": 70000, "end_ms": 83000, "text": "Ten percent. Sending it."}]
    assert body["media_url"] == "https://docs.google.com/document/d/d1/view"
    assert body["context"]["meeting_url"] == "https://meet.google.com/abc-mnop-xyz"
    assert "ana@acme.example" in body["participants"] and "Dana Reyes" in body["participants"]


# ---------------------------------------------------------------- the service

def test_service_runs_only_available_importers_and_ignores_retired_old_server_assignments(tmp_path):
    hub = Hub()
    hub.enabled = ["fireflies", "unknown", "zoom"]
    importer = SimpleNamespace(name="Zoom", interval=600, tick=lambda stop: 1)
    seen = []
    def factory(source):
        seen.append(source)
        return lambda *args, **kwargs: importer
    service = ImporterService({"projects_dir": str(tmp_path), "url": "https://hub.test", "token": "t"}, tmp_path / "s",
                              client=hub, now=lambda: NOW, classes=factory)
    assert "fireflies" not in REGISTRY
    assert service.tick() == {"zoom": 1}
    assert seen == ["zoom"]
    assert hub.statuses == [("zoom", {"state": "ok", "imported": 1})]
    assert service.tick() == {}                                  # not due again yet
    hub.enabled = []
    service.due.clear()
    assert service.tick() == {}


