"""The Directory's routes, end to end (track 4): `hub directory` as a bot runs it, the same questions over MCP, and what a
reader without access gets back from any route. The store's own rules are in test_entities_store.py; these pin that each
route answers what the store says over the API's real request path, and that a reader without access learns nothing.
"""

import pytest

from backend import hubdb as H
from backend import mailbox_access as MA
from backend.entities import store as ES
from backend.tests.helpers.api import headers  # noqa: F401
from backend.tests.test_api import api  # noqa: F401
from backend.tests.helpers.mcp import call
from clients import hubcli, remotecli
from clients.tico import APIError

ANA, CARA = "human:ana", "human:cara"


class Forward:
    """The client `hub directory` calls, answered by the app's own test client as `token`: each request is the real one."""

    def __init__(self, api, token):
        self.api, self.token = api, token

    def _answer(self, r):
        if r.status_code >= 400:
            error = r.json()["error"]
            raise APIError(error["code"], error["detail"], r.status_code)
        return r.json()

    def get(self, path, /, **query):
        params = {k: v for k, v in query.items() if v is not None}
        return self._answer(self.api.get("/api/v2/" + path, params=params, headers=headers(self.token)))

    def post(self, path, body=None, key=None):
        return self._answer(self.api.post("/api/v2/" + path, json=body or {}, headers=headers(self.token, key)))


def answer(api, method, path, token, **kwargs):
    return getattr(api, method)("/api/v2/" + path, headers=headers(token), **kwargs)


@pytest.fixture
def cli(api, monkeypatch):
    """`hub directory` argv in, as ana: the command's request goes to the app and its answer comes back."""
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: Forward(api, "ana-test"))
    return lambda *argv: remotecli.run(hubcli.parser().parse_args(["directory", *argv]))


def seed_person_title(c):
    ES.add_attribute(c, ANA, "person", "title", "Title", "text", "The job title the person holds.")


def hidden_jane(api):
    """A person whose only source is ana's mailbox: ana sees her, nobody else does. Her title has one past value (CFO)."""
    with api.app.state.store.transaction() as c:
        seed_person_title(c)
        box = MA.ensure_mailbox(c, "ana@acme.example", person_id="ana", created_by=ANA)
        jane = ES.upsert(
            c, ANA, "person", match={"email": "jane@quiet.example"}, name="Jane Quiet", values={"title": "CFO"}
        )["id"]
        ES.add_source(c, ANA, jane, "mailbox", box["id"], visibility="restricted")
        ES.upsert(c, ANA, "person", match={"id": jane}, values={"title": "CEO"})
    return jane


def test_a_person_written_by_the_cli_is_read_back_by_the_cli_and_the_mcp_door(api, cli):
    with api.app.state.store.transaction() as c:
        seed_person_title(c)
    jane = cli("upsert", "person", "--match", "email=jane@example.test", "--set", "title=CFO", "--name", "Jane Ortiz")[
        "id"
    ]

    card = cli("get", "jane@example.test")
    assert card["id"] == jane and card["name"] == "Jane Ortiz"
    assert card["values"]["title"]["value"] == "CFO" and card["values"]["title"]["source_type"] == "hub"

    cli("upsert", "person", "--match", f"id={jane}", "--set", "title=Chief Financial Officer")
    past = cli("history", "jane@example.test", "--attribute", "title")
    assert [row["value"] for row in past["rows"]] == ["CFO"]

    cli("note", "jane@example.test", "Prefers phone", "--audience", "me")
    assert [note["body"] for note in cli("get", jane)["notes"]] == ["Prefers phone"]

    assert [row["id"] for row in cli("find", "person", "--name", "jane")["rows"]] == [jane]
    isError, over_mcp = call(api, "hub_directory_get", {"ref": "jane@example.test"})
    assert isError is False and over_mcp["values"]["title"]["value"] == "Chief Financial Officer"


def test_links_merges_and_unlinks_round_trip_through_the_app(api, cli):
    with api.app.state.store.transaction() as c:
        seed_person_title(c)
        ES.ensure_self(c, company_name="Acme Lights", domain="acme.example")
    jane = cli("upsert", "person", "--match", "email=jane@example.test", "--name", "Jane Ortiz")["id"]
    acme = cli("upsert", "organization", "--match", "domain=acme.example", "--name", "Acme Lights")["id"]

    link = cli("link", jane, "works_at", acme)
    assert link["from"] == jane and link["to"] == acme and link["link_key"] == "works_at"
    assert [item["link_key"] for item in cli("get", jane)["links"]] == ["works_at"]
    assert cli("unlink", link["id"]) == {"id": link["id"]}
    assert cli("get", jane)["links"] == []

    old = cli("upsert", "organization", "--match", "domain=old.example", "--name", "Acme Old")["id"]
    merged = cli("merge", acme, old)
    assert merged["kept"] == acme and merged["merged"] == old
    assert cli("get", "old.example")["id"] == acme  # a merged reference answers with the kept entity
    assert cli("unmerge", old) == {"unmerged": old}
    assert cli("get", old)["id"] == old


def test_types_attributes_and_link_types_are_added_and_described_through_the_app(cli):
    cli("type", "add", "vendor", "--name", "Vendor", "--description", "A company we buy from")
    assert "vendor" in [kind["key"] for kind in cli("types")["types"]]

    cli(
        "attribute",
        "add",
        "vendor",
        "tier",
        "--name",
        "Tier",
        "--value-type",
        "select",
        "--description",
        "How much we spend with them",
        "--option",
        "gold=Gold",
        "--option",
        "silver=Silver",
    )
    cli(
        "link-type",
        "add",
        "supplies",
        "--name",
        "supplies",
        "--inverse",
        "is supplied by",
        "--description",
        "Sells to us",
        "--from",
        "vendor",
        "--to",
        "organization",
    )

    described = cli("describe", "vendor")
    assert described["type"]["key"] == "vendor"
    assert [attr["key"] for attr in described["attributes"]] == ["tier"]
    assert [option["key"] for option in described["attributes"][0]["options"]] == ["gold", "silver"]
    assert "supplies" in [link_type["key"] for link_type in described["link_types"]]


def test_self_and_rules_answer_the_same_through_the_cli_and_the_mcp_door(api, cli):
    with api.app.state.store.transaction() as c:
        self_id = ES.ensure_self(c, company_name="Acme Lights", domain="acme.example")
        ontario = ES.upsert(c, ANA, "place", match={"name": "Ontario"}, name="Ontario")["id"]
        ES.link(c, ANA, self_id, "operates_in", ontario)

    me = cli("self")
    assert me["name"] == "Acme Lights" and [place["name"] for place in me["operates_in"]] == ["Ontario"]
    assert call(api, "hub_directory_self") == (False, me)
    assert cli("rules", "acme.example") == {"entity": self_id, "action": None, "as_bot": None, "rules": []}


def test_a_reference_two_entities_share_comes_back_ambiguous_from_get_and_rules(api, cli):
    with api.app.state.store.transaction() as c:
        seed_person_title(c)
        parent = ES.upsert(c, ANA, "organization", match={"domain": "shared.example"}, name="Parent Co")["id"]
        child = ES.upsert(c, ANA, "organization", match={"name": "Child Co"})["id"]
        c.execute(
            "INSERT INTO entity_identifiers (entity_id, kind, value, normalized_value, primary_identifier, derived) "
            "VALUES (?, 'domain', 'shared.example', 'shared.example', 0, 0)",
            (child,),
        )

    got = cli("get", "shared.example")
    assert got["ambiguous"] is True and got["ref"] == "shared.example"
    assert sorted(match["id"] for match in got["matches"]) == sorted([parent, child])
    assert cli("rules", "shared.example")["ambiguous"] is True


def test_a_reader_without_access_gets_nothing_from_card_find_history_or_rules(api):
    jane = hidden_jane(api)
    assert answer(api, "get", "entities/card", "ana-test", params={"ref": "jane@quiet.example"}).status_code == 200

    for path in ("entities/card", "entities/rules", "entities/history"):
        r = answer(api, "get", path, "cara-test", params={"ref": "jane@quiet.example"})
        assert r.status_code == 404, path
        assert r.json()["error"]["code"] == "not_found", path
        assert jane not in r.text and "Jane Quiet" not in r.text, path

    listed = answer(api, "get", "entities/find/person", "cara-test", params={"name": "jane"})
    assert listed.status_code == 200 and listed.json()["rows"] == [] and listed.json()["count"] == 0

    isError, payload = call(api, "hub_directory_get", {"ref": "jane@quiet.example"}, token="cara-test")
    assert isError is True and payload["error"] == "not_found"
    assert jane not in str(payload) and "Jane Quiet" not in str(payload)


def test_a_write_naming_a_hidden_entity_is_refused_without_naming_it(api):
    jane = hidden_jane(api)
    writes = [
        ("entities/notes", {"ref": "jane@quiet.example", "body": "Asked about pricing"}),
        ("entities/links", {"from_ref": "jane@quiet.example", "link_key": "works_at", "to_ref": "acme.example"}),
        ("entities", {"type": "person", "match": {"email": "jane@quiet.example"}, "values": {"title": "Chair"}}),
    ]
    for path, body in writes:
        r = answer(api, "post", path, "cara-test", json=body)
        assert r.status_code == 422, path
        assert r.json()["error"]["code"] == "not_found", path
        assert jane not in r.text and "Jane Quiet" not in r.text, path

    # Nothing changed: the person who holds her still reads her title as it was left.
    card = answer(api, "get", "entities/card", "ana-test", params={"ref": "jane@quiet.example"}).json()
    assert card["values"]["title"]["value"] == "CEO" and card["notes"] == []


def test_the_store_resolves_targets_types_and_the_company_summary_by_the_one_read_rule(api):
    with api.app.state.store.transaction() as c:
        self_id = ES.ensure_self(c, company_name="Acme Lights", domain="acme.example")
        open_place = ES.upsert(c, ANA, "place", match={"name": "Ontario"}, name="Ontario")["id"]
        box = MA.ensure_mailbox(c, "ana@acme.example", person_id="ana", created_by=ANA)
        private = ES.upsert(c, ANA, "place", match={"name": "Secret Lot"}, name="Secret Lot")["id"]
        ES.add_source(c, ANA, private, "mailbox", box["id"], visibility="restricted")
        ES.link(c, ANA, self_id, "operates_in", open_place)
        ES.link(c, ANA, self_id, "operates_in", private)

        assert ES.target(c, ANA, "acme.example") == self_id
        with pytest.raises(H.Refused) as refused:
            ES.target(c, CARA, private)
        assert refused.value.rule == "not_found"

        # A place a reader cannot see is left out of the company summary, for that reader only.
        assert [place["name"] for place in ES.self_summary(c, ANA)["operates_in"]] == ["Ontario", "Secret Lot"]
        assert [place["name"] for place in ES.self_summary(c, CARA)["operates_in"]] == ["Ontario"]

        assert ES.describe_type(c, "person")["type"]["key"] == "person"
        assert ES.describe_type(c, "no-such-type") is None
        assert ES.type_exists(c, "place") and not ES.type_exists(c, "no-such-type")
