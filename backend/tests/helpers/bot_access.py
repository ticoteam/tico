"""Helpers that other tests share, moved out of test_bot_access.py so they import no test file."""

import pytest
from backend.auth import Identity
from backend.store import H, encode
from backend.tests.helpers.api import get, headers, post


EVERYONE = {"everyone": True}


def bot(api, slug, reports_to=None):
    return post(
        api,
        "bots",
        {
            "slug": slug,
            "display_name": slug.title(),
            "description": "A test bot.",
            "reports_to": reports_to,
            "status": "active",
            "repo": "emp-" + slug,
            "thread_mode": "personal",
            "model": "hermes-profile",
            "effort": "as-configured",
            "harness": "hermes",
            "operator": "ana",
            "owners": ["ana"],
            "runner_id": None,
        },
    )


def bot_id(api, slug):
    """The id a bot is stored under: tables, updates and actors use it; the slug is only its label."""
    with api.app.state.store.read() as c:
        return H.resolve_bot(c, slug)["id"]


@pytest.fixture
def world(api):
    """The base fixture's company plus Dee, a Legal team, the Legal bot under Ben, and two bots."""
    store = api.app.state.store
    store.settings.test_identities["dee-test"] = Identity("human:dee", "human", "dee@acme.example")
    with store.transaction() as c:
        people = [
            {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
            {"id": "ben", "email": "ben@acme.example", "primary_for": ["cpo", "product-design", "ops"]},
            {"id": "cara", "email": "cara@acme.example", "team": "legal"},
            {"id": "dee", "email": "dee@acme.example", "team": "sales"},
        ]
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode({"people": people}),))
        H.sync_registry(c, {}, {"people": people})
    bot(api, "counsel", reports_to="human:ben")
    bot(api, "stranger")
    bot(api, "analyst")
    tokens = {"ana": "ana-test", "ben": "ben-test", "cara": "cara-test", "dee": "dee-test"}
    tokens.update({slug: post(api, f"bots/{slug}/agent-credential", {})["token"] for slug in ("stranger", "analyst")})
    task = post(api, "tasks", {"owner": "counsel", "title": "Review the NDA", "body": "Redline it."})
    with store.transaction() as c:
        c.execute(
            "INSERT INTO updates(id,bot,kind,day,headline,body,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                "u1",
                H.resolve_bot(c, "counsel")["id"],
                "daily",
                "2026-09-29",
                "Two contracts reviewed",
                "- Two contracts reviewed",
                H.now(),
                H.now(),
            ),
        )
    made = api.post(
        "/api/v2/files/uploads",
        json={"name": "memo.md", "text": "# Memo", "scope": "bot"},
        headers=headers(post(api, "bots/counsel/agent-credential", {})["token"]),
    )
    assert made.status_code == 200, made.text
    return api, tokens, task


def set_access(api, see=None, read=None, write=None, token="ana-test", expected=200):
    current = get(api, "bots/counsel/access", token)
    body = {
        "see": see or EVERYONE,
        "read": read or EVERYONE,
        "write": write or EVERYONE,
        "revision": current["revision"],
    }
    r = api.put("/api/v2/bots/counsel/access", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()
