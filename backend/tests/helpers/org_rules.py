"""Helpers that other tests share, moved out of test_org_rules.py so they import no test file."""

from backend.auth import Identity
from backend.store import H, encode
from backend.tests.helpers.api import headers
from backend import bot_settings as BS


def set_roster(api, doc):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(doc),))


def as_bot(api, slug, token):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            (slug, "test", "x-" + slug, H.now(), "human:ana"),
        )
    api.app.state.store.settings.test_identities[token] = Identity(f"bot:{slug}", "bot", agent="test")


def org(api):
    set_roster(
        api,
        {
            "people": [
                {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
                {"id": "ben", "email": "ben@acme.example", "primary_for": ["cpo", "product-design", "ops"]},
                {"id": "cara", "email": "cara@acme.example", "team": "support", "name": "Cara"},
            ],
            "org_groups": {
                "support": {"name": "Support"},
                "support-tier-2": {"name": "Support Tier 2", "parent": "support"},
            },
        },
    )
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET team='support-tier-2' WHERE bot='finance'")
        BS.update(c, 'finance', {'team': 'support-tier-2'})
        c.execute("UPDATE bot_config SET team='product' WHERE bot='ops'")
        BS.update(c, 'ops', {'team': 'product'})
    as_bot(api, "ops", "ops-test")
    as_bot(api, "finance", "finance-test")


def rule(api, body, token="ana-test", expected=200):
    r = api.post("/api/v2/rules", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def edit(api, rule_id, body, token="ana-test", expected=200):
    r = api.post(f"/api/v2/rules/{rule_id}", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def builtin_off(api):
    """Turn off the built-in "Asks to a person" (org_rules.ensure_builtin), as a team may on the Rules page: for a test
    that lists the rules it made, where the built-in one would be one more."""
    from backend import org_rules as OR

    with api.app.state.store.transaction() as c:
        c.execute("UPDATE org_rules SET status='retired' WHERE text=?", (OR.ASKS_TO_A_PERSON,))
