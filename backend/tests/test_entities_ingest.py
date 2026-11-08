"""Directory ingest (backend/entities/ingest.py): what mail, meetings, Counsel rules and Company page readers make, on the API
fixture's team (ana is the owner, ben and cara are members, all at acme.example).

What these pin: a sender becomes a person at their organization, linked, and known from the mailbox alone; a second copy
of the same message adds nothing and renames nothing; an automated sender makes its organization only; a free-mail sender
makes no organization; an inbox switched off makes nothing and hides what it made, the owner included, until it is switched
back on; a reader without the mailbox cannot see a person known only from it; an entity with no source is not narrowed by a
restricted one; a failing step is undone and the action goes on; an entity's last source cannot be removed.
"""

import pytest

from backend import hubdb as H
from backend import mailbox_access as MA
from backend.company_page import readers as R
from backend.entities import ingest as ING
from backend.entities import store as ES
from backend.entities import visible as V
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (the api fixture)
from backend.tests.test_org_rules import org, rule

ANA, BEN, CARA = "human:ana", "human:ben", "human:cara"


def tx(api):
    return api.app.state.store.transaction()


def _links(c, from_id, key):
    return [
        row["to_entity_id"]
        for row in c.execute(
            "SELECT to_entity_id FROM entity_links WHERE from_entity_id = ? AND link_key = ? AND valid_until_on IS NULL",
            (from_id, key),
        )
    ]


def _counts(c):
    return tuple(
        c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("entities", "entity_identifiers", "entity_links", "entity_sources")
    )


def _ana_inbox(c):
    return MA.ensure_mailbox(c, "ana@acme.example", person_id="ana", created_by=ANA)["id"]


JANE = {"from_addr": "jane@example.test", "from_header": '"Jane Ortiz" <jane@example.test>'}


def test_a_sender_becomes_a_person_at_their_organization_known_from_the_mailbox(api):
    with tx(api) as c:
        box = _ana_inbox(c)
        out = ING.from_mail(c, box, JANE)
        person, company = out["person"], out["organization"]
        assert ES.card(c, ANA, person)["name"] == "Jane Ortiz"
        assert ES.card(c, ANA, company)["name"] == "Example"
        assert _links(c, person, "works_at") == [company]
        sources = c.execute(
            "SELECT source_type, source_id, visibility FROM entity_sources WHERE entity_id = ?", (person,)
        )
        assert [tuple(row) for row in sources] == [("mailbox", box, "restricted")]
        assert sorted(out["created"]) == sorted([person, company])


def test_a_second_identical_message_adds_nothing_and_renames_nothing(api):
    with tx(api) as c:
        box = _ana_inbox(c)
        first = ING.from_mail(c, box, JANE)
        before = _counts(c)
        again = ING.from_mail(c, box, {**JANE, "from_header": '"J. Ortiz" <jane@example.test>'})
        assert _counts(c) == before
        assert (again["person"], again["organization"], again["created"]) == (
            first["person"],
            first["organization"],
            [],
        )
        assert ES.card(c, ANA, first["person"])["name"] == "Jane Ortiz"


def test_an_automated_sender_makes_its_organization_and_no_person(api):
    with tx(api) as c:
        box = _ana_inbox(c)
        out = ING.from_mail(c, box, {"from_addr": "no-reply@vendor-mail.example", "from_header": "Billing"})
        assert out["person"] is None and out["organization"] is not None
        assert ES.matching(c, "person", {"email": "no-reply@vendor-mail.example"}) == []
        listed = ING.from_mail(
            c,
            box,
            {
                "from_addr": "news@lists.vendor-mail.example",
                "from_header": "News",
                "list_id": "<news.vendor-mail.example>",
            },
        )
        assert listed["person"] is None
        assert ES.matching(c, "person", {"email": "news@lists.vendor-mail.example"}) == []


def test_a_free_mail_sender_makes_a_person_and_no_organization(api):
    with tx(api) as c:
        box = _ana_inbox(c)
        out = ING.from_mail(c, box, {"from_addr": "pat@gmail.com", "from_header": "Pat Lane <pat@gmail.com>"})
        assert out["organization"] is None and out["person"] is not None
        assert _links(c, out["person"], "works_at") == []
        assert ES.matching(c, "organization", {"domain": "gmail.com"}) == []


def test_a_teammate_links_to_self_and_self_keeps_no_source(api):
    with tx(api) as c:
        ES.ensure_self(c, company_name="Acme Lights", domain="acme.example")
        box = _ana_inbox(c)
        out = ING.from_mail(c, box, {"from_addr": "ben@acme.example", "from_header": "Ben"})
        assert out["organization"] == ES.self_id(c)
        assert _links(c, out["person"], "works_at") == [ES.self_id(c)]
        assert ES.has_sources(c, ES.self_id(c)) is False


def test_an_inbox_switched_off_makes_nothing_and_hides_what_it_made_from_its_owner(api):
    with tx(api) as c:
        box = _ana_inbox(c)
        person = ING.from_mail(c, box, JANE)["person"]
        assert V.visible(c, ANA, person)

        MA.set_feeds_directory(c, ANA, box, False)
        assert not V.visible(c, ANA, person)
        assert ING.from_mail(c, box, {"from_addr": "lee@example.test", "from_header": "Lee"}) == {}
        assert ES.matching(c, "person", {"email": "lee@example.test"}) == []

        MA.set_feeds_directory(c, ANA, box, True)
        assert V.visible(c, ANA, person)


def test_a_reader_without_the_mailbox_cannot_see_a_person_known_only_from_it(api):
    with tx(api) as c:
        box = _ana_inbox(c)
        person = ING.from_mail(c, box, JANE)["person"]
        assert V.visible(c, ANA, person)
        assert not V.visible(c, BEN, person)
        assert not V.visible(c, CARA, person)
        assert ES.card(c, CARA, person) is None
        assert ES.get(c, CARA, "jane@example.test") == []


def test_an_existing_entity_with_no_source_is_not_narrowed_by_a_mailbox(api):
    with tx(api) as c:
        box = _ana_inbox(c)
        known = ES.upsert(c, ANA, "person", match={"email": "lee@example.test"}, name="Lee Park")["id"]
        ING.from_mail(c, box, {"from_addr": "lee@example.test", "from_header": "Lee Park"})
        assert ES.has_sources(c, known) is False
        assert V.visible(c, CARA, known)


def test_a_team_meeting_makes_its_outside_attendees_and_a_private_one_makes_nothing(api):
    with tx(api) as c:
        attendees = [
            {"name": "Lee Park", "email": "lee@example.test", "person_id": None},
            {"name": "Ben", "email": "ben@acme.example", "person_id": "ben"},
        ]
        private = ING.from_meeting(c, "mtg_private", audience="private", review_state="live", participants=attendees)
        assert private == {}
        assert ES.matching(c, "person", {"email": "lee@example.test"}) == []

        out = ING.from_meeting(c, "mtg_team", audience="team", review_state="live", participants=attendees)
        lee = ES.matching(c, "person", {"email": "lee@example.test"})
        assert out["people"] == lee
        assert _links(c, lee[0], "works_at") == ES.matching(c, "organization", {"domain": "example.test"})
        assert V.visible(c, CARA, lee[0])
        ben = ES.matching(c, "person", {"email": "ben@acme.example"})
        assert ben == []  # a roster teammate is not an outside attendee


def test_a_company_page_board_member_advises_self_and_an_investor_invests(api):
    with tx(api) as c:
        ES.ensure_self(c, company_name="Acme Lights", domain="acme.example")
        board = H.new_id()
        c.execute(
            "INSERT INTO audiences(id,name,kind,enabled,created_at,updated_at) VALUES(?,?,?,1,?,?)",
            (board, "Board", "board", H.now(), H.now()),
        )
        investors = H.new_id()
        c.execute(
            "INSERT INTO audiences(id,name,kind,enabled,created_at,updated_at) VALUES(?,?,?,1,?,?)",
            (investors, "Investors", "investors", H.now(), H.now()),
        )
        R.add(c, ANA, "dana@example.test", [board], "Dana Board")
        R.add(c, ANA, "ivy@fund.example.test", [investors], "Ivy Fund")
        me = ES.self_id(c)
        dana = ES.matching(c, "person", {"email": "dana@example.test"})[0]
        ivy = ES.matching(c, "person", {"email": "ivy@fund.example.test"})[0]
        assert _links(c, dana, "advises") == [me]
        assert _links(c, ivy, "invests_in") == [me]
        assert V.visible(c, CARA, dana)


def test_a_failing_step_is_undone_and_the_action_goes_on(api):
    with tx(api) as c:

        def broken(c):
            ES.upsert(c, ING.ACTOR, "person", match={"email": "half@example.test"}, name="Half")
            raise RuntimeError("the step broke")

        assert ING.safely(c, "test", broken, c) is None
        assert ES.matching(c, "person", {"email": "half@example.test"}) == []
        assert ING.safely(c, "test", lambda c: 7, c) == 7


def test_a_counterparty_of_an_external_rule_is_an_organization_of_its_kind(api):
    org(api)
    source = post(api, "rule-sources", {"name": "Acme Tax Office", "kind": "government"})["source"]["id"]
    rule(api, {"kind": "external", "text": "File the annual return.", "who": {"bots": ["ops"]}, "source_id": source})
    with tx(api) as c:
        found = ES.matching(c, "organization", {"name": "Acme Tax Office"})
        assert len(found) == 1
        assert ES.card(c, ANA, found[0])["values"]["kind"]["value"] == "government"


def test_remove_source_keeps_the_last_source_and_refuses_it(api):
    with tx(api) as c:
        jane = ES.upsert(c, ANA, "person", match={"email": "jane@example.test"}, name="Jane Ortiz")["id"]
        ES.add_source(c, ANA, jane, "meeting", "mtg_a")
        ES.add_source(c, ANA, jane, "meeting", "mtg_b")
        assert ES.remove_source(c, ANA, jane, "meeting", "mtg_a") is True
        assert ES.remove_source(c, ANA, jane, "meeting", "mtg_missing") is False
        with pytest.raises(H.Refused) as error:
            ES.remove_source(c, ANA, jane, "meeting", "mtg_b")
        assert error.value.rule == "last_source"
        assert ES.has_sources(c, jane)
