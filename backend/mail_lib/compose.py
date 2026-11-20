# pyright: strict
"""Building a plain-text message as base64url MIME, adding a calendar invitation to one, and putting a warning line
above one's text.

Pure. Nothing here talks to Google; the callers hand the raw string to Gmail or to the send queue. Attachments come in
already read ({name, type, data}); with any, the message is multipart/mixed with the text first, so lint and the
reviewer read the same body Gmail shows.
"""

import base64
import email
import html
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Any

from icalendar import Calendar, Component, Event, vCalAddress, vUri

from .calendar import meeting_url

Json = dict[str, Any]


def _add(component: Component, name: str, value: object, parameters: dict[str, str] | None = None) -> None:
    component.add(name, value, parameters=parameters or {})  # pyright: ignore[reportUnknownMemberType] - icalendar leaves `value` untyped


def with_invitation(
    raw: str, event: Json, mailbox: str, attendees: Iterable[str], start: datetime, end: datetime, summary: str
) -> str:
    """The invitation is part of the same frozen MIME send and approval as its confirmation."""
    join = meeting_url(event)
    conference_data: Json = event.get("conferenceData") or {}
    points: list[Json] = conference_data.get("entryPoints") or []
    conference = [str(point["uri"]).replace("\r", "").replace("\n", "") for point in points if point.get("uri")]
    description = "\n".join(filter(None, [event.get("description"), "Join: " + join if join else "", *conference]))
    calendar = Calendar()
    _add(calendar, "version", "2.0")
    _add(calendar, "prodid", "-//Tico//Calendar//EN")
    _add(calendar, "method", "REQUEST")
    invite = Event()
    _add(invite, "uid", event.get("iCalUID") or str(event["id"]) + "@google.com")
    _add(invite, "sequence", 0)
    _add(invite, "dtstamp", start.astimezone(timezone.utc))
    _add(invite, "dtstart", start.astimezone(timezone.utc))
    _add(invite, "dtend", end.astimezone(timezone.utc))
    _add(invite, "summary", summary)
    _add(invite, "organizer", vCalAddress("mailto:" + mailbox))
    if join:
        _add(invite, "url", vUri(join))
    if event.get("location"):
        _add(invite, "location", str(event["location"]))
    if description:
        _add(invite, "description", description)
    for uri in dict.fromkeys([join, *conference]):
        if uri:
            _add(invite, "conference", vUri(uri), parameters={"VALUE": "URI"})
    for address in attendees:
        _add(invite, "attendee", vCalAddress("mailto:" + address), parameters={"RSVP": "TRUE"})
    calendar.add_component(invite)
    msg = BytesParser(policy=policy.SMTP).parsebytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    details = "\n".join(
        filter(
            None,
            [
                "Join: " + join if join else "",
                "Location: " + str(event["location"]) if event.get("location") else "",
                str(event.get("description") or ""),
            ],
        )
    )
    if details:
        body = msg.get_body(preferencelist=("plain",))
        if body is None:
            raise ValueError("An invitation needs a visible plain-text body")
        body.set_content(body.get_content().rstrip() + "\n\n" + details + "\n")
    ics = calendar.to_ical(sorted=False).decode()  # in the order added, folded at 75 octets
    msg.add_attachment(ics, subtype="calendar", filename="invitation.ics", params={"method": "REQUEST"})
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def build(
    to: str | list[str] | tuple[str, ...],
    subject: str,
    body: str,
    from_addr: str = "",
    cc: Sequence[str] = (),
    in_reply_to: str = "",
    references: str = "",
    attachments: Iterable[Json] = (),
) -> str:
    """A plain-text message, with any attachments, base64url encoded the way the Gmail API wants it."""
    msg = EmailMessage()
    msg["To"] = ", ".join(to if isinstance(to, (list, tuple)) else [to])
    if cc:
        msg["Cc"] = ", ".join(cc)
    if from_addr:
        msg["From"] = from_addr
    msg["Subject"] = subject or ""
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body or "")
    for a in attachments or ():
        main, _, sub = str(a.get("type") or "application/octet-stream").partition("/")
        msg.add_attachment(a["data"], maintype=main or "application", subtype=sub or "octet-stream", filename=a["name"])
    return base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")


def with_warning(raw: str, line: str) -> str:
    """The same message with `line` above its text (each text/plain part, and as a paragraph above each text/html
    part), headers and attachments untouched: an org rule's Warn on the recipient's copy."""
    s = str(raw or "").replace("-", "+").replace("_", "/")
    msg = email.message_from_bytes(base64.b64decode(s + "=" * (-len(s) % 4)), policy=policy.default)
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        kind = part.get_content_type()
        if kind == "text/plain":
            part.set_content(line + "\n\n" + part.get_content())
        elif kind == "text/html":
            part.set_content("<p>" + html.escape(line) + "</p>\n" + part.get_content(), subtype="html")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
