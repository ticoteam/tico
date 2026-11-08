"""Deleting a person (backend/entities/erase.py) on the API fixture's team: ana is the owner, ben an Admin (bot_admins in the
fixture), cara a member. What these pin: the erase leaves no row that names the person in any table of the database (the
test scans every column of every table, so a table added later is covered too); the full-text row goes; evidence only the
person rested on goes and evidence another entity cites stays; an erased identifier is not created again by upsert, and the
suppression keeps only a salted hash; only the owner or an Admin may erase, a bot or a member may not, self is never erased,
and the audit row keeps a reason code, never the name.
"""

import json
import sqlite3

import pytest

from backend import hubdb as H
from backend import market as M
from backend.entities import erase as ER
from backend.entities import store as ES
from backend.tests.helpers.api import get, headers  # noqa: F401
from backend.tests.test_api import api  # noqa: F401

ANA, BEN, CARA, BOT = "human:ana", "human:ben", "human:cara", "bot:ops"
# What names the person in the database: her id, names, addresses and CRM id. A scan for any of them must find nothing.
NEEDLES = ("person/jane-ortiz", "Jane Ortiz", "Jane O.", "jane@example.test", "j.ortiz@example.test", "cl_4411")


def tx(api):
    return api.app.state.store.transaction()


def refused(fn, *args, **kwargs):
    with pytest.raises(H.Refused) as error:
        fn(*args, **kwargs)
    return error.value


def _setup(c):
    ES.add_attribute(c, ANA, "person", "title", "Title", "text", "The job title the person holds.")
    ES.add_attribute(c, ANA, "person", "work_email", "Work email", "email", "A work address the person uses.")
    ES.ensure_self(c, company_name="Acme Lights", domain="acme.example")


def _rows_naming(c, needles):
    """Every table, every column: the tables and columns that hold a needle, with how many rows. The whole database is
    scanned, so a table nobody listed here still fails the test."""
    hits = []
    for (table,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        columns = [row[1] for row in c.execute(f'PRAGMA table_info("{table}")').fetchall()]
        for column in columns:
            for needle in needles:
                count = c.execute(
                    f'SELECT count(*) FROM "{table}" WHERE instr(lower(CAST("{column}" AS TEXT)), ?) > 0',
                    (needle.lower(),),
                ).fetchone()[0]
                if count:
                    hits.append(f"{table}.{column} ({needle}): {count}")
    return hits


def _a_person_with_everything(c):
    """Jane, with a row in every table an erase must clear: values and their history, identifiers, links, notes, activities,
    connections, mentions, external refs, sources, a watch, a citation, a history event, and a merged-away duplicate."""
    harbor = ES.upsert(c, ANA, "organization", match={"domain": "harbor.example"}, name="Harbor Works")["id"]
    ev_only = M.create_evidence(c, ANA, quote="A pricing page excerpt that only Jane's note rests on.")[0]["id"]
    ev_shared = M.create_evidence(
        c, ANA, quote="A board page excerpt that Jane's link and Harbor's note both rest on."
    )[0]["id"]
    jane = ES.upsert(
        c, ANA, "person", match={"email": "jane@example.test"}, name="Jane Ortiz", values={"title": "CFO"}
    )["id"]
    ES.upsert(c, ANA, "person", match={"id": jane}, values={"title": "Chief Financial Officer"})
    ES.add_source(c, ANA, jane, "sync", "contacts-sheet")
    ES.link(c, ANA, jane, "works_at", harbor, evidence_id=ev_shared)
    ES.note(c, ANA, jane, "Asked for the old price list", evidence_id=ev_only)
    ES.note(c, ANA, harbor, "Harbor's account note", evidence_id=ev_shared)
    M.cite(c, ANA, "entity", jane, ev_only)
    drop = ES.upsert(c, ANA, "person", match={"email": "j.ortiz@example.test"}, name="Jane O.")["id"]
    ES.merge(c, ANA, jane, drop)
    now = H.now()
    c.execute(
        "INSERT INTO entity_activities (id, entity_id, kind, occurred_at, summary) VALUES ('act_jane', ?, 'email', ?, 'Replied')",
        (jane, now),
    )
    c.execute(
        "INSERT INTO entity_connections (id, entity_id, actor_type, actor_id, interactions_count, strength) "
        "VALUES ('conn_jane', ?, 'human', 'ben', 3, 0.5)",
        (jane,),
    )
    c.execute(
        "INSERT INTO entity_mentions (id, mentionable_type, mentionable_id, entity_id, role, quote, created_at) "
        "VALUES ('men_jane', 'task', 'task_jane', ?, 'mentioned', 'Jane asked for terms', ?)",
        (jane, now),
    )
    c.execute(
        "INSERT INTO entity_lookups (id, attempt_id, entity_id, created_at) VALUES ('look_jane', 'att_jane', ?, ?)",
        (jane, now),
    )
    c.execute(
        "INSERT INTO entity_external_refs (id, entity_id, connection_id, system, external_type, external_id, url, revision, "
        "synced_at, status) VALUES ('ext_jane', ?, 'conn_close', 'close', 'contact', 'cl_4411', '', '', ?, 'active')",
        (jane, now),
    )
    c.execute(
        "INSERT INTO entity_watches (id, entity_id, url, kind, active, created_by, created_at) "
        "VALUES ('watch_jane', ?, 'https://example.test/jane', 'page', 1, 'ana', ?)",
        (jane, now),
    )
    H.history_event(c, "market_entity", jane, ANA, "name", "Jane O.", "Jane Ortiz")
    return jane, harbor, ev_only, ev_shared


def test_an_erase_leaves_no_row_that_names_the_person(api):
    with tx(api) as c:
        _setup(c)
        jane, harbor, ev_only, ev_shared = _a_person_with_everything(c)
        seq = c.execute("SELECT seq FROM entities WHERE id = ?", (jane,)).fetchone()[0]
        # The scan sees the person before the erase; without that the empty scan below would prove nothing.
        assert _rows_naming(c, NEEDLES)

        result = ER.erase(c, ANA, jane, "request")

        assert result["type"] == "person"
        assert result["removed"]["entities"] == 2  # Jane and the duplicate merged into her
        assert _rows_naming(c, NEEDLES) == []
        assert c.execute("SELECT count(*) FROM market_fts WHERE rowid = ?", (seq,)).fetchone()[0] == 0
        assert ES.card(c, ANA, jane) is None
        assert ES.get(c, ANA, "jane@example.test") == []
        # The company's own rows stay: Harbor and its note, and the evidence Harbor's note shares with the link.
        assert ES.card(c, ANA, harbor)["notes"][0]["body"] == "Harbor's account note"
        assert M.evidence(c, ev_shared) is not None
        assert M.evidence(c, ev_only) is None


def test_an_erased_identifier_is_not_created_again(api):
    with tx(api) as c:
        _setup(c)
        jane, _harbor, _ev_only, _ev_shared = _a_person_with_everything(c)
        ER.erase(c, ANA, jane, "request")

        assert ES.is_suppressed(c, "email", "Jane@Example.Test")
        assert not ES.is_suppressed(c, "email", "ana.marsh@example.test")
        # A new entity from the erased address is refused, whatever its name; so is the merged duplicate's address.
        err = refused(ES.upsert, c, ANA, "person", match={"email": "jane@example.test"}, name="Jane Ortiz")
        assert err.rule == "suppressed"
        err = refused(ES.upsert, c, ANA, "person", match={"email": "j.ortiz@example.test"}, name="Jane O.")
        assert err.rule == "suppressed"
        # A value that sets the address on a new entity is refused the same way.
        err = refused(
            ES.upsert, c, ANA, "person", match={"name": "J. Ortiz"}, values={"work_email": "JANE@example.test"}
        )
        assert err.rule == "suppressed"
        # Everyone else is unaffected.
        assert ES.upsert(c, ANA, "person", match={"email": "ana.marsh@example.test"}, name="Ana Marsh")["created"]
        # The suppression keeps a salted hash, never the address.
        stored = c.execute("SELECT kind, value_hash, reason FROM entity_suppressions").fetchall()
        assert stored and all("jane" not in repr(row).lower() for row in stored)
        assert {row[2] for row in stored} == {"request"}


def test_only_the_owner_or_an_admin_erases_a_person(api):
    with tx(api) as c:
        _setup(c)
        jane, _harbor, _ev_only, _ev_shared = _a_person_with_everything(c)
        assert refused(ER.erase, c, CARA, jane, "request").rule == "forbidden"
        assert refused(ER.erase, c, BOT, jane, "request").rule == "forbidden"
        assert ES.card(c, ANA, jane) is not None
        # The owner's own company is never erased, whoever asks.
        assert refused(ER.erase, c, ANA, ES.SELF_ID, "request").rule == "kind"
        # An Admin may erase.
        ER.erase(c, BEN, jane, "request")
        assert ES.card(c, ANA, jane) is None


def test_the_reason_is_a_short_code_so_the_audit_keeps_no_name(api):
    with tx(api) as c:
        _setup(c)
        jane, _harbor, _ev_only, _ev_shared = _a_person_with_everything(c)
        assert refused(ER.erase, c, ANA, jane, "Jane Ortiz asked to be forgotten").rule == "kind"
        assert refused(ER.erase, c, ANA, jane, "").rule == "kind"
        assert ES.card(c, ANA, jane) is not None

        ER.erase(c, ANA, jane, "request")
        audit = c.execute("SELECT actor, after FROM audit_log WHERE object LIKE 'directory_erase:%'").fetchall()
        assert len(audit) == 1 and audit[0][0] == ANA
        after = json.loads(audit[0][1])
        assert after["reason"] == "request"
        assert all(needle.lower() not in audit[0][1].lower() for needle in NEEDLES)


def test_a_live_entitys_history_stays_append_only(api):
    with tx(api) as c:
        _setup(c)
        jane, _harbor, _ev_only, _ev_shared = _a_person_with_everything(c)
        before = c.execute("SELECT count(*) FROM history_events WHERE subject_id = ?", (jane,)).fetchone()[0]
        assert before > 0
        # Only the erase may remove a history row, and only once its subject is gone: a live entity's row is refused.
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM history_events WHERE subject_id = ?", (jane,))
        assert c.execute("SELECT count(*) FROM history_events WHERE subject_id = ?", (jane,)).fetchone()[0] == before
