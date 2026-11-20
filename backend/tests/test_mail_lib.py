"""The pure email helpers the server keeps from the retired mail CLI (backend/mail_lib): the lint rules, the Gmail
normalizer, attachment references and decoding, the invitation and warning MIME, and the hub label names.

Pure: no network, no Gmail, no store.
"""

import base64
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from backend.email_content import attachment, parsed, snapshot
from backend.mail_lib import MAX_BODY, Failure, Refused, zone
from backend.mail_lib import compose, gmail as gm, labels as lb, lint as ln
from backend.mail_lib.calendar import meeting_url
from backend.store import Problem

TZ = zone("America/Los_Angeles")


# ---------------------------------------------------------------- raw Gmail messages


def b64(data):
    data = data.encode("utf-8") if isinstance(data, str) else data
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def part(mime, text=None, filename="", size=None, charset="utf-8"):
    p = {
        "mimeType": mime,
        "filename": filename,
        "headers": [{"name": "Content-Type", "value": f"{mime}; charset={charset}"}],
    }
    p["body"] = {"attachmentId": "att-1", "size": size or 0} if text is None else {"data": b64(text), "size": len(text)}
    return p


def message(
    mid="m1", headers=None, body=None, parts=None, labels=("INBOX", "UNREAD"), epoch_ms=1788368400000, snippet=""
):
    """A raw Gmail message dict. epoch default: 2026-09-02 10:00 America/Los_Angeles."""
    payload = {"headers": [{"name": k, "value": v} for k, v in (headers or {}).items()]}
    if parts is not None:
        payload.update(mimeType="multipart/mixed", parts=parts)
    else:
        payload.update(mimeType="text/plain", body={"data": b64(body or ""), "size": len(body or "")})
    return {
        "id": mid,
        "threadId": "t-" + mid,
        "labelIds": list(labels),
        "internalDate": str(epoch_ms),
        "snippet": snippet,
        "payload": payload,
    }


# ---------------------------------------------------------------- lint

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=TZ)  # Wednesday 2026-09-02, 10:00 PT
CTA = "https://acme.example/rentals?utm_source=influencer&utm_medium=email&utm_campaign=ava-reyes"
CLEAN = """Hi Ava,

I'm Ana, the founder of Acme. We're the AI property manager: pricing, guest messaging and
turnovers for 3.9% instead of the 30% a manager charges.

I'd like to work with you. What is your rate for posts/collaborations?

Ana
Founder, Acme
"""


def lint(body, **kw):
    kw.setdefault("subject", "Ava, 6 months of Acme on us")
    kw.setdefault("now", NOW)
    kw.setdefault("signature_names", ["ana"])
    return ln.run(body, **kw)


def ids(res):
    return [x["id"] for x in res["findings"]]


def test_the_shipped_outreach_shape_passes():
    res = lint(CLEAN + f"\nHere's the product: {CTA}\n\nAna\n", to=["ava@creator.example"], slug="influencer")
    assert res.ok, ids(res)
    assert res["findings"] == [] and res["errors"] == [] and res["warnings"] == []


def test_L030_secret_shapes():
    for bad in ("xoxb-123", "AKIAIOSFODNN7", "sk-abc123", "ghp_abc", "-----BEGIN KEY"):
        assert "L030" in ids(lint(CLEAN + f"\n{bad}\n\nAna\n")), bad
    assert "L030" not in ids(lint(CLEAN))


def test_L040_internal_leakage():
    for bad in (
        "s3://acme-tico-hub/x",
        "runtime/mail/mail.db",
        "emp-influencer",
        "bot-influencer/knowledge/notes.md",
        "see AGENTS.md",
    ):
        assert "L040" in ids(lint(CLEAN + f"\n{bad}\n\nAna\n")), bad
    assert "L040" not in ids(lint(CLEAN))
    assert "L040" not in ids(lint(CLEAN + "\nA bot-driven approach.\n\nAna\n"))


def test_L010_only_the_company_domains_may_be_linked():
    assert "L010" in ids(lint(CLEAN + "\nhttps://elsewhere.example/x\n\nAna\n"))
    assert "L010" not in ids(lint(CLEAN + "\nhttps://www.tico.example/x\n\nAna\n", internal_domains=("tico.example",)))


def test_the_server_skips_the_signature_rule_and_an_issue_ref_only_warns():
    unsigned = "Thanks for the note, the order ships Tuesday. Invoice #4471 is attached."
    assert "L055" in ids(lint(unsigned))
    res = lint(unsigned, signature_names=None)
    assert res.ok and [f["id"] for f in res["warnings"]] == ["L041"]


def test_a_named_zone_sets_the_instant_and_meet_is_not_a_zone():
    utc = zone("UTC")
    (t,) = ln.parse_times("Can we do Sep 8, 1pm ET?", now=NOW)
    assert t["start"].astimezone(utc) == datetime(2026, 9, 8, 17, 0, tzinfo=utc)
    assert t["start"].hour == 10  # the same instant, read in the lint's own zone
    (t,) = ln.parse_times("Happy to meet Sep 8, 1pm.", now=NOW)
    assert not t["tz_named"]
    assert "L065" in ids(lint(CLEAN + "\nHappy to meet Sep 8 at 1pm or Sep 9 at 1pm.\n\nAna\n"))


def test_an_impossible_end_minute_is_a_finding_not_a_crash():
    res = lint(CLEAN + "\nSep 8, 1pm-2:99pm PT or Sep 9, 1pm PT.\n\nAna\n")
    assert "L065" not in ids(res)
    (t,) = ln.parse_times("Sep 8, 1pm-2:99pm PT", now=NOW)
    assert t["end"] is None


def test_a_word_that_starts_like_a_zone_is_not_one():
    for text in ("Sep 8, 1pm mtg with Ava", "Sep 8 at 1pm etc", "Sep 8, 1pm CTO sync"):
        (t,) = ln.parse_times(text, now=NOW)
        assert (t["start"].hour, t["tz_named"]) == (13, False), text
    (t,) = ln.parse_times("Can we meet" + " " * 38 + "Sep 8, 1pm?", now=NOW)
    assert not t["tz_named"]  # the window may begin inside "meet"


def test_offered_times_must_be_two_ordered_future_weekday_business_hours_and_a_confirmation_needs_a_slot():
    assert "L060" in ids(lint(CLEAN + "\nDoes Thu Sep 3, 1pm PT work?\n\nAna\n"))
    found = ids(
        lint(CLEAN + "\nSep 4, 1pm PT or Sep 3, 1pm PT or Sep 1, 1pm PT or Sep 5, 1pm PT or Sep 7, 7pm PT\n\nAna\n")
    )
    assert {"L061", "L062", "L063", "L064"} <= set(found)
    assert "L067" in ids(lint(CLEAN + "\nYou're all set for Sep 3, 1pm PT.\n\nAna\n"))
    assert {"L060", "L067"}.isdisjoint(
        ids(lint(CLEAN + "\nYou're all set for Sep 3, 1pm PT.\n\nAna\n", slot="2026-09-03T13:00"))
    )


# ---------------------------------------------------------------- compose


def test_build_puts_the_text_first_and_the_files_after_it():
    raw = compose.build(
        ("a@x.example", "b@x.example"),
        "Hi",
        "Body text",
        from_addr="me@acme.example",
        cc=["c@x.example"],
        in_reply_to="<m@x>",
        references="<r@x> <m@x>",
        attachments=[{"name": "q.pdf", "type": "application/pdf", "data": b"%PDF"}],
    )
    msg = parsed(raw)
    assert (msg["To"], msg["Cc"], msg["From"], msg["In-Reply-To"]) == (
        "a@x.example, b@x.example",
        "c@x.example",
        "me@acme.example",
        "<m@x>",
    )
    leaves = [p for p in msg.walk() if not p.is_multipart()]
    assert [p.get_content_type() for p in leaves] == ["text/plain", "application/pdf"]
    assert leaves[0].get_content().strip() == "Body text" and leaves[1].get_content() == b"%PDF"
    assert not raw.endswith("=")
    assert parsed(compose.build("solo@x.example", "", ""))["To"] == "solo@x.example"


def test_an_invitation_is_frozen_in_the_same_mime_with_its_join_link_and_escaped_ics():
    start = datetime(2030, 1, 8, 10, tzinfo=TZ)
    join = "https://meet.google.com/abc-defg-hij"
    event = {
        "id": "event",
        "hangoutLink": join,
        "location": "Room A; upstairs",
        "description": "Agenda: budget, review\nSecond line",
        "conferenceData": {
            "entryPoints": [
                {"entryPointType": "video", "uri": join},
                {"entryPointType": "phone", "uri": "tel:+15555550123"},
            ]
        },
    }
    raw = compose.with_invitation(
        compose.build(["guest@vendor.example"], "Meeting", "Meeting tomorrow", from_addr="ana@acme.example"),
        event,
        "ana@acme.example",
        ["guest@vendor.example"],
        start,
        start + timedelta(minutes=30),
        "Meeting",
    )
    message_ = parsed(raw)
    text = message_.get_body(preferencelist=("plain",)).get_content()
    assert join in text and "Room A; upstairs" in text
    ics = next(p.get_content() for p in message_.walk() if p.get_content_type() == "text/calendar")
    assert all(len(line.encode()) <= 75 for line in ics.replace("\r\n", "\n").split("\n"))
    unfolded = ics.replace("\r\n ", "").replace("\n ", "")
    assert "URL:" + join in unfolded and "CONFERENCE;VALUE=URI:" + join in unfolded
    assert "CONFERENCE;VALUE=URI:tel:+15555550123" in unfolded
    assert (
        "LOCATION:Room A\\; upstairs" in unfolded and "DESCRIPTION:Agenda: budget\\, review\\nSecond line" in unfolded
    )
    assert "DTSTART:20300108T180000Z" in unfolded and "UID:event@google.com" in unfolded
    assert "ATTENDEE;RSVP=TRUE:mailto:guest@vendor.example" in unfolded
    frozen = snapshot(raw, "ana@acme.example")
    assert join.encode() in attachment(raw, frozen["attachments"][0]["part"])


def test_a_line_break_in_an_invitation_value_cannot_add_a_property():
    start = datetime(2030, 1, 8, 10, tzinfo=TZ)
    evil = "\r\nATTENDEE:mailto:evil@x.example"
    raw = compose.with_invitation(
        compose.build(["guest@vendor.example"], "M", "x", from_addr="ana@acme.example"),
        {"id": "e", "iCalUID": "u" + evil, "location": "Room" + evil},
        "ana@acme.example",
        ["guest@vendor.example"],
        start,
        start + timedelta(minutes=30),
        "Hi" + evil,
    )
    ics = next(p.get_content() for p in parsed(raw).walk() if p.get_content_type() == "text/calendar")
    lines = ics.replace("\r\n ", "").split("\r\n")
    assert [line for line in lines if line.startswith("ATTENDEE")] == ["ATTENDEE;RSVP=TRUE:mailto:guest@vendor.example"]
    assert "SUMMARY:Hi\\nATTENDEE:mailto:evil@x.example" in lines


def test_an_invitation_with_details_needs_a_plain_text_body():
    html_only = EmailMessage()
    html_only["To"] = "guest@vendor.example"
    html_only.set_content("<p>hi</p>", subtype="html")
    raw = base64.urlsafe_b64encode(html_only.as_bytes()).decode()
    start = datetime(2030, 1, 8, 10, tzinfo=TZ)
    with pytest.raises(ValueError):
        compose.with_invitation(raw, {"id": "e", "location": "Room A"}, "a@acme.example", [], start, start, "M")


def test_a_warning_goes_above_every_text_part_and_leaves_headers_and_files_alone():
    msg = EmailMessage()
    msg["To"], msg["Subject"] = "guest@vendor.example", "Quote"
    msg.set_content("Plain body")
    msg.add_alternative("<p>Rich body</p>", subtype="html")
    msg.add_attachment(b"PDFDATA", maintype="application", subtype="pdf", filename="q.pdf")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    warned = parsed(compose.with_warning(raw, "Careful <here>"))
    assert (warned["To"], warned["Subject"]) == ("guest@vendor.example", "Quote")
    assert warned.get_body(preferencelist=("plain",)).get_content().startswith("Careful <here>\n\nPlain body")
    assert (
        warned.get_body(preferencelist=("html",))
        .get_content()
        .startswith("<p>Careful &lt;here&gt;</p>\n<p>Rich body</p>")
    )
    (pdf,) = [p for p in warned.walk() if p.get_content_disposition() == "attachment"]
    assert pdf.get_content() == b"PDFDATA"


# ---------------------------------------------------------------- calendar


def test_the_meeting_link_comes_from_the_event_first_then_its_text():
    meet = "https://meet.google.com/abc-defg-hij"
    assert meeting_url({"hangoutLink": meet, "location": "https://zoom.us/j/1"}) == meet
    phone_then_video = {
        "conferenceData": {
            "entryPoints": [
                {"entryPointType": "phone", "uri": "https://zoom.us/j/9"},
                {"entryPointType": "video", "uri": "https://acme.zoom.us/j/2"},
            ]
        }
    }
    assert meeting_url(phone_then_video) == "https://acme.zoom.us/j/2"
    assert (
        meeting_url({"description": "Join (https://teams.microsoft.com/l/meetup/x)."})
        == "https://teams.microsoft.com/l/meetup/x"
    )
    assert meeting_url({"hangoutLink": "https://example.com/x", "location": "Room 4"}) == ""


# ---------------------------------------------------------------- labels


def test_label_names_are_hub_labels_or_gmail_system_labels():
    assert lb.label_name("waiting") == "hub/waiting"
    assert lb.label_name(" hub/needs-owner ") == lb.NEEDS_OWNER
    assert lb.label_name("STARRED") == "STARRED"
    with pytest.raises(Refused):
        lb.label_name("  ")
    assert lb.check_name("INBOX") == "INBOX"
    with pytest.raises(Failure) as bad:
        lb.check_name("Personal/Receipts")
    assert not isinstance(bad.value, Refused)


# ---------------------------------------------------------------- normalizer

PLAIN = {
    "From": "Real Person <Person@Customer.example>",
    "To": "ana@acme.example, Bob <bob@acme.example>",
    "Cc": "carol@other.example",
    "Subject": "Pricing question",
    "Date": "Tue, 02 Sep 2026 09:30:00 -0700",
    "Message-ID": "<m1@customer.example>",
}


def test_normalize_reads_the_people_time_labels_and_body():
    m = gm.normalize(
        message(headers=PLAIN, body="How does pricing work?", snippet="How &amp; why"), {"Label_7": "hub/drafted"}
    )
    assert (m["id"], m["thread_id"], m["from"], m["to"], m["cc"]) == (
        "m1",
        "t-m1",
        "person@customer.example",
        ["ana@acme.example", "bob@acme.example"],
        ["carol@other.example"],
    )
    assert (m["date"], m["epoch"]) == ("2026-09-02T10:00:00-07:00", 1788368400)
    assert (m["body"], m["body_truncated"], m["snippet"]) == ("How does pricing work?", False, "How & why")
    assert (
        m["headers"]["message-id"] == "<m1@customer.example>"
        and m["send_facts"]["message-id"] == "<m1@customer.example>"
    )
    assert not m["is_internal"] and not m["unsubscribe"] and not m["is_calendar_invite"]
    m = gm.normalize(message(headers=PLAIN, labels=("INBOX", "Label_7")), {"Label_7": "hub/drafted"})
    assert m["labels"] == ["INBOX", "hub/drafted"]


def test_without_internal_date_the_date_header_is_used_and_a_bad_one_is_empty():
    raw = message(headers=PLAIN, body="x")
    del raw["internalDate"]
    assert gm.normalize(raw)["date"] == "2026-09-02T09:30:00-07:00"
    raw["payload"]["headers"] = [{"name": "Date", "value": "not a date"}]
    m = gm.normalize(raw)
    assert (m["date"], m["epoch"]) == ("", 0)


def test_html_only_mail_is_stripped_to_text():
    page = "<html><head><title>T</title></head><style>p{}</style><script>alert(1)</script><p>Hello&nbsp;&amp; welcome</p><div>Line\xa0two</div><br><br><br><br>End</html>"
    m = gm.normalize(message(headers=PLAIN, parts=[part("text/html", page)]))
    assert m["body"] == "Hello & welcome\nLine two\n\nEnd"
    assert gm.body_text(message(parts=[part("text/plain", "  "), part("text/html", "<b>x</b>")])["payload"]) == (
        "x",
        "<b>x</b>",
    )


def test_a_plain_part_wins_over_html_and_a_charset_is_honoured():
    latin = {
        "mimeType": "text/plain",
        "filename": "",
        "headers": [{"name": "Content-Type", "value": "text/plain; charset=ISO-8859-1"}],
        "body": {"data": b64("café".encode("latin-1"))},
    }
    m = gm.normalize(message(parts=[latin, part("text/html", "<p>other</p>")]))
    assert m["body"] == "café"


def test_a_long_body_is_cut_to_the_limit_note_included():
    m = gm.normalize(message(body="x" * (MAX_BODY + 50)))
    assert m["body_truncated"] and len(m["body"]) == MAX_BODY and m["body"].endswith("characters ...]")
    m = gm.normalize(message(body="x" * MAX_BODY))
    assert not m["body_truncated"] and len(m["body"]) == MAX_BODY


@pytest.mark.parametrize(
    "headers,parts,url",
    [
        (
            {"List-Unsubscribe": "<https://acme.io/u/1>, <mailto:u@acme.io>"},
            [part("text/plain", "hi")],
            "https://acme.io/u/1",
        ),
        ({"List-Unsubscribe": "<mailto:u@acme.io>"}, [part("text/plain", "hi")], ""),
        (
            {},
            [part("text/html", '<a href="https://x.example/email-preferences?a=1&amp;b=2">prefs</a>')],
            "https://x.example/email-preferences?a=1&b=2",
        ),
        (
            {},
            [part("text/html", '<a class="f" href="https://t.example/r/abc">Unsubscribe here</a>')],
            "https://t.example/r/abc",
        ),
        ({}, [part("text/plain", "Bye. https://x.example/opt-out?id=4")], "https://x.example/opt-out?id=4"),
        ({}, [part("text/plain", "Thanks\nUnsubscribe: mailto:stop@x.example")], "mailto:stop@x.example"),
    ],
)
def test_an_unsubscribe_link_is_found_in_the_header_then_the_body(headers, parts, url):
    m = gm.normalize(message(headers=headers, parts=parts))
    assert (m["unsubscribe"], m["unsubscribe_url"]) == (True, url)


def test_internal_mail_and_invitations_are_recognised():
    internal = {"From": "ana@acme.example", "To": "team@eng.acme.example"}
    assert gm.normalize(message(headers=internal, body="x"))["is_internal"]
    assert not gm.normalize(message(headers={**internal, "Cc": "x@acme.example.evil"}, body="x"))["is_internal"]
    invite = gm.normalize(message(parts=[part("text/plain", "x"), part("text/calendar", "BEGIN:VCALENDAR")]))
    assert invite["is_calendar_invite"] and invite["attachments"] == [
        {"name": "invite.ics", "type": "text/calendar", "size": 15}
    ]
    assert gm.normalize(message(headers={"Content-Type": "multipart/mixed; method=REQUEST"}, body="x"))[
        "is_calendar_invite"
    ]


def test_a_bounce_keeps_its_delivery_status_bounded():
    status = {
        "mimeType": "message/delivery-status",
        "filename": "",
        "headers": [],
        "body": {"data": b64("Status: 5.1.1\n" * 1000)},
    }
    m = gm.normalize(
        message(
            headers={"Auto-Submitted": "auto-replied", "Return-Path": "<>"},
            parts=[part("text/plain", "failed"), status],
        )
    )
    facts = m["send_facts"]
    assert (facts["auto-submitted"], facts["return-path"], facts["precedence"]) == ("auto-replied", "<>", "")
    assert facts["delivery-status"].startswith("Status: 5.1.1") and len(facts["delivery-status"]) == 4096


# ---------------------------------------------------------------- attachments

PDF = b"%PDF-1.7\nMurphy evidence\n%%EOF\n"
INLINE = b"inline evidence\x00\xff"


def attachment_payload():
    remote = {
        "partId": "1.0",
        "mimeType": "application/pdf",
        "filename": "../../Murphy\nCase?.pdf",
        "headers": [],
        "body": {"attachmentId": "gmail-pdf-id", "size": len(PDF)},
    }
    inline = {
        "partId": "1.1",
        "mimeType": "application/octet-stream",
        "filename": "..\\inline?.bin",
        "headers": [],
        "body": {"data": b64(INLINE), "size": len(INLINE)},
    }
    unnamed = {
        "partId": "1.2",
        "mimeType": "application/octet-stream",
        "filename": "",
        "headers": [{"name": "Content-Disposition", "value": "attachment"}],
        "body": {"data": b64(b"unnamed"), "size": 7},
    }
    invite = {"mimeType": "text/calendar", "filename": "", "headers": [], "body": {"data": b64("BEGIN:VCALENDAR")}}
    nested = {
        "partId": "1",
        "mimeType": "multipart/mixed",
        "filename": "",
        "body": {},
        "headers": [],
        "parts": [remote, inline, unnamed, invite],
    }
    return message(headers={"Subject": "Murphy matter"}, parts=[part("text/plain", "See attached."), nested])["payload"]


def test_attachment_refs_name_every_downloadable_part_safely_and_in_order():
    refs = gm.attachment_refs(attachment_payload())
    assert [(r["ref"], r["name"], r["part_id"], r["attachment_id"], r["inline"], r["size"]) for r in refs] == [
        ("a1", "Murphy_Case_.pdf", "1.0", "gmail-pdf-id", False, len(PDF)),
        ("a2", "inline_.bin", "1.1", "", True, len(INLINE)),
        ("a3", "attachment.bin", "1.2", "", True, 7),
        ("a4", "invite.ics", "0.1.3", "", True, 0),
    ]
    assert gm.attachment_bytes(refs[1]["_data"], refs[1]["size"]) == INLINE
    assert refs[0]["_data"] is None
    listed = gm.normalize({"payload": attachment_payload()})["attachments"]
    assert [a["name"] for a in listed] == ["Murphy_Case_.pdf", "inline_.bin", "invite.ics"]


def test_safe_filename_never_keeps_a_path_or_dot_dot():
    assert gm.safe_filename("../../etc/passwd") == "passwd"
    assert gm.safe_filename("a..b...c.txt") == "a.b.c.txt"
    assert gm.safe_filename(" ../ ") == "attachment.bin"
    assert len(gm.safe_filename("x" * 300 + ".pdf")) == 180


def test_only_short_a_refs_are_valid():
    assert [gm.valid_attachment_ref(r) for r in ("a1", "a999999", "a0", "a01", "a1000000", "b1", "a1\n", "", None)] == [
        True,
        True,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
    ]


def test_attachment_bytes_are_bounded_before_and_after_decoding():
    assert gm.attachment_bytes(b64(PDF), len(PDF)) == PDF
    assert gm.attachment_bytes(b64(PDF), "not a number") == PDF
    for encoded, declared, limit in (
        (b64(PDF), 11, 10),  # declared over the limit: refused before decoding
        (b64(b"x" * 100), 0, 10),  # the encoded text alone is too long
        (b64(b"x" * 11), 0, 10),  # within the encoded bound, over the limit once decoded
        (None, 0, 10),
        ("not*base64", 0, 100),
        ("café", 0, 100),
    ):
        with pytest.raises(Failure):
            gm.attachment_bytes(encoded, declared, limit=limit)
    assert gm.attachment_bytes(b64(b"x" * 10), 0, limit=10) == b"x" * 10


# ---------------------------------------------------------------- the send gate's frozen copy (email_content.snapshot)


def mail(sender="legal@tidy.example", to=("records@police.example",), body="Please find attached.", files=()):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(to), "Records production"
    msg.set_content(body)
    for name, mime, data in files:
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")


def test_a_production_of_large_pdfs_and_a_long_text_index_is_frozen_whole():
    """Live on v0.6.65: a legal inbox's draft with an index and PDF exhibits was refused as 'over 256 KiB of visible
    text'. Files are never visible text; a long text file leaves the preview (it stays a file) and asks a person."""
    pdfs = [(f"Exhibit {n}.pdf", "application/pdf", b"%PDF-1.7\n" + bytes(range(256)) * 4000) for n in range(6)]
    index = ("Index.txt", "text/plain", ("Exhibit A - incident report, 12 pages\n" * 9000).encode())
    frozen = snapshot(mail(sender="Legal <Legal@Tidy.example>", files=[index, *pdfs]), "legal@tidy.example")
    assert [f["name"] for f in frozen["attachments"]] == ["Index.txt"] + [f"Exhibit {n}.pdf" for n in range(6)]
    assert [p["text"].strip() for p in frozen["visible_bodies"]] == ["Please find attached."]
    assert frozen["classification_complete"] is False
    small = snapshot(mail(files=[("Index.txt", "text/plain", b"Exhibit A")]), "legal@tidy.example")
    assert "Exhibit A" in [p["text"] for p in small["visible_bodies"]] and small["classification_complete"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sender": "ana@tidy.example"}, "From ana@tidy.example doesn't match the inbox legal@tidy.example"),
        ({"to": ()}, "it has no recipients in To, Cc or Bcc"),
        ({"to": [f"p{n}@police.example" for n in range(101)]}, "it has 101 recipients; the limit is 100"),
        ({"to": ["records@localhost"]}, "not a valid recipient address: records@localhost"),
        ({"files": [(f"{n}.pdf", "application/pdf", b"%PDF") for n in range(51)]}, "it has 51 files; the limit is 50"),
        ({"body": "x" * 310 * 1024}, "its visible text is 310 KiB; the limit is 256 KiB"),
    ],
)
def test_a_refused_email_names_the_check_it_failed_and_its_value(kwargs, message):
    with pytest.raises(Problem) as refused:
        snapshot(mail(**kwargs), "legal@tidy.example")
    assert refused.value.status == 422 and refused.value.detail == "Invalid email: " + message
