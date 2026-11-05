"""Who may use which mailbox (backend/mailbox_access.py), the one check every mail call goes through.

The fixture's team (backend/tests/test_api.py): ana is the owner, ben an admin, cara a member, all at acme.example.
"""

import pytest

from backend import mailbox_access as MA
from backend.auth import Identity
from backend.store import H, Problem
from backend.tests.test_api import api, get, headers  # noqa: F401

ANA, BEN, CARA = "ana@acme.example", "ben@acme.example", "cara@acme.example"
SUPPORT = "support@acme.example"
OUTSIDE = "team@partner.example"
ASSISTANT = Identity("human:ana", "owner", ANA, via_token=True, via="assistant", task_actor="bot:coo")


def tx(api):
    return api.app.state.store.transaction()


def bot(c, slug):
    return H.resolve_bot(c, slug)["id"]


def shared(c, address=SUPPORT):
    return MA.ensure_mailbox(c, address, kind="shared", created_by="human:ana")


def personal(c, address, person=None):
    return MA.ensure_mailbox(c, address, person_id=person, created_by="human:ana")


def awaiting(c, address="dana@acme.example"):
    row = personal(c, address)
    assert MA.awaiting(row)
    return row


def levels(c, who):
    return MA.levels_for(c, who)


def refused(fn, *args, code=None, status=None):
    with pytest.raises(Problem) as e:
        fn(*args)
    if code:
        assert e.value.code == code, e.value.detail
    if status:
        assert e.value.status == status, e.value.detail
    return e.value


# ------------------------------------------------------------------------------------------- a person's own mailbox
def test_a_person_has_read_and_write_on_their_own_mailbox_before_and_after_it_has_a_row(api):
    with tx(api) as c:
        assert MA.level(c, "person:cara", CARA) == "read_write"
        assert levels(c, "person:cara") == {CARA: "read_write"}
        row = personal(c, CARA)
        assert row["person_id"] == "cara" and row["kind"] == "personal"          # linked by the roster email
        assert MA.level(c, "person:cara", CARA) == "read_write"
        assert MA.mailboxes_for(c, "person:cara")[0]["id"] == row["id"]


def test_the_owner_admins_and_managers_get_nothing_on_someone_elses_mailbox(api):
    with tx(api) as c:
        personal(c, CARA)
        for who in ("person:ana", "person:ben", "bot:coo", "bot:inbox"):
            assert MA.level(c, who, CARA) == "none", who
            assert CARA not in levels(c, who), who
        assert MA.may(c, "person:ana", CARA) == {"read": False, "organize": False, "send": False,
                                                  "change_calendar": False, "draft": False}


def test_a_manager_gets_no_automatic_view_of_a_reports_mail(api):
    from backend.store import encode
    with tx(api) as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode({"people": [
            {"id": "ana", "email": ANA, "primary_for": ["*"]},
            {"id": "ben", "email": BEN, "reports_to": "ana"},
            {"id": "cara", "email": CARA, "reports_to": "ben"}]}),))
    with tx(api) as c:
        assert MA.level(c, "person:ben", CARA) == "none"
        assert MA.level(c, "person:ana", BEN) == "none"


def test_someone_who_left_holds_nothing(api):
    with tx(api) as c:
        assert MA.level(c, "person:nobody", "nobody@acme.example") == "none"
        assert MA.level(c, "person:", CARA) == "none"
        assert MA.level(c, None, CARA) == "none"
        assert MA.level(c, "runner:r1", CARA) == "none"


# ---------------------------------------------------------------------------------------------------- shared inbox
def test_admins_have_read_and_write_on_a_shared_inbox_and_members_need_a_grant(api):
    with tx(api) as c:
        row = shared(c)
        assert row["kind"] == "shared" and row["person_id"] is None
        assert MA.level(c, "person:ana", SUPPORT) == "read_write"              # the owner is an admin
        assert MA.level(c, "person:ben", SUPPORT) == "read_write"
        assert MA.level(c, "person:cara", SUPPORT) == "none"
        assert SUPPORT in levels(c, "person:ben")
        MA.grant(c, "person:ben", row["id"], "person", "cara", "read")
        assert MA.level(c, "person:cara", SUPPORT) == "read"


def test_a_shared_inbox_is_shared_only_by_an_admin_and_never_a_linked_mailbox(api):
    with tx(api) as c:
        row = awaiting(c)
        refused(MA.make_shared, c, "person:cara", row["id"], status=403)
        refused(MA.make_shared, c, ASSISTANT, row["id"], status=403)
        made = MA.make_shared(c, "person:ben", row["id"])
        assert made["kind"] == "shared" and made["shared_by"] == "person:ben"
        assert MA.level(c, "person:ben", made["address"]) == "read_write"
        linked = personal(c, CARA)
        refused(MA.make_shared, c, "person:ana", linked["id"], code="linked", status=409)


# ---------------------------------------------------------------------------------------------- awaiting its person
def test_an_awaiting_mailbox_is_private_until_its_person_joins(api):
    with tx(api) as c:
        row = awaiting(c)
        for who in ("person:ana", "person:ben", "person:cara", "bot:coo"):
            assert MA.level(c, who, row["address"]) == "none", who
        # A grant written on it anyway (an older row, a hand edit) counts for nothing.
        MA.give(c, row, "bot", bot(c, "coo"), "read_write", "test")
        MA.give(c, row, "person", "cara", "read_write", "test")
        assert MA.level(c, "bot:coo", row["address"]) == "none"
        assert MA.level(c, "person:cara", row["address"]) == "none"
        assert row["address"] not in levels(c, "bot:coo")
        refused(MA.grant, c, "person:ana", row["id"], "person", "ben", "read", code="awaiting_person", status=409)


def test_joining_links_an_awaiting_mailbox_and_never_makes_one(api):
    with tx(api) as c:
        assert MA.link_person(c, "cara", CARA) is None                          # no row: joining connects nothing
        assert MA._row(c, CARA) is None
        row = awaiting(c)
        linked = MA.link_person(c, "human:dana", "Dana@Acme.example")
        assert linked["person_id"] == "dana" and linked["id"] == row["id"]
        assert MA.link_person(c, "eve", "dana@acme.example") is None             # already someone's
        assert MA._row(c, "dana@acme.example")["person_id"] == "dana"
        s = shared(c)
        assert MA.link_person(c, "sam", SUPPORT) is None and MA.row_by_id(c, s["id"])["kind"] == "shared"


def test_adding_a_person_links_their_awaiting_mailbox(api):
    with tx(api) as c:
        awaiting(c, "eve@acme.example")
    r = api.post("/api/v2/access/people", json={"email": "eve@acme.example", "name": "Eve"}, headers=headers())
    assert r.status_code == 200, r.text
    with tx(api) as c:
        row = MA._row(c, "eve@acme.example")
        assert row["person_id"] == r.json()["person"]
        assert MA.level(c, "person:" + row["person_id"], "eve@acme.example") == "read_write"


# ---------------------------------------------------------------------------------------------- explicit grants
def test_a_person_grants_and_revokes_a_bot_on_their_own_mailbox(api):
    with tx(api) as c:
        row = personal(c, CARA)
        coo = bot(c, "coo")
        access = MA.grant(c, "person:cara", row["id"], "bot", "coo", "read")
        assert access["grantee_id"] == coo and access["level"] == "read"
        assert MA.level(c, "bot:coo", CARA) == "read"
        assert MA.may(c, "bot:coo", CARA)["organize"] is False
        again = MA.grant(c, "person:cara", row["id"], "bot", coo, "read_write")
        assert again["id"] == access["id"] and again["level"] == "read_write"
        assert MA.level(c, "bot:coo", CARA) == "read_write"
        assert MA.may(c, "bot:coo", CARA) == {"read": True, "organize": True, "send": True, "change_calendar": True,
                                              "draft": True}
        MA.revoke(c, "person:cara", row["id"], access["id"])
        assert MA.level(c, "bot:coo", CARA) == "none"
        refused(MA.revoke, c, "person:cara", row["id"], access["id"], code="not_found", status=404)
        logged = [r["action"] for r in c.execute("SELECT action FROM events WHERE target=? ORDER BY rowid", (row["id"],))]
        assert logged == ["mailbox.access_granted", "mailbox.access_granted", "mailbox.access_revoked"]


def test_a_grant_is_checked(api):
    with tx(api) as c:
        row = personal(c, CARA)
        refused(MA.grant, c, "person:cara", row["id"], "team", "x", "read", code="invalid")
        refused(MA.grant, c, "person:cara", row["id"], "person", "ben", "admin", code="invalid")
        refused(MA.grant, c, "person:cara", row["id"], "person", "nobody", "read", code="not_found")
        refused(MA.grant, c, "person:cara", row["id"], "bot", "no-such-bot", "read", code="not_found")
        refused(MA.grant, c, "person:cara", row["id"], "person", "cara", "read", code="invalid")      # their own
        refused(MA.grant, c, "person:cara", "missing", "person", "ben", "read", code="not_found", status=404)


def test_an_archived_bot_holds_nothing(api):
    with tx(api) as c:
        row = personal(c, CARA)
        MA.grant(c, "person:cara", row["id"], "bot", "coo", "read_write")
        c.execute("UPDATE bots SET state='archived' WHERE id=?", (bot(c, "coo"),))
        assert MA.level(c, "bot:coo", CARA) == "none"


def test_give_never_lowers_a_level(api):
    with tx(api) as c:
        row = personal(c, CARA)
        coo = bot(c, "coo")
        first = MA.give(c, row, "bot", coo, "read_write", "test")
        assert MA.give(c, row, "bot", coo, "read", "test") == first
        assert MA.level(c, "bot:coo", CARA) == "read_write"
        with pytest.raises(ValueError):
            MA.give(c, row, "bot", coo, "admin", "test")


# ---------------------------------------------------------------------------------------------------- no reshare
def test_nobody_reshares(api):
    with tx(api) as c:
        row = personal(c, CARA)
        MA.grant(c, "person:cara", row["id"], "person", "ben", "read_write")
        # ben holds Read and write on cara's mailbox, and is an admin: he still may not pass it on.
        refused(MA.grant, c, "person:ben", row["id"], "bot", "coo", "read", status=403)
        refused(MA.grant, c, "person:ana", row["id"], "bot", "coo", "read", status=403)     # nor may the owner
        refused(MA.grant, c, "bot:coo", row["id"], "bot", "coo", "read", status=403)        # nor a bot
        access = c.execute("SELECT id FROM mailbox_accesses WHERE mailbox_id=?", (row["id"],)).fetchone()["id"]
        refused(MA.revoke, c, "person:ben", row["id"], access, status=403)
        s = shared(c)
        refused(MA.grant, c, "person:cara", s["id"], "bot", "coo", "read", status=403)     # a member, on a shared one
        MA.grant(c, "person:ben", s["id"], "person", "cara", "read_write")
        refused(MA.grant, c, "person:cara", s["id"], "bot", "coo", "read", status=403)     # even holding it


def test_the_assistant_may_not_share_or_stop_a_mailbox(api):
    with tx(api) as c:
        row = personal(c, ANA)
        refused(MA.grant, c, ASSISTANT, row["id"], "bot", "coo", "read", status=403)
        refused(MA.stop, c, ASSISTANT, row["id"], status=403)
        MA.grant(c, "person:ana", row["id"], "bot", "coo", "read")
        access = c.execute("SELECT id FROM mailbox_accesses WHERE mailbox_id=?", (row["id"],)).fetchone()["id"]
        refused(MA.revoke, c, ASSISTANT, row["id"], access, status=403)


# ------------------------------------------------------------------------------------------- the Assistant is them
def test_the_assistant_holds_exactly_its_persons_access(api):
    with tx(api) as c:
        s = shared(c)
        row = personal(c, CARA)
        MA.grant(c, "person:cara", row["id"], "person", "ana", "read")
        assert MA.principal(c, ASSISTANT) == MA.Principal("person", "ana")
        assert levels(c, ASSISTANT) == levels(c, "person:ana") == {ANA: "read_write", CARA: "read",
                                                                   s["address"]: "read_write"}
        # Not the assistant bot's own grants.
        assert levels(c, "bot:coo") == {}


def test_principals_from_identities(api):
    with tx(api) as c:
        coo = bot(c, "coo")
        assert MA.principal(c, Identity("human:cara", "human", CARA)) == MA.Principal("person", "cara")
        assert MA.principal(c, Identity("bot:" + coo, "bot")) == MA.Principal("bot", coo)
        assert MA.principal(c, "bot:coo") == MA.Principal("bot", coo)                     # by slug too
        assert MA.principal(c, Identity("runner:r1", "runner", runner_id="r1")) is MA.NOBODY
        assert MA.principal(c, Identity("service:k1", "service")) is MA.NOBODY
        assert MA.principal(c, "bot:no-such-bot") is MA.NOBODY


# ------------------------------------------------------------------------------------------------ outside the domain
def test_outside_the_company_domains_read_and_write_is_read(api):
    with tx(api) as c:
        assert MA.company_domains(c) == ["acme.example"]
        out = shared(c, OUTSIDE)
        assert MA.level(c, "person:ben", OUTSIDE) == "read"
        MA.grant(c, "person:ben", out["id"], "bot", "coo", "read_write")
        assert MA.level(c, "bot:coo", OUTSIDE) == "read"
        assert MA.may(c, "bot:coo", OUTSIDE)["send"] is False
        assert MA.inside("x@acme.example", []) is True                                    # nothing known: no cap
        assert MA.inside("x@partner.example", ["acme.example"]) is False


def test_allowed_domains_are_the_company_domains(api):
    from backend import access as A
    with tx(api) as c:
        stored = A._load_json(c, A.ACCESS) or {}
        A._store(c, A.ACCESS, {**stored, "allowed_domains": ["acme.example", "partner.example"]})
    with tx(api) as c:
        assert set(MA.company_domains(c)) == {"acme.example", "partner.example"}
        shared(c, OUTSIDE)
        assert MA.level(c, "person:ben", OUTSIDE) == "read_write"


# ---------------------------------------------------------------------------------------------------- company cap
def test_what_tico_may_do_caps_every_level(api):
    with tx(api) as c:
        row = personal(c, CARA)
        MA.grant(c, "person:cara", row["id"], "bot", "coo", "read_write")
    r = api.put("/api/v2/google/permissions", json={"organize_mail": False, "send_mail": True, "change_calendar": False},
                headers=headers())
    assert r.status_code == 200, r.text
    with tx(api) as c:
        assert MA.level(c, "bot:coo", CARA) == "read_write"                    # the level is kept
        assert MA.may(c, "bot:coo", CARA) == {"read": True, "organize": False, "send": True, "change_calendar": False,
                                              "draft": True}
        assert MA.may(c, "person:cara", CARA)["organize"] is False              # the person too
    r = api.put("/api/v2/google/permissions", json={"organize_mail": False, "send_mail": False, "change_calendar": False},
                headers=headers())
    assert r.status_code == 200, r.text
    with tx(api) as c:
        assert MA.may(c, "bot:coo", CARA) == {"read": True, "organize": False, "send": False, "change_calendar": False,
                                              "draft": False}


# ----------------------------------------------------------------------------------------------------- stopping
def test_a_stopped_mailbox_is_used_by_nobody_and_stopped_only_by_its_person_or_an_admin(api):
    with tx(api) as c:
        row = personal(c, CARA)
        MA.grant(c, "person:cara", row["id"], "bot", "coo", "read_write")
        refused(MA.stop, c, "bot:coo", row["id"], status=403)
        refused(MA.stop, c, "person:eve", row["id"], status=403)
        assert MA.stop(c, "person:cara", row["id"])["status"] == "stopped"
        assert MA.level(c, "person:cara", CARA) == "none"
        assert MA.level(c, "bot:coo", CARA) == "none"
        assert CARA in MA.stopped(c)
        refused(MA.grant, c, "person:cara", row["id"], "person", "ben", "read", code="stopped", status=409)
        s = shared(c)
        refused(MA.stop, c, "person:cara", s["id"], status=403)
        assert MA.stop(c, "person:ben", s["id"])["status"] == "stopped"


# ---------------------------------------------------------------------------------------------------- the connector
def test_the_connector_reads_its_targets_and_never_writes_here(api):
    with tx(api) as c:
        personal(c, CARA)
        p = MA.connector([{"address": "Cara@Acme.example"}, "ana@acme.example", {"address": ""}])
        assert p.targets == frozenset({CARA, ANA})
        assert MA.level(c, p, CARA) == "read"
        assert MA.level(c, p, BEN) == "none"
        assert MA.may(c, p, CARA)["organize"] is False
        assert {box["address"]: box["level"] for box in MA.mailboxes_for(c, p)} == {CARA: "read", ANA: "read"}


# ---------------------------------------------------------------------------------------------- automation actions
def test_an_automation_action_needs_its_owner_s_level_now(api):
    with tx(api) as c:
        row = personal(c, CARA)
        assert MA.action_refusal(c, "person:cara", CARA, "archive") is None
        assert MA.action_refusal(c, "person:cara", CARA, "send") is None
        coo = bot(c, "coo")
        assert "Settings > Mailboxes" in MA.action_refusal(c, "bot:" + coo, CARA, "label")
        MA.grant(c, "person:cara", row["id"], "bot", coo, "read")
        assert MA.action_refusal(c, "bot:" + coo, CARA, "archive")
        MA.grant(c, "person:cara", row["id"], "bot", coo, "read_write")
        assert MA.action_refusal(c, "bot:" + coo, CARA, "archive") is None
        assert MA.action_refusal(c, "person:ana", CARA, "draft")                # no owner bypass
    api.put("/api/v2/google/permissions", json={"organize_mail": True, "send_mail": False, "change_calendar": True},
            headers=headers())
    with tx(api) as c:
        assert MA.action_refusal(c, "person:cara", CARA, "send")                # Send mail off
        assert MA.action_refusal(c, "person:cara", CARA, "send", queued=True) is None     # becomes a draft at claim
        assert MA.action_refusal(c, "person:cara", CARA, "forward", {"draft_only": True}) is None
        assert MA.action_refusal(c, "person:cara", CARA, "something_new")       # unknown: needs what a send needs


# ------------------------------------------------------------------------------------------- the message bot
def test_setting_a_message_bot_grants_it_and_moving_it_takes_the_old_grant(api):
    with tx(api) as c:
        coo, ops = bot(c, "coo"), bot(c, "ops")
        cara = {"id": "cara", "email": CARA}
        row = MA.message_bot_changed(c, cara, None, "", coo)
        assert row["address"] == CARA and row["person_id"] == "cara"
        assert MA.level(c, "bot:coo", CARA) == "read"                           # no gmail entry asks to draft
        MA.message_bot_changed(c, cara, coo, CARA, ops)
        assert MA.level(c, "bot:coo", CARA) == "none"
        assert MA.level(c, "bot:ops", CARA) == "read"
        MA.message_bot_changed(c, cara, ops, CARA, None)
        assert MA.level(c, "bot:ops", CARA) == "none"


# ---------------------------------------------------------------------------------------------- the API's answer
def test_mail_access_answers_the_caller(api):
    with tx(api) as c:
        s = shared(c)
        row = personal(c, CARA)
        MA.grant(c, "person:cara", row["id"], "person", "ben", "read")
    ben = get(api, "mail/access", token="ben-test")
    assert ben["principal"] == "person:ben"
    assert [(b["address"], b["level"]) for b in ben["mailboxes"]] == [
        (BEN, "read_write"), (CARA, "read"), (s["address"], "read_write")]
    assert set(ben["company"]) == {"organize_mail", "send_mail", "change_calendar"}
    one = api.get("/api/v2/mail/access", params={"mailbox": CARA}, headers=headers("ben-test")).json()
    assert one["mailbox"] == CARA and one["level"] == "read" and one["may"]["organize"] is False
    ana = api.get("/api/v2/mail/access", params={"mailbox": CARA}, headers=headers("ana-test")).json()
    assert ana["level"] == "none" and CARA not in [b["address"] for b in ana["mailboxes"]]


def test_a_database_before_the_migration_answers_only_a_persons_own_email(api):
    with tx(api) as c:
        c.execute("DROP TABLE mailbox_accesses")
        c.execute("DROP TABLE mailboxes")
        assert levels(c, "person:cara") == {CARA: "read_write"}
        assert levels(c, "bot:coo") == {}
        assert MA.stopped(c) == set()
        assert MA.backfill(c) == {"mailboxes": 0, "accesses": 0}


# ------------------------------------------------------------------------------- a message bot made, not assigned
def test_making_a_message_bot_links_only_a_mailbox_its_maker_may_grant(api):
    """Settings' add from template, a hire, BotOps (backend/message_bots.py link): the link gives the bot a grant, so a
    member gets it only on their own email, and an admin on any."""
    from backend import bot_settings as BS
    from backend import message_bots
    with tx(api) as c:
        for slug in ("ops", "doc-updater", "finance"):
            BS.write(c, slug, {"name": slug, "template": "inbox"})
        assert message_bots.link(c, "human:cara", "ops", "human:cara", "Mailbox: ana@acme.example\n") is None
        assert message_bots.link(c, "human:cara", "ops", "human:cara", "Mailbox: support@acme.example\n") is None
        assert MA.level(c, "bot:ops", ANA) == "none" and MA._row(c, SUPPORT) is None
        assert message_bots.link(c, "bot:coo", "ops", "bot:coo", "Mailbox: cara@acme.example\n") is None
        assert message_bots.link(c, "human:cara", "ops", "human:cara", "Mailbox: cara@acme.example\n") == "cara"
        assert MA.level(c, "bot:ops", CARA) == "read_write"                    # the template's read and draft
        assert message_bots.link(c, "human:ben", "doc-updater", "human:ben", "Mailbox: support@acme.example\n") == "ben"
        assert MA._row(c, SUPPORT)["kind"] == "shared"
        assert MA.level(c, "bot:doc-updater", SUPPORT) != "none"
