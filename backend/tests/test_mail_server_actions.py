"""Every mail action an automation queues runs on the server, against Gmail, once: a retry finds its draft and its
queued send, never a second one. Bots draft, send, read attachments and unsubscribe through the same server routes at
their own mailbox level."""

import base64
import ipaddress
import json
from pathlib import Path

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
from backend.tests.helpers.google_workspace_authz import workspace_key  # noqa: F401

REAL_CLASSIFY = S.classify  # before the autouse fixture below swaps it
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
    """The message's raw bytes in Gmail: the dkimpy-signed newsletter (its signature breaks if `url` differs)."""
    box.box(BOX)["raws"]["m1"] = vector("relaxed/relaxed").replace(LIST.encode(), url.encode())


@pytest.fixture
def web(monkeypatch, keys):
    """The sender's site: every request it gets, and the address its name resolves to."""
    seen = {"requests": [], "status": 200, "address": "93.184.216.34"}

    def answer(request):
        seen["requests"].append(request)
        return httpx.Response(seen["status"], headers={"Location": "https://elsewhere.example/"})

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


# Messages signed by dkimpy, with the public key: relaxed and simple RSA. From news@vendor.example; the signatures cover
# From and both unsubscribe headers.
VECTORS = json.loads((Path(__file__).parent / "fixtures/dkim_vectors.json").read_text())


def dns(records):
    """dkimpy's dnsfunc over these TXT records: {name: record}."""

    def txt(name, timeout=5):
        name = name.decode() if isinstance(name, bytes) else name
        found = records.get(name.rstrip("."))
        return found.encode() if found else None

    return txt


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(U, "DNSFUNC", dns({"s1._domainkey.vendor.example": f"v=DKIM1; k=rsa; p={VECTORS['pub']}"}))


def vector(name):
    return base64.b64decode(VECTORS[name])


@pytest.mark.parametrize("name", ["relaxed/relaxed", "simple/simple"])
def test_a_signature_over_the_unsubscribe_headers_verifies(keys, name):
    assert U.verify_dkim(vector(name)) is True


@pytest.mark.parametrize(
    "change",
    [
        (b"https://lists.vendor.example/u/token-123", b"https://victim.example/u/token-123"),  # the link swapped
        (b"More news.", b"More newz."),  # the body changed
        (b"From: Lists <news@vendor.example>", b"From: Bank <alerts@bank.example>"),  # another sender's From
    ],
)
def test_a_changed_message_does_not_verify(keys, change):
    assert U.verify_dkim(vector("relaxed/relaxed").replace(*change)) is False


def test_a_forged_signature_carrying_a_passing_b_tag_is_not_trusted(keys):
    """A real signature's b= copied into a signature that claims to sign the unsubscribe headers it never signed."""
    raw = vector("relaxed/relaxed")
    forged = raw.replace(b"h=from : subject :", b"h=from :")
    assert forged != raw and U.verify_dkim(forged) is False


def test_without_the_signers_key_nothing_verifies(monkeypatch):
    monkeypatch.setattr(U, "DNSFUNC", dns({}))
    assert U.verify_dkim(vector("relaxed/relaxed")) is False


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


# ------------------------------------------------------------------------------------------------- review findings
def test_the_servers_mcp_never_reads_a_file_into_a_draft(api, box, tmp_path):
    """A file path given to the server's MCP is refused: only the local CLI reads a local file."""
    from backend.tests.helpers.mcp import call

    secret = tmp_path / "server-secret.txt"
    secret.write_text("SERVER PRIVATE KEY")
    failed, answer = call(api, "tico_email_draft", {"mailbox": BOX, "to": [SENDER], "body_file": str(secret)})
    assert failed and all(b"SERVER PRIVATE KEY" not in mime for mime in box.drafts(BOX).values())


def test_the_servers_mcp_never_writes_an_attachment_to_a_path(api, box, tmp_path):
    from backend.tests.helpers.mcp import call

    box.box(BOX)["messages"]["m1"]["payload"]["parts"].append(
        {"filename": "invoice.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "att1", "size": 5}}
    )
    box.box(BOX)["attachments"][("m1", "att1")] = b"%PDF1"
    target = tmp_path / "written-by-the-server.pdf"
    failed, answer = call(api, "tico_email_attachment", {"mailbox": BOX, "id": "m1", "ref": "a1", "out": str(target)})
    assert not target.exists()
    failed, answer = call(api, "tico_email_attachment", {"mailbox": BOX, "id": "m1", "ref": "a1"})
    assert not failed and answer["name"] == "invoice.pdf" and "a1" in answer["download"]


def test_the_cli_saves_an_attachment_only_under_a_safe_folder(api, box, monkeypatch, tmp_path, capsys):
    from backend.tests.helpers.automation_cli import Client
    from clients import hubcli, remotecli

    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.setenv("HUB_TOKEN", "ana-test")
    monkeypatch.setattr(remotecli, "Client", lambda url, token, **kwargs: Client(api, token))
    box.box(BOX)["messages"]["m1"]["payload"]["parts"].append(
        {"filename": "invoice.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "att1", "size": 5}}
    )
    box.box(BOX)["attachments"][("m1", "att1")] = b"%PDF1"
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "no-temp"))
    outside = tmp_path / "elsewhere.pdf"
    assert hubcli.main(["email", "attachment", BOX, "m1", "a1", "--out", str(outside)]) != 0
    assert not outside.exists()
    capsys.readouterr()
    assert hubcli.main(["email", "attachment", BOX, "m1", "a1", "--out", "invoice.pdf"]) == 0
    assert (work / "invoice.pdf").read_bytes() == b"%PDF1"


def test_a_new_draft_is_sent_without_a_thread_it_does_not_answer(api, box):
    autonomous(api)
    made = post(api, "mail/drafts", {"mailbox": BOX, "to": ["books@vendor.example"], "subject": "Books", "body": "Hi."})
    queued = post(api, "email/sends", {"mailbox": BOX, "draft_id": made["draft"]})["send"]
    assert queued["state"] == "pending"


def test_two_requests_for_the_same_draft_at_once_make_one(api, box, monkeypatch):
    """The second asks while the first is between Gmail's "no such draft" and making it: it is told to try again."""
    from backend import gmail_live, mail_compose
    from backend.store import Problem

    store, mid = api.app.state.store, mail_compose.message_id("draft", "same", BOX)
    raw = mail_compose.build([SENDER], "Hi", "Hello there.", from_addr=BOX, message_id=mid)
    real, second = gmail_live.draft_find, []

    def find_while_another_asks(c, mailbox, message_id):
        found = real(c, mailbox, message_id)
        if not second:
            with pytest.raises(Problem) as busy:
                mail_compose.ensure_draft(store, BOX, raw, "", mid)
            second.append(busy.value)
        return found

    monkeypatch.setattr(gmail_live, "draft_find", find_while_another_asks)
    first = mail_compose.ensure_draft(store, BOX, raw, "", mid)
    assert first["created"] and second[0].status == 409 and second[0].retryable
    assert len(box.drafts(BOX)) == 1
    again = mail_compose.ensure_draft(store, BOX, raw, "", mid)
    assert again["id"] == first["id"] and not again["created"]


def test_the_drain_never_reruns_a_draft_an_older_computer_let_run_out(api, box):
    machine_id = "runner-old"
    with api.app.state.store.transaction() as c:
        owned_by_ana(c, "auto_mail")
        mid = A._queue_mail(
            c,
            {"id": "auto_mail", "flow": {"owner": "person:ana"}},
            "run_mail",
            "draft",
            {"mailbox": BOX, "id": "m1"},
            {"body": "Thanks."},
        )["mail_action"]
        c.execute(
            "UPDATE automation_mail_actions SET state='claimed',claimed_by=?,claimed_at=?,attempts=1 WHERE id=?",
            (machine_id, H.shift(H.now(), minutes=-M.MAIL_LEASE_MINUTES - 1), mid),
        )
    M.drain_workspace_mail(api.app.state.store)  # the drain alone, before any tick
    with api.app.state.store.read() as c:
        row = c.execute("SELECT state,result FROM automation_mail_actions WHERE id=?", (mid,)).fetchone()
    assert row["state"] == "failed" and "not run again" in row["result"] and box.drafts(BOX) == {}


def test_bots_are_offered_the_mail_read_tools():
    from clients import hubtools

    offered = {t["name"] for t in hubtools.listing(kind="bot")}
    assert {"tico_email_mailboxes", "tico_email_inbox", "tico_email_search", "tico_email_thread"} <= offered
    assert "tico_email_message" in offered


# ------------------------------------------------------------------------------------------------- files on a draft
def tico_file(api, token, name, data):
    """A file the bot publishes (POST /api/v2/files/uploads): its id."""
    body = {"name": name, "content_base64": base64.b64encode(data).decode(), "scope": "bot"}
    return post(api, "files/uploads", body, token)["file"]["id"]


def files_of(mime):
    from email import policy
    from email.parser import BytesParser

    msg = BytesParser(policy=policy.default).parsebytes(mime)
    return msg.get_content_type(), [(p.get_filename(), p.get_content()) for p in msg.iter_attachments()]


REPLY = {"mailbox": BOX, "reply_to": "t1", "body": "Thanks for the invoice.\n\nOps"}


def test_a_draft_carries_tico_files_and_is_found_again_with_them(api, box):
    from backend import mail_compose

    token = as_bot(api, "ops")
    grant(api, "ops", "read_write")
    pdf, csv = tico_file(api, token, "records.pdf", b"%PDF-1.4 records"), tico_file(api, token, "log.csv", b"a,b\n")
    body = {**REPLY, "attachments": [pdf, csv]}
    first = post(api, "mail/drafts", body, token)
    again = post(api, "mail/drafts", body, token)
    assert first["created"] and not again["created"] and again["draft"] == first["draft"] and len(box.drafts(BOX)) == 1
    kind, files = files_of(box.drafts(BOX)[first["draft"]])
    assert kind == "multipart/mixed" and files == [("records.pdf", b"%PDF-1.4 records"), ("log.csv", "a,b\n")]
    assert [(f["ref"], f["name"]) for f in first["attachments"]] == [
        (pdf + "@1", "records.pdf"),
        (csv + "@1", "log.csv"),
    ]
    # Other files are another draft; no files is the draft (and the Message-ID) a draft without them always had.
    assert post(api, "mail/drafts", {**body, "attachments": [pdf]}, token)["draft"] != first["draft"]
    plain = post(api, "mail/drafts", REPLY, token)
    # A reply's key also names the message it answers and whom it goes to.
    key = mail_compose.key("bot:ops", BOX, "", "t1", [], [], "", REPLY["body"], "answers:m1", [SENDER], [])
    assert mail_compose.message_id("draft", key, BOX).encode() in box.drafts(BOX)[plain["draft"]]


def test_a_file_the_caller_may_not_open_is_refused_and_nothing_is_drafted(api, box):
    token = as_bot(api, "ops")
    grant(api, "ops", "read_write")
    hidden = tico_file(api, as_bot(api, "legal"), "private.pdf", b"%PDF-1.4 private")
    for ref, status in ((hidden, 404), ("file-" + "0" * 24, 404), (hidden + "@x", 422)):
        refused = api.post("/api/v2/mail/drafts", json={**REPLY, "attachments": [ref]}, headers=headers(token))
        assert refused.status_code == status, refused.text
    assert box.drafts(BOX) == {}


def test_files_over_the_email_limit_are_refused(api, box, monkeypatch):
    from backend import mail_compose

    token = as_bot(api, "ops")
    grant(api, "ops", "read_write")
    pdf = tico_file(api, token, "records.pdf", b"%PDF-1.4 " + b"x" * 3000)
    monkeypatch.setattr(mail_compose, "RAW_MAX", 2048)
    refused = api.post("/api/v2/mail/drafts", json={**REPLY, "attachments": [pdf]}, headers=headers(token))
    assert refused.status_code == 413 and refused.json()["error"]["code"] == "attachments"
    assert box.drafts(BOX) == {}


def test_a_large_draft_and_its_send_go_up_as_uploads(api, box, monkeypatch):
    from backend import gmail_live

    autonomous(api)
    monkeypatch.setattr(gmail_live, "INLINE_RAW_MAX", 64)  # every draft here is "large"
    pdf = tico_file(api, as_bot(api, "ops"), "records.pdf", b"%PDF-1.4 records")
    made = post(
        api, "mail/drafts", {"mailbox": BOX, "reply_to": "t1", "body": "Records attached.", "attachments": [pdf]}
    )
    assert files_of(box.drafts(BOX)[made["draft"]])[1] == [("records.pdf", b"%PDF-1.4 records")]
    # A change to it goes up as an upload too, onto the same draft.
    changed = api.patch(
        f"/api/v2/mail/drafts/{made['draft']}", json={"mailbox": BOX, "body": "Records enclosed."}, headers=headers()
    )
    assert changed.status_code == 200 and changed.json()["draft"] == made["draft"] and len(box.drafts(BOX)) == 1
    assert b"Records enclosed." in box.drafts(BOX)[made["draft"]]
    queued = post(api, "email/sends", {"mailbox": BOX, "draft_id": made["draft"]})["send"]
    assert [f["name"] for f in queued["attachments"]] == ["records.pdf"]
    assert S.drain(api.app.state.store, api.app.state.auth) == 1
    ((_, mime, thread),) = box.sent  # read back from Gmail and sent as an upload too
    assert thread == "t1" and mime == box.drafts(BOX)[made["draft"]]


def test_the_cli_attaches_a_path_and_a_file_id(api, box, monkeypatch, tmp_path, capsys):
    from backend.tests.helpers.automation_cli import Client
    from clients import hubcli, remotecli

    from backend.tests.helpers.files import turn

    _, attempt = turn(api, bot="ops")  # a file the CLI publishes goes on the task the bot is working on
    token = attempt["token"]
    grant(api, "ops", "read_write")
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.setenv("HUB_TOKEN", token)
    monkeypatch.setattr(remotecli, "Client", lambda url, token, **kwargs: Client(api, token))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "packet.pdf").write_bytes(b"%PDF-1.4 packet")
    known = tico_file(api, as_bot(api, "ops"), "log.csv", b"a,b\n")
    argv = ["email", "draft", BOX, "--reply-to", "t1", "--body", REPLY["body"], "--attach", "packet.pdf"]
    assert hubcli.main([*argv, "--attach", known]) == 0
    made = json.loads(capsys.readouterr().out)
    assert [f["name"] for f in made["attachments"]] == ["packet.pdf", "log.csv"]
    assert files_of(box.drafts(BOX)[made["draft"]])[1] == [("packet.pdf", b"%PDF-1.4 packet"), ("log.csv", "a,b\n")]


def test_a_bots_stopped_draft_says_why_to_the_bot_and_keeps_it(api, box, monkeypatch, tmp_path, capsys):
    """The second reviewer's stop reaches the bot in words, over the route and the CLI, and the mail.draft event keeps
    the verdict and the reason. An email that says files are attached and has none is stopped for exactly that."""
    from backend.tests.helpers.automation_cli import Client
    from backend.tests.helpers.files import turn
    from backend.tests.helpers.judge import fake
    from clients import hubcli, remotecli

    monkeypatch.setattr(S, "classify", REAL_CLASSIFY)
    _, attempt = turn(api, bot="ops")
    token = attempt["token"]
    grant(api, "ops", "read_write")
    fake(api, legal_or_money="no", review="stop", review_reason="commitment")
    made = post(api, "mail/drafts", {**REPLY, "body": "We will pay the invoice by Friday.\n\nOps"}, token)
    assert (made["review"], made["review_reason"]) == ("stop", S.STOPPED["commitment"])
    with api.app.state.store.read() as c:
        (kept,) = [
            json.loads(r[0])
            for r in c.execute(
                "SELECT detail FROM events WHERE action='mail.draft' AND json_extract(detail,'$.draft')=?",
                (made["draft"],),
            )
        ]
    assert (kept["review"], kept["review_reason"]) == ("stop", S.STOPPED["commitment"])
    fake(api, legal_or_money="no", review="send", review_reason="none")
    claim = {**REPLY, "body": "The records you asked for are attached.\n\nOps"}
    unfiled = post(api, "mail/drafts", claim, token)
    assert (unfiled["review"], unfiled["review_reason"]) == ("stop", S.STOPPED["missing_file"])
    filed = post(
        api,
        "mail/drafts",
        {**claim, "attachments": [tico_file(api, as_bot(api, "ops"), "records.pdf", b"%PDF-1.4")]},
        token,
    )
    assert (filed["review"], filed["review_reason"]) == ("send", "")
    # `tico email draft` prints the route's answer: the reason is in what the bot reads.
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.setenv("HUB_TOKEN", token)
    monkeypatch.setattr(remotecli, "Client", lambda url, token, **kwargs: Client(api, token))
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()
    assert hubcli.main(["email", "draft", BOX, "--reply-to", "t1", "--body", claim["body"]]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert (printed["review"], printed["review_reason"]) == ("stop", S.STOPPED["missing_file"])


# ------------------------------------------------------------------------------------------------- drafts by any id
OLD_TOOL_DRAFT = (
    b"From: ana@acme.example\r\nTo: billing@vendor.example\r\nSubject: Re: Invoice 4471\r\n"
    b"In-Reply-To: <inv-4471@vendor.example>\r\nMessage-ID: <old-tool-1@acme.example>\r\n"
    b"Content-Type: text/plain\r\n\r\nWe paid on Monday.\r\n"
)


def test_a_draft_is_discarded_by_its_message_id_and_a_missing_one_is_not_found(api, box):
    """A draft another tool made is discarded by the message id a search shows; a name that is no draft is a clear
    not_found, never deleted false."""
    box.put_draft(BOX, "r-900", "dm-old", "t1", OLD_TOOL_DRAFT)
    gone = api.delete(f"/api/v2/mail/drafts/dm-old?mailbox={BOX}", headers=headers())
    assert gone.status_code == 200 and gone.json() == {
        "mailbox": BOX,
        "draft": "r-900",
        "message": "dm-old",
        "deleted": True,
    }
    assert "r-900" not in box.box(BOX)["drafts"]
    again = api.delete(f"/api/v2/mail/drafts/dm-old?mailbox={BOX}", headers=headers())
    assert again.status_code == 404 and "tico email drafts" in again.json()["error"]["detail"]
    made = post(api, "mail/drafts", {"mailbox": BOX, "reply_to": "t1", "body": "Thanks, paid today."})
    assert api.delete(f"/api/v2/mail/drafts/{made['draft']}?mailbox={BOX}", headers=headers()).json()["deleted"]
    with api.app.state.store.read() as c:
        logged = [json.loads(r[0]) for r in c.execute("SELECT detail FROM events WHERE action='mail.draft_discarded'")]
    assert [e["draft"] for e in logged] == ["r-900", made["draft"]]


def test_drafts_are_listed_read_and_named_in_threads_and_searches(api, box):
    made = post(api, "mail/drafts", {"mailbox": BOX, "to": ["books@vendor.example"], "subject": "Books", "body": "Hi."})
    box.put_draft(BOX, "r-900", "dm-old", "t1", OLD_TOOL_DRAFT)
    listed = get(api, f"mail/drafts?mailbox={BOX}")["drafts"]
    assert [(d["draft_id"], d["msg_id"], d["subject"]) for d in listed] == [
        ("r-900", "dm-old", "Re: Invoice 4471"),
        (made["draft"], made["message"], "Books"),
    ]
    assert listed[0]["to"] == [SENDER] and listed[0]["thread_id"] == "t1" and listed[0]["updated"]
    assert [d["draft_id"] for d in get(api, f"mail/drafts?mailbox={BOX}&thread=t1")["drafts"]] == ["r-900"]
    assert [d["draft_id"] for d in get(api, f"mail/drafts?mailbox={BOX}&q=to:books@vendor.example")["drafts"]] == [
        made["draft"]
    ]
    # A thread and a search show which message is a draft, and its draft id.
    thread = get(api, f"mail/threads/t1?mailbox={BOX}")["messages"]
    assert [(m["msg_id"], m.get("draft_id")) for m in thread] == [("m1", None), ("dm-old", "r-900")]
    found = get(api, f"mail/messages?mailbox={BOX}&q=Invoice")["messages"]
    assert {m["msg_id"]: m.get("draft_id") for m in found} == {"m1": None, "dm-old": "r-900"}
    # Read by its draft id, by its message id, or through `message` with a draft id.
    for path in ("mail/drafts/r-900", "mail/drafts/dm-old", "mail/messages/r-900"):
        read = get(api, f"{path}?mailbox={BOX}")
        assert (read["draft_id"], read["msg_id"], read["body"].strip()) == ("r-900", "dm-old", "We paid on Monday.")
    missing = api.get(f"/api/v2/mail/messages/r-404?mailbox={BOX}", headers=headers())
    assert missing.status_code == 404 and "tico email drafts" in missing.json()["error"]["detail"]


def test_a_draft_is_changed_in_place_and_stays_a_reply_with_its_files(api, box):
    token = as_bot(api, "ops")
    grant(api, "ops", "read_write")
    pdf = tico_file(api, token, "records.pdf", b"%PDF-1.4 records")
    made = post(api, "mail/drafts", {**REPLY, "attachments": [pdf]}, token)
    patch = {"mailbox": BOX, "body": "Thanks for the invoice; it is paid.\n\nOps"}
    changed = api.patch(f"/api/v2/mail/drafts/{made['draft']}", json=patch, headers=headers(token))
    assert changed.status_code == 200, changed.text
    updated = changed.json()
    assert updated["draft"] == made["draft"] and updated["message"] != made["message"] and updated["updated"]
    assert (updated["to"], updated["thread"], updated["subject"]) == ([SENDER], "t1", "Re: Invoice 4471")
    mime = box.drafts(BOX)[made["draft"]]
    assert b"it is paid." in mime and b"In-Reply-To: <inv-4471@vendor.example>" in mime
    assert files_of(mime)[1] == [("records.pdf", b"%PDF-1.4 records")]  # left out: its files are kept
    assert box.box(BOX)["drafts"][made["draft"]]["message"]["threadId"] == "t1"
    # By its new message id, files off and a new subject; the lint still refuses what it would refuse in a new draft.
    off = {"mailbox": BOX, "attachments": [], "cc": ["ap@vendor.example"]}
    api.patch(f"/api/v2/mail/drafts/{updated['message']}", json=off, headers=headers(token)).raise_for_status()
    assert (
        files_of(box.drafts(BOX)[made["draft"]])[1] == [] and b"Cc: ap@vendor.example" in box.drafts(BOX)[made["draft"]]
    )
    before = box.drafts(BOX)[made["draft"]]
    refused = api.patch(
        f"/api/v2/mail/drafts/{made['draft']}", json={"mailbox": BOX, "body": "Paid."}, headers=headers(token)
    )
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "lint"
    assert box.drafts(BOX)[made["draft"]] == before and len(box.drafts(BOX)) == 1
    # The draft's old words asked for again are a new draft, not this changed one.
    assert post(api, "mail/drafts", {**REPLY, "attachments": [pdf]}, token)["draft"] != made["draft"]


def test_the_assistants_draft_change_meets_the_lint_and_the_reviewer_and_a_discard_asks(api, box, monkeypatch):
    """An Assistant turn arrives as its person but writes as the Assistant: its change to the person's draft meets the
    lint and the second reviewer, as a bot's does. A discard (Gmail deletes it for good) is a proposal."""
    from backend import assistant
    from backend.tests.helpers.assistant import assistant_turn

    _, attempt = assistant_turn(api, "ana-test", "Tidy my reply to the invoice.")
    made = post(api, "mail/drafts", {"mailbox": BOX, "reply_to": "t1", "body": "Thanks, paid today."})
    path = f"/api/v2/mail/drafts/{made['draft']}"
    refused = api.patch(path, json={"mailbox": BOX, "body": "Paid."}, headers=headers(attempt["token"]))
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "lint"
    assert b"Thanks, paid today." in box.drafts(BOX)[made["draft"]]
    reviewed = []

    def stop(app, store, snap, review=None):
        reviewed.append(review)
        return {"review": False, "review_reason": S.STOPPED["commitment"], "status": "classified"}

    monkeypatch.setattr(S, "classify", stop)
    words = {"mailbox": BOX, "body": "Thanks for the invoice; we will pay it by Friday.\n\nAna"}
    changed = api.patch(path, json=words, headers=headers(attempt["token"]))
    assert changed.status_code == 200, changed.text
    assert (changed.json()["review"], changed.json()["review_reason"]) == ("stop", S.STOPPED["commitment"])
    assert reviewed and reviewed[0] is not None  # the reviewer read it with the Assistant's context
    assert b"pay it by Friday" in box.drafts(BOX)[made["draft"]]
    assert not assistant.write_allowed("DELETE", path)


def test_a_send_names_its_draft_by_message_id_and_a_gone_draft_says_so(api, box):
    autonomous(api)
    made = post(api, "mail/drafts", {"mailbox": BOX, "reply_to": "t1", "body": "Thanks, paid today."})
    queued = post(api, "email/sends", {"mailbox": BOX, "draft_id": made["message"]})["send"]
    assert (queued["state"], queued["draft_id"]) == ("pending", made["draft"])
    canceled = post(api, f"email/sends/{queued['id']}/cancel", {})
    assert canceled["send"]["state"] == "canceled" and canceled["draft"]["id"] == made["draft"]
    assert canceled["draft"]["kept"] and made["draft"] in box.drafts(BOX)  # the cancel never touched Gmail
    api.delete(f"/api/v2/mail/drafts/{made['draft']}?mailbox={BOX}", headers=headers()).raise_for_status()
    gone = api.post("/api/v2/email/sends", json={"mailbox": BOX, "draft_id": made["draft"]}, headers=headers())
    assert gone.status_code == 404 and "no longer exists" in gone.json()["error"]["detail"]


def test_a_queued_send_prints_its_id_task_and_approval_and_a_retry_asks_no_reviewer(api, box, monkeypatch, capsys):
    from backend.tests.helpers.automation_cli import Client
    from backend.tests.helpers.files import turn
    from clients import hubcli, remotecli

    _, attempt = turn(api, bot="ops")
    grant(api, "ops", "read_write")
    made = post(api, "mail/drafts", REPLY, attempt["token"])
    task = post(api, "tasks", {"owner": "ops", "title": "Pay the invoice", "body": "Pay it."})["id"]
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.setenv("HUB_TOKEN", attempt["token"])
    monkeypatch.setattr(remotecli, "Client", lambda url, token, **kwargs: Client(api, token))
    capsys.readouterr()
    argv = ["email", "send", BOX, "--draft", made["draft"], "--task", task, "--approval-id", "msg-1"]
    assert hubcli.main(argv) == 0
    send = json.loads(capsys.readouterr().out)["send"]
    assert send["id"] and send["state"] == "waiting_approval" and send["task_id"] == task
    assert send["approval_id"] and send["approval_task_id"] and "policy" not in send["decision"]
    asked = []
    monkeypatch.setattr(S, "classify", lambda *a, **k: asked.append(1) or {"status": "classified", "review": None})
    key, body = "send-once", {"mailbox": BOX, "draft_id": made["draft"]}
    first = post(api, "email/sends", body, attempt["token"], key=key)["send"]
    assert post(api, "email/sends", body, attempt["token"], key=key)["send"]["id"] == first["id"] and asked == [1]


GMAIL_UI_DRAFT = (
    b"From: ana@acme.example\r\nTo: billing@vendor.example\r\nBcc: books@acme.example\r\nSubject: Statement\r\n"
    b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=outer\r\n\r\n"
    b"--outer\r\nContent-Type: multipart/alternative; boundary=inner\r\n\r\n"
    b"--inner\r\nContent-Type: text/plain\r\n\r\nHere is the statement.\r\n"
    b"--inner\r\nContent-Type: text/html\r\n\r\n<p>Here is the <b>statement</b>.</p>\r\n--inner--\r\n"
    b"--outer\r\nContent-Type: message/rfc822\r\nContent-Disposition: attachment; filename=original.eml\r\n\r\n"
    b"From: bank@bank.example\r\nSubject: Your statement\r\n\r\nBalance attached.\r\n--outer--\r\n"
)


def test_a_draft_change_keeps_everything_it_does_not_name_and_refuses_what_it_cannot_apply(api, box):
    """A subject change to a draft made in Gmail keeps its Bcc, its HTML version and its forwarded .eml byte for byte;
    new words for it are refused rather than dropping the HTML."""
    from email import policy
    from email.parser import BytesParser

    box.put_draft(BOX, "r-900", "dm-ui", "t-ui", GMAIL_UI_DRAFT)
    path = "/api/v2/mail/drafts/r-900"
    changed = api.patch(path, json={"mailbox": BOX, "subject": "Statement for October"}, headers=headers())
    assert changed.status_code == 200, changed.text
    msg = BytesParser(policy=policy.default).parsebytes(box.drafts(BOX)["r-900"])
    assert msg["Subject"] == "Statement for October" and msg["Bcc"] == "books@acme.example"
    assert msg.get_body(preferencelist=("html",)).get_content().strip() == "<p>Here is the <b>statement</b>.</p>"
    (eml,) = msg.iter_attachments()
    assert eml.get_filename() == "original.eml" and b"Balance attached." in eml.as_bytes()
    before = box.drafts(BOX)["r-900"]
    refused = api.patch(path, json={"mailbox": BOX, "body": "New words."}, headers=headers())
    assert refused.status_code == 422 and "HTML version" in refused.json()["error"]["detail"]
    assert box.drafts(BOX)["r-900"] == before


def test_a_draft_changed_since_it_was_read_is_never_overwritten_discarded_or_sent(api, box):
    autonomous(api)
    made = post(api, "mail/drafts", {"mailbox": BOX, "reply_to": "t1", "body": "Thanks, paid today."})
    path = f"/api/v2/mail/drafts/{made['draft']}"
    first = api.patch(
        path, json={"mailbox": BOX, "body": "Thanks, paid this morning.", "message": made["message"]}, headers=headers()
    )
    assert first.status_code == 200, first.text
    # A second editor still holding the first version is refused: the first edit stays.
    late = api.patch(path, json={"mailbox": BOX, "subject": "Late", "message": made["message"]}, headers=headers())
    assert late.status_code == 409 and late.json()["error"]["code"] == "draft_changed"
    by_old_id = api.patch(
        f"/api/v2/mail/drafts/{made['message']}", json={"mailbox": BOX, "subject": "Late"}, headers=headers()
    )
    assert by_old_id.status_code == 404  # the old message id names no draft any more
    gone = api.delete(f"{path}?mailbox={BOX}&message={made['message']}", headers=headers())
    assert gone.status_code == 409 and made["draft"] in box.drafts(BOX)
    sent = api.post(
        "/api/v2/email/sends",
        json={"mailbox": BOX, "draft_id": made["draft"], "draft_message": made["message"]},
        headers=headers(),
    )
    assert sent.status_code == 409 and b"paid this morning" in box.drafts(BOX)[made["draft"]]
    # The same change asked again under its key answers the first, writing nothing again.
    key = "edit-once"
    body = {"mailbox": BOX, "subject": "Paid", "message": first.json()["message"]}
    once = api.patch(path, json=body, headers=headers(key=key)).json()
    edits = box.box(BOX)["edits"]
    again = api.patch(path, json=body, headers=headers(key=key))
    assert again.status_code == 200 and again.json() == once and box.box(BOX)["edits"] == edits


def test_a_lost_send_answer_replays_after_the_draft_is_changed_or_gone(api, box):
    autonomous(api)
    made = post(api, "mail/drafts", {"mailbox": BOX, "reply_to": "t1", "body": "Thanks, paid today."})
    body = {"mailbox": BOX, "draft_id": made["draft"]}
    first = post(api, "email/sends", body, key="send-1")["send"]
    api.patch(f"/api/v2/mail/drafts/{made['draft']}", json={"mailbox": BOX, "body": "Changed."}, headers=headers())
    assert post(api, "email/sends", body, key="send-1")["send"]["id"] == first["id"]
    api.delete(f"/api/v2/mail/drafts/{made['draft']}?mailbox={BOX}", headers=headers()).raise_for_status()
    box.down.add(BOX)  # and Gmail is down: the replay never reads it
    assert post(api, "email/sends", body, key="send-1")["send"]["id"] == first["id"]
    box.down.discard(BOX)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM email_sends").fetchone()[0] == 1


def test_the_same_words_after_a_newer_message_are_a_new_reply_to_its_sender(api, box):
    first = post(api, "mail/drafts", {"mailbox": BOX, "reply_to": "t1", "body": "Thanks, noted."})
    box.add(
        BOX,
        "m9",
        thread="t1",
        sender="ap@vendor.example",
        subject="Re: Invoice 4471",
        body="Paid?",
        epoch=1_783_368_900,
    )
    second = post(api, "mail/drafts", {"mailbox": BOX, "reply_to": "t1", "body": "Thanks, noted."})
    assert second["draft"] != first["draft"] and second["to"] == ["ap@vendor.example"]
    assert b"To: ap@vendor.example" in box.drafts(BOX)[second["draft"]]


def test_a_draft_past_many_pages_is_found_by_its_message_and_a_thread_list_pages(api, box, monkeypatch):
    from backend import gmail_live

    monkeypatch.setattr(gmail_live, "DRAFT_PAGE", 1)  # one draft a page: the oldest is fourteen pages in
    for n in range(14):
        box.put_draft(BOX, f"r-{800 + n}", f"dm-{n}", "t1" if n < 2 else f"t-{n}", OLD_TOOL_DRAFT)
    assert get(api, f"mail/drafts/dm-0?mailbox={BOX}")["draft_id"] == "r-800"
    page = get(api, f"mail/drafts?mailbox={BOX}&thread=t1&limit=1")
    nxt = get(api, f"mail/drafts?mailbox={BOX}&thread=t1&limit=1&cursor={page['next_cursor']}")
    assert [page["drafts"][0]["draft_id"], nxt["drafts"][0]["draft_id"]] == ["r-801", "r-800"]
    assert nxt["next_cursor"] is None


# ------------------------------------------------------------------------------------------------- organize, labels
@pytest.fixture
def organized(box, workspace_key):
    box.add(BOX, "m2", thread="t1", sender=SENDER, subject="Re: Invoice 4471", body="Reminder.")
    box.add(BOX, "m3", thread="t3", sender="news@list.example", subject="Deals", body="Big deals.")
    return box


def organize(api, token="ana-test", expected=200, **body):
    answer = api.post("/api/v2/mail/messages/organize", json={"mailbox": BOX, **body}, headers=headers(token))
    assert answer.status_code == expected, answer.text
    return answer.json()


def labels_of(box, mid):
    return set(box.box(BOX)["messages"][mid]["labelIds"])


@pytest.mark.parametrize(
    ("action", "label", "undo"),
    [
        ("star", "STARRED", "unstar"),
        ("important", "IMPORTANT", "not_important"),
        ("trash", "TRASH", "untrash"),
        ("spam", "SPAM", "not_spam"),
    ],
)
def test_a_whole_thread_is_organized_and_undone_and_nothing_is_deleted(api, organized, action, label, undo):
    done = organize(api, action=action, threads=["t1"])
    assert done["ids"] == ["m1", "m2"] and done["undo"] == {"action": undo, "ids": ["m1", "m2"]}
    assert all(label in labels_of(organized, mid) for mid in ("m1", "m2")) and label not in labels_of(organized, "m3")
    if action in ("trash", "spam"):  # out of the inbox, found again where Gmail keeps it
        assert [m["msg_id"] for m in get(api, f"mail/messages?mailbox={BOX}&q=in:{action}")["messages"]] == [
            "m2",
            "m1",
        ]
    organize(api, **done["undo"])
    assert not any(label in labels_of(organized, mid) for mid in ("m1", "m2"))
    assert set(organized.box(BOX)["messages"]) == {"m1", "m2", "m3"}  # nothing deleted
    assert all(not call[0].endswith("/delete") for call in organized.calls)


def test_trash_needs_organize_access_and_a_reader_is_refused(api, organized):
    token = as_bot(api, "ops")
    grant(api, "ops", "read")
    assert "may not organize" in organize(api, token, 403, action="trash", ids=["m3"])["error"]["detail"]
    assert "TRASH" not in labels_of(organized, "m3")
    organize(api, action="trash", ids=["m3"], threads=["t3"], expected=422)  # one selection only


def test_labels_are_listed_with_counts_made_renamed_and_deleted_and_system_labels_are_refused(api, organized):
    organize(api, action="label", label="Clients/Vendor", threads=["t1"])
    listed = {row["name"]: row for row in get(api, f"mail/labels?mailbox={BOX}")["labels"]}
    assert (listed["Clients/Vendor"]["messages_total"], listed["Clients/Vendor"]["threads_total"]) == (2, 1)
    assert listed["INBOX"]["messages_unread"] == 3 and listed["INBOX"]["type"] == "system"
    made = post(api, "mail/labels", {"mailbox": BOX, "name": "Receipts"})
    assert made["created"] and not post(api, "mail/labels", {"mailbox": BOX, "name": "Receipts"})["created"]
    renamed = api.patch(
        "/api/v2/mail/labels/Clients%2FVendor", json={"mailbox": BOX, "name": "Clients/Billing"}, headers=headers()
    )
    assert renamed.status_code == 200 and renamed.json()["previous_name"] == "Clients/Vendor"
    gone = api.delete(f"/api/v2/mail/labels/Clients%2FBilling?mailbox={BOX}", headers=headers())
    assert gone.status_code == 200 and gone.json()["deleted"]
    assert set(organized.box(BOX)["messages"]) == {"m1", "m2", "m3"}  # its messages are kept
    for path in ("INBOX", "TRASH"):
        refused = api.delete(f"/api/v2/mail/labels/{path}?mailbox={BOX}", headers=headers())
        assert refused.status_code == 422, refused.text
    assert {row["id"] for row in organized.box(BOX)["labels"]} >= {"INBOX", "TRASH"}
    with api.app.state.store.read() as c:
        actions = [r[0] for r in c.execute("SELECT action FROM events WHERE action LIKE 'mail.label_%' ORDER BY rowid")]
    assert actions == ["mail.label_created", "mail.label_renamed", "mail.label_deleted"]


def test_the_original_message_has_every_header_and_saves_as_an_eml(api, box, monkeypatch, tmp_path, capsys):
    from backend.tests.helpers.automation_cli import Client
    from clients import hubcli, remotecli

    mime = b"Received: from mx.vendor.example\r\nDKIM-Signature: v=1; d=vendor.example\r\nFrom: " + SENDER.encode()
    box.box(BOX)["raws"]["m1"] = mime + b"\r\nSubject: Invoice 4471\r\n\r\nPlease pay invoice 4471."
    read = get(api, f"mail/messages/m1/original?mailbox={BOX}")
    assert [h["name"] for h in read["headers"]] == ["Received", "DKIM-Signature", "From", "Subject"]
    assert "raw" not in read and read["size"] == len(box.box(BOX)["raws"]["m1"])
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.setenv("HUB_TOKEN", "ana-test")
    monkeypatch.setattr(remotecli, "Client", lambda url, token, **kwargs: Client(api, token))
    monkeypatch.chdir(tmp_path)
    assert hubcli.main(["email", "original", BOX, "m1", "--out", "invoice.eml"]) == 0
    assert (tmp_path / "invoice.eml").read_bytes() == box.box(BOX)["raws"]["m1"]
    assert json.loads(capsys.readouterr().out)["path"].endswith("invoice.eml")
    with api.app.state.store.read() as c:
        kinds = [json.loads(r[0])["kind"] for r in c.execute("SELECT detail FROM events WHERE action='mail.read'")]
    assert kinds == ["original", "original"]


def test_whole_threads_are_organized_past_500_messages_and_their_drafts_are_left_alone(api, organized):
    for n in range(500):
        organized.add(BOX, f"b{n}", thread="t-big", subject="Bulk", body="Bulk.")
    organized.put_draft(BOX, "r-900", "dm-old", "t1", OLD_TOOL_DRAFT)
    done = organize(api, action="trash", threads=["t-big", "t1"])
    assert done["count"] == 502 and "dm-old" not in done["ids"]  # every message, the unsent reply left out
    assert all("TRASH" in labels_of(organized, mid) for mid in done["ids"])
    assert "TRASH" not in labels_of(organized, "dm-old")


# ------------------------------------------------------------------------------------------------- reply-all, forward
def test_a_forward_carries_the_messages_files_and_waits_for_the_send_gate(api, box, monkeypatch, capsys):
    from backend.tests.helpers.automation_cli import Client
    from clients import hubcli, remotecli

    box.box(BOX)["messages"]["m1"]["payload"]["parts"].append(
        {"filename": "invoice.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "att1", "size": 5}}
    )
    box.box(BOX)["attachments"][("m1", "att1")] = b"%PDF1"
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.setenv("HUB_TOKEN", "ana-test")
    monkeypatch.setattr(remotecli, "Client", lambda url, token, **kwargs: Client(api, token))
    capsys.readouterr()
    argv = ["email", "forward", BOX, "m1", "--to", "books@vendor.example", "--body", "For the books."]
    assert hubcli.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["draft"]["forwarded"] == ["invoice.pdf"] and out["send"]["state"] == "waiting_approval"
    mime = box.drafts(BOX)[out["draft"]["draft"]]
    assert b"To: books@vendor.example" in mime and b"Subject: Fwd: Invoice 4471" in mime
    text = mime.decode()
    assert text.index("For the books.") < text.index("Forwarded message") < text.index("Please pay invoice 4471.")
    assert files_of(mime)[1] == [("invoice.pdf", b"%PDF1")]
    # Held for a person's OK: the drain delivers nothing, and nothing ever went around the gate.
    S.drain(api.app.state.store, api.app.state.auth)
    assert box.sent == []
    refused = api.post(
        "/api/v2/mail/drafts",
        json={"mailbox": BOX, "forward": "m1", "reply_to": "t1", "to": [SENDER]},
        headers=headers(),
    )
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "forward"


def test_a_reply_to_all_goes_to_everyone_on_the_message_but_the_mailbox(api, box):
    box.add(
        BOX,
        "m5",
        thread="t5",
        sender=SENDER,
        to=[BOX, "ap@vendor.example"],
        cc=["cfo@vendor.example", BOX],
        subject="Invoice 4480",
        body="Second invoice.",
    )
    made = post(api, "mail/drafts", {"mailbox": BOX, "reply_to": "t5", "reply_all": True, "body": "Paid, thanks."})
    assert (made["to"], made["cc"], made["thread"]) == (["ap@vendor.example", SENDER], ["cfo@vendor.example"], "t5")
    mime = box.drafts(BOX)[made["draft"]]
    assert f"To: {SENDER}, ap@vendor.example".encode() in mime and b"Cc: cfo@vendor.example" in mime
    plain = post(api, "mail/drafts", {"mailbox": BOX, "reply_to": "t5", "body": "Paid, thanks."})
    assert plain["draft"] != made["draft"] and plain["to"] == [SENDER] and plain["cc"] == []
    post(api, "mail/drafts", {"mailbox": BOX, "reply_all": True, "to": [SENDER], "body": "Hi."}, expected=422)


def test_a_forward_carries_the_whole_text_and_refuses_a_file_it_cannot_read(api, box):
    from backend.mail_lib import TRUNCATED

    long_text = "Statement line.\n" * 2500 + "Footer: reference 4471."  # over the read API's 32,768 characters
    box.add(BOX, "m7", thread="t7", sender=SENDER, subject="Statement", body=long_text)
    made = post(api, "mail/drafts", {"mailbox": BOX, "forward": "m7", "to": ["books@vendor.example"]})
    text = box.drafts(BOX)[made["draft"]].decode()
    assert "Footer: reference 4471." in text and TRUNCATED.strip() not in text
    box.box(BOX)["messages"]["m7"]["payload"]["parts"].append(
        {"filename": "scan.pdf", "mimeType": "application/pdf", "body": {"size": 5}}  # no data, no attachment id
    )
    drafts = dict(box.drafts(BOX))
    refused = api.post(
        "/api/v2/mail/drafts",
        json={"mailbox": BOX, "forward": "m7", "to": ["ap@vendor.example"], "body": "FYI."},
        headers=headers(),
    )
    assert refused.status_code == 502 and "scan.pdf" in refused.json()["error"]["detail"]
    assert box.drafts(BOX) == drafts
