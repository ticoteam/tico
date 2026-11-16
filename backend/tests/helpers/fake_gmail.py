"""A fake Gmail for tests: mailboxes of raw Gmail messages, their history, labels and the searches Tico makes.

`fake_gmail` replaces backend/gmail_live.py `gmail_call` and the token door (backend/google_tokens.py), so no test
reaches Google or needs a key. `put_mail` (backend/tests/helpers/mail_api.py) puts a message here. A search understands
the terms Tico writes: words, `from:`, `to:`, `cc:`, `in:sent`, `label:`, `after:`, `before:`, `rfc822msgid:` and one
`{a b}` group of alternatives.
"""

import base64
import copy
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from backend import gmail_live, gmail_watch, google_tokens, mail_facts, messaging

CURRENT = {}  # the fixture's fake, for put_mail


def _addresses(value):
    return [a.lower() for a in re.findall(r"[^\s<>,\"]+@[^\s<>,\"]+", value or "")]


class FakeGmail:
    def __init__(self):
        self.boxes = {}
        self.calls = []
        self.down = set()  # mailboxes whose every call answers 503
        self.refused = set()  # mailboxes Gmail refuses (403)
        self.expired = set()  # mailboxes whose history id Gmail no longer has (404)

    def box(self, mailbox):
        return self.boxes.setdefault(
            mailbox.lower(),
            {
                "messages": {},
                "history": [],
                "next": 1000,
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

    # ------------------------------------------------------------------------------------------------- HTTP
    def __call__(self, url, *, token, data=None):
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
        if tail.startswith("messages/"):
            raw = box["messages"].get(tail.split("/", 1)[1])
            if raw is None:
                return 404, {}
            raw = copy.deepcopy(raw)
            if query.get("format") == ["metadata"]:
                raw["payload"].pop("parts", None)
            return 200, raw
        raise AssertionError("the fake Gmail has no route for " + path + " " + json.dumps(query))


def forget_caches():
    mail_facts.forget()
    messaging._REVIEW.clear()
    gmail_watch._TRIED.clear()


@pytest.fixture
def fake_gmail(monkeypatch):
    fake = FakeGmail()
    monkeypatch.setattr(gmail_live, "gmail_call", fake)
    monkeypatch.setattr(google_tokens, "token", lambda c, mailbox, service, write=False: "fake-gmail-token")
    forget_caches()
    CURRENT["fake"] = fake
    yield fake
    CURRENT.pop("fake", None)
    forget_caches()
