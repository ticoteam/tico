"""Contact support (PRIVACY.md, "Support tickets"): the form's request is forwarded to HQ, only on submit and never in demo
mode, and a person sees their own tickets and HQ's replies, and no one else's."""

import json
import logging

import httpx
import pytest

from backend import support
from backend.auth import Identity
from backend.tests.helpers import engine as X
from backend.tests.helpers.onboarding import TOKEN, as_person, environment, signed_in  # noqa: F401
from backend import bot_settings as BS
from backend.tests.helpers.support import ContractHQ, TICKET_ID, documented, file, hq


def test_submit_forwards_exactly_what_the_form_showed_and_keeps_the_secret_on_the_server(environment, hq):
    api = environment()
    form = api.get("/api/v2/support/compose", headers=signed_in()).json()
    assert form["version"] == "0.2.17" and len(form["install_id"]) == 36 and form["to"] == "updates.tico.team"
    assert hq.seen == []  # opening the form sends nothing

    r = file(api, email="ana@acme.example")
    assert r.status_code == 200
    ticket = r.json()
    assert ticket["status"] == "open" and ticket["sent"] == {"version": "0.2.17", "install_id": form["install_id"]}
    assert "secret" not in r.text and "TK-" not in r.text and "hq_secret" not in r.text
    (request,) = hq.seen
    assert request.method == "POST" and str(request.url) == "https://updates.tico.team/v1/support"
    assert json.loads(request.content) == {
        "message": "The board will not load",
        "email": "ana@acme.example",
        "version": "0.2.17",
        "install_id": form["install_id"],
        "source": "person",
    }

    # Without the checkbox: only the message (and the email if one is given).
    file(api, include_ids=False, app_version="0.3.47")
    assert json.loads(hq.seen[-1].content) == {"message": "The board will not load", "source": "person"}
    assert file(api, include_ids=False).json()["sent"] == {}

    # Bad input never reaches HQ.
    reached = len(hq.seen)
    assert file(api, message="x" * 4001).status_code == 422
    assert len(hq.seen) == reached


def test_the_desktop_apps_version_goes_labelled_apart_and_an_older_hq_still_gets_the_ticket(environment, hq):
    api = environment()
    assert file(api, app_version="0.3.47").json()["sent"]["app_version"] == "0.3.47"
    sent = json.loads(hq.seen[-1].content)
    assert sent["version"] == "0.2.17" and sent["app_version"] == "0.3.47"
    assert "app_version" not in file(api, app_version="0.3.47; rm -rf").json()["sent"]
    assert "app_version" not in json.loads(hq.seen[-1].content)

    hq.fields = {"message", "email", "install_id", "version"}
    r = file(api, app_version="0.3.47")
    assert r.status_code == 200 and "app_version" not in r.json()["sent"]
    assert [json.loads(x.content).get("app_version") for x in hq.seen[-2:]] == ["0.3.47", None]
    assert len(hq.tickets) == 3

    # Only that refusal is retried: HQ objecting to the value itself is answered at once; objecting to another field is
    # answered after the one retry without source (an older HQ's refusal does not say which field it was).
    for fields, refuse, body, asks in (
        (None, "app_version", {"app_version": "0.3.47"}, 1),
        ({"message"}, None, {"email": "ana@acme.example"}, 2),
    ):
        hq.fields, hq.refuse_field, before = fields, refuse, len(hq.seen)
        r = file(api, **body)
        assert r.status_code < 500 and r.status_code >= 400, r.text
        assert len(hq.seen) == before + asks
    assert "source" not in json.loads(hq.seen[-1].content)
    assert len(hq.tickets) == 3


def test_demo_mode_and_the_off_switch_refuse_everything(environment, hq, monkeypatch):
    api = environment(demo=True)
    api.headers["Host"] = "localhost:8765"  # the demo answers loopback names only
    for call in (
        lambda: file(api),
        lambda: api.get("/api/v2/support/compose", headers=signed_in()),
        lambda: api.post("/api/v2/support/tickets/refresh", headers=signed_in()),
    ):
        assert call().status_code == 409
    assert api.get("/api/v2/support/tickets", headers=signed_in()).json() == {
        "enabled": False,
        "tickets": [],
        "unread": 0,
    }
    assert hq.seen == []
    # A ticket filed while support was on: once it is off, nothing about it, or anything else, reaches HQ.
    api = environment()
    ticket = "/api/v2/support/tickets/" + file(api).json()["id"]
    hq.seen.clear()
    monkeypatch.setenv("TICO_SUPPORT", "off")
    for call in (
        lambda: file(api),
        lambda: api.get("/api/v2/support/compose", headers=signed_in()),
        lambda: api.post("/api/v2/support/tickets/refresh", headers=signed_in()),
        lambda: api.post(ticket + "/messages", json={"message": "hi"}, headers=signed_in()),
        lambda: api.delete(ticket, headers=signed_in()),
    ):
        assert call().status_code == 409
    assert hq.seen == []


def test_only_a_signed_in_person_may_file(environment, hq):
    api = environment()
    for identity in (
        Identity("human:riley", "human", email="riley@acme.example", via_token=True),
        Identity("human:riley", "human", email="riley@acme.example", via="assistant"),
    ):
        api.app.state.store.settings.test_identities["viaa"] = identity
        assert file(api, headers=signed_in("viaa")).status_code == 403
    assert api.get("/api/v2/support/tickets").status_code in (401, 403)
    assert hq.seen == []


def test_a_person_sees_their_own_tickets_and_no_one_elses(environment, hq):
    api = environment()
    riley = as_person(api, "riley")
    mine, theirs = file(api).json(), file(api, headers=riley, message="Riley's problem").json()
    owner_list = api.get("/api/v2/support/tickets", headers=signed_in()).json()["tickets"]
    riley_list = api.get("/api/v2/support/tickets", headers=riley).json()["tickets"]
    assert [t["id"] for t in owner_list] == [mine["id"]] and [t["id"] for t in riley_list] == [theirs["id"]]
    other = "/api/v2/support/tickets/" + theirs["id"]
    assert api.post(other + "/messages", json={"message": "hi"}, headers=signed_in()).status_code == 404
    assert api.post(other + "/read", headers=signed_in()).status_code == 404
    assert api.delete(other, headers=signed_in()).status_code == 404
    assert len(hq.tickets) == 2  # the other person's ticket is untouched
    # Refresh asks HQ about the caller's tickets only.
    hq.seen.clear()
    api.post("/api/v2/support/tickets/refresh", headers=signed_in())
    assert [r.url.path for r in hq.seen] == ["/v1/support/TK-00000001"]


def test_refresh_paces_tickets_by_activity_and_asks_about_the_one_in_view(environment, hq):
    """HQ counts every ask from an install together, so a ticket written on in the last hour is asked about every
    30 s, a quiet open one every 5 minutes, a closed one every 6 hours, and the one a person has open every 30 s
    whatever it is; the most recently written on first, and a 429 ends the round like HQ being down."""
    api = environment()
    ids = {name: file(api, message=name).json()["id"] for name in ("busy", "quiet", "stale", "closed")}
    hq_of = dict(zip(ids, hq.tickets))
    now = support.H.now()
    ago = lambda seconds: support.H.shift(now, seconds=-seconds)
    hq_stamp = lambda seconds: ago(seconds)[:19] + "Z"  # HQ's own format
    old = ago(2 * 86400)
    rows = {  # name: (status, created, checked, newest message)
        "busy": ("answered", old, ago(60), hq_stamp(600)),  # the person wrote 10 minutes ago
        "quiet": ("answered", old, ago(60), hq_stamp(86400)),  # nothing for a day, asked a minute ago
        "stale": ("open", old, ago(400), hq_stamp(86400)),  # nothing for a day, asked 6+ minutes ago
        "closed": ("closed", old, ago(3600), hq_stamp(86400)),
    }
    with api.app.state.store.transaction() as c:
        for name, (status, created, checked, said) in rows.items():
            hq.tickets[hq_of[name]]["status"] = status
            hq.tickets[hq_of[name]]["messages"] = [{"id": 1, "created": said, "from": "person", "body": "hi"}]
            c.execute(
                "UPDATE support_tickets SET status=?, created_at=?, checked_at=?, messages=? WHERE id=?",
                (
                    status,
                    created,
                    checked,
                    json.dumps(
                        [{"id": 1, "created_at": said, "from": "person", "body": "hi", "has_diagnostics": False}]
                    ),
                    ids[name],
                ),
            )

    def asked(body=None):
        hq.seen.clear()
        r = api.post("/api/v2/support/tickets/refresh", headers=signed_in(), **({"json": body} if body else {}))
        assert r.status_code == 200, r.text
        return [request.url.path.rsplit("/", 1)[1] for request in hq.seen], r.json()["refresh_failed"]

    # Busy is due at 30 s and asked first; quiet waits 5 minutes; stale is due; closed waits 6 hours.
    assert asked() == ([hq_of["busy"], hq_of["stale"]], False)
    # The one in view is asked about first, even closed, and every 30 s; another person's ticket is not.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE support_tickets SET checked_at=? WHERE id IN (?,?)", (ago(60), ids["busy"], ids["closed"]))
    assert asked({"ticket": ids["closed"]}) == ([hq_of["closed"], hq_of["busy"]], False)
    riley = file(api, headers=as_person(api, "riley"), message="Riley's").json()["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE support_tickets SET checked_at=?", (now,))
    assert asked({"ticket": riley}) == ([], False)
    # HQ answering 429 ends the round after the first ask, the most recent first, and keeps what was known.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE support_tickets SET checked_at=?", (ago(400),))
    hq.read_status = 429
    assert asked() == ([hq_of["busy"]], True)


def test_the_ticket_round_trip_keeps_to_hq_s_documented_contract(environment, monkeypatch):
    """The page's whole ticket round trip, with every request the install makes held exactly to HQ's ticket routes as
    docs/telemetry.md ("Support tickets") documents them: the method and path, the ticket's secret in `X-Ticket-Secret`
    (never in the address, never a staff key), and the JSON fields. HQ answers in the documented shapes, and the page
    shows what they say: a staff reply arrives as unread, writing back reaches the ticket, reading clears the count,
    deleting removes it. HQ's own side (its queue, the staff routes) is tested in ticoteam/tico-services."""
    documented(
        "telemetry.md",
        "POST /v1/support",
        "GET /v1/support/{id}",
        "POST /v1/support/{id}/messages",
        "DELETE /v1/support/{id}",
        "POST /v1/staff/tickets/{id}/reply",
    )
    hq = ContractHQ()
    monkeypatch.setattr(support, "TRANSPORT", httpx.MockTransport(hq))
    monkeypatch.setattr(support, "FRESH_S", 0)
    api = environment()
    install_id = api.get("/api/v2/support/compose", headers=signed_in()).json()["install_id"]

    # Filing: `POST /v1/support` with the documented fields only and no secret; `201 {ticket_id, secret, status}`.
    mine = file(api).json()
    ((tid, held),) = hq.tickets.items()
    assert TICKET_ID.fullmatch(tid) and mine["status"] == "open"
    assert hq.calls() == [
        (
            "POST",
            "/v1/support",
            "",
            None,
            {"message": "The board will not load", "source": "person", "version": "0.2.17", "install_id": install_id},
        )
    ]
    secret = held["secret"]
    assert secret not in json.dumps(api.get("/api/v2/support/tickets", headers=signed_in()).json())

    # The team replies (`POST /v1/staff/tickets/{id}/reply`, HQ's side); the page's refresh asks
    # `GET /v1/support/{id}` with the secret and shows the documented answer, the reply unread.
    hq.staff_replies(tid, "Try a reload")
    mark = len(hq.seen)
    api.post("/api/v2/support/tickets/refresh", headers=signed_in())
    assert hq.calls(mark) == [("GET", f"/v1/support/{tid}", "", secret, None)]
    listed = api.get("/api/v2/support/tickets", headers=signed_in()).json()
    (ticket,) = listed["tickets"]
    assert ticket["id"] == mine["id"] and ticket["status"] == "answered" and listed["unread"] == 1
    assert [(m["id"], m["from"], m["body"]) for m in ticket["messages"]] == [(1, "staff", "Try a reload")]

    # Writing back: `POST /v1/support/{id}/messages` `{message}`, then the ticket is read again.
    mark = len(hq.seen)
    assert (
        api.post(
            f"/api/v2/support/tickets/{mine['id']}/messages", json={"message": "That fixed it"}, headers=signed_in()
        ).json()["status"]
        == "open"
    )
    assert hq.calls(mark) == [
        ("POST", f"/v1/support/{tid}/messages", "", secret, {"message": "That fixed it"}),
        ("GET", f"/v1/support/{tid}", "", secret, None),
    ]
    assert [m["body"] for m in hq.tickets[tid]["messages"]] == ["Try a reload", "That fixed it"]

    # Reading the thread clears the unread count and asks HQ nothing.
    mark = len(hq.seen)
    api.post(f"/api/v2/support/tickets/{mine['id']}/read", headers=signed_in())
    assert api.get("/api/v2/support/tickets", headers=signed_in()).json()["unread"] == 0
    assert hq.calls(mark) == []

    # Deleting: `DELETE /v1/support/{id}` with the secret.
    mark = len(hq.seen)
    assert api.delete(f"/api/v2/support/tickets/{mine['id']}", headers=signed_in()).json() == {"deleted": True}
    assert hq.calls(mark) == [("DELETE", f"/v1/support/{tid}", "", secret, None)] and not hq.tickets

    # Every request went to HQ's address over HTTPS, as JSON when it had a body, and never with a staff key.
    for request in hq.seen:
        assert (request.url.scheme, request.url.host) == ("https", "updates.tico.team")
        assert "authorization" not in request.headers
        assert request.headers.get("content-type") == ("application/json" if request.content else None)


def stored_secret(api, ticket_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT hq_secret FROM support_tickets WHERE id=?", (ticket_id,)).fetchone()[0]


def test_the_ticket_secret_is_sealed_with_the_credential_vault_and_every_use_still_works(environment, hq):
    api = environment()
    key = api.app.state.store.settings.db_path.parent / "credential.key"
    assert not key.exists()  # a fresh install: the vault has no key yet
    ticket = file(api).json()
    ((tid, held),) = hq.tickets.items()
    stored = stored_secret(api, ticket["id"])
    assert held["secret"] not in stored and stored.startswith("sealed:v1:")
    assert key.exists() and oct(key.stat().st_mode & 0o777) == "0o600"  # the first ticket made the vault's key
    # Writing, refreshing and deleting send HQ the real secret (FakeHQ answers 404 to any other).
    hq.staff_replies(tid, "Try a reload")
    assert api.post("/api/v2/support/tickets/refresh", headers=signed_in()).json()["tickets"][0]["status"] == "answered"
    assert (
        api.post(
            f"/api/v2/support/tickets/{ticket['id']}/messages", json={"message": "Thanks"}, headers=signed_in()
        ).status_code
        == 200
    )
    assert api.delete(f"/api/v2/support/tickets/{ticket['id']}", headers=signed_in()).json() == {"deleted": True}
    assert not hq.tickets


def test_plaintext_ticket_secrets_from_before_are_sealed_once_on_start_and_a_vault_that_cannot_seal_still_fails(
    environment, hq, caplog
):
    api = environment()
    ticket = file(api).json()
    ((tid, held),) = hq.tickets.items()
    with api.app.state.store.transaction() as c:  # as the release before this one kept it
        c.execute("UPDATE support_tickets SET hq_secret=? WHERE id=?", (held["secret"], ticket["id"]))

    # A vault that cannot seal still fails, so startup names it, and the secret stays as it was.
    class KmsDown:
        class cipher:
            @staticmethod
            def seal(c, context, value):
                raise OSError("KMS unreachable")

    with pytest.raises(OSError):
        support.seal_plaintext_secrets(api.app.state.store, KmsDown)
    assert stored_secret(api, ticket["id"]) == held["secret"], "a failed seal leaves the secret as it was"

    # With the real vault it is sealed once, and every use still works.
    vault = api.app.state.vault
    assert support.seal_plaintext_secrets(api.app.state.store, vault) == 1
    sealed = stored_secret(api, ticket["id"])
    assert sealed.startswith("sealed:v1:") and held["secret"] not in sealed
    assert (
        support.seal_plaintext_secrets(api.app.state.store, vault) == 0 and stored_secret(api, ticket["id"]) == sealed
    )
    hq.staff_replies(tid, "Fixed")
    assert api.post("/api/v2/support/tickets/refresh", headers=signed_in()).json()["tickets"][0]["status"] == "answered"

    # No ticket table yet (a fresh database: the seal runs before Store.initialize makes it) is nothing to seal, and no warning.
    with api.app.state.store.transaction() as c:
        c.execute("DROP TABLE support_tickets")
    caplog.clear()
    assert support.seal_plaintext_secrets(api.app.state.store, vault) == 0
    assert not [r for r in caplog.records if r.name == "tico.support" and r.levelno >= logging.WARNING]


def test_a_vault_that_cannot_be_opened_refuses_a_ticket_before_anything_reaches_hq(environment, hq):
    api = environment()
    file(api)
    hq.seen.clear()
    (api.app.state.store.settings.db_path.parent / "credential.key").unlink()
    api.app.state.vault.cipher.cached = None
    r = file(api, message="Another problem")
    assert r.status_code == 503 and r.json()["error"]["code"] == "vault_unavailable"
    assert hq.seen == [] and len(hq.tickets) == 1


# ----------------------------------------------------------------------------- a bot's report, approved by a person
REPORT = "tico task update failed since 0.2.17 with: refused (version). Tried a fresh read; same error."


def as_bot(api, slug="assistant"):
    """The bot, connected as an external agent (a Hermes profile): its own standing credential, no runner lease."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug=?", (slug,))
        c.execute(
            "INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            (slug, "hermes", "hash-" + slug, "2026-01-01T00:00:00Z", "human:test"),
        )
    api.app.state.store.settings.test_identities["bot-" + slug] = Identity("bot:" + slug, "bot", agent="hermes")
    return signed_in("bot-" + slug)


def draft(api, headers, message=REPORT, **extra):
    """A report drafted through POST /api/v2/support/reports: with the team rule off, an OK asked of a person
    (backend/task_asks.py); the answer's `id` is the OK's requirement id."""
    import hashlib

    payload = {"message": message, "content_sha256": hashlib.sha256(message.encode()).hexdigest(), **extra}
    return api.post("/api/v2/support/reports", json={"payload": payload}, headers=signed_in(_token(headers)))


def _token(headers):
    return headers["Authorization"].split(" ", 1)[1]


def decide(api, approval_id, decision="approved", token=TOKEN):
    """The person asked answers through the real OK routes: OK from the preview (GET /api/v2/oks/{rid}, POST
    /api/v2/tasks/{id}/asks/{rid}/ok), or a send back. Returns (whom it was asked of, the answer)."""
    from backend import task_asks

    with api.app.state.store.read() as c:
        to = task_asks.grant(c, approval_id)["asked_of_id"]
        tid = task_asks.task_of(c, approval_id)
    if decision != "approved":
        return to, api.post(
            f"/api/v2/tasks/{tid}/asks/{approval_id}/answer",
            json={"value": "no", "note": "Not this one."},
            headers=signed_in(token),
        )
    shown = api.get(f"/api/v2/oks/{approval_id}", headers=signed_in(token))
    if shown.status_code != 200:
        return to, shown
    return to, api.post(
        f"/api/v2/tasks/{tid}/asks/{approval_id}/ok",
        json={"shown_hash": shown.json()["shown_hash"]},
        headers=signed_in(token),
    )


def send(api, headers, approval_id):
    return api.post("/api/v2/support/tickets/from-approval", json={"approval": approval_id}, headers=headers)


def consumed(api, approval_id):
    from backend import task_asks

    with api.app.state.store.read() as c:
        return (task_asks.grant(c, approval_id) or {}).get("used_at")


def drafts_only(api):
    """Bots send without asking when the team lets them, which it does by default. The tests of a drafted report turn
    that off first: the owner's own setting (backend/team_rules.py bot_reports_direct)."""
    assert api.put("/api/v2/access/rules", json={"bot_reports_direct": False}, headers=signed_in()).status_code == 200


def test_a_bot_drafts_a_person_approves_and_the_bot_sends_one_ticket_owned_by_the_person(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    drafts_only(api)
    bot = as_bot(api)
    r = draft(api, bot)
    assert r.status_code == 200, r.text
    approval = r.json()
    assert approval["status"] == "pending" and hq.seen == []  # drafting sends nothing
    assert send(api, bot, approval["id"]).json()["error"]["code"] == "approval"  # not OK'd yet
    assert hq.seen == [] and not consumed(api, approval["id"])
    with api.app.state.store.read() as c:
        filed = c.execute("SELECT title, private FROM tasks WHERE id=?", (approval["task_id"],)).fetchone()
    assert tuple(filed) == ("Send a report to Tico support", 1)
    approver, decided = decide(api, approval["id"])
    assert decided.status_code == 200, decided.text
    r = send(api, bot, approval["id"])
    assert r.status_code == 200, r.text
    sent = r.json()
    assert sent["filed_as"] == approver and sent["ticket"]["message"] == REPORT and consumed(api, approval["id"])
    assert sent["ticket"]["sent"]["version"] == "0.2.17" and "secret" not in r.text
    (request,) = hq.seen
    assert set(json.loads(request.content)) == {
        "message",
        "version",
        "install_id",
        "source",
    }  # no email, no diagnostics
    assert json.loads(request.content)["source"] == "bot_report"  # a bot's report a person approved
    # The approver's ticket is kept under their name, but a bot's report is not in their chat (support.OWN).
    with api.app.state.store.read() as c:
        assert [tuple(x) for x in c.execute("SELECT actor FROM support_tickets")] == [(approver,)]
    assert api.get("/api/v2/support/tickets", headers=signed_in()).json()["tickets"] == []
    # Again: the same ticket back, nothing more reaches HQ.
    again = send(api, bot, approval["id"])
    assert again.status_code == 200 and again.json()["ticket"]["id"] == sent["ticket"]["id"] and again.json()["again"]
    assert len(hq.seen) == 1 and len(hq.tickets) == 1


def test_without_ids_only_the_message_goes(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    drafts_only(api)
    bot = as_bot(api)
    approval = draft(api, bot, include_ids=False).json()
    decide(api, approval["id"])
    assert send(api, bot, approval["id"]).json()["ticket"]["sent"] == {"source": "bot_report"}
    assert json.loads(hq.seen[-1].content) == {"message": REPORT, "source": "bot_report"}


def test_a_report_is_refused_when_declined_sent_by_another_or_not_what_was_approved(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    drafts_only(api)
    bot = as_bot(api)
    declined = draft(api, bot, message="Declined report").json()
    decide(api, declined["id"], "declined")
    r = send(api, bot, declined["id"])
    assert r.status_code == 409 and "sent back" in r.json()["error"]["detail"]
    approval = draft(api, bot).json()
    decide(api, approval["id"])
    for other in (as_bot(api, "botops"), signed_in()):  # another bot, or the approver themselves
        assert send(api, other, approval["id"]).status_code == 403
    assert send(api, bot, "nope").status_code == 404
    # The stored message changed after the person read it: its hash no longer matches.
    with api.app.state.store.transaction() as c:
        row = c.execute("SELECT content FROM ok_grants WHERE requirement_id=?", (approval["id"],)).fetchone()[0]
        c.execute(
            "UPDATE ok_grants SET content=? WHERE requirement_id=?",
            (row.replace("tico task update", "tico task delete"), approval["id"]),
        )
    r = send(api, bot, approval["id"])
    assert r.status_code == 409 and "hash" in r.json()["error"]["detail"]
    # The person's click claimed the OK (support.ok_given), but nothing was sent and no ticket is kept.
    assert hq.seen == [] and not consumed(api, declined["id"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 0


def test_a_draft_that_is_not_exactly_a_report_is_refused(environment, hq):  # noqa: F811  (the fixture)
    import hashlib
    import uuid

    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    api = environment()
    drafts_only(api)
    bot = as_bot(api)
    for payload, why in [
        ({"message": REPORT, "content_sha256": "0" * 64}, "doesn't match"),
        ({"message": REPORT, "content_sha256": "ABC"}, "64 lower-case hex"),
        ({"message": REPORT}, "needs content_sha256"),
        ({"message": " padded ", "content_sha256": sha(" padded ")}, "blank space"),
        ({"message": "x" * 4001, "content_sha256": sha("x" * 4001)}, "4000"),
        ({"message": REPORT, "content_sha256": sha(REPORT), "diagnostics": "abc"}, "not diagnostics"),
        ({"message": REPORT, "content_sha256": sha(REPORT), "include_ids": "yes"}, "true or false"),
    ]:
        r = api.post(
            "/api/v2/support/reports",
            json={"payload": payload},
            headers={**bot, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert r.status_code >= 400 and why in r.text, (why, r.status_code, r.text)
    assert hq.seen == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM ok_grants").fetchone()[0] == 0  # nobody is asked about it either


def test_support_off_refuses_drafting_and_sending(environment, hq, monkeypatch):  # noqa: F811  (the fixture)
    api = environment()
    drafts_only(api)
    bot = as_bot(api)
    approval = draft(api, bot).json()
    decide(api, approval["id"])
    monkeypatch.setenv("TICO_SUPPORT", "off")
    assert draft(api, bot, message="Another").status_code == 409
    assert send(api, bot, approval["id"]).status_code == 409
    # The person's click claimed it (support.ok_given); with support off nothing is sent.
    assert hq.seen == [] and consumed(api, approval["id"])


def test_an_okd_report_is_claimed_before_it_leaves_and_a_retry_sends_the_same_report_once(environment, hq):  # noqa: F811  (the fixture)
    """Claim, then send (rule D): the OK is used and committed before HQ hears anything, every send of it carries
    Idempotency-Key tico-support:<rid>, HQ down or refusing leaves it claimed with no ticket, and a retry sends the
    same report, even once the person who OK'd it has left: nothing is refused after it may have gone."""
    api = environment()
    drafts_only(api)
    bot = as_bot(api)
    approval = draft(api, bot).json()
    rid = approval["id"]
    approver, given = decide(api, rid)
    assert given.status_code == 200, given.text
    hq.down = True
    r = send(api, bot, rid)
    assert r.status_code == 502 and r.json()["error"]["retryable"] is True and consumed(api, rid)
    hq.down, hq.status_override = False, 429
    assert send(api, bot, rid).status_code == 429
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 0
    hq.status_override = None
    from backend.tests.helpers import engine as X

    with api.app.state.store.transaction() as c:  # the person who OK'd it can no longer sign in after the claim
        X.no_sign_in(c, approver)
    r = send(api, bot, rid)
    assert r.status_code == 200 and not r.json()["again"] and len(hq.tickets) == 1, r.text
    assert {q.headers.get("idempotency-key") for q in hq.seen} == {"tico-support:" + rid}
    again = send(api, bot, rid)
    assert again.status_code == 200 and again.json()["again"] and len(hq.seen) == 3
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 1


def _rule(api, on):
    assert api.put("/api/v2/access/rules", json={"bot_reports_direct": on}, headers=signed_in()).status_code == 200


def test_an_okd_report_not_yet_delivered_is_sent_once_when_the_bot_then_sends_it_under_the_rule(environment, hq):  # noqa: F811
    """A person OK'd a bot's report (their click claimed it) and Maintenance has not delivered it yet. The team rule
    is turned on and the bot sends the same report: one report, one send, whichever path gets there first."""
    api = environment()
    drafts_only(api)
    bot = as_bot(api)
    rid = draft(api, bot).json()["id"]
    assert decide(api, rid)[1].status_code == 200 and consumed(api, rid)
    _rule(api, True)
    r = draft(api, bot)
    assert r.status_code == 200 and r.json()["status"] == "sent", r.text
    assert len(hq.tickets) == 1 and {q.headers.get("idempotency-key") for q in hq.seen} == {"tico-support:" + rid}
    again = send(api, bot, rid)  # Maintenance's delivery, later: the ticket it already has
    assert again.status_code == 200 and again.json()["again"] and len(hq.tickets) == 1
    with api.app.state.store.read() as c:
        assert support.claimed(c) == []
    # The other order: sent under the rule first, then a person's claimed OK of the same report is that one send.
    _rule(api, False)
    other = draft(api, bot, message=REPORT + " Again.").json()["id"]
    assert decide(api, other)[1].status_code == 200
    _rule(api, True)
    with api.app.state.store.read() as c:
        assert [r["requirement_id"] for r in support.claimed(c)] == [other]
    sent = draft(api, bot, message=REPORT + " Again.")
    assert sent.json()["status"] == "sent" and len(hq.tickets) == 2
    with api.app.state.store.read() as c:
        assert support.claimed(c) == []
    assert send(api, bot, other).json()["again"] and len(hq.tickets) == 2


def test_a_claimed_report_that_no_longer_matches_its_hash_is_refused_once_and_said_on_its_task(environment, hq):  # noqa: F811
    from backend import health, task_asks
    from backend.store import H

    api = environment()
    drafts_only(api)
    bot = as_bot(api)
    rid = draft(api, bot).json()["id"]
    assert decide(api, rid)[1].status_code == 200
    with api.app.state.store.transaction() as c:  # the stored report changed after it was OK'd
        c.execute(
            "UPDATE ok_grants SET content=json_set(content, '$.message', 'Something else') WHERE requirement_id=?",
            (rid,),
        )
    r = send(api, bot, rid)
    assert r.status_code == 409 and "no longer matches its hash" in r.text and hq.seen == []
    with api.app.state.store.read() as c:
        assert support.claimed(c) == []  # out of the queue: Maintenance does not try it again
        assert (
            c.execute("SELECT count(*) FROM events WHERE action='ok.not_done' AND target=?", (rid,)).fetchone()[0] == 1
        )
        task_id = task_asks.task_of(c, rid)
        why = [e for e in H.task_history(c, task_id) if "no longer matches its hash" in (e.get("note") or "")] or [
            v for v in c.execute("SELECT why FROM visits WHERE task_id=? AND how='closed'", (task_id,))
        ]
        assert why, "the task says why it was not sent"
        assert health._claimed_oks_check(c)["counts"] == {"claimed_not_delivered": 0, "claimed_refused": 1}
    assert send(api, bot, rid).status_code == 409
    with api.app.state.store.read() as c:  # refused once: a second try records nothing new
        assert (
            c.execute("SELECT count(*) FROM events WHERE action='ok.not_done' AND target=?", (rid,)).fetchone()[0] == 1
        )


def test_a_refusal_while_drafting_a_report_comes_back_as_a_clean_refusal(environment, hq, monkeypatch):  # noqa: F811
    """A rule that refuses the ask (the task engine's, here faked) is the caller's 409 with its words, never an
    uncaught error: the MCP and CLI sweeps call this route with whatever they generate."""
    from backend.store import H

    api = environment()
    drafts_only(api)
    headers = as_bot(api)

    def refuses(c, by, payload, **_):
        raise H.Refused("task_requirement", "Nobody answers their own ask: ask someone else")

    monkeypatch.setattr(support, "ask_report", refuses)
    r = draft(api, headers)
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "task_requirement", r.text  # a bot's agent reads the flat shape
    assert "Nobody answers their own ask" in r.json()["detail"], r.text


def test_the_person_only_routes_still_refuse_a_bot(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    bot = as_bot(api)
    assert file(api, headers=bot).status_code == 403
    for path in ("/api/v2/support/tickets", "/api/v2/support/compose"):
        assert api.get(path, headers=bot).status_code == 403
    assert hq.seen == []


def test_a_bot_reports_and_sends_over_mcp(environment, hq):  # noqa: F811  (the fixture)
    from backend.tests.helpers.mcp import call, rpc

    api = environment()
    drafts_only(api)
    as_bot(api)
    tools = {t["name"] for t in rpc(api, "tools/list", token="bot-assistant")["result"]["tools"]}
    assert {"tico_support_report", "tico_support_send"} <= tools
    err, drafted = call(
        api, "tico_support_report", {"message": "  " + REPORT.replace(". ", ".\r\n") + "\n"}, token="bot-assistant"
    )
    assert not err, drafted
    assert "must OK it" in drafted["next"] and hq.seen == []
    err, early = call(api, "tico_support_send", {"ok": drafted["ok"]}, token="bot-assistant")
    assert err and hq.seen == []
    decide(api, drafted["ok"])
    err, sent = call(api, "tico_support_send", {"ok": drafted["ok"]}, token="bot-assistant")
    assert not err, sent
    assert json.loads(hq.seen[-1].content)["message"] == REPORT.replace(". ", ".\n")  # what the person read
    err, again = call(api, "tico_support_send", {"ok": drafted["ok"]}, token="bot-assistant")
    assert not err and again["ticket"]["id"] == sent["ticket"]["id"] and len(hq.tickets) == 1


def test_a_persons_own_draft_is_theirs_to_approve_and_is_filed_as_them(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    as_person(api, "riley")
    tokens = {
        "riley-token": Identity("human:riley", "human", email="riley@acme.example", via_token=True),
        "riley-assistant": Identity("human:riley", "human", email="riley@acme.example", via="assistant"),
    }
    api.app.state.store.settings.test_identities.update(tokens)
    # The Assistant drafts nothing on its own: like any write it may not make, it proposes it on a Confirm card.
    assert draft(api, signed_in("riley-assistant")).json()["error"]["code"] == "confirm_required"
    from backend import task_asks

    for token, message in (("riley-token", REPORT), ("riley-token", "A second report from Riley's token")):
        approval = draft(api, signed_in(token), message=message).json()
        with api.app.state.store.read() as c:
            to = task_asks.grant(c, approval["id"])["asked_of_id"]
        assert to == "human:riley"  # not the default person
        # Neither their token nor the Assistant says yes for them, nor the owner (it isn't theirs): only Riley at
        # their browser.
        for proxy in (token, "riley-assistant", TOKEN):
            _, refused = decide(api, approval["id"], token=proxy)
            assert refused.status_code in (403, 404), (proxy, refused.text)
            assert consumed(api, approval["id"]) is None
        with api.app.state.store.read() as c:
            assert task_asks.standing(c, approval["id"]) == task_asks.WAITING
        _, given = decide(api, approval["id"], token="riley")
        assert given.status_code == 200, given.text
        sent = send(api, signed_in(token), approval["id"])
        assert sent.status_code == 200, sent.text
        assert sent.json()["filed_as"] == "human:riley"
    riley = api.get("/api/v2/support/tickets", headers=signed_in("riley")).json()["tickets"]
    assert len(riley) == 2 and api.get("/api/v2/support/tickets", headers=signed_in()).json()["tickets"] == []


def test_a_persons_token_cannot_say_yes_to_a_report_through_the_needs_you_list(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    drafts_only(api)
    from backend import task_asks

    approval = draft(api, as_bot(api)).json()
    with api.app.state.store.read() as c:
        approver = task_asks.grant(c, approval["id"])["asked_of_id"]
    me = api.get("/api/me", headers=signed_in()).json()
    api.app.state.store.settings.test_identities["approver-token"] = Identity(
        approver, me.get("role") or "owner", email=me.get("email") or "", via_token=True
    )
    token = signed_in("approver-token")
    b = api.post("/api/v2/batch", json={}, headers=token).json()
    with api.app.state.store.read() as c:
        assert b.get("item", {}).get("key") == X.ask_key(c, approval["id"]), b
    # A report's OK is bound to its preview: walking the list, an OK is refused as it is said.
    r = api.post(f"/api/v2/batch/{b['id']}/respond", json={"kind": "decide", "decision": "ok"}, headers=token)
    assert r.status_code == 422 and task_asks.FROM_PREVIEW in r.text, r.text
    with api.app.state.store.read() as c:
        assert task_asks.standing(c, approval["id"]) == task_asks.WAITING
    assert send(api, as_bot(api), approval["id"]).status_code != 200
    assert hq.seen == [] and not consumed(api, approval["id"])


def test_a_setup_request_starts_from_a_snapshot_and_keeps_its_kind_even_at_an_older_hq(environment, hq):  # noqa: F811
    from backend.store import H

    api = environment()
    with api.app.state.store.transaction() as c:  # a parked bot whose name must not go
        H.sync_registry(c, {"payroll": {"name": "Payroll for Acme Corp", "status": "active"}}, None)
        c.execute(
            "INSERT OR REPLACE INTO bot_config(bot,owner_id,onboarding_state) VALUES('payroll','human:test','needs_setup')"
        )
        BS.write(c, 'payroll', {})
        c.execute("UPDATE bots SET display_name='Payroll for Acme Corp' WHERE slug='payroll'")
    owner_items = api.get("/api/v2/getting-started", headers=signed_in()).json()["items"]
    assert any("Payroll for Acme Corp" in item["label"] for item in owner_items)
    snapshot = api.get("/api/v2/support/setup-snapshot?asked_from=bot", headers=signed_in()).json()
    assert "Payroll" not in snapshot["message"] and "- Bots in Needs setup: 1" in snapshot["message"]
    plain = api.get("/api/v2/support/setup-snapshot?asked_from=health", headers=as_person(api, "quinn")).json()
    assert "Needs setup" not in plain["message"] and "Payroll" not in plain["message"]
    assert hq.seen == [] and snapshot["kind"] == "setup"  # building the snapshot sends nothing
    lines = snapshot["message"].splitlines()
    assert "Setup snapshot (asked from a bot in Needs setup):" in lines and "- Computer online: no" in lines
    assert any(line.startswith("- Steps done: ") for line in lines) and "- First bot ready: no" in lines
    assert file(api, message=snapshot["message"], kind="setup").json()["sent"]["kind"] == "setup"
    assert json.loads(hq.seen[-1].content)["kind"] == "setup"
    hq.fields = {"message", "email", "install_id", "version"}
    assert "kind" not in file(api, message="Stuck on the computer step", kind="setup").json()["sent"]
    assert json.loads(hq.seen[-1].content)["message"] == "[Setup] Stuck on the computer step"
    assert file(api, kind="billing").status_code == 422


def test_the_feedback_question_waits_for_a_first_result_and_an_answer_arrives_as_a_feedback_request(
    environment, hq, monkeypatch
):  # noqa: F811
    from backend.store import H
    from backend.tests.helpers.usage_count import complete_a_bot_turn

    api = environment()
    store = api.app.state.store
    question = lambda h=None: api.get("/api/v2/config", headers=h or signed_in()).json()["feedback_question"]
    with store.transaction() as c:  # everyone so far acted before any result
        c.execute("UPDATE events SET created_at=?", (H.shift(H.now(), days=-3),))
    from backend import onboarding

    def setup(done):
        with store.transaction() as c:
            record = {**onboarding.load(c), "completed": H.shift(H.now(), days=-3) if done else None}
            c.execute(
                "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (onboarding.KEY, json.dumps(record)),
            )

    def task(requester):
        with store.transaction() as c:
            c.execute(
                "INSERT INTO tasks(id,title,requester_id,owner_id,status,created_at) VALUES(?,'Run payroll',?,'payroll','open',?)",
                ("t-" + requester, requester, H.shift(H.now(), days=-2)),
            )
            c.execute("UPDATE attempts SET task_id=? WHERE bot_id='payroll'", ("t-" + requester,))

    setup(True)
    complete_a_bot_turn(api, H.shift(H.now(), days=-2))
    assert question() is None  # only the Assistant has answered
    complete_a_bot_turn(api, H.shift(H.now(), days=-2), bot="payroll")
    assert question() is None  # a new company's bots go live by themselves: nobody has used Tico yet
    task("payroll")
    assert question() is None  # nor is a task one bot gave another a person's use of it
    task("human:test")
    with store.transaction() as c:
        c.execute("UPDATE attempts SET finished_at=? WHERE bot_id='payroll'", (H.shift(H.now(), hours=-2),))
    assert question() is None  # a person's first result today: the question waits a day
    with store.transaction() as c:
        c.execute("UPDATE attempts SET finished_at=? WHERE bot_id='payroll'", (H.shift(H.now(), days=-1),))
    setup(False)
    assert question() is None  # never before the first-run setup is complete
    setup(True)
    assert question() == {"moment": "first_result", "question": "How is Tico working for you?"}
    assert api.get("/api/v2/config", headers=as_bot(api)).json()["feedback_question"] is None
    r = api.post(
        "/api/v2/support/feedback", json={"moment": "first_result", "answer": "Good so far"}, headers=signed_in()
    )
    assert r.status_code == 200 and r.json()["answered"]
    sent = json.loads(hq.seen[-1].content)  # only their words: no IDs, no diagnostics
    assert sent == {"message": sent["message"], "kind": "feedback", "source": "person"} and sent["message"].endswith(
        "\n\nGood so far"
    )
    assert question() is None
    # A week on, the person is asked once more, but not within 3 days of their last answer.
    with store.transaction() as c:
        c.execute("UPDATE attempts SET finished_at=? WHERE bot_id='payroll'", (H.shift(H.now(), days=-8),))
        c.execute("UPDATE events SET created_at=?", (H.shift(H.now(), days=-9),))
        c.execute(
            "UPDATE preferences SET value=json_set(value,'$.first_result.at',?) WHERE key=?",
            (H.shift(H.now(), days=-1), "support.feedback"),
        )
    assert question() is None
    with store.transaction() as c:
        c.execute(
            "UPDATE preferences SET value=json_set(value,'$.first_result.at',?) WHERE key=?",
            (H.shift(H.now(), days=-7), "support.feedback"),
        )
    assert question()["moment"] == "day_7"
    before = len(hq.seen)
    assert not api.post("/api/v2/support/feedback", json={"moment": "day_7"}, headers=signed_in()).json()["answered"]
    assert len(hq.seen) == before and question() is None
    # Someone who joined after the first result is asked from their own first action, never about that first result.
    riley = as_person(api, "riley")
    assert question(riley) is None  # has not done anything yet
    with store.transaction() as c:
        H.event(c, "human:riley", "test.acted", "x", {})
    assert question(riley) is None
    with store.transaction() as c:
        c.execute("UPDATE events SET created_at=? WHERE actor='human:riley'", (H.shift(H.now(), days=-7),))
    from backend import support_asks

    support_asks.JOINED.clear()  # the server keeps a first action once read; only this test rewrites one
    assert question(riley)["moment"] == "day_7"
    monkeypatch.setenv("TICO_SUPPORT", "off")
    assert question(riley) is None and api.get("/api/v2/config", headers=riley).json()["support_on"] is False


def test_each_filing_says_its_source_and_an_older_hq_still_gets_the_ticket(environment, hq):
    """Spec ticket-source.md criteria 1, 3 and 8: the person's own app sends `person` (it never names one), a person's
    claim of `botops` is filed as theirs (BotOps' own request is tested in test_botops_parity.py), nothing else may be
    claimed, and an HQ that refuses `source` as an unknown field gets the ticket again without it, its message
    unchanged (BotOps' name stays at its start, which such an HQ reads)."""
    api = environment()
    file(api)
    assert json.loads(hq.seen[-1].content)["source"] == "person"
    assert "source" not in file(api).json()["sent"]  # HQ's sorting, not something the person sent
    file(api, message="[BotOps] Sync fails", source="botops")
    assert json.loads(hq.seen[-1].content)["source"] == "person"
    for claimed in ("bot_report", "health_monitor", "hq_alert", "website", "unknown"):
        assert file(api, source=claimed).status_code == 422
    filed = len(hq.tickets)

    hq.fields = {"message", "email", "install_id", "version", "app_version", "kind"}
    r = file(api, message="[BotOps] Sync fails")
    assert r.status_code == 200, r.text
    first, again = (json.loads(x.content) for x in hq.seen[-2:])
    assert first["source"] == "person" and "source" not in again and again["message"] == "[BotOps] Sync fails"
    assert len(hq.tickets) == filed + 1
    # Today's HQ knows kind but not source: only source is dropped, so a setup request keeps its kind.
    r = file(api, message="Stuck on the computer step", kind="setup")
    assert r.status_code == 200 and r.json()["sent"]["kind"] == "setup"
    last = json.loads(hq.seen[-1].content)
    assert "source" not in last and last["kind"] == "setup" and last["message"] == "Stuck on the computer step"
    # An HQ from before kind as well: both go, as before, and the kind is named in the message.
    hq.fields = {"message", "email", "install_id", "version"}
    r = file(api, message="Stuck again", kind="setup")
    assert r.status_code == 200 and "kind" not in r.json()["sent"]
    last = json.loads(hq.seen[-1].content)
    assert set(last) <= hq.fields and last["message"] == "[Setup] Stuck again"
    # Only those refusals are retried: HQ objecting to the value itself is answered as it is, after one request.
    hq.fields, hq.refuse_field, before = None, "source", len(hq.seen)
    assert 400 <= file(api).status_code < 500 and len(hq.seen) == before + 1


def test_the_botops_tool_says_botops_and_keeps_its_name_in_the_message(monkeypatch):
    """Criterion 3: `tico support file` sends `source: botops` beside the `[BotOps] ` prefix older HQs read."""
    from clients import hubtools

    calls = []

    class Api:
        def post(self, path, body, key=None):
            calls.append((path, body))
            return {}

    monkeypatch.setitem(hubtools.support_file.__globals__, '_as_person', lambda api, ref=None: api)
    hubtools.support_file(Api(), {"message": "Sync fails"})
    assert calls == [("support/tickets", {"message": "[BotOps] Sync fails", "include_ids": True, "source": "botops"})]


def grants(api):
    """The OKs asked for reports (task_asks): (requirement id, its standing, used_at), oldest first."""
    from backend import task_asks

    with api.app.state.store.read() as c:
        rows = c.execute("SELECT requirement_id FROM ok_grants WHERE subject LIKE 'support:%' ORDER BY created_at")
        return [(r[0], task_asks.standing(c, r[0]), consumed(api, r[0])) for r in rows.fetchall()]


def test_with_the_team_rule_on_a_bots_report_is_sent_at_once_filed_as_the_default_person(environment, hq):  # noqa: F811
    from backend.store import H

    api = environment()
    bot = as_bot(api)
    assert api.get("/api/v2/access", headers=signed_in()).json()["rules"]["bot_reports_direct"] is True
    r = draft(api, bot)
    assert r.status_code == 200, r.text
    body = r.json()
    with api.app.state.store.read() as c:
        person = H.human_actor(H.default_human(c))
    # Filed as the install's default person, with no OK asked of anyone: nothing waits in Needs you.
    assert body["sent"] is True and body["status"] == "sent" and body["filed_as"] == person
    assert body["ticket"]["message"] == REPORT and grants(api) == []
    (request,) = hq.seen
    sent = json.loads(request.content)
    assert set(sent) == {"message", "version", "install_id", "source"} and sent["source"] == "bot_report"
    # The same report again is the same ticket, and nothing more reaches HQ.
    again = draft(api, bot)
    assert again.status_code == 200 and again.json()["ticket"]["id"] == body["ticket"]["id"]
    assert len(hq.seen) == 1 and len(hq.tickets) == 1
    # A report that does not match its hash is refused and sends nothing.
    bad = api.post(
        "/api/v2/support/reports", json={"payload": {"message": REPORT, "content_sha256": "0" * 64}}, headers=bot
    )
    assert bad.status_code >= 400 and "doesn't match" in bad.text and len(hq.seen) == 1


def test_a_copy_run_from_source_sends_no_bots_report_by_the_rule_until_it_names_an_hq(environment, hq, monkeypatch):  # noqa: F811
    from backend import task_asks

    # A checkout, a test or the factory simulation runs as "dev": with the rule on, its bot's report waits for a person.
    monkeypatch.setenv("TICO_VERSION", "dev")
    api = environment()
    bot = as_bot(api)
    waiting = draft(api, bot)
    assert waiting.status_code == 200, waiting.text
    assert waiting.json()["status"] == "pending" and "sent" not in waiting.json() and hq.seen == []
    assert grants(api) == [(waiting.json()["id"], task_asks.WAITING, None)]
    # TICO_HQ_URL set on purpose sends as a release does.
    monkeypatch.setenv("TICO_HQ_URL", "https://updates.tico.team")
    sent = draft(api, bot, message="A second report, once an HQ is named.")
    assert sent.status_code == 200 and sent.json()["sent"] is True and len(hq.tickets) == 1


def test_with_the_team_rule_off_a_bots_report_waits_for_a_yes_and_only_the_owner_turns_the_rule_on(environment, hq):  # noqa: F811
    from backend import task_asks

    api = environment()
    bot = as_bot(api)
    drafts_only(api)
    approval = draft(api, bot).json()
    assert approval["status"] == "pending" and hq.seen == []
    assert grants(api) == [(approval["id"], task_asks.WAITING, None)]
    assert api.put("/api/v2/access/rules", json={"bot_reports_direct": True}, headers=bot).status_code >= 400


def test_a_persons_token_may_decline_a_report_and_nothing_is_sent(environment, hq):  # noqa: F811
    from backend import task_asks

    api = environment()
    bot = as_bot(api)
    drafts_only(api)
    declined, waiting = draft(api, bot, message="Declined through a token").json(), draft(api, bot).json()
    with api.app.state.store.read() as c:
        approver = task_asks.grant(c, declined["id"])["asked_of_id"]
    me = api.get("/api/me", headers=signed_in()).json()
    api.app.state.store.settings.test_identities["approver-token"] = Identity(
        approver, me.get("role") or "owner", email=me.get("email") or "", via_token=True
    )
    _, sent_back = decide(api, declined["id"], "declined", token="approver-token")
    assert sent_back.status_code == 200, sent_back.text
    _, refused = decide(api, waiting["id"], token="approver-token")
    assert refused.status_code == 403, refused.text
    with api.app.state.store.read() as c:
        assert task_asks.standing(c, declined["id"]) == task_asks.SENT_BACK
        assert task_asks.standing(c, waiting["id"]) == task_asks.WAITING
    assert hq.seen == [] and not consumed(api, declined["id"]) and not consumed(api, waiting["id"])


def test_a_report_is_its_content_the_same_one_again_is_one_ticket(environment, hq):  # noqa: F811  (the fixture)
    """The route keeps no request keys: a report is known by who drafted it and its content. The same report sent
    again is the same ticket; another report is another."""
    api = environment()
    bot = as_bot(api)
    first = draft(api, bot)
    assert first.status_code == 200 and first.json()["sent"] is True, first.text
    again = draft(api, bot)
    assert again.status_code == 200 and again.json()["ticket"]["id"] == first.json()["ticket"]["id"]
    assert len(hq.seen) == 1
    other = draft(api, bot, message="Another report, with other words")
    assert other.status_code == 200 and other.json()["ticket"]["id"] != first.json()["ticket"]["id"]
    assert len(hq.seen) == 2 and len(hq.tickets) == 2


def _payload(message=REPORT):
    import hashlib

    return {"message": message, "content_sha256": hashlib.sha256(message.encode()).hexdigest()}


@pytest.mark.parametrize("ending", ["credential", "bot"])
def test_a_bot_whose_credential_or_self_has_ended_sends_nothing_by_the_rule(environment, hq, ending):  # noqa: F811  (the fixture)
    from backend import task_asks
    from backend.store import Problem

    api = environment()
    bot = as_bot(api)
    drafts_only(api)
    approval = draft(api, bot).json()
    assert api.put("/api/v2/access/rules", json={"bot_reports_direct": True}, headers=signed_in()).status_code == 200
    with api.app.state.store.transaction() as c:
        if ending == "credential":
            c.execute("UPDATE agents SET revoked_at=? WHERE bot=?", ("2026-01-02T00:00:00Z", "assistant"))
        else:
            c.execute("UPDATE bots SET state='paused' WHERE slug=?", ("assistant",))
    with pytest.raises(Problem) as ended:
        api.app.state.support.report(Identity("bot:assistant", "bot", agent="hermes"), _payload())
    assert ended.value.code == ("revoked" if ending == "credential" else "paused")
    assert hq.seen == [] and not consumed(api, approval["id"])
    assert grants(api) == [(approval["id"], task_asks.WAITING, None)]  # its earlier draft still waits for a person


def test_a_retry_after_the_owner_turns_direct_reports_off_gets_the_same_ticket(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    bot = as_bot(api)
    first = draft(api, bot)
    assert first.status_code == 200 and first.json()["sent"] is True, first.text
    drafts_only(api)  # the owner turns direct reports off
    again = draft(api, bot)
    assert again.status_code == 200, again.text
    assert again.json()["sent"] is True and again.json()["ticket"]["id"] == first.json()["ticket"]["id"]
    assert grants(api) == [] and len(hq.seen) == 1  # no person is asked to OK a report HQ already has


def test_a_retry_after_the_owner_turns_direct_reports_on_sends_it_once_and_the_waiting_ok_goes(environment, hq):  # noqa: F811  (the fixture)
    from backend import task_asks

    api = environment()
    bot = as_bot(api)
    drafts_only(api)
    drafted = draft(api, bot)
    assert drafted.status_code == 200 and drafted.json()["status"] == "pending" and "sent" not in drafted.json()
    assert hq.seen == []
    assert api.put("/api/v2/access/rules", json={"bot_reports_direct": True}, headers=signed_in()).status_code == 200
    again = draft(api, bot)
    assert again.status_code == 200 and again.json()["sent"] is True, again.text
    assert again.json()["ticket"]["message"] == REPORT and len(hq.seen) == 1
    # The OK it asked for before is not needed now: it leaves Needs you, and can never send the report a second time.
    assert grants(api) == [(drafted.json()["id"], task_asks.WITHDRAWN, None)]
    once_more = draft(api, bot)
    assert once_more.json()["ticket"]["id"] == again.json()["ticket"]["id"] and len(hq.seen) == 1


def test_a_send_that_fails_asks_nobody_and_a_retry_sends_it_once(environment, hq):  # noqa: F811  (the fixture)
    api = environment()
    bot = as_bot(api)
    hq.down = True
    failed = draft(api, bot)
    assert failed.status_code == 502 and failed.json()["error"]["retryable"] is True
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 0
    assert grants(api) == []
    hq.down = False
    again = draft(api, bot)
    assert again.status_code == 200 and again.json()["sent"] is True, again.text
    assert again.json()["ticket"]["message"] == REPORT and len(hq.tickets) == 1


def test_a_report_the_privacy_check_refuses_waits_for_a_person(environment, hq):  # noqa: F811  (the fixture)
    from backend import task_asks
    from backend.store import Problem

    api = environment()
    as_bot(api)

    def refuses(c):
        raise Problem("privacy", "This report may not leave the install from here.", 403)

    who = Identity("bot:assistant", "bot", agent="hermes")
    asked = api.app.state.support.report(who, _payload(), check=refuses)
    assert asked["status"] == "pending" and "sent" not in asked
    assert hq.seen == [] and grants(api) == [(asked["id"], task_asks.WAITING, None)]


def test_a_rule_send_leaves_no_unread_notice_for_the_default_person_and_needs_you_unchanged(environment, hq):  # noqa: F811  (the fixture)
    from backend.store import H

    api = environment()
    bot = as_bot(api)
    with api.app.state.store.read() as c:
        person = H.human_actor(H.default_human(c))
        unread_before = {m["id"] for m in H.inbox(c, person)["messages"]}
    needs_before = api.get("/api/v2/needs-you?count=1", headers=signed_in()).json()["count"]
    r = draft(api, bot)  # the team rule is on by default: the report is sent in this request
    assert r.status_code == 200 and r.json()["sent"] is True, r.text
    assert api.get("/api/v2/needs-you?count=1", headers=signed_in()).json()["count"] == needs_before
    assert grants(api) == []
    with api.app.state.store.read() as c:
        assert {m["id"] for m in H.inbox(c, person)["messages"]} == unread_before


def test_a_bots_ticket_is_not_in_the_persons_chat_and_its_replies_do_not_count(environment, hq, monkeypatch):  # noqa: F811  (the fixture)
    """A bot's report is not in the person's chat (support.OWN): it is not listed, its replies do not count toward
    `unread`, and it cannot be read, answered or deleted there. It is still filed, its replies are still pulled and kept,
    and a bot's follow-up (note) still reaches HQ. Covers a bot's report, one kept before `source` was stored (by its
    approval's key), and the Health Monitor's (`health:`). A key of the person's own that starts like a bot's does not
    hide the person's ticket."""
    monkeypatch.setattr(support, "FRESH_S", 0)
    api = environment()
    mine = file(api).json()["id"]
    bot = as_bot(api)
    filed = draft(api, bot).json()  # the team rule is on by default: sent at once
    assert filed["sent"] is True and filed["ticket"]["sent"]["source"] == "bot_report"
    bot_ticket = filed["ticket"]["id"]
    older_bot = draft(api, bot, message="The checkout button does nothing").json()["ticket"]["id"]
    health = file(api, message="Sync stalled since 0.3.47").json()["id"]
    spoof = file(api, headers={**signed_in(), "idempotency-key": "health:sig"}, message="My own report").json()["id"]
    with api.app.state.store.transaction() as c:  # kept before `source` was stored
        c.execute("UPDATE support_tickets SET sent='{}' WHERE id=?", (older_bot,))
        c.execute("UPDATE support_tickets SET request_key='health:sig', sent='{}' WHERE id=?", (health,))
    hq_ids = list(hq.tickets)  # HQ's ids, in filing order
    assert len(hq_ids) == 5
    hq.staff_replies(hq_ids[0], "Try a reload")  # mine
    hq.staff_replies(hq_ids[1], "The report is with the team")  # the bot's
    hq.staff_replies(hq_ids[2], "Fixed in 0.3.48")  # the older bot's
    hq.staff_replies(hq_ids[3], "Thanks, fixed")  # the Health Monitor's
    hq.staff_replies(hq_ids[4], "Got it")  # the person's own
    assert api.post("/api/v2/support/tickets/refresh", headers=signed_in()).status_code == 200
    listed = api.get("/api/v2/support/tickets", headers=signed_in()).json()
    assert {t["id"] for t in listed["tickets"]} == {mine, spoof}
    assert listed["unread"] == 2  # only the replies on the person's own tickets
    with api.app.state.store.read() as c:  # the bots' tickets are pulled and kept, not dropped
        kept = {r["id"]: r["messages"] for r in c.execute("SELECT id, messages FROM support_tickets")}
    assert "The report is with the team" in kept[bot_ticket] and "Fixed in 0.3.48" in kept[older_bot]
    assert "Thanks, fixed" in kept[health]
    for hidden in (bot_ticket, older_bot, health):
        assert api.post(f"/api/v2/support/tickets/{hidden}/read", headers=signed_in()).status_code == 404
        assert (
            api.post(
                f"/api/v2/support/tickets/{hidden}/messages", json={"message": "Hello"}, headers=signed_in()
            ).status_code
            == 404
        )
        assert api.delete(f"/api/v2/support/tickets/{hidden}", headers=signed_in()).status_code == 404
    assert len(hq.tickets) == 5  # nothing was deleted at HQ
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM support_tickets WHERE id=?", (bot_ticket,)).fetchone()
    api.app.state.support.note(row, "[Health Monitor] Seen again (2 times)")  # the bot's follow-up still reaches HQ
    assert json.loads(hq.seen[-1].content) == {"message": "[Health Monitor] Seen again (2 times)"}


def test_bots_tickets_do_not_crowd_the_persons_own_out_of_the_list(environment, hq, monkeypatch):  # noqa: F811  (the fixture)
    """The chat's filter runs before the limit: three newer bot tickets do not push the person's own out of a list of two."""
    monkeypatch.setattr(support, "LISTED", 2)
    api = environment()
    mine = file(api).json()["id"]
    bot = as_bot(api)
    for n in range(3):
        assert draft(api, bot, message=f"Report {n}").json()["sent"] is True
    listed = api.get("/api/v2/support/tickets", headers=signed_in()).json()
    assert [t["id"] for t in listed["tickets"]] == [mine]
    assert listed["unread"] == 0


def test_the_persons_own_tickets_refresh_ahead_of_newer_bot_tickets(environment, hq, monkeypatch):  # noqa: F811  (the fixture)
    """The refresh's limit cuts the bots' tickets first: three newer bot tickets do not stop HQ's reply on the person's own
    ticket from being pulled."""
    monkeypatch.setattr(support, "LISTED", 2)
    monkeypatch.setattr(support, "FRESH_S", 0)
    api = environment()
    mine = file(api).json()["id"]
    bot = as_bot(api)
    for n in range(3):
        assert draft(api, bot, message=f"Report {n}").json()["sent"] is True
    with api.app.state.store.read() as c:
        hq_id = c.execute("SELECT hq_id FROM support_tickets WHERE id=?", (mine,)).fetchone()[0]
    hq.staff_replies(hq_id, "We have a fix.")
    refreshed = api.post("/api/v2/support/tickets/refresh", json={}, headers=signed_in()).json()
    assert [t["id"] for t in refreshed["tickets"]] == [mine] and refreshed["unread"] == 1


def test_the_ticket_in_view_is_asked_about_whatever_its_place_under_the_limit(environment, hq, monkeypatch):  # noqa: F811  (the fixture)
    """The ticket a person has open is refreshed even when two newer tickets of theirs fill the limit."""
    monkeypatch.setattr(support, "LISTED", 2)
    monkeypatch.setattr(support, "FRESH_S", 0)
    api = environment()
    old = file(api, message="The first question").json()["id"]
    file(api, message="A second question")
    file(api, message="A third question")
    with api.app.state.store.read() as c:
        hq_id = c.execute("SELECT hq_id FROM support_tickets WHERE id=?", (old,)).fetchone()[0]
    hq.staff_replies(hq_id, "Here is the answer.")
    api.post("/api/v2/support/tickets/refresh", json={"ticket": old}, headers=signed_in())
    with api.app.state.store.read() as c:
        messages = c.execute("SELECT messages FROM support_tickets WHERE id=?", (old,)).fetchone()[0]
    assert "Here is the answer." in messages


def test_a_retry_across_the_upgrade_finds_the_ticket_filed_under_the_bare_key(environment, hq):  # noqa: F811  (the fixture)
    """A ticket filed before the person's keys were kept as `idem:<key>` is the one a retry with the same key gets back."""
    api = environment()
    headers = {**signed_in(), "Idempotency-Key": "retry-7"}
    first = file(api, headers=headers).json()["id"]
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE support_tickets SET request_key='retry-7' WHERE id=?", (first,)
        )  # as an older release kept it
    sent = len(hq.seen)
    again = file(api, headers=headers)
    assert again.status_code in (200, 201) and again.json()["id"] == first, again.text
    assert len(hq.seen) == sent  # nothing new reached HQ
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 1
