"""Read Close call and Notetaker transcripts without downloading their recordings.

The Close API key stays on this runner. Only bounded speaker turns, source context, and a
provider summary cross to the hub. A transcript may arrive after the activity, so pending IDs
survive restarts and recent activities are revisited for corrections.
"""

import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clients.tico import APIError, Client

from .outage import Outage, log
from .state import State

SOURCE = "close"
API = "https://api.close.com/api/v1/"
APP = "https://app.close.com"
KEY_NAME = "CLOSE_API_KEY"
SECRET_FILE = "secrets/close-calls.env"
INTERVAL = 300
RETRY_SECONDS = 60
LOOKBACK_DAYS = 30
RECENT_HOURS = 24
PENDING_DAYS = 30
PAGE = 100
PAGES = 50
TIMEOUT = 60
MAX_RESPONSE = 5_000_000
ID = re.compile(r"^[A-Za-z0-9_]{1,80}$")
CALL_FIELDS = ("id,lead_id,contact_id,user_id,direction,duration,"
               "date_created,date_updated,activity_at,phone,remote_phone,status,disposition")
MEETING_FIELDS = ("id,lead_id,user_id,title,starts_at,ends_at,duration,actual_duration,"
                  "status,date_created,date_updated,activity_at,provider_calendar_event_id,attached_call_ids")


class CloseError(RuntimeError):
    """Sanitized provider failure. It never carries a key, URL, or transcript."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def load_key(path, name=KEY_NAME):
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        for raw in Path(path).read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, val = line.partition("=")
            if sep and key.strip() == name:
                return val.strip().strip("'\"")
    except OSError:
        pass
    return ""


def ident(value):
    value = str(value or "")
    return value if ID.fullmatch(value) else ""


def moment(value, default=None):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return default
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def seconds_of(value):
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def lead_link(lead_id, value=""):
    value = str(value or "").strip()
    if urllib.parse.urlparse(value).scheme == "https" and urllib.parse.urlparse(value).netloc == "app.close.com":
        return value
    if value.startswith("/"):
        return APP + value
    return APP + "/lead/" + lead_id + "/" if lead_id else ""


def normalize_utterances(transcript):
    """Close seconds become bounded Tico milliseconds; never keep provider HTML."""
    rows = transcript.get("utterances") if isinstance(transcript, dict) else None
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows[:10_000]:
        if not isinstance(row, dict):
            continue
        words = str(row.get("text") or "").strip()
        if not words:
            continue
        if len(words) > 20_000:
            raise CloseError("Close transcript turn exceeds the import limit")
        try:
            start = max(0, min(86_400_000, round(float(row.get("start") or 0) * 1000)))
            end = max(start, min(86_400_000, round(float(row.get("end") or 0) * 1000)))
        except (TypeError, ValueError, OverflowError):
            continue
        out.append({"text": words, "start_ms": start, "end_ms": end,
                    "speaker": str(row.get("speaker_label") or "").strip()[:100],
                    "side": str(row.get("speaker_side") or "").strip()[:40]})
    if len(rows) > 10_000 or sum(len(t["text"]) for t in out) > 1_000_000:
        raise CloseError("Close transcript exceeds the import limit")
    return out


class CloseCallImporter:
    """Compatibility name for the installed close-calls runner; syncs calls and meetings."""

    def __init__(self, config, directory=None, *, client=None, transport=None, state=None, now=None,
                 backfill_days=None):
        self.config = config
        self.client = client or Client(config["url"], config["token"], timeout=60, retries=1)
        self.projects = Path(config["projects_dir"])
        self.secret_path = self.projects / SECRET_FILE
        self.state = state if state is not None else (State(directory) if directory else None)
        self.transport = transport or self.http
        self.remote = transport is None
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.backfill_days = backfill_days
        self.stop = threading.Event()
        self.warned_at = 0.0
        self.users, self.leads, self.contacts = {}, {}, {}
        self._owner = None

    def key(self):
        return load_key(self.secret_path)

    def fallback_owner(self):
        """Who owns an activity whose Close rep is not on the roster: the environment's owner."""
        if self._owner is None:
            self._owner = str((self.client.get("config") or {}).get("owner_email") or "").strip().lower()
        if not self._owner:
            raise CloseError("The Tico server did not name an owner to file unmatched Close activity under")
        return self._owner

    def ready(self):
        if self.remote and not self.key():
            raise RuntimeError("Close API key missing on this Mac: write CLOSE_API_KEY to "
                               + SECRET_FILE + " (the same value as secrets/sales-ops.env)")

    def http(self, url, params=None):
        target = API + url
        if params:
            target += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        request = urllib.request.Request(target, headers={
            "Authorization": "Basic " + base64.b64encode((self.key() + ":").encode()).decode(),
            "Accept": "application/json", "User-Agent": "Tico-CloseTranscripts/1"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                payload = response.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as exc:
            raise CloseError("Close returned HTTP " + str(exc.code), exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise CloseError("Close could not be reached") from None
        if len(payload) > MAX_RESPONSE:
            raise CloseError("Close response exceeds the import limit")
        try:
            return json.loads(payload or b"{}")
        except ValueError:
            raise CloseError("Close returned an unreadable response") from None

    def lookup(self, url, params):
        try:
            row = self.transport(url, params)
        except CloseError as exc:
            log("Tico Close transcripts: " + str(exc) + " looking up " + url)
            row = {}
        return row if isinstance(row, dict) else {}

    def lead(self, lead_id):
        if lead_id not in self.leads:
            row = self.lookup("lead/" + lead_id + "/", {"_fields": "display_name,url"}) if lead_id else {}
            self.leads[lead_id] = {"name": str(row.get("display_name") or "").strip(),
                                   "url": lead_link(lead_id, row.get("url"))}
        return self.leads[lead_id]

    def user(self, user_id):
        if user_id not in self.users:
            row = self.lookup("user/" + user_id + "/", {"_fields": "email"}) if user_id else {}
            self.users[user_id] = str(row.get("email") or "").strip().lower()
        return self.users[user_id]

    def contact(self, contact_id):
        if contact_id not in self.contacts:
            row = self.lookup("contact/" + contact_id + "/", {"_fields": "name"}) if contact_id else {}
            self.contacts[contact_id] = str(row.get("name") or "").strip()
        return self.contacts[contact_id]

    def pull(self, kind, since, until):
        fields = CALL_FIELDS if kind == "call" else MEETING_FIELDS
        # Close sorts an organization-wide activity list by date_created. Its API rejects
        # activity_at filters unless the request is scoped to one lead (which this sync is not).
        # Keep future meetings in the pending queue so their transcripts are checked after they end.
        stamp = "date_created"
        rows, skip = [], 0
        for _ in range(PAGES):
            params = {"_fields": fields, stamp + "__gte": since, stamp + "__lte": until,
                      "_skip": skip, "_limit": PAGE}
            # A meeting may start after it was created. The pending queue revisits it
            # after it ends and after Close adds a transcript.
            page = self.transport("activity/" + kind + "/", params)
            part = page.get("data") if isinstance(page, dict) else None
            if not isinstance(part, list):
                raise CloseError("Close returned no " + kind + " list")
            rows.extend(row for row in part if isinstance(row, dict) and ident(row.get("id")))
            if not page.get("has_more"):
                return sorted(rows, key=lambda row: (str(row.get(stamp) or row.get("starts_at") or ""), row["id"]))
            skip += len(part)
            if not part:
                raise CloseError("Close pagination did not advance")
        # Never move a cursor past unseen activities.
        raise CloseError("Close returned over " + str(PAGES * PAGE) + " " + kind
                         + " activities in one pull")

    def details(self, kind, activity):
        fields = (CALL_FIELDS + ",recording_transcript" if kind == "call"
                  else MEETING_FIELDS + ",transcripts")
        row = self.transport("activity/" + kind + "/" + activity["id"] + "/", {"_fields": fields})
        if not isinstance(row, dict):
            raise CloseError("Close returned no " + kind + " details")
        return {**activity, **row}

    def body(self, kind, row, transcript_index, transcript):
        cid = row["id"]
        lead_id = ident(row.get("lead_id"))
        lead = self.lead(lead_id)
        rep = self.user(ident(row.get("user_id")))
        start = moment(row.get("activity_at") if kind == "call" else row.get("starts_at"))
        start = start or moment(row.get("date_created")) or self.now()
        duration = seconds_of(row.get("actual_duration") or row.get("duration"))
        end = moment(row.get("ends_at")) if kind == "meeting" else None
        if end and end > start:
            duration = (end - start).total_seconds()
        turns = normalize_utterances(transcript)
        summary = str(transcript.get("summary_text") or "")
        if len(summary) > 200_000:
            raise CloseError("Close summary exceeds the import limit")
        if not duration and turns:
            duration = max(t["end_ms"] for t in turns) / 1000
        ended = end or start + timedelta(seconds=duration)
        context = {"lead_id": lead_id, "lead_name": lead["name"], "lead_url": lead["url"],
                   "close_user": rep, "close_activity_id": cid,
                   "close_type": kind}
        if kind == "call":
            direction = str(row.get("direction") or "").strip().lower()
            context.update(contact_name=self.contact(ident(row.get("contact_id"))),
                           phone=str(row.get("remote_phone") or row.get("phone") or "").strip(),
                           direction=direction)
            title = "Call with " + (lead["name"] or "an unnamed lead") + " (" + (direction or "call") + ")"
        else:
            title = str(row.get("title") or "").strip() or "Close meeting with " + (lead["name"] or "a lead")
            direction = ""
        return {"source": SOURCE, "resource_type": kind, "external_id": cid,
                "owner_email": rep or self.fallback_owner(), "title": title[:300],
                "started": start.isoformat(), "ended": ended.isoformat(),
                "duration_ms": min(86_400_000, round(duration * 1000)),
                "context": {k: v for k, v in context.items() if v},
                "source_updated_at": (moment(row.get("date_updated")) or self.now()).isoformat(),
                "transcript_index": transcript_index,
                "summary_text": summary,
                "turns": turns,
                "calendar_event_id": str(row.get("provider_calendar_event_id") or "")[:500] if kind == "meeting" else "",
                "attached_call_ids": [i for i in (row.get("attached_call_ids") or []) if ident(i)][:20]
                if kind == "meeting" else []}

    def import_activity(self, kind, row):
        cid = row["id"]
        complete = (str(row.get("status") or "").lower() == "completed")
        if not complete:
            self.state.close_pending(kind, cid, created=(row.get("ends_at") if kind == "meeting" else None)
                                     or row.get("date_created") or self.now().isoformat(),
                                     checked=self.now().isoformat())
            return False
        detail = self.details(kind, row)
        transcripts = ([detail.get("recording_transcript")] if kind == "call"
                       else detail.get("transcripts") or [])
        usable = [(index, part) for index, part in enumerate(transcripts)
                  if isinstance(part, dict) and normalize_utterances(part)]
        if not usable:
            self.state.close_pending(kind, cid, created=(detail.get("ends_at") if kind == "meeting" else None)
                                     or row.get("date_created") or self.now().isoformat(),
                                     checked=self.now().isoformat())
            return False
        changed = False
        for index, part in usable:
            body = self.body(kind, detail, index, part)
            try:
                result = self.client.post("imports/transcripts", body)
            except APIError as exc:
                if exc.code != "not_found":
                    raise
                owner = self.fallback_owner()
                if body["owner_email"] == owner:
                    raise
                log("Tico Close transcripts: " + body["owner_email"] + " is not on the Tico roster; "
                    + kind + " " + cid + " is owned by " + owner)
                result = self.client.post("imports/transcripts", {**body, "owner_email": owner})
            changed = changed or bool(result.get("changed"))
        self.state.close_pending(kind, cid, remove=True)
        self.state.import_phase("close-transcripts:" + kind, cid, "finished")
        if changed:
            log("Tico Close transcripts: imported " + kind + " " + cid)
        return changed

    def tick(self):
        self.ready()
        if self.state is None:
            raise RuntimeError("This importer has no local state directory")
        self.users, self.leads, self.contacts = {}, {}, {}
        imported, seen = 0, set()
        for kind in ("call", "meeting"):
            source = "close-transcripts:" + kind
            cursor = self.state.import_cursor(source) or (
                self.now() - timedelta(days=LOOKBACK_DAYS)).isoformat()
            recent = (self.now() - timedelta(hours=RECENT_HOURS)).isoformat()
            since = ((self.now() - timedelta(days=self.backfill_days)).isoformat()
                     if self.backfill_days else min(cursor, recent))
            window_start = moment(since)
            until = self.now()
            while window_start < until and not self.stop.is_set():
                window_end = min(window_start + timedelta(days=1), until)
                rows = self.pull(kind, window_start.isoformat(), window_end.isoformat())
                for row in rows:
                    if self.stop.is_set():
                        break
                    cid = row["id"]
                    seen.add((kind, cid))
                    imported += bool(self.import_activity(kind, row))
                if not self.stop.is_set():
                    # Every page in this bounded window was read and imported. Preserve that
                    # progress if a later day fails; the rolling overlap revisits corrections.
                    self.state.import_cursor(source, max(cursor, window_end.isoformat()))
                window_start = window_end
        for pending in self.state.close_pending(limit=100):
            kind, cid = pending["resource_type"], pending["external_id"]
            if (kind, cid) in seen:
                continue
            created = moment(pending["created"])
            if created and self.now() - created > timedelta(days=PENDING_DAYS):
                self.state.close_pending(kind, cid, remove=True)
                continue
            checked = moment(pending["last_checked"])
            if checked and self.now() - checked < timedelta(minutes=5):
                continue
            # One activity Close no longer has (a deleted call or meeting answers 404) stopped every
            # pull until a person removed it (2026-09-28, and again 2026-10-02 for 8 hours), and the
            # queue behind it was never rechecked. Gone is gone; any other failure goes to the back.
            try:
                detail = self.details(kind, {"id": cid})
            except CloseError as exc:
                if exc.status == 404:
                    self.state.close_pending(kind, cid, remove=True)
                else:
                    log("Tico Close transcripts: " + str(exc) + " checking a pending " + kind)
                    self.state.close_pending(kind, cid, created=pending["created"], checked=self.now().isoformat())
                continue
            imported += bool(self.import_activity(kind, detail))
        self.client.post("imports/sources/close/status", {
            "state": "ok", "pending": len(self.state.close_pending(limit=1_000_000)),
            "imported": imported})
        return imported

    def run(self):
        from .freshness import CodeWatch
        cloud = Outage("Tico Close transcripts", "pull failed", "pull still failing",
                       "pull working again")
        watch = CodeWatch("Tico Close transcripts")
        failures = 0
        while not self.stop.is_set():
            try:
                self.tick()
                cloud.recovered()
                failures = 0
                delay = INTERVAL
            except Exception as exc:
                failures += 1
                cloud.failed(exc)
                try:
                    self.client.post("imports/sources/close/status", {
                        "state": "error", "error_code": "missing_key" if isinstance(exc, RuntimeError)
                        and not isinstance(exc, CloseError) else "sync_error", "pending": len(self.state.close_pending()) if self.state else 0})
                except Exception:
                    pass
                delay = min(INTERVAL, RETRY_SECONDS * 2 ** min(failures - 1, 3))
            if watch.wait(self.stop, delay):    # the checkout moved: the supervisor starts it on the new code
                return
