"""Free/busy, real slots, and the one event `mail schedule` creates.

Two halves, the same split as gmail.py. `Calendar` wraps a googleapiclient calendar service;
everything below it is pure and takes intervals, so the tests need no network.

The rules are not ours to soften (policies/shared-rules.md):
  - every calendar the mailbox can see counts as busy, not just the primary one
  - weekdays, 09:00-17:00 America/Los_Angeles
  - a 30-minute buffer on both sides of every slot
  - chronological order, always
  - a confirmation never goes out without the invite; `mail schedule` does both or neither
"""

from datetime import datetime, timedelta
import hashlib
import re

from . import DEFAULT_TZ, Failure, zone
from .gmail import gmail_hint
from . import oauth

DAY_START, DAY_END = 9, 17                              # America/Los_Angeles
BUFFER = timedelta(minutes=30)
DEFAULT_MINUTES = 20
DEFAULT_DAYS = 7
STEP_MINUTES = 15                                       # slots start on the quarter hour
MAX_CALENDARS = 50
MAX_EVENTS_PER_CALENDAR = 100
MEETING_URL = re.compile(
    r"https?://(?:meet\.google\.com|(?:[a-z0-9-]+\.)?zoom\.us|teams\.microsoft\.com|"
    r"(?:[a-z0-9-]+\.)?webex\.com)/[^\s<>\"]+", re.I)


# ---------------------------------------------------------------- client

class Calendar:
    """Every Calendar call the connector makes goes through here."""

    def __init__(self, service, mailbox):
        self.service, self.mailbox = service, mailbox
        self.granted = getattr(service, "_tico_granted", None)   # a connected account's scopes; None: service account

    def _exec(self, request, what):
        try:
            return request.execute()
        except Exception as e:
            status = getattr(e, "status_code", None) or getattr(e, "status", None)
            if status is None:
                resp = getattr(e, "resp", None)
                if resp and hasattr(resp, "status"):
                    status = resp.status
            raise Failure(f"Calendar {what} failed for {self.mailbox}: {e}",
                          gmail_hint(e, self.mailbox), status=status)

    def calendar_ids(self):
        """Every calendar this mailbox can see. All of them are busy, per shared-rules.md."""
        ids, page = [], None
        while True:
            request = self.service.calendarList().list(
                maxResults=MAX_CALENDARS, **({"pageToken": page} if page else {}))
            data = self._exec(request, "calendarList.list") or {}
            ids.extend(c["id"] for c in (data.get("items") or []) if c.get("id"))
            page = data.get("nextPageToken")
            if not page:
                break
        return ids or ["primary"]

    def busy(self, start, end, calendar_ids=None):
        """[(start, end)] across every calendar, merged, in the mailbox's timezone."""
        ids = calendar_ids or self.calendar_ids()
        if not ids:
            raise Failure("No calendars available for a complete availability check.")
        spans = []
        for offset in range(0, len(ids), 50):
            batch = ids[offset:offset+50]
            body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                    "items": [{"id": i} for i in batch]}
            data = self._exec(self.service.freebusy().query(body=body), "freebusy.query") or {}
            calendars = data.get("calendars") or {}
            if data.get("groups") and any(g.get("errors") for g in data["groups"].values()):
                raise Failure("A calendar group could not be checked.")
            for cid in batch:
                result = calendars.get(cid)
                if not isinstance(result, dict) or result.get("errors") or "busy" not in result:
                    spans.extend(self.event_busy(cid, start, end))
                    continue
                for busy in result["busy"]:
                    try:
                        lo, hi = iso(busy["start"],start.tzinfo), iso(busy["end"],start.tzinfo)
                        if lo >= hi:
                            raise ValueError("invalid interval")
                        spans.append((lo,hi))
                    except (KeyError, ValueError, TypeError):
                        raise Failure(f"Invalid busy interval returned for calendar {cid}.")
        return merge(spans)

    def event_busy(self, calendar_id, start, end):
        """Some subscribed calendars lack freebusy support. Read their actual event instances.

        A failed fallback still raises; missing access never becomes an empty busy list.
        Transparent/cancelled/declined events do not block time. Federal holidays are also
        excluded independently by the scheduling policy.
        """
        spans, page = [], None
        while True:
            data = self._exec(self.service.events().list(
                calendarId=calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
                singleEvents=True, showDeleted=False, maxResults=100,
                **({"pageToken":page} if page else {})), "events.list availability")
            if not isinstance(data, dict) or data.get("error"):
                raise Failure(f"Could not read calendar {calendar_id}.")
            for event in data.get("items", []):
                if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
                    continue
                if any(a.get("self") and a.get("responseStatus") == "declined" for a in event.get("attendees", [])):
                    continue
                try:
                    bounds = []
                    for key in ("start", "end"):
                        part = event[key]
                        value = part.get("dateTime") or part["date"]
                        bounds.append(iso(value, zone(part.get("timeZone") or str(start.tzinfo))))
                    if bounds[0] >= bounds[1]:
                        raise ValueError("invalid event")
                    spans.append(tuple(bounds))
                except (KeyError, TypeError, ValueError):
                    raise Failure(f"Invalid event interval in calendar {calendar_id}.")
            page = data.get("nextPageToken")
            if not page:
                return spans

    def upcoming(self, start, end, calendar_ids=None, include_private=False):
        """Deduplicated, recordable meetings visible to this mailbox in a time window.

        Calendar descriptions can contain private material, so the normalized result exposes
        only the small amount of context the desktop prompt and saved note need.
        """
        rows = []
        for calendar_id in calendar_ids or self.calendar_ids():
            page = None
            while True:
                request = self.service.events().list(
                    calendarId=calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
                    singleEvents=True, orderBy="startTime", showDeleted=False,
                    maxResults=MAX_EVENTS_PER_CALENDAR,
                    **({"pageToken": page} if page else {}))
                data = self._exec(request, "events.list") or {}
                rows.extend(normalize_event(event, self.mailbox, calendar_id, include_private=include_private)
                            for event in (data.get("items") or []))
                page = data.get("nextPageToken")
                if not page:
                    break
        unique = {}
        for row in filter(None, rows):
            unique.setdefault(row["occurrence_id"], row)
        return sorted(unique.values(), key=lambda event: (event["start"], event["title"]))

    def create_event(self, start, end, summary, attendees=(), description="",
                     send_updates="none", calendar_id="primary", status="tentative",
                     event_id=""):
        oauth.require(self.granted, "calendar-write", self.mailbox, "add calendar events")
        body = {"summary": summary, "description": description,
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
                "status": status,
                "attendees": [{"email": a} for a in attendees]}
        if event_id:
            body["id"] = event_id
        try:
            return self._exec(
                self.service.events().insert(calendarId=calendar_id, body=body,
                                             sendUpdates=send_updates), "events.insert")
        except Failure as exc:
            is_409 = exc.status == 409 or "409" in str(exc) or "already exists" in str(exc).lower()
            if not event_id or not is_409:
                raise
            return self._exec(
                self.service.events().get(calendarId=calendar_id, eventId=event_id),
                "events.get after duplicate")

    def create_scheduling_event(self, start, end, attendee, event_id, description, summary="Meeting"):
        oauth.require(self.granted, "calendar-write", self.mailbox, "add calendar events")
        body = {"id": event_id, "summary": summary, "status": "confirmed",
                "start": {"dateTime": start.isoformat(), "timeZone": "America/Los_Angeles"},
                "end": {"dateTime": end.isoformat(), "timeZone": "America/Los_Angeles"},
                "attendees": [{"email": attendee}], "description": description,
                "conferenceData": {"createRequest": {"requestId": event_id,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"}}}}
        return self._exec(self.service.events().insert(calendarId="primary", body=body,
                            sendUpdates="all", conferenceDataVersion=1), "events.insert")

    def create_hub_event(self, start, end, summary, attendees, event_id, description="",
                         add_meet=True):
        """Create the deterministic event claimed from the Hub connector queue."""
        oauth.require(self.granted, "calendar-write", self.mailbox, "add calendar events")
        body = {"id": event_id, "summary": summary, "description": description,
                "status": "confirmed",
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
                "attendees": [{"email": address} for address in attendees]}
        kwargs = {}
        if add_meet:
            body["conferenceData"] = {"createRequest": {"requestId": event_id,
                "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
            kwargs["conferenceDataVersion"] = 1
        return self._exec(self.service.events().insert(
            calendarId="primary", body=body,
            sendUpdates="all" if attendees else "none", **kwargs), "events.insert")

    def get_event(self, event_id, calendar_id="primary"):
        """Get one normalized event by ID."""
        request = self.service.events().get(calendarId=calendar_id, eventId=event_id)
        raw = self._exec(request, "events.get")
        if not raw:
            return None
        return normalize_event(raw, self.mailbox, calendar_id=calendar_id, include_private=True)

    def delete_event(self, event_id, calendar_id="primary"):
        oauth.require(self.granted, "calendar-write", self.mailbox, "delete calendar events")
        return self._exec(
            self.service.events().delete(calendarId=calendar_id, eventId=event_id,
                                         sendUpdates="none"), "events.delete")


# ---------------------------------------------------------------- pure

def iso(text, tz=None):
    dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz or zone(DEFAULT_TZ))
    return dt.astimezone(tz or dt.tzinfo)


def _meeting_url(event):
    for candidate in [event.get("hangoutLink")]:
        if candidate and MEETING_URL.search(str(candidate)):
            return MEETING_URL.search(str(candidate)).group(0).rstrip(".,)")
    for point in ((event.get("conferenceData") or {}).get("entryPoints") or []):
        candidate = point.get("uri") if point.get("entryPointType") == "video" else ""
        if candidate and MEETING_URL.search(str(candidate)):
            return MEETING_URL.search(str(candidate)).group(0).rstrip(".,)")
    # Location and description are inspected only to find a conferencing URL; neither field is
    # returned to the caller.
    for field in (event.get("location"), event.get("description")):
        hit = MEETING_URL.search(str(field or ""))
        if hit:
            return hit.group(0).rstrip(".,)")
    return ""


def normalize_event(event, mailbox, calendar_id="primary", include_private=False):
    """A safe meeting-shaped row, or None for non-meetings/all-day/declined events."""
    event = event if isinstance(event, dict) else {}
    if event.get("status") == "cancelled" or event.get("eventType") in {
            "focusTime", "outOfOffice", "workingLocation"}:
        return None
    start_raw = (event.get("start") or {}).get("dateTime")
    end_raw = (event.get("end") or {}).get("dateTime")
    if not start_raw or not end_raw:                   # all-day or malformed
        return None
    try:
        start_zone = zone((event.get("start") or {}).get("timeZone")) \
            if (event.get("start") or {}).get("timeZone") else None
        start_dt = iso(start_raw, start_zone)
        end_zone = zone((event.get("end") or {}).get("timeZone")) \
            if (event.get("end") or {}).get("timeZone") else start_dt.tzinfo
        end_dt = iso(end_raw, end_zone)
        if end_dt <= start_dt:
            return None
    except (ValueError, Failure):
        return None
    mailbox = str(mailbox or "").strip().lower()
    attendees = []
    self_declined = False
    for attendee in event.get("attendees") or []:
        address = str(attendee.get("email") or "").strip().lower()
        if not address:
            continue
        if (attendee.get("self") or address == mailbox) and attendee.get("responseStatus") == "declined":
            self_declined = True
        if address not in attendees:
            attendees.append(address)
    if self_declined:
        return None
    organizer = str((event.get("organizer") or {}).get("email") or "").strip().lower()
    participants = set(attendees)
    if organizer:
        participants.add(organizer)
    if mailbox and mailbox not in participants:
        return None                                    # visible shared calendar, not this person
    url = _meeting_url(event)
    if not include_private and len(participants) < 2 and not url:
        return None
    event_id = str(event.get("id") or "")
    stable = str(event.get("iCalUID") or event_id)
    start_iso, end_iso = start_dt.isoformat(), end_dt.isoformat()
    occurrence = hashlib.sha256(f"{stable}\n{start_iso}".encode()).hexdigest()[:24]
    title = str(event.get("summary") or "Meeting")[:240]
    return {"occurrence_id": occurrence, "event_id": event_id, "id": event_id,
            "iCalUID": str(event.get("iCalUID") or ""),
            "title": title, "summary": title,
            "start": start_iso, "end": end_iso, "attendees": attendees[:100],
            "meeting_url": url,
            "htmlLink": event.get("htmlLink") or f"https://calendar.google.com/calendar/event?eid={event_id}",
            "status": event.get("status") or "confirmed"}


def merge(spans):
    """Overlapping busy intervals collapsed into one sorted list."""
    out = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return [tuple(x) for x in out]


def windows(start, days, tz, day_start=DAY_START, day_end=DAY_END):
    """The weekday business-hours windows in the next `days` days, from `start`."""
    out = []
    day = start.astimezone(tz)
    for i in range(days + 1):
        d = (day + timedelta(days=i)).date()
        if d.weekday() >= 5:
            continue
        lo = datetime(d.year, d.month, d.day, day_start, 0, tzinfo=tz)
        hi = datetime(d.year, d.month, d.day, day_end, 0, tzinfo=tz)
        lo = max(lo, start.astimezone(tz))
        if lo < hi:
            out.append((lo, hi))
    return out


def free_slots(busy, n=2, minutes=DEFAULT_MINUTES, days=DEFAULT_DAYS, start_after=None,
               now=None, tz_name=DEFAULT_TZ, buffer=BUFFER, step=STEP_MINUTES):
    """The first `n` free slots. Pure: `busy` is whatever the calendars said."""
    tz = zone(tz_name)
    now = (now or datetime.now(tz)).astimezone(tz)
    begin = max(now, (start_after or now).astimezone(tz))
    minute = (begin.minute // step + 1) * step
    begin = begin.replace(second=0, microsecond=0, minute=0) + timedelta(minutes=minute)
    spans = merge(busy or [])
    out = []
    for lo, hi in windows(begin, days, tz):
        t = lo
        while t + timedelta(minutes=minutes) <= hi and len(out) < n:
            end = t + timedelta(minutes=minutes)
            if not any(b0 < end + buffer and t - buffer < b1 for b0, b1 in spans):
                out.append({"start": t, "end": end})
                t = end + buffer
            else:
                t += timedelta(minutes=step)
        if len(out) >= n:
            break
    return out


def _clock(dt):
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d}" + ("am" if dt.hour < 12 else "pm")


def human(slot, tz_label="PT"):
    """"Tue Sep 8, 1:00-1:20pm PT" - the shape the playbooks paste into a draft."""
    start, end = slot["start"], slot["end"]
    same_half = (start.hour < 12) == (end.hour < 12)
    left = _clock(start)[:-2] if same_half else _clock(start)
    return f"{start.strftime('%a %b')} {start.day}, {left}–{_clock(end)} {tz_label}"


def render(slots, mailbox, tz_label="PT"):
    if not slots:
        return f"No free slot on {mailbox} in that window.\n"
    lines = [f"# Free slots on {mailbox}", ""]
    for s in slots:
        lines.append(human(s, tz_label))
    lines += ["", "Every calendar on that mailbox counts as busy, 30-minute buffer both sides "
              "(policies/shared-rules.md)."]
    return "\n".join(lines) + "\n"


def as_json(slots, tz_label="PT"):
    return [{"start": s["start"].isoformat(), "end": s["end"].isoformat(),
             "human": human(s, tz_label)} for s in slots]
