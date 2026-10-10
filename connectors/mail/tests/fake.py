"""A fake Gmail service and message builders. Stdlib only, no network, no credentials.

The fake mimics exactly the slice of googleapiclient's Resource that connectors/mail/gmail.py
uses: users().getProfile / labels().list / labels().create / messages().list / messages().get /
messages().attachments().get / messages().modify / threads().get / history().list, and for
stage 2 drafts().create / update / get / delete / send. Its query support is deliberately small -
`in:inbox`, `in:sent`, `label:"X"`, `-label:"X"`,
`from:`, `subject:"X"` and `after:<epoch>` - which is all the CLI ever sends.

`FakeCalendarService` is the same idea for calendar.py: calendarList().list, freebusy().query
and events().insert / delete.
"""

import base64, re, sys
from pathlib import Path

HUB = Path(__file__).resolve().parents[3]
if str(HUB) not in sys.path:
    sys.path.insert(0, str(HUB))


def b64(s):
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode().rstrip("=")


def part(mime, text=None, filename="", size=None, charset="utf-8"):
    p = {"mimeType": mime, "filename": filename,
         "headers": [{"name": "Content-Type", "value": f"{mime}; charset={charset}"}]}
    if text is None:
        p["body"] = {"attachmentId": "att-1", "size": size or 0}
    else:
        p["body"] = {"data": b64(text), "size": len(text)}
    return p


def message(mid="m1", thread=None, headers=None, body=None, parts=None,
            labels=("INBOX", "UNREAD"), epoch_ms=1788368400000, snippet=""):
    """A raw Gmail message dict. epoch default: 2026-09-02 10:00 America/Los_Angeles."""
    headers = dict(headers or {})
    payload = {"headers": [{"name": k, "value": v} for k, v in headers.items()]}
    if parts is not None:
        payload["mimeType"] = "multipart/mixed"
        payload["parts"] = parts
    else:
        payload["mimeType"] = "text/plain"
        payload["body"] = {"data": b64(body or ""), "size": len(body or "")}
    return {"id": mid, "threadId": thread or ("t-" + mid), "labelIds": list(labels),
            "internalDate": str(epoch_ms), "snippet": snippet, "payload": payload}


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class Boom(Exception):
    """An HttpError lookalike: gmail.py reads .resp.status without importing googleapiclient."""

    class _Resp:
        def __init__(self, status):
            self.status = status

    def __init__(self, status, text="boom"):
        super().__init__(text)
        self.resp = self._Resp(status)


class FakeGmailService:
    def __init__(self, messages=None, labels=None, profile=None, fail_times=0, fail_status=503,
                 attachments=None, history=None, history_expired=False):
        self.store = {m["id"]: m for m in (messages or [])}   # `messages()` is the resource
        self.labels_by_id = dict(labels or {"INBOX": "INBOX", "UNREAD": "UNREAD",
                                            "STARRED": "STARRED", "SENT": "SENT"})
        self._profile = profile or {"emailAddress": "ana@acme.example", "messagesTotal": 3,
                                    "historyId": "12345"}
        self.created, self.modified, self.calls = [], [], []
        self.fail_times, self.fail_status = fail_times, fail_status
        self._next = 1
        self.drafts_by_id, self.sent, self.deleted_drafts = {}, [], []
        self.attachment_data = dict(attachments or {})
        self.send_fails = False                          # make drafts.send blow up, for rollback
        self.history_records = list(history or [])
        self.history_expired = history_expired

    # -- resource surface -------------------------------------------
    def users(self):
        return self

    def getProfile(self, userId="me"):
        return _Req(lambda: self._maybe_fail(dict(self._profile)))

    def labels(self):
        return _Labels(self)

    def messages(self):
        return _Messages(self)

    def threads(self):
        return _Threads(self)

    def drafts(self):
        return _Drafts(self)

    def history(self):
        return _History(self)

    # -- internals --------------------------------------------------
    def _maybe_fail(self, value):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise Boom(self.fail_status)
        return value

    def name_of(self, lid):
        return self.labels_by_id.get(lid, lid)

    def id_of(self, name):
        for lid, n in self.labels_by_id.items():
            if n == name:
                return lid
        return None

    def matches(self, msg, q):
        names = {self.name_of(l) for l in msg.get("labelIds") or []}
        for token in re.findall(r'-?\w+:"[^"]*"|-?[\w+-]+:\S+|\S+', q or ""):
            neg = token.startswith("-")
            t = token[1:] if neg else token
            key, _, val = t.partition(":")
            val = val.strip('"')
            if key == "in" and val == "inbox":
                hit = "INBOX" in names
            elif key == "label":
                hit = val in names
            elif key == "after":
                hit = int(msg.get("internalDate", 0)) / 1000 >= float(val)
            elif key == "in" and val == "sent":
                hit = "SENT" in names
            elif key == "from":
                hit = val.lower() in self.header(msg, "from").lower()
            elif key == "subject":
                hit = val.lower() in self.header(msg, "subject").lower()
            else:
                continue
            if hit == neg:
                return False
        return True

    @staticmethod
    def header(msg, name):
        for h in (msg.get("payload") or {}).get("headers") or []:
            if h["name"].lower() == name.lower():
                return h.get("value", "")
        return ""


class _Labels:
    def __init__(self, s):
        self.s = s

    def list(self, userId="me"):
        return _Req(lambda: self.s._maybe_fail(
            {"labels": [{"id": i, "name": n} for i, n in self.s.labels_by_id.items()]}))

    def create(self, userId="me", body=None):
        def go():
            lid = f"Label_{self.s._next}"
            self.s._next += 1
            self.s.labels_by_id[lid] = body["name"]
            self.s.created.append(body["name"])
            return {"id": lid, "name": body["name"]}
        return _Req(go)


class _Messages:
    def __init__(self, s):
        self.s = s

    def list(self, userId="me", q=None, labelIds=None, maxResults=100, pageToken=None):
        def go():
            self.s.calls.append(("list", q))
            hits = [m for m in self.s.store.values() if self.s.matches(m, q)]
            if labelIds:
                hits = [m for m in hits if set(labelIds) <= set(m.get("labelIds") or [])]
            hits.sort(key=lambda m: -int(m.get("internalDate", 0)))
            return self.s._maybe_fail({"messages": [{"id": m["id"]} for m in hits[:maxResults]]})
        return _Req(go)

    def get(self, userId="me", id=None, format="full"):
        def go():
            if id not in self.s.store:
                raise Boom(404, "notFound")
            return self.s._maybe_fail(self.s.store[id])
        return _Req(go)

    def attachments(self):
        return _Attachments(self.s)

    def modify(self, userId="me", id=None, body=None):
        def go():
            m = self.s.store[id]
            have = [l for l in m.get("labelIds") or []]
            for lid in (body or {}).get("removeLabelIds") or []:
                if lid in have:
                    have.remove(lid)
            for lid in (body or {}).get("addLabelIds") or []:
                if lid not in have:
                    have.append(lid)
            m["labelIds"] = have
            self.s.modified.append({"id": id, "add": (body or {}).get("addLabelIds") or [],
                                    "remove": (body or {}).get("removeLabelIds") or []})
            return self.s._maybe_fail(m)
        return _Req(go)


class _Attachments:
    def __init__(self, s):
        self.s = s

    def get(self, userId="me", messageId=None, id=None):
        def go():
            self.s.calls.append(("attachment.get", messageId, id, userId))
            key = (messageId, id)
            if key not in self.s.attachment_data:
                raise Boom(404, "notFound")
            return self.s._maybe_fail(dict(self.s.attachment_data[key]))
        return _Req(go)


class _Threads:
    def __init__(self, s):
        self.s = s

    def get(self, userId="me", id=None, format="full"):
        def go():
            msgs = [m for m in self.s.store.values() if m["threadId"] == id]
            msgs.sort(key=lambda m: int(m.get("internalDate", 0)))
            return self.s._maybe_fail({"id": id, "messages": msgs})
        return _Req(go)


class _History:
    def __init__(self, s):
        self.s = s

    def list(self, userId="me", startHistoryId=None, historyTypes=None, maxResults=100,
             pageToken=None, labelId=None):
        def go():
            self.s.calls.append(("history", str(startHistoryId or ""),
                                 list(historyTypes or [])))
            if self.s.history_expired:
                raise Boom(404, "notFound")
            try:
                start = int(startHistoryId or 0)
            except (TypeError, ValueError):
                start = 0
            recs = [h for h in self.s.history_records if int(h.get("id") or 0) > start]
            return self.s._maybe_fail({
                "history": recs[:maxResults],
                "historyId": str(self.s._profile.get("historyId", "12345")),
            })
        return _Req(go)


# ---------------------------------------------------------------- drafts (stage 2)

def message_from_raw(raw, mid, thread_id="", labels=("DRAFT",)):
    """A stored Gmail message built from the base64url MIME the connector produced."""
    import email
    s = str(raw or "").replace("-", "+").replace("_", "/")
    parsed = email.message_from_bytes(base64.b64decode(s + "=" * (-len(s) % 4)))
    headers = {k: v for k, v in parsed.items()}
    if parsed.is_multipart():                           # a draft with --attach files
        parts = []
        for sub in parsed.get_payload():
            data = sub.get_payload(decode=True) or b""
            parts.append({"mimeType": sub.get_content_type(), "filename": sub.get_filename() or "",
                          "headers": [{"name": k, "value": v} for k, v in sub.items()],
                          "body": {"data": base64.urlsafe_b64encode(data).decode(),
                                   "size": len(data)}})
        return message(mid, thread_id or ("t-" + mid), headers, parts=parts, labels=list(labels))
    body = parsed.get_payload(decode=True) or b""
    m = message(mid, thread_id or ("t-" + mid), headers, body=body.decode("utf-8", "replace"),
                labels=list(labels))
    return m


class _Drafts:
    def __init__(self, s):
        self.s = s

    def _make(self, body, draft_id=None):
        msg_body = (body or {}).get("message") or {}
        did = draft_id or f"d-{self.s._next}"
        mid = f"dm-{self.s._next}"
        self.s._next += 1
        if draft_id and draft_id in self.s.drafts_by_id:
            mid = self.s.drafts_by_id[draft_id]["message"]["id"]
        m = message_from_raw(msg_body.get("raw"), mid, msg_body.get("threadId", ""))
        self.s.store[mid] = m
        rec = {"id": did, "message": {"id": mid, "threadId": m["threadId"], "raw": msg_body.get("raw")}}
        self.s.drafts_by_id[did] = rec
        return rec

    def create(self, userId="me", body=None):
        return _Req(lambda: self.s._maybe_fail(self._make(body)))

    def update(self, userId="me", id=None, body=None):
        return _Req(lambda: self.s._maybe_fail(self._make(body, draft_id=id)))

    def get(self, userId="me", id=None, format="full"):
        def go():
            if id not in self.s.drafts_by_id:
                raise Boom(404, "notFound")
            rec = self.s.drafts_by_id[id]
            return {"id": id, "message": self.s.store[rec["message"]["id"]]}
        return _Req(go)

    def delete(self, userId="me", id=None):
        def go():
            rec = self.s.drafts_by_id.pop(id, None)
            if rec is None:
                raise Boom(404, "notFound")
            self.s.store.pop(rec["message"]["id"], None)
            self.s.deleted_drafts.append(id)
            return {}
        return _Req(go)

    def send(self, userId="me", body=None):
        def go():
            if self.s.send_fails:
                raise Boom(500, "the send blew up")
            did = (body or {}).get("id")
            rec = self.s.drafts_by_id.pop(did, None)
            if rec is None:
                raise Boom(404, "notFound")
            m = self.s.store[rec["message"]["id"]]
            m["labelIds"] = ["SENT"]
            self.s.sent.append({"draft": did, "id": m["id"], "threadId": m["threadId"]})
            return {"id": m["id"], "threadId": m["threadId"], "labelIds": ["SENT"]}
        return _Req(go)


# ---------------------------------------------------------------- calendar

class FakeCalendarService:
    """calendarList, freebusy, and the event operations used by calendar.py."""

    def __init__(self, calendars=("primary", "team@acme.example"), busy=None, fail_insert=False):
        self.calendar_ids = list(calendars)
        self.busy = list(busy or [])                    # [(iso start, iso end)] on every calendar
        self.created_events, self.deleted, self.queries = {}, [], []
        self.events_by_calendar, self.event_queries = {}, []
        self.fail_insert = fail_insert
        self._next = 1

    def calendarList(self):
        return self

    def freebusy(self):
        return self

    def events(self):
        return self

    def list(self, maxResults=50, calendarId=None, pageToken=None, **kwargs):
        if calendarId is None:
            def calendar_page():
                start = int(pageToken or 0)
                end = start + maxResults
                out = {"items": [{"id": i} for i in self.calendar_ids[start:end]]}
                if end < len(self.calendar_ids):
                    out["nextPageToken"] = str(end)
                return out
            return _Req(calendar_page)
        def go():
            if getattr(self, "fail_list", False):
                raise Boom(500, "calendar list failed")
            self.event_queries.append(dict(kwargs, calendarId=calendarId, pageToken=pageToken,
                                           maxResults=maxResults))
            return {"items": list(self.events_by_calendar.get(calendarId) or [])}
        return _Req(go)

    def query(self, body=None):
        def go():
            self.queries.append(body)
            return {"calendars": {i: {"busy": [{"start": a, "end": b} for a, b in self.busy]}
                                  for i in self.calendar_ids}}
        return _Req(go)

    def insert(self, calendarId="primary", body=None, sendUpdates="none", **kwargs):
        def go():
            if self.fail_insert:
                raise Boom(500, "calendar refused")
            eid = str((body or {}).get("id") or f"ev-{self._next}")
            if eid in self.created_events:
                raise Boom(409, "calendar event already exists")
            if not (body or {}).get("id"):
                self._next += 1
            ev = dict(body or {}, id=eid, sendUpdates=sendUpdates, **kwargs)
            self.created_events[eid] = ev
            self.events_by_calendar.setdefault(calendarId, []).append(ev)
            return ev
        return _Req(go)

    def get(self, calendarId="primary", eventId=None):
        def go():
            if eventId not in self.created_events:
                raise Boom(404, "calendar event not found")
            return self.created_events[eventId]
        return _Req(go)

    def delete(self, calendarId="primary", eventId=None, sendUpdates="none"):
        def go():
            self.created_events.pop(eventId, None)
            self.deleted.append(eventId)
            return {}
        return _Req(go)
