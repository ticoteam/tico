"""The Directory's store (backend/entities/store.py) and the one read rule (backend/entities/visible.py), on the API fixture's
team: ana is the owner, ben and cara are members, all at acme.example.

What these pin: one entity per email; a changed value keeps its old one in history and an unchanged one does not; a field
a connection owns is refused to a person; an ambiguous match is refused, never guessed; a merge unmerges exactly; and the
read rule holds across get, find and card: a teammate's mailbox source hides an entity from everyone else (the owner
included), a `me` note hides its entity from everyone but its author, and a sensitive attribute shows to the system only.
"""

import pytest

from backend import hubdb as H
from backend import mailbox_access as MA
from backend.entities import store as ES
from backend.entities import visible as V
from backend.tests.test_api import api, get, headers  # noqa: F401

ANA, BEN, CARA = "human:ana", "human:ben", "human:cara"


def tx(api):
    return api.app.state.store.transaction()


def refused(fn, *args, **kwargs):
    with pytest.raises(H.Refused) as error:
        fn(*args, **kwargs)
    return error.value


def _setup_types(c):
    ES.add_attribute(c, ANA, "person", "title", "Title", "text", "The job title the person holds.")
    ES.add_attribute(c, ANA, "person", "tax_id", "Tax ID", "text", "The tax identifier.", sensitive=True)
    ES.add_attribute(
        c, ANA, "organization", "crm_stage", "CRM stage", "text", "The stage the CRM holds.", owner="connection:close"
    )


def test_an_email_is_one_entity_and_a_changed_value_keeps_its_history(api):
    with tx(api) as c:
        _setup_types(c)
        first = ES.upsert(
            c, ANA, "person", match={"email": "Jane@Example.Test"}, name="Jane Ortiz", values={"title": "CFO"}
        )
        again = ES.upsert(c, ANA, "person", match={"email": "jane@example.test"}, values={"title": "CFO"})
        assert again["id"] == first["id"] and again["created"] is False

        # An unchanged value refreshes its source and writes no history; a changed one moves the old value to history.
        assert ES.history(c, ANA, first["id"], "title")["rows"] == []
        ES.upsert(c, ANA, "person", match={"id": first["id"]}, values={"title": "Chief Financial Officer"})
        past = ES.history(c, ANA, first["id"], "title")["rows"]
        assert [row["value"] for row in past] == ["CFO"]
        assert ES.card(c, ANA, first["id"])["values"]["title"]["value"] == "Chief Financial Officer"


def test_a_connection_owned_field_is_refused_to_a_person(api):
    with tx(api) as c:
        _setup_types(c)
        ES.upsert(c, ANA, "organization", match={"domain": "acme.example"}, name="Acme Lights")
        err = refused(ES.upsert, c, ANA, "organization", match={"domain": "acme.example"}, values={"crm_stage": "Won"})
        assert err.rule == "owner"


def test_an_identifier_two_entities_share_is_ambiguous_for_get_and_for_upsert(api):
    with tx(api) as c:
        _setup_types(c)
        parent = ES.upsert(c, ANA, "organization", match={"domain": "shared.example"}, name="Parent Co")
        child = ES.upsert(c, ANA, "organization", match={"name": "Child Co"})
        c.execute(
            "INSERT INTO entity_identifiers (entity_id, kind, value, normalized_value, primary_identifier, derived) "
            "VALUES (?, 'domain', 'shared.example', 'shared.example', 0, 0)",
            (child["id"],),
        )
        assert sorted(e["id"] for e in ES.get(c, ANA, "shared.example")) == sorted([parent["id"], child["id"]])
        err = refused(ES.upsert, c, ANA, "organization", match={"domain": "shared.example"}, values={})
        assert err.rule == "ambiguous"


def test_a_merge_redirects_and_an_unmerge_puts_every_row_back(api):
    with tx(api) as c:
        _setup_types(c)
        ES.ensure_self(c, company_name="Acme Lights", domain="acme.example")
        # Not our company: a domain of ours resolves to the self row now, and a merge into self is a self-loop.
        keep = ES.upsert(c, ANA, "organization", match={"domain": "acme-group.example"}, name="Acme Group")["id"]
        drop = ES.upsert(c, ANA, "organization", match={"domain": "old.example"}, name="Acme Old")["id"]
        ES.link(c, ANA, drop, "customer_of", ES.SELF_ID)
        ES.note(c, ANA, drop, "Asked for the old price list")
        before = c.execute("SELECT count(*) FROM entity_identifiers WHERE entity_id = ?", (drop,)).fetchone()[0]

        ES.merge(c, ANA, keep, drop)
        assert [e["id"] for e in ES.get(c, ANA, drop)] == [keep]
        assert c.execute("SELECT count(*) FROM entity_identifiers WHERE entity_id = ?", (drop,)).fetchone()[0] == 0
        assert "customer_of" in c.execute("SELECT self_link_keys FROM entities WHERE id = ?", (keep,)).fetchone()[0]

        ES.unmerge(c, ANA, drop)
        assert [e["id"] for e in ES.get(c, ANA, drop)] == [drop]
        assert c.execute("SELECT count(*) FROM entity_identifiers WHERE entity_id = ?", (drop,)).fetchone()[0] == before
        assert c.execute("SELECT self_link_keys FROM entities WHERE id = ?", (keep,)).fetchone()[0] == ""
        assert c.execute("SELECT from_entity_id FROM entity_links WHERE link_key = 'customer_of'").fetchone()[0] == drop
        assert c.execute("SELECT count(*) FROM entity_notes WHERE entity_id = ?", (drop,)).fetchone()[0] == 1


def test_a_mailbox_source_shows_only_to_the_person_who_holds_that_mailbox(api):
    with tx(api) as c:
        _setup_types(c)
        ana_box = MA.ensure_mailbox(c, "ana@acme.example", person_id="ana", created_by=ANA)
        ben_box = MA.ensure_mailbox(c, "ben@acme.example", person_id="ben", created_by=ANA)
        mine = ES.upsert(c, ANA, "organization", match={"domain": "quiet.example"}, name="Quiet Co")["id"]
        ES.add_source(c, ANA, mine, "mailbox", ana_box["id"], visibility="restricted")
        theirs = ES.upsert(c, ANA, "organization", match={"domain": "other.example"}, name="Other Co")["id"]
        ES.add_source(c, ANA, theirs, "mailbox", ben_box["id"], visibility="restricted")

        assert [e["id"] for e in ES.get(c, ANA, "quiet.example")] == [mine]
        assert [e["id"] for e in ES.get(c, CARA, "quiet.example")] == []
        # The owner of the workspace has no bypass: Ben's mailbox does not show to Ana.
        assert [e["id"] for e in ES.get(c, ANA, "other.example")] == []
        assert ES.card(c, CARA, mine) is None
        listed = ES.find(c, CARA, "organization", name="other")
        assert listed["rows"] == [] and listed["count"] == 0


def test_a_me_note_shows_only_to_its_author_and_never_hides_its_entity(api):
    with tx(api) as c:
        _setup_types(c)
        shared = ES.upsert(c, BEN, "organization", match={"domain": "shared.example"}, name="Shared Co")["id"]
        ES.note(c, BEN, shared, "Prefers phone; the CFO decides", audience="me")

        assert [e["id"] for e in ES.get(c, ANA, "shared.example")] == [shared]
        assert ES.card(c, CARA, shared)["notes"] == []
        notes = ES.card(c, BEN, shared)["notes"]
        assert [note["body"] for note in notes] == ["Prefers phone; the CFO decides"]


def test_a_sensitive_attribute_shows_to_the_system_and_to_no_teammate(api):
    with tx(api) as c:
        _setup_types(c)
        jane = ES.upsert(
            c, ANA, "person", match={"email": "jane@example.test"}, name="Jane Ortiz", values={"tax_id": "12-3456789"}
        )["id"]
        assert "tax_id" not in ES.card(c, CARA, jane)["values"]
        assert "tax_id" not in ES.card(c, ANA, jane)["values"]
        assert ES.card(c, V.SYSTEM, jane)["values"]["tax_id"]["value"] == "12-3456789"


def test_find_pages_by_name_with_a_cursor_and_says_its_count(api):
    with tx(api) as c:
        for n, name in enumerate(("Kilo One", "Kilo Two", "Kilo Three")):
            ES.upsert(c, ANA, "organization", match={"domain": f"kilo{n}.example"}, name=name)
        page = ES.find(c, ANA, "organization", name="kilo", limit=2)
        assert [row["name"] for row in page["rows"]] == ["Kilo One", "Kilo Three"]  # by name_key, then id
        assert page["count"] == 3 and page["approximate"] is False
        rest = ES.find(c, ANA, "organization", name="kilo", limit=2, after=page["next"])
        assert [row["name"] for row in rest["rows"]] == ["Kilo Two"] and rest["next"] is None
