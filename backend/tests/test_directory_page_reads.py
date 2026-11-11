"""The reads the Directory page adds (ui/app/directory.ts): the list's organization and sources columns, the relationship
filter that reaches a person through the organization they work at, the per-type counts, and a shared place's mark.

What these pin is privacy: a column, a count or a filter never shows a source, an organization or a relationship the
reader cannot see. ana is the owner, ben and cara are members (the API fixture's team).
"""

from backend import mailbox_access as MA
from backend.entities import store as ES
from backend.entities.places import pack
from backend.entities.places.adapter import directory_adapter
from backend.tests.helpers.api import headers  # noqa: F401

ANA, BEN, CARA = "human:ana", "human:ben", "human:cara"


def _seed(c):
    me = ES.ensure_self(c, company_name="Acme Lights", domain="acme.example")
    shown = ES.upsert(c, ANA, "organization", match={"domain": "bright.example"}, name="Bright Homes")["id"]
    ES.link(c, ANA, shown, "customer_of", me)
    hidden = ES.upsert(c, ANA, "organization", match={"domain": "quiet.example"}, name="Quiet Co")["id"]
    ES.link(c, ANA, hidden, "customer_of", me)
    ben_box = MA.ensure_mailbox(c, "ben@acme.example", person_id="ben", created_by=ANA)
    jane = ES.upsert(c, ANA, "person", match={"email": "jane@bright.example"}, name="Jane Ortiz")["id"]
    ES.link(c, ANA, jane, "works_at", shown)
    ES.add_source(c, ANA, jane, "market", "seed")
    ES.add_source(c, ANA, jane, "mailbox", ben_box["id"], visibility="restricted")
    omar = ES.upsert(c, ANA, "person", match={"email": "omar@quiet.example"}, name="Omar Quiet")["id"]
    ES.link(c, ANA, omar, "works_at", hidden)
    # Last: once only Ben's inbox knows Quiet Co, no one else (not even Ana) can write against it.
    ES.add_source(c, ANA, hidden, "mailbox", ben_box["id"], visibility="restricted")
    return jane, omar


def test_the_list_columns_show_only_what_the_reader_can_see(api):
    with api.app.state.store.transaction() as c:
        jane, omar = _seed(c)
        rows = {row["id"]: row for row in ES.find(c, CARA, "person", detail=True)["rows"]}
        # Cara sees Jane's company-wide source, not that Ben's inbox knows her, and Omar's employer not at all.
        assert rows[jane]["sources"] == ["market"]
        assert rows[jane]["organization"]["name"] == "Bright Homes"
        assert rows[jane]["organization"]["links_to_us"] == ["customer_of"]
        assert rows[omar]["organization"] is None
        # The person page reads the same columns.
        page = ES.card(c, CARA, jane)
        assert (page["organization"]["name"], page["sources"]) == ("Bright Homes", ["market"])
        assert {row["id"]: row for row in ES.find(c, BEN, "person", detail=True)["rows"]}[jane]["sources"] == [
            "market",
            "mailbox",
        ]


def test_a_relationship_filter_reaches_people_through_a_visible_organization_only(api):
    with api.app.state.store.transaction() as c:
        jane, omar = _seed(c)
        assert [row["id"] for row in ES.find(c, CARA, "person", link="customer_of")["rows"]] == [jane]
        assert sorted(row["id"] for row in ES.find(c, BEN, "person", link="customer_of")["rows"]) == sorted(
            [jane, omar]
        )


def test_counts_follow_the_read_rule(api):
    with api.app.state.store.transaction() as c:
        _seed(c)
    cara = api.get("/api/v2/entities/counts", headers=headers("cara-test")).json()["counts"]
    ben = api.get("/api/v2/entities/counts", headers=headers("ben-test")).json()["counts"]
    count = lambda rows, kind: next(row["count"] for row in rows if row["type"] == kind)  # noqa: E731
    assert count(ben, "organization") == count(cara, "organization") + 1
    assert all(row["approximate"] is False for row in cara)


def test_a_place_from_the_shared_tree_says_so_and_a_typed_one_does_not(api):
    with api.app.state.store.transaction() as c:
        pack.ensure_places(c, ["place:us-ca"], directory_adapter())
        typed = ES.upsert(c, ANA, "place", match={"name": "Back office"})["id"]
        california = ES.card(c, CARA, "place:us-ca")
        assert california["place"] == {
            "kind": "state",
            "governing_body": california["place"]["governing_body"],
            "shared": True,
        }
        assert ES.card(c, CARA, typed)["place"]["shared"] is False


def test_a_document_is_not_a_contact_and_its_party_links_to_its_file(api):
    """Contacts never list documents: no type, no count, no find, for people or bots. A contact's page links the
    documents it is party to to their files (an external ref system 'tico', external_type 'file'), and names one with no
    file without a link."""
    from backend.tests.helpers.cards import page

    fid = page(api, "inbox", "Bright Homes MSA")
    with api.app.state.store.transaction() as c:
        jane, _ = _seed(c)
        msa = ES.upsert(c, ANA, "document", match={"name": "Bright Homes MSA"})["id"]
        nda = ES.upsert(c, ANA, "document", match={"name": "Bright Homes NDA"})["id"]
        ES.link(c, ANA, jane, "party_to", msa)
        ES.link(c, ANA, jane, "party_to", nda)
        c.execute(
            "INSERT INTO entity_external_refs (id, entity_id, system, external_type, external_id) "
            "VALUES ('ref_msa', ?, 'tico', 'file', ?)",
            (msa, fid),
        )
    ana = headers("ana-test")
    types = [row["key"] for row in api.get("/api/v2/entities/types", headers=ana).json()["types"]]
    assert "person" in types and "document" not in types
    counts = [row["type"] for row in api.get("/api/v2/entities/counts", headers=ana).json()["counts"]]
    assert "person" in counts and "document" not in counts
    refused = api.get("/api/v2/entities/find/document", headers=ana)
    assert refused.status_code == 404 and "No contact type document" in refused.text
    listed = api.get("/api/v2/entities/find/person", headers=ana).json()["rows"]
    assert jane in [row["id"] for row in listed] and not {msa, nda} & {row["id"] for row in listed}
    card = api.get("/api/v2/entities/card", params={"ref": jane}, headers=ana).json()
    files = {link["other_id"]: link.get("file") for link in card["links"] if link["other_type"] == "document"}
    assert files == {msa: {"id": fid, "title": "Bright Homes MSA", "page": True}, nda: None}
    # A contact that is not party to a document gets no file field on its links.
    assert all("file" not in link for link in card["links"] if link["other_type"] != "document")
