"""Every mail action an automation queues runs on the server, against Gmail, once: a retry finds its draft and its
queued send, never a second one. Bots draft, send, read attachments and unsubscribe through the same server routes at
their own mailbox level."""

import base64
import ipaddress
import json

import httpx
import pytest

from backend import automation_actions as A, automations as M, egress, email_sending as S, gmail_watch
from backend import mail_unsubscribe as U
from backend import mailbox_access as MA
from backend.store import H
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.automation_api import as_bot
from backend.tests.helpers.automation_mail_actions import BOX, automate, later, owned_by_ana
from backend.tests.helpers.email_sending import autonomous
from backend.tests.helpers.fake_gmail import fake_gmail  # noqa: F401

SENDER = "billing@vendor.example"
LIST = "https://lists.vendor.example/u/token-123"


@pytest.fixture
def box(api, fake_gmail, monkeypatch):
    monkeypatch.setattr(gmail_watch, "watched", lambda c, mailbox: True)  # the server reads its Gmail (fake_gmail)
    with api.app.state.store.transaction() as c:
        MA.ensure_mailbox(c, BOX, person_id="ana", created_by="human:ana")
    fake_gmail.add(
        BOX,
        "m1",
        thread="t1",
        sender=SENDER,
        subject="Invoice 4471",
        body="Please pay invoice 4471.",
        message_id="<inv-4471@vendor.example>",
    )
    return fake_gmail


@pytest.fixture(autouse=True)
def classified(monkeypatch):
    """The decision model reads each email as neither legal nor money (no model runs in tests)."""
    monkeypatch.setattr(
        S,
        "classify",
        lambda app, store, snap, review=None: {"legal_or_money": False, "review": None, "status": "classified"},
    )


def run_mail(api, *actions):
    step = {"id": "act", "type": "filter", "rules": [{"when": [], "then": list(actions)}]}
    aid = automate(api, step)
    run = post(api, f"automations/{aid}/run", {"input": {"message": {"mailbox": BOX, "id": "m1", "from": SENDER}}})
    return aid, run["run"]


def rows(api, aid):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute("SELECT * FROM automation_mail_actions WHERE automation_id=? ORDER BY rowid", (aid,))
        ]


def lose_the_lease(api, mid):
    """The worker that held this action stopped before it reported: the lease runs out and it is tried again."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE automation_mail_actions SET state='claimed',claimed_by=?,claimed_at=? WHERE id=?",
            (M.SERVER_MAIL, H.shift(H.now(), minutes=-M.MAIL_LEASE_MINUTES - 1), mid),
        )
    with later(M.MAIL_LEASE_MINUTES + 1):
        M.tick(api.app.state.store, drain=False)
        M.drain_workspace_mail(api.app.state.store)


def sends(api, aid):
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT s.* FROM email_sends s JOIN automation_mail_actions a ON a.id=s.automation_action_id "
            "WHERE a.automation_id=?",
            (aid,),
        ).fetchall()


def test_a_draft_reply_is_made_once_in_its_thread(api, box):
    aid, run = run_mail(api, {"do": "draft_reply", "args": {"body": "Thanks, we pay it today."}})
    assert run["status"] == "ok"
    (row,) = rows(api, aid)
    (draft_id, mime), *more = box.drafts(BOX).items()
    assert not more and json.loads(row["result"])["draft"] == draft_id
    assert f"Message-ID: <tico-action-{row['id']}@acme.example>".encode() in mime
    assert b"In-Reply-To: <inv-4471@vendor.example>" in mime and b"To: " + SENDER.encode() in mime
    assert b"Subject: Re: Invoice 4471" in mime
    assert box.box(BOX)["drafts"][draft_id]["message"]["threadId"] == "t1"
    lose_the_lease(api, row["id"])
    assert len(box.drafts(BOX)) == 1 and json.loads(rows(api, aid)[0]["result"])["draft"] == draft_id


def test_a_send_is_queued_with_the_send_gate_and_sent_once(api, box):
    autonomous(api)
    aid, run = run_mail(api, {"do": "send_reply", "args": {"body": "Thanks, we pay it today."}})
    (row,) = rows(api, aid)
    assert run["status"] == "waiting" and row["state"] == "waiting_send"
    (queued,) = sends(api, aid)
    assert queued["actor"] == "human:ana" and queued["reply_to_id"] == "m1" and queued["thread_id"] == "t1"
    lose_the_lease(api, row["id"])  # a second try finds the draft and the send its first one made
    assert len(sends(api, aid)) == 1 and len(box.drafts(BOX)) == 1
    store = api.app.state.store
    assert S.drain(store, api.app.state.auth) == 1 and S.drain(store, api.app.state.auth) == 0
    ((mailbox, mime, thread),) = box.sent
    assert (mailbox, thread) == (BOX, "t1") and b"Thanks, we pay it today." in mime
    assert rows(api, aid)[0]["state"] == "done"
    assert get(api, f"automations/{aid}/runs")["runs"][0]["status"] == "ok"


def test_a_forward_goes_to_its_target_with_the_note_first(api, box):
    autonomous(api)
    aid, _ = run_mail(api, {"do": "forward", "args": {"to": "books@vendor.example", "body": "For the books."}})
    S.drain(api.app.state.store, api.app.state.auth)
    ((_, mime, thread),) = box.sent
    assert thread == "" and b"To: books@vendor.example" in mime and b"Subject: Fwd: Invoice 4471" in mime
    text = mime.decode()
    assert text.index("For the books.") < text.index("Forwarded message") < text.index("Please pay invoice 4471.")
    assert rows(api, aid)[0]["state"] == "done"


def test_a_send_claimed_after_sending_went_off_is_only_drafted(api, box):
    autonomous(api)
    with api.app.state.store.transaction() as c:
        owned_by_ana(c, "auto_mail")
        mid = A._queue_mail(
            c,
            {"id": "auto_mail", "flow": {"owner": "person:ana"}},
            "run_mail",
            "send",
            {"mailbox": BOX, "id": "m1"},
            {"body": "Thanks, we pay it today."},
        )["mail_action"]
        c.execute("INSERT INTO automation_inboxes VALUES(?,0,?,'human:ana')", (BOX, H.now()))
    M.drain_workspace_mail(api.app.state.store)
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM automation_mail_actions WHERE id=?", (mid,)).fetchone()
        assert c.execute("SELECT count(*) FROM email_sends").fetchone()[0] == 0
    assert (row["state"], row["action"], json.loads(row["result"])["send_off"]) == ("done", "draft", True)
    assert len(box.drafts(BOX)) == 1 and box.sent == []


# ------------------------------------------------------------------------------------------------- unsubscribe
def one_click(box, url=LIST):
    box.box(BOX)["raws"]["m1"] = (
        f"From: Lists <{SENDER}>\r\nList-Unsubscribe: <{url}>\r\n"
        "List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\nDKIM-Signature: v=1; h=from:list-unsubscribe:"
        "list-unsubscribe-post\r\n\r\nNews."
    ).encode()


@pytest.fixture
def web(monkeypatch):
    """The sender's site: every request it gets, and the address its name resolves to."""
    seen = {"requests": [], "status": 200, "address": "93.184.216.34"}

    def answer(request):
        seen["requests"].append(request)
        return httpx.Response(seen["status"], headers={"Location": "https://elsewhere.example/"})

    monkeypatch.setattr(U, "verify_dkim", lambda raw: True)
    monkeypatch.setattr(U, "TRANSPORT", httpx.MockTransport(answer))
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address(seen["address"])])
    return seen


def test_an_unsubscribe_is_sent_once_from_the_server(api, box, web):
    one_click(box)
    aid, run = run_mail(api, {"do": "unsubscribe"})
    (row,) = rows(api, aid)
    assert run["status"] == "ok" and json.loads(row["result"])["detail"] == "unsubscribed"
    (request,) = web["requests"]
    assert request.method == "POST" and request.content == U.BODY and request.headers["host"] == "lists.vendor.example"
    lose_the_lease(api, row["id"])
    assert len(web["requests"]) == 1 and json.loads(rows(api, aid)[0]["result"])["detail"] == "already unsubscribed"
    with api.app.state.store.read() as c:
        (kept,) = c.execute("SELECT * FROM mail_unsubscribes").fetchall()
    assert kept["host"] == "lists.vendor.example" and "token-123" not in json.dumps(dict(kept))


@pytest.mark.parametrize("case", ["private", "http", "redirect", "starred"])
def test_an_unsubscribe_tico_may_not_send_is_refused(api, box, web, case):
    one_click(box, "http://lists.vendor.example/u/token-123" if case == "http" else LIST)
    if case == "private":
        web["address"] = "10.0.0.5"
    if case == "redirect":
        web["status"] = 302
    if case == "starred":
        box.box(BOX)["messages"]["m1"]["labelIds"].append("STARRED")
    answer = post(api, "mail/messages/m1/unsubscribe", {"mailbox": BOX})
    assert answer["ok"] is False
    expected = {
        "private": "not confirmed by the sender: refused",
        "http": "does not name exactly one https unsubscribe link",
        "redirect": "not confirmed by the sender: manual_required",
        "starred": "the message is starred",
    }[case]
    assert expected in answer["detail"]
    assert len(web["requests"]) == (1 if case == "redirect" else 0)  # a redirect is never followed


def signed(results, h="from:list-unsubscribe:list-unsubscribe-post", b="AbCdEf12345"):
    top = "".join(f"Authentication-Results: {r}\r\n" for r in results)
    return (
        top + f"DKIM-Signature: v=1; a=rsa-sha256; d=vendor.example; h={h};\r\n b={b[:6]}\r\n {b[6:]}\r\n"
        f"From: {SENDER}\r\nList-Unsubscribe: <{LIST}>\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n\r\nNews."
    ).encode()


GMAIL_PASS = "mx.google.com; dkim=pass header.i=@vendor.example header.s=s1 header.b=AbCdEf12; spf=pass"


@pytest.mark.parametrize(
    "raw,trusted",
    [
        (signed([GMAIL_PASS]), True),
        (signed([GMAIL_PASS], h="from:subject"), False),  # the signature leaves the unsubscribe headers unsigned
        (signed([GMAIL_PASS.replace("AbCdEf12", "Other999")]), False),  # Gmail passed some other signature
        (signed(["mx.google.com; dkim=fail header.b=AbCdEf12"]), False),
        (signed(["evil.example; dkim=pass header.b=AbCdEf12", GMAIL_PASS]), False),  # not Gmail's, on top
        (signed([]), False),
    ],
)
def test_the_unsubscribe_link_is_trusted_only_when_gmail_passed_a_signature_over_it(raw, trusted):
    assert U.verify_dkim(raw) is trusted


# ------------------------------------------------------------------------------------------------- bots and people
def grant(api, bot, level):
    with api.app.state.store.transaction() as c:
        MA.grant(c, "human:ana", MA._row(c, BOX)["id"], "bot", bot, level)


def test_a_bot_reads_and_drafts_only_at_its_own_level(api, box):
    token = as_bot(api, "ops")
    assert get(api, "mail/mailboxes", token)["mailboxes"] == []
    get(api, f"mail/messages?mailbox={BOX}", token, expected=403)
    body = {"mailbox": BOX, "reply_to": "t1", "body": "Thanks for the invoice.\n\nOps"}
    post(api, "mail/drafts", body, token, expected=403)
    grant(api, "ops", "read")
    assert [m["msg_id"] for m in get(api, f"mail/messages?mailbox={BOX}", token)["messages"]] == ["m1"]
    (mine,) = get(api, "mail/mailboxes", token)["mailboxes"]
    assert (mine["level"], mine["may"]["draft"]) == ("read", False)
    post(api, "mail/drafts", body, token, expected=403)
    post(api, "mail/messages/m1/unsubscribe", {"mailbox": BOX}, token, expected=403)
    grant(api, "ops", "read_write")
    first = post(api, "mail/drafts", body, token)
    again = post(api, "mail/drafts", body, token)
    assert first["created"] and not again["created"] and again["draft"] == first["draft"]
    assert first["to"] == [SENDER] and first["thread"] == "t1" and len(box.drafts(BOX)) == 1


def test_a_bots_draft_meets_the_lint_first(api, box):
    token = as_bot(api, "ops")
    grant(api, "ops", "read_write")
    refused = api.post(
        "/api/v2/mail/drafts",
        json={"mailbox": BOX, "to": [SENDER], "subject": "Hi", "body": "Paid."},
        headers=headers(token),
    )
    assert refused.status_code == 422 and "L052" in refused.json()["error"]["lint"]
    assert box.drafts(BOX) == {}


def test_a_draft_is_sent_as_gmail_holds_it_when_queued(api, box):
    autonomous(api)
    made = post(api, "mail/drafts", {"mailbox": BOX, "reply_to": "t1", "body": "Thanks, paid today."})
    queued = post(api, "email/sends", {"mailbox": BOX, "draft_id": made["draft"]})["send"]
    assert queued["state"] == "pending" and queued["subject"] == "Re: Invoice 4471"
    assert S.drain(api.app.state.store, api.app.state.auth) == 1
    ((_, mime, thread),) = box.sent
    assert thread == "t1" and mime == box.drafts(BOX)[made["draft"]]
    post(api, "email/sends", {"mailbox": BOX}, expected=422)  # neither a MIME nor a draft


def test_the_email_commands_run_against_the_server(api, box, monkeypatch, tmp_path, capsys):
    from backend.tests.helpers.automation_cli import Client
    from clients import hubcli, remotecli

    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.setenv("HUB_TOKEN", "ana-test")
    monkeypatch.setattr(remotecli, "Client", lambda url, token, **kwargs: Client(api, token))
    autonomous(api)
    box.box(BOX)["messages"]["m1"]["payload"]["parts"].append(
        {"filename": "invoice.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "att1", "size": 5}}
    )
    box.box(BOX)["attachments"][("m1", "att1")] = b"%PDF1"

    def tico(*argv):
        assert hubcli.main(["email", *argv]) == 0
        return json.loads(capsys.readouterr().out)

    (tmp_path / "reply.txt").write_text("Thanks, paid today.")
    made = tico("draft", BOX, "--reply-to", "t1", "--body-file", str(tmp_path / "reply.txt"))
    assert tico("send", BOX, "--draft", made["draft"])["send"]["state"] == "pending"
    other = tico("draft", BOX, "--to", "books@vendor.example", "--subject", "Books", "--body", "For the books.")
    assert tico("discard", BOX, other["draft"])["deleted"] is True and other["draft"] not in box.drafts(BOX)
    (listed,) = tico("attachments", BOX, "m1")["attachments"]
    assert (listed["ref"], listed["name"]) == ("a1", "invoice.pdf")
    saved = tico("attachment", BOX, "m1", "a1", "--out", str(tmp_path / "invoice.pdf"))
    assert (tmp_path / "invoice.pdf").read_bytes() == b"%PDF1" and "data" not in saved
    assert (tmp_path / "invoice.pdf").stat().st_mode & 0o777 == 0o600
    with api.app.state.store.read() as c:
        kinds = [json.loads(r[0])["kind"] for r in c.execute("SELECT detail FROM events WHERE action='mail.read'")]
    assert kinds == ["attachments", "attachment"]
    assert base64.b64decode(get(api, f"mail/messages/m1/attachments/a1?mailbox={BOX}")["data"]) == b"%PDF1"
