"""Granola through its public API (https://docs.granola.ai/introduction), v1, with an API key.

Granola publishes an official API, so nothing here reads the app's local files. A personal key
(Business and Enterprise plans) reads that person's notes and what is shared with them; a
workspace key (an admin makes it) reads the notes the workspace made public. The key stays in
secrets/granola.env on this computer. Private notes typed by the note-taker are never imported.
"""

import hashlib
import re
import urllib.parse
from datetime import timedelta

from .base import Importer, Item, ProviderError, Scope, email_of, https_url, moment, people, text, turns_of

API = "https://public-api.granola.ai/v1"
PAGE = 30
PAGES = 100
TRANSCRIPT_PAGE = 100
TRANSCRIPT_PAGES = 200
NOTE = re.compile(r"^not_[A-Za-z0-9]{14}$")


class TooLarge(Exception):
    pass


class Granola(Importer):
    source = "granola"
    name = "Granola"
    prefix = "GRANOLA"
    secret_file = "granola.env"
    required = ("GRANOLA_API_KEY",)
    fallback = False                # a note shared by someone outside the roster is not the owner's to keep
    private_by_default = True       # a person's notes stay theirs until the file says otherwise
    pace = 0.25                     # the API allows 5 calls a second sustained

    def get(self, key, path, /, **query):
        url = API + path + ("?" + urllib.parse.urlencode({k: v for k, v in query.items() if v}) if query else "")
        return self.send("GET", url, headers={"Authorization": "Bearer " + key})

    def scopes(self):
        keys = [k.strip() for k in self.env()["GRANOLA_API_KEY"].split(",") if k.strip()]
        return [Scope(hashlib.sha256(k.encode()).hexdigest()[:10], "", {"key": k}) for k in keys[:50]]

    def notes(self, key, since, until):
        cursor, out = "", []
        for _ in range(PAGES):
            page = self.get(key, "/notes", created_after=since.isoformat().replace("+00:00", "Z"),
                            created_before=until.isoformat().replace("+00:00", "Z"),
                            page_size=PAGE, cursor=cursor)
            rows = page.get("notes") if isinstance(page, dict) else None
            if not isinstance(rows, list):
                raise ProviderError("bad_response", "Granola returned no note list")
            out.extend(r for r in rows if isinstance(r, dict) and NOTE.fullmatch(str(r.get("id") or "")))
            cursor = page.get("cursor") or ""
            if not page.get("hasMore") or not cursor:
                return out
        raise ProviderError("provider_error", "Granola returned more notes than one pass reads; use a shorter --backfill-days")

    def fetch(self, scope, since, until):
        key = scope.data["key"]
        for row in sorted(self.notes(key, since, until), key=lambda r: (str(r.get("created_at")), r["id"])):
            started = moment(row.get("created_at"), since)
            revision = str(row.get("updated_at") or "")
            if self.known(scope, row["id"], revision):
                continue
            item = self.item(key, row, started, revision)
            if item:
                yield item

    def transcript_pages(self, key, note_id):
        cursor, out = "", []
        for _ in range(TRANSCRIPT_PAGES):
            page = self.get(key, "/notes/" + note_id + "/transcript", page_size=TRANSCRIPT_PAGE, cursor=cursor)
            rows = page.get("transcript") if isinstance(page, dict) else None
            if not isinstance(rows, list):
                raise ProviderError("bad_response", "Granola returned no transcript")
            out.extend(rows)
            cursor = page.get("cursor") or ""
            if not page.get("hasMore") or not cursor:
                return out
        raise ProviderError("bad_response", "The Granola transcript exceeds the import limit")

    def detail(self, key, note_id):
        try:
            note = self.get(key, "/notes/" + note_id, include="transcript")
        except ProviderError as exc:
            if exc.status != 413:
                raise
            note = self.get(key, "/notes/" + note_id)
            note["transcript"] = self.transcript_pages(key, note_id)
        if not isinstance(note, dict):
            raise ProviderError("bad_response", "Granola returned no note")
        return note

    def item(self, key, row, started, revision):
        note = self.detail(key, row["id"])
        items = [t for t in note.get("transcript") or [] if isinstance(t, dict)]
        owner = note.get("owner") if isinstance(note.get("owner"), dict) else row.get("owner") or {}
        owner_name = text((owner or {}).get("name"), 100)
        moments = [moment(t.get("start_time")) for t in items]
        origin = min((m for m in moments if m), default=started)
        rows = []
        for t, when in zip(items, moments):
            if not when:
                continue
            end = moment(t.get("end_time"), when)
            speaker = t.get("speaker") if isinstance(t.get("speaker"), dict) else {}
            who = speaker.get("name") or speaker.get("diarization_label") or (
                (owner_name or "Note-taker") if speaker.get("attribution") == "me"
                else "Others" if speaker.get("attribution") == "them" else "")
            rows.append((who, (when - origin).total_seconds() * 1000, (end - origin).total_seconds() * 1000, t.get("text")))
        turns = turns_of(rows)
        notes = str(note.get("summary_markdown") or note.get("summary_text") or "").strip()[:200_000]
        if not turns and not notes:
            return None
        event = note.get("calendar_event") if isinstance(note.get("calendar_event"), dict) else {}
        attendees = [(a.get("name") or "", a.get("email")) for a in note.get("attendees") or [] if isinstance(a, dict)]
        invitees = [i.get("email") for i in event.get("invitees") or [] if isinstance(i, dict)]
        planned = moment(event.get("scheduled_start_time"))
        started = planned or started
        body = {"title": text(note.get("title") or event.get("event_title"), 300) or "Granola meeting",
                "started_at": started.isoformat(),
                "participants": people(*attendees, *invitees, (owner_name, (owner or {}).get("email"))),
                "transcript": turns, "notes": notes, "media_url": https_url(note.get("web_url")),
                "context": {k: v for k, v in {"calendar_event_id": text(event.get("calendar_event_id"))}.items() if v}}
        if not turns:
            body["transcript"] = ""
        end = moment(event.get("scheduled_end_time"))
        if end and planned and 0 < (end - planned).total_seconds() <= 86_400 and not turns:
            body["duration_seconds"] = (end - planned).total_seconds()
        return Item(row["id"], email_of((owner or {}).get("email")), body, started, revision)
