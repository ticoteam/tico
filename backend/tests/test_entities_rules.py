"""Directory rules (track 5): path resolution along inheriting links, the depth cap and its truncation, the fail-closed
check, privacy on restricted steps, contact resolution, and the verdicts `check` gives.

The hub is the track 3 fixture (test_directory_tables_migration). Jane works at Acme Lights, which is self; Sam works at
River Capital, which is not. The link keys these tests walk are made inheriting in the fixture, as the built-in link
types are meant to be.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from backend.config import Settings
from backend.entities import rules as D
from backend.entities import store as S
from backend.store import Store
from backend.tests.helpers.directory_tables_migration import FIXTURE, SAMPLE

NOW = "2026-10-01T00:00:00Z"
ACTOR = "bot:ops"


def _entity(c, entity_id, entity_type, name):
    c.execute(
        "INSERT INTO entities (id, type, name, name_key, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, lower(?), 'seed', ?, ?)",
        (entity_id, entity_type, name, name, NOW, NOW),
    )


def _link(c, link_id, source, key, target):
    c.execute(
        "INSERT INTO entity_links (id, from_entity_id, link_key, to_entity_id, valid_from_on, valid_until_on, "
        "confidence, properties, created_by, created_by_type, created_by_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, NULL, NULL, 0.9, '{}', 'seed', 'system', '', ?, ?)",
        (link_id, source, key, target, NOW, NOW),
    )


@pytest.fixture
def directory(tmp_path: Path) -> Store:
    path = tmp_path / "hub.db"
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text())
    conn.close()
    conn = sqlite3.connect(path)
    conn.executescript(SAMPLE)
    conn.close()
    store = Store(Settings(db_path=path))
    store.initialize(seed_market=False)
    with store.transaction() as c:
        c.execute("UPDATE entities SET self = 1 WHERE id = 'company/acme'")
        c.execute("UPDATE entities SET primary_domain = 'acme.example' WHERE id = 'company/acme'")
        c.execute(
            "UPDATE link_types SET inherits_rules = 1 WHERE key IN ('works_at', 'located_in', 'party_to', 'subsidiary_of')"
        )
        _entity(c, "person/sam", "person", "Sam Lee")
        _entity(c, "document/nfpa-70", "document", "NFPA 70")
        _link(c, "lnk-jane-us", "person/jane", "located_in", "geography/united-states")
        _link(c, "lnk-acme-ul", "company/acme", "party_to", "regulation/ul-1573")
        _link(c, "lnk-sam-river", "person/sam", "works_at", "company/river")
        _link(c, "lnk-river-nfpa", "company/river", "party_to", "document/nfpa-70")
    return store


def _rule(c, rule_id, scope, *, if_broken="block", applies_when=None, status="active"):
    c.execute(
        "INSERT INTO org_rules (id, kind, text, who, with_scope, if_broken, status, suggested_by, created_at, updated_at, "
        "toward_scope, applies_when) VALUES (?, 'external', ?, '{\"everyone\": true}', '{}', ?, ?, 'human:jane', ?, ?, ?, ?)",
        (rule_id, f"Rule {rule_id}", if_broken, status, NOW, NOW, json.dumps(scope), applies_when),
    )


def _effects(result):
    return {rule["id"]: (rule["effect"], rule["path"]) for rule in result["rules"]}


def test_path_follows_works_at_located_in_and_party_to(directory):
    with directory.transaction() as c:
        _rule(c, "r-river", ["entity:company/river"], if_broken="warn")
        _rule(c, "r-us", ["entity:geography/united-states"], if_broken="warn")
        _rule(c, "r-nfpa", ["entity:document/nfpa-70"])
        _rule(c, "r-ul", ["entity:regulation/ul-1573"])
    with directory.read() as c:
        sam = _effects(D.rules_for(c, ACTOR, "person/sam", "email"))
        jane = _effects(D.rules_for(c, ACTOR, "person/jane", "email"))
    assert sam["r-river"] == ("warn", ["Sam Lee → works_at → River Capital"])
    assert sam["r-nfpa"] == ("block", ["Sam Lee → works_at → River Capital → party_to → NFPA 70"])
    assert jane["r-us"] == ("warn", ["Jane Ortiz → located_in → United States"])
    # Self starts every walk, so a rule on the company's own side is reached from self, not through Jane.
    assert jane["r-ul"] == ("block", ["Acme Lights → party_to → UL 1573"])


def test_depth_cap_stops_at_four_links_and_says_so(directory):
    with directory.transaction() as c:
        for i in range(6):
            _entity(c, f"organization/chain-{i}", "organization", f"Chain {i}")
        for i in range(5):
            _link(c, f"lnk-chain-{i}", f"organization/chain-{i}", "subsidiary_of", f"organization/chain-{i + 1}")
        _rule(c, "r-four", ["entity:organization/chain-4"])
        _rule(c, "r-five", ["entity:organization/chain-5"])
    with directory.read() as c:
        result = D.rules_for(c, ACTOR, "organization/chain-0", "email")
    effects = _effects(result)
    # Four links reach chain-4, so its rule applies with its whole path; chain-5 is one link past the cap.
    assert effects["r-four"][0] == "block"
    assert effects["r-four"][1][0].count("subsidiary_of") == 4
    assert effects["r-five"] == ("skip", [])
    assert result["truncated"] is True
    assert result["depth"] == 4


def test_a_forced_error_holds_the_action(directory, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("the walk broke")

    monkeypatch.setattr(S, "walk", broken)
    with directory.read() as c:
        verdict = D.check(c, ACTOR, "person/jane", "email")
    assert verdict["outcome"] == "held"
    assert verdict["error"] == "RuntimeError"
    assert "could not be checked (RuntimeError)" in verdict["held"][0]["text"]


def test_a_restricted_step_shows_no_name(directory):
    with directory.transaction() as c:
        _entity(c, "organization/secret-holdings", "organization", "Secret Holdings")
        c.execute(
            "INSERT INTO entity_sources (id, entity_id, source_type, source_id, visibility, created_at) "
            "VALUES ('src-secret', 'organization/secret-holdings', 'finance_book', 'book-1', 'restricted', ?)",
            (NOW,),
        )
        _link(c, "lnk-acme-secret", "company/acme", "subsidiary_of", "organization/secret-holdings")
        _rule(c, "r-secret", ["entity:organization/secret-holdings"])
    with directory.read() as c:
        result = D.rules_for(c, ACTOR, "person/jane", "email")
    dumped = json.dumps(result)
    assert "Secret Holdings" not in dumped
    assert "organization/secret-holdings" not in dumped
    assert _effects(result)["r-secret"] == ("block", ["Acme Lights → subsidiary_of → a restricted entity"])


def test_a_condition_that_does_not_hold_skips_the_rule(directory):
    with directory.transaction() as c:
        _rule(c, "r-conditional", ["entity:company/acme"], applies_when="self operates_in geography/united-states")
    with directory.read() as c:
        assert _effects(D.rules_for(c, ACTOR, "person/jane", "email"))["r-conditional"] == ("skip", [])
    with directory.transaction() as c:
        _link(c, "lnk-acme-operates", "company/acme", "operates_in", "geography/united-states")
    with directory.read() as c:
        assert _effects(D.rules_for(c, ACTOR, "person/jane", "email"))["r-conditional"][0] == "block"


def test_a_scope_it_cannot_read_fails_closed_on_that_rule(directory):
    with directory.transaction() as c:
        _rule(c, "r-unknown", ["vendor:acme"])
    with directory.read() as c:
        effect, _path = _effects(D.rules_for(c, ACTOR, "person/jane", "email"))["r-unknown"]
    assert effect == "block"


def test_check_verdicts_follow_the_rules(directory):
    with directory.transaction() as c:
        _rule(c, "r-warn", ["entity:geography/united-states"], if_broken="warn")
    with directory.read() as c:
        assert D.check(c, ACTOR, "person/jane", "email")["outcome"] == "warn"
    with directory.transaction() as c:
        _rule(c, "r-block", ["entity:company/acme"])
    with directory.read() as c:
        verdict = D.check(c, ACTOR, "person/jane", "email")
    assert verdict["outcome"] == "held"
    assert [rule["id"] for rule in verdict["held"]] == ["r-block"]
    with directory.transaction() as c:
        c.execute("UPDATE org_rules SET status = 'retired'")
    with directory.read() as c:
        assert D.check(c, ACTOR, "person/jane", "email")["outcome"] == "clear"


def test_contacts_resolve_by_email_and_by_domain(directory):
    with directory.transaction() as c:
        c.execute(
            "INSERT INTO entity_identifiers (entity_id, kind, value, normalized_value, primary_identifier, derived) "
            "VALUES ('person/jane', 'email', 'Jane.Ortiz@acme.example', 'jane.ortiz@acme.example', 1, 0)"
        )
    with directory.read() as c:
        assert S.contact_entities(c, ["JANE.ortiz@acme.example"]) == ["company/acme", "person/jane"]
        assert S.contact_entities(c, ["someone@acme.example"]) == ["company/acme"]
        assert S.contact_entities(c, ["someone@unknown.example"]) == []


def test_scope_terms_parse_or_refuse():
    assert D.parse_scope(["entity:company/acme", "link:located_in:place/california"]) == [
        ("entity", "company/acme"),
        ("link", "located_in", "place/california"),
    ]
    for bad in ["vendor:acme", "link:located_in", "entity:", "link:a:b:c"]:
        with pytest.raises(D.ScopeError):
            D.parse_scope([bad])
    assert D.parse_condition("self operates_in geography/united-states") == ("operates_in", "geography/united-states")
    with pytest.raises(D.ScopeError):
        D.parse_condition("self employs >= 50")
