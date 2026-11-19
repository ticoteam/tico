"""A fake Gmail for tests: mailboxes of raw Gmail messages, their history, labels and the searches Tico makes.

`fake_gmail` replaces backend/gmail_live.py `gmail_call` and the token door (backend/google_tokens.py), so no test
reaches Google or needs a key. `put_mail` (backend/tests/helpers/mail_api.py) puts a message here. A search understands
the terms Tico writes: words, `from:`, `to:`, `cc:`, `in:sent`, `label:`, `after:`, `before:`, `rfc822msgid:` and one
`{a b}` group of alternatives. Drafts (`drafts`, found by `rfc822msgid:`), a message's raw bytes (`raws`), its
attachments and `messages/send` (`sent`) are kept here too; a large draft or send arrives as a multipart upload.
"""

import base64
import copy
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from backend import gmail_live, gmail_watch, google_tokens, mail_facts, messaging
from backend.tests.helpers.gmail_live import masked

CURRENT = {}  # the fixture's fake, for put_mail
REAL_TOKEN = google_tokens.token  # for a test that fakes the key behind the door instead


def _addresses(value):
    return [a.lower() for a in re.findall(r"[^\s<>,\"]+@[^\s<>,\"]+", value or "")]


class FakeGmail:
    def __init__(self):
        self.boxes = {}
        self.calls = []
        self.down = set()  # mailboxes whose every call answers 503
        self.refused = set()  # mailboxes Gmail refuses (403)
        self.expired = set()  # mailboxes whose history id Gmail no longer has (404)
        self.sent = []  # (mailbox, decoded MIME bytes, thread id) per messages.send
        self.send_answer = None  # (status, body) messages.send answers instead of a new id

    def box(self, mailbox):
        return self.boxes.setdefault(
            mailbox.lower(),
            {
                "messages": {},
                "history": [],
                "next": 1000,
                "drafts": {},
                "raws": {},
                "attachments": {},
                "labels": [
                    {"id": name, "name": name, "type": "system"}
                    for name in ("INBOX", "UNREAD", "SENT", "DRAFT", "STARRED", "IMPORTANT", "SPAM", "TRASH")
                ],
            },
        )

    def label(self, mailbox, name):
        """A user label's id, made on first use."""
        rows = self.box(mailbox)["labels"]
        for row in rows:
            if row["name"] == name:
                return row["id"]
        rows.append({"id": f"Label_{len(rows)}", "name": name, "type": "user"})
        return rows[-1]["id"]

    def add(
        self,
        mailbox,
        mid,
        *,
        thread="",
        sender="person@customer.example",
        sender_header="",
        to=None,
        cc=(),
        subject="Hello",
        body="Hello there.",
        epoch=1_783_368_400,
        labels=("INBOX", "UNREAD"),
        message_id="",
        headers=None,
        snippet="",
        same_record=False,
    ):
        """One message in the mailbox, recorded in its history (in the last history record with `same_record`). Label
        names that are not system labels become user labels."""
        box = self.box(mailbox)
        system = {row["id"] for row in box["labels"] if row["type"] == "system"}
        ids = [label if label in system else self.label(mailbox, label) for label in labels]
        listed = [
            {"name": "From", "value": sender_header or sender},
            {"name": "To", "value": ", ".join(to if to is not None else [mailbox])},
            {"name": "Subject", "value": subject},
            {"name": "Message-ID", "value": message_id or f"<{mid}@mail.example>"},
        ]
        if cc:
            listed.append({"name": "Cc", "value": ", ".join(cc)})
        for name, value in (headers or {}).items():
            listed.append({"name": name, "value": value})
        raw = {
            "id": mid,
            "threadId": thread or "t-" + mid,
            "internalDate": str(int(epoch) * 1000),
            "labelIds": ids,
            "snippet": snippet or body[:100],
            "historyId": str(box["next"]),
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": listed,
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}
                ],
            },
        }
        box["messages"][mid] = raw
        if same_record and box["history"]:
            box["history"].append((box["history"][-1][0], mid))
        else:
            box["history"].append((box["next"], mid))
            box["next"] += 1
        return raw

    def history_id(self, mailbox):
        return self.box(mailbox)["next"] - 1

    # ------------------------------------------------------------------------------------------------- search
    def _header(self, raw, name):
        for row in raw["payload"]["headers"]:
            if row["name"].lower() == name:
                return row["value"]
        return ""

    def _term(self, mailbox, raw, term):
        box = self.box(mailbox)
        names = {row["id"]: row["name"] for row in box["labels"]}
        key, _, value = term.partition(":")
        value = value.strip('"').lower()
        epoch = int(raw["internalDate"]) // 1000
        if key == "after" and value.isdigit():
            return epoch > int(value)
        if key == "before" and value.isdigit():
            return epoch < int(value)
        if key == "from":
            return value in _addresses(self._header(raw, "from"))
        if key in ("to", "cc"):
            return value in _addresses(self._header(raw, key))
        if key == "in":
            return value.upper() in raw["labelIds"]
        if key == "label":
            return any(value in (lid.lower(), names.get(lid, "").lower()) for lid in raw["labelIds"])
        if key == "rfc822msgid":
            return self._header(raw, "message-id").strip("<>").lower() == value
        text = (self._header(raw, "subject") + " " + raw["snippet"] + " " + self._header(raw, "from")).lower()
        return term.lower().strip("()") in text

    def matches(self, mailbox, raw, q):
        for group, single in re.findall(r"\{([^}]*)\}|(\S+)", q or ""):
            if group:
                if not any(self._term(mailbox, raw, t) for t in group.split()):
                    return False
            elif single.upper() != "OR" and not self._term(mailbox, raw, single):
                return False
        return True

    def drafts(self, mailbox):
        """{draft id: decoded MIME bytes} of the mailbox's drafts."""
        return {did: _decoded(d["message"]["raw"]) for did, d in self.box(mailbox)["drafts"].items()}

    def put_draft(self, mailbox, did, mid, thread, mime):
        """A draft made outside Tico (in Gmail, or by an older tool), its message also in the mailbox with DRAFT."""
        box = self.box(mailbox)
        raw = base64.urlsafe_b64encode(mime).decode().rstrip("=")
        box["drafts"][did] = {"id": did, "message": {"id": mid, "threadId": thread, "raw": raw}}
        box["messages"][mid] = _gmail_message(mid, thread, mime)
        return did

    def _draft_matches(self, draft, q):
        mime = _decoded(draft["message"]["raw"])
        if "rfc822msgid:" in q:
            return _message_id(mime) == q.partition("rfc822msgid:")[2].strip().lower()
        text = mime.decode("utf-8", "replace").lower()
        for term in q.split():
            key, _, value = term.partition(":")
            header = re.search(rf"(?im)^{key}:(.*)$", text) if value and key in ("to", "subject", "cc") else None
            if (header and value.strip('"').lower() not in header.group(1)) or (not value and term.lower() not in text):
                return False
        return True

    def _draft_call(self, mailbox, box, tail, query, data, method=None):
        if tail == "drafts" and data is not None:
            box["drafts_made"] = box.get("drafts_made", 0) + 1
            did = f"r-{box['drafts_made']}"
            message = data["message"]
            box["drafts"][did] = {
                "id": did,
                "message": {
                    "id": "dm-" + did,
                    "threadId": message.get("threadId") or "dt-" + did,
                    "raw": message["raw"],
                },
            }
            return 200, {"id": did, "message": {k: v for k, v in box["drafts"][did]["message"].items() if k != "raw"}}
        if tail == "drafts":
            q = query.get("q", [""])[0]
            found = [d for d in reversed(box["drafts"].values()) if self._draft_matches(d, q)]  # newest first
            start, size = int(query.get("pageToken", ["0"])[0]), int(query.get("maxResults", ["100"])[0])
            rows = [
                {"id": d["id"], "message": {"id": d["message"]["id"], "threadId": d["message"]["threadId"]}}
                for d in found[start : start + size]
            ]
            return 200, {"drafts": rows, **({"nextPageToken": str(start + size)} if start + size < len(found) else {})}
        did = tail.split("/", 1)[1]
        draft = box["drafts"].get(did)
        if draft is None:
            return 404, {}
        if method == "PUT":
            box["edits"] = box.get("edits", 0) + 1
            message = data["message"]
            draft["message"] = {
                "id": f"dm-{did}-{box['edits']}",
                "threadId": message.get("threadId") or draft["message"]["threadId"],
                "raw": message["raw"],
            }
            return 200, {"id": did, "message": {k: v for k, v in draft["message"].items() if k != "raw"}}
        if query.get("format") == ["full"]:
            message = draft["message"]
            full = _gmail_message(message["id"], message["threadId"], _decoded(message["raw"]))
            return 200, {"id": did, "message": masked(full) if "fields" in query else full}
        return 200, copy.deepcopy(draft)

    def delete(self, url, *, token):
        path = unquote(urlsplit(url).path)
        mailbox, _, tail = path.split("/gmail/v1/users/", 1)[1].partition("/")
        self.calls.append((path, {"method": ["DELETE"]}, None))
        if mailbox.lower() in self.down:
            return 503
        box = self.box(mailbox)
        gone = box["drafts"].pop(tail.split("/", 1)[1], None)
        if gone:
            box["messages"].pop(gone["message"]["id"], None)
        return 204 if gone else 404

    def send(self, url, *, token, body, content_type, method=None):
        mailbox, _, tail = unquote(urlsplit(url).path).split("/gmail/v1/users/", 1)[1].partition("/")
        mailbox = mailbox.lower()
        if "uploadType=multipart" in url:  # the metadata JSON, then the MIME (gmail_live._upload_body)
            boundary = b"--" + content_type.split("boundary=", 1)[1].encode()
            meta, mime = (part.split(b"\r\n\r\n", 1)[1].removesuffix(b"\r\n") for part in body.split(boundary)[1:3])
            found = json.loads(meta)
            raw = base64.urlsafe_b64encode(mime).decode().rstrip("=")
            if tail.startswith("drafts/") and method == "PUT":
                message = {**found.get("message", {}), "raw": raw}
                return self._draft_call(mailbox, self.box(mailbox), tail, {}, {**found, "message": message}, "PUT")
            if tail == "drafts":
                return self._draft_call(
                    mailbox,
                    self.box(mailbox),
                    "drafts",
                    {},
                    {**found, "message": {**found.get("message", {}), "raw": raw}},
                )
            found = {**found, "raw": raw}
        else:
            found = json.loads(body)
        self.sent.append((mailbox, _decoded(found["raw"]), found.get("threadId", "")))
        if self.send_answer is not None:
            return self.send_answer
        return 200, {"id": f"sent-{len(self.sent)}"}

    # ------------------------------------------------------------------------------------------------- HTTP
    def __call__(self, url, *, token, data=None, max_bytes=None, method=None):
        parsed = urlsplit(url)
        path, query = unquote(parsed.path), parse_qs(parsed.query)
        self.calls.append((path, query, data))
        rest = path.split("/gmail/v1/users/", 1)[1]
        mailbox, _, tail = rest.partition("/")
        mailbox = mailbox.lower()
        if mailbox in self.down:
            return 503, {}
        if mailbox in self.refused:
            return 403, {}
        box = self.box(mailbox)
        if tail == "profile":
            return 200, {"historyId": str(self.history_id(mailbox))}
        if tail == "history":
            start = int(query["startHistoryId"][0])
            if mailbox in self.expired:
                return 404, {}
            records = {}
            for hid, mid in box["history"]:
                if hid > start:
                    records.setdefault(hid, []).append({"message": {"id": mid}})
            rows = [{"id": str(hid), "messagesAdded": added} for hid, added in records.items()]
            return 200, {"history": rows, "historyId": str(self.history_id(mailbox))}
        if tail == "drafts" or tail.startswith("drafts/"):
            return self._draft_call(mailbox, box, tail, query, data, method)
        if tail == "labels" and data is not None:
            return 200, {"id": self.label(mailbox, data["name"]), "name": data["name"], "type": "user"}
        if tail == "messages/batchModify":
            for mid in data["ids"]:
                raw = box["messages"].get(mid)
                if raw is not None:
                    raw["labelIds"] = [x for x in raw["labelIds"] if x not in data["removeLabelIds"]] + [
                        x for x in data["addLabelIds"] if x not in raw["labelIds"]
                    ]
            return 200, {}
        if tail == "labels":
            return 200, {"labels": copy.deepcopy(box["labels"])}
        if tail.startswith("labels/"):
            lid = tail.split("/", 1)[1]
            return 200, {"messagesTotal": sum(lid in raw["labelIds"] for raw in box["messages"].values())}
        if tail == "messages":
            q = query.get("q", [""])[0]
            found = sorted(
                (raw for raw in box["messages"].values() if self.matches(mailbox, raw, q)),
                key=lambda raw: (int(raw["internalDate"]), raw["id"]),
                reverse=True,
            )
            start, size = int(query.get("pageToken", ["0"])[0]), int(query.get("maxResults", ["100"])[0])
            page = {"messages": [{"id": raw["id"], "threadId": raw["threadId"]} for raw in found[start : start + size]]}
            if start + size < len(found):
                page["nextPageToken"] = str(start + size)
            return 200, page
        if tail.startswith("threads/"):
            tid = tail.split("/", 1)[1]
            rows = [copy.deepcopy(raw) for raw in box["messages"].values() if raw["threadId"] == tid]
            return (200, {"id": tid, "messages": rows}) if rows else (404, {})
        if tail.startswith("messages/") and "/attachments/" in tail:
            _, mid, _, aid = tail.split("/")
            found = box["attachments"].get((mid, aid))
            return (200, {"data": base64.urlsafe_b64encode(found).decode(), "size": len(found)}) if found else (404, {})
        if tail.startswith("messages/"):
            raw = box["messages"].get(tail.split("/", 1)[1])
            if raw is None:
                return 404, {}
            if query.get("format") == ["raw"]:
                data = box["raws"].get(raw["id"]) or b"From: " + self._header(raw, "from").encode() + b"\r\n\r\nHello"
                return 200, {"id": raw["id"], "raw": base64.urlsafe_b64encode(data).decode()}
            raw = copy.deepcopy(raw)
            if "fields" in query:
                masked(raw)
            elif query.get("format") == ["metadata"]:  # Gmail's metadata read has no parts tree
                raw["payload"].pop("parts", None)
            return 200, raw
        raise AssertionError("the fake Gmail has no route for " + path + " " + json.dumps(query))


def _decoded(raw):
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def _gmail_message(mid, thread, mime, labels=("DRAFT",)):
    """A MIME message as Gmail answers a format=full read: its parts tree, each leaf's bytes base64url."""
    from email import policy
    from email.parser import BytesParser

    msg = BytesParser(policy=policy.default).parsebytes(mime)

    def part(p, pid):
        out = {
            "partId": pid,
            "mimeType": p.get_content_type(),
            "filename": p.get_filename() or "",
            "headers": [{"name": k, "value": str(v)} for k, v in p.items()],
        }
        if p.is_multipart():
            out["body"] = {"size": 0}
            out["parts"] = [part(child, f"{pid}.{i}" if pid else str(i)) for i, child in enumerate(p.iter_parts())]
        else:
            data = p.get_payload(decode=True) or b""
            out["body"] = {"size": len(data), "data": base64.urlsafe_b64encode(data).decode()}
        return out

    text = msg.get_body(preferencelist=("plain",))
    return {
        "id": mid,
        "threadId": thread,
        "labelIds": list(labels),
        "snippet": (text.get_content() if text is not None else "")[:100],
        "internalDate": "1783369000000",  # saved after the fake's mail arrives
        "payload": part(msg, ""),
    }


def _message_id(mime):
    found = re.search(rb"(?im)^message-id:\s*<([^>]+)>", mime)
    return found.group(1).decode().lower() if found else ""


def forget_caches():
    mail_facts.forget()
    messaging._REVIEW.clear()
    gmail_watch._TRIED.clear()


@pytest.fixture
def fake_gmail(monkeypatch):
    fake = FakeGmail()
    monkeypatch.setattr(gmail_live, "gmail_call", fake)
    monkeypatch.setattr(gmail_live, "gmail_delete_call", fake.delete)
    monkeypatch.setattr(gmail_live, "send_call", fake.send)
    monkeypatch.setattr(google_tokens, "token", lambda c, mailbox, service, write=False: "fake-gmail-token")
    forget_caches()
    CURRENT["fake"] = fake
    yield fake
    CURRENT.pop("fake", None)
    forget_caches()
