"""Migration 0082's backfill (backend/mailbox_access.py `backfill`): every access people and bots have today is written
as mailboxes and grants, a second run writes nothing, and a company with no inbox bots (TIDY) upgrades to almost
nothing. A manager's automatic view of their reports' mail is the one thing dropped."""

import importlib
import json

from backend import bot_settings as BS
from backend import mailbox_access as MA
from backend.store import H, encode
from backend.tests.test_api import api  # noqa: F401

ANA, BEN, CARA = "ana@acme.example", "ben@acme.example", "cara@acme.example"
SUPPORT = "support@acme.example"
UP = importlib.import_module("backend.migrations.0082_mailboxes").up


def tx(api):
    return api.app.state.store.transaction()


def gmail(identity, *can, **extra):
    return {"service": "gmail", "identity": identity, "can": list(can), **extra}


def config(c, slug, *tools):
    stored = MA._bot_config(c, slug)
    BS.write(c, slug, {**stored, "tools": list(tools)})


def roster(c, people):
    c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode({"people": people}),))


def automation(c, owner, flow):
    now = H.now()
    aid = H.new_id()
    c.execute("INSERT INTO automations(id,name,owner_id,trigger_type,enabled,flow,version,created_at,created_by_id,"
              "updated_at) VALUES(?,?,?,?,1,?,1,?,?,?)",
              (aid, "Triage", owner, (flow.get("trigger") or {}).get("type") or "manual",
               json.dumps({"owner": owner, **flow}), now, owner, now))
    return aid


def rows(c):
    return {r["address"]: (r["kind"], r["person_id"], r["connection"], r["created_by"])
            for r in c.execute("SELECT * FROM mailboxes")}


def grants(c):
    return {(r["address"], r["grantee_type"], r["grantee_id"]): (r["level"], r["granted_by"])
            for r in c.execute("SELECT m.address, a.* FROM mailbox_accesses a JOIN mailboxes m ON m.id=a.mailbox_id")}


def team(c):
    """A Tico Team-like company: ben's inbox bot reads his mail and may send; cara's reads the support@ inbox and only
    reads; finance declares ana's mailbox with org_read; chris-like ben runs a triage automation on his own email."""
    roster(c, [{"id": "ana", "email": ANA, "primary_for": ["*"]},
               {"id": "ben", "email": BEN, "reports_to": "ana", "inbox_bot": "inbox"},
               {"id": "cara", "email": CARA, "reports_to": "ben", "inbox_bot": "ops"},
               {"id": "gone", "email": "gone@acme.example", "hidden": True, "inbox_bot": "cpo"}])
    config(c, "inbox", gmail(BEN, "read", "draft", "send"))
    config(c, "ops", gmail(SUPPORT, "read"))
    config(c, "finance", gmail(ANA, "read", "draft", org_read=True))
    config(c, "cpo", gmail("gone@acme.example", "read", "draft"))
    automation(c, "person:ben", {"trigger": {"type": "message", "inbox": BEN}, "steps": []})


def test_a_tico_team_like_company_keeps_every_access_it_has(api):
    with tx(api) as c:
        team(c)
        made = MA.backfill(c)
        assert rows(c) == {
            BEN: ("personal", "ben", "workspace", "migration"),
            SUPPORT: ("shared", None, "workspace", "migration"),
            ANA: ("personal", "ana", "workspace", "migration"),
            CARA: ("personal", "cara", "workspace", "migration"),            # finance's org_read reaches cara's
            "gone@acme.example": ("personal", "gone", "workspace", "migration"),   # cpo's own gmail entry
        }
        got = grants(c)
        assert got[(BEN, "bot", "inbox")] == ("read_write", "migration")      # it may draft and send
        assert got[(SUPPORT, "bot", "ops")] == ("read", "migration")          # can: [read] only reads now
        assert got[(SUPPORT, "person", "cara")] == ("read_write", "migration")   # the shared inbox she had a bot on
        assert got[(ANA, "bot", "finance")] == ("read_write", "migration")
        # org_read: read on the mailboxes below ana (ben's, and cara's message-bot mailbox, support@).
        assert got[(BEN, "bot", "finance")] == ("read", "migration")
        assert got[(SUPPORT, "bot", "finance")] == ("read", "migration")
        assert not any(kind == "person" and address == BEN for address, kind, _ in got)   # ben's own: implicit
        assert made == {"mailboxes": len(rows(c)), "accesses": len(got)}
        # What that gives each, through the one check.
        assert MA.level(c, "bot:inbox", BEN) == "read_write"
        assert MA.may(c, "bot:inbox", BEN)["send"] is True
        assert MA.level(c, "bot:ops", SUPPORT) == "read"
        assert MA.level(c, "person:cara", SUPPORT) == "read_write"
        assert MA.level(c, "person:ben", BEN) == "read_write"
        # ben's live triage automation on his own mailbox goes on doing what it does.
        for action in ("archive", "label", "draft", "send"):
            assert MA.action_refusal(c, "person:ben", BEN, action) is None, action
        # Managers lose their automatic view of reports' mail.
        assert MA.level(c, "person:ana", BEN) == "none"
        assert MA.level(c, "person:ben", CARA) == "none"


def test_a_second_run_writes_nothing_and_changes_nothing_people_changed(api):
    with tx(api) as c:
        team(c)
        MA.backfill(c)
        before_rows, before = rows(c), grants(c)
        # A person changed access after the upgrade: a revoked grant stays revoked only if the source is gone, and a
        # raised level is never lowered back.
        support = MA._row(c, SUPPORT)
        c.execute("UPDATE mailbox_accesses SET level='read_write', granted_by='person:ana' "
                  "WHERE mailbox_id=? AND grantee_id='ops'", (support["id"],))
        assert MA.backfill(c) == {"mailboxes": 0, "accesses": 0}
        assert rows(c) == before_rows
        after = grants(c)
        assert after[(SUPPORT, "bot", "ops")] == ("read_write", "person:ana")
        assert {k: v for k, v in after.items() if k != (SUPPORT, "bot", "ops")} == \
            {k: v for k, v in before.items() if k != (SUPPORT, "bot", "ops")}


def test_the_migration_itself_is_idempotent(api):
    with tx(api) as c:
        team(c)
        UP(c)
        once = (rows(c), grants(c))
        UP(c)
        assert (rows(c), grants(c)) == once


def test_a_company_with_no_inbox_bots_upgrades_to_almost_nothing(api):
    """TIDY: people sign in, nobody has an inbox bot or a gmail entry; a person's automation on their own email."""
    with tx(api) as c:
        roster(c, [{"id": "ana", "email": ANA, "primary_for": ["*"]}, {"id": "ben", "email": BEN}])
        for slug in ("coo", "ops", "cpo", "product-design", "finance", "inbox", "doc-updater"):
            config(c, slug)
        automation(c, "person:ana", {"trigger": {"type": "message", "inbox": ANA}, "steps": []})
        assert MA.backfill(c) == {"mailboxes": 0, "accesses": 0}
        assert MA.level(c, "person:ana", ANA) == "read_write"                  # her own, with no row
        assert MA.action_refusal(c, "person:ana", ANA, "archive") is None


def test_mailboxes_the_server_keeps_and_connect_google_grants_get_rows(api):
    with tx(api) as c:
        c.execute("INSERT INTO mail_mailboxes(address, person_id) VALUES(?, ?)", ("Old@Acme.example", None))
        c.execute("INSERT INTO mail_mailboxes(address, person_id) VALUES(?, ?)", (ANA, "ana"))
        now = H.now()
        cols = MA._columns(c, "google_connects")
        extra = ", grant_hash" if "grant_hash" in cols else ""
        values = ", 'h'" if extra else ""
        c.execute(f"INSERT INTO google_connects(person_id, mailbox, status, created_at, updated_at{extra}) "
                  f"VALUES('cara', ?, 'connected', ?, ?{values})", (CARA, now, now))
        c.execute(f"INSERT INTO google_connects(person_id, mailbox, status, created_at, updated_at{extra}) "
                  f"VALUES('ben', ?, 'disconnected', ?, ?{values})", (BEN, now, now))
        MA.backfill(c)
        got = rows(c)
        assert got["old@acme.example"][0] == "shared"                          # nobody's own address
        assert got[ANA][:2] == ("personal", "ana")
        assert got[CARA][:3] == ("personal", "cara", "personal")              # her own Connect Google grant
        assert BEN not in got                                                 # a disconnected grant is not one
        assert grants(c) == {}


def test_a_bot_s_mail_automation_keeps_the_mailbox_it_heard(api):
    with tx(api) as c:
        roster(c, [{"id": "ana", "email": ANA, "primary_for": ["*"]}, {"id": "ben", "email": BEN},
                   {"id": "cara", "email": CARA}])
        coo = H.resolve_bot(c, "coo")["id"]
        c.execute("UPDATE bot_config SET owner_id='ana' WHERE bot=?", (coo,))
        automation(c, "bot:" + coo, {"trigger": {"type": "message", "inbox": ANA}, "steps": []})
        automation(c, "bot:" + coo, {"trigger": {"type": "message", "inbox": CARA}, "steps": []})   # never heard
        automation(c, "person:ben", {"trigger": {"type": "message", "inbox": CARA}, "steps": []})   # never heard
        MA.backfill(c)
        got = grants(c)
        assert got == {(ANA, "bot", coo): ("read_write", "migration")}
        assert MA.action_refusal(c, "bot:" + coo, ANA, "archive") is None
        assert MA.level(c, "person:ben", CARA) == "none"


def test_a_new_database_has_nothing_to_backfill(api):
    with tx(api) as c:
        assert MA.backfill(c) == {"mailboxes": 0, "accesses": 0}
        assert rows(c) == {} and grants(c) == {}
