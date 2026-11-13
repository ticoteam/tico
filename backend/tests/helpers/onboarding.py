"""Helpers that other tests share, moved out of test_onboarding.py so they import no test file."""

import uuid
import pytest
import yaml
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import H, encode
from backend import bot_settings as BS


COMPANY = {
    "environment_id": "acme-7",
    "company_name": "Acme",
    "app_name": "Atlas",
    "assistant_name": "Morgan",
    "assistant_bot": "assistant",
    "github_owner": "AcmeCorp",
}


OWNER_EMAIL = "morgan@acme.example"


PEOPLE = {
    "default_user": "morgan",
    "people": [
        {"id": "morgan", "name": "Morgan Reed", "email": OWNER_EMAIL, "primary_for": ["*"]},
        {"id": "riley", "name": "Riley Quinn", "email": "riley@acme.example", "primary_for": []},
        {"id": "quinn", "name": "Quinn Ellis", "email": "quinn@acme.example", "primary_for": []},
    ],
}


# What a fresh environment seeds: the assistant people talk to, and the bot that builds the
# rest. Both start planned, exactly as templates/environment-registry/employees.yaml does.
SEED = {"assistant": {"name": "assistant", "status": "planned"}, "botops": {"name": "botops", "status": "planned"}}


TOKEN = "local-owner-secret-token-0123456789"


ASSISTANT_CARD = {
    "template": "assistant",
    "slug": "assistant",
    "name": "{{assistant_name}}",
    "required": True,
    "default": True,
    "bootstrap": True,
    "summary": "The assistant {{company_name}} talks to in {{app_name}}.",
    "owns": ["{{company_name}}'s task list"],
    "never": ["Spend {{company_name}}'s money"],
    "reasoning_effort": "high",
    "recommend_when": ["always"],
}


BOTOPS_CARD = {
    "template": "botops",
    "slug": "botops",
    "name": "BotOps",
    "required": True,
    "bootstrap": True,
    "summary": "Sets every other bot up.",
    "owns": ["bot repositories"],
    "never": ["publishes"],
    "reasoning_effort": "high",
    "recommend_when": ["always"],
}


SUPPORT_CARD = {
    "template": "support-owner",
    "slug": "support",
    "name": "Support",
    "required": False,
    "bootstrap": False,
    "summary": "Answers the support inbox.",
    "owns": ["the inbox"],
    "never": ["refunds"],
    "reasoning_effort": "high",
    "recommend_when": ["has_support_inbox", "uses_tickets"],
}


SALES_CARD = {
    "template": "inbound-sales-owner",
    "slug": "sales",
    "name": "Sales",
    "required": False,
    "bootstrap": False,
    "summary": "Researches accounts and drafts outreach.",
    "owns": ["the pipeline"],
    "never": ["sends"],
    "reasoning_effort": "high",
    "recommend_when": ["sells_to_businesses", "has_pipeline"],
}


ASSISTANT_AGENT = "# {{bot_name}}\n\nYou are {{assistant_name}}, {{company_name}}'s assistant in {{app_name}}.\n"


SUPPORT_AGENT = "# {{bot_name}}\n\nYou answer {{company_name}}'s support inbox.\n"


def write_catalog(root, cards):
    """One catalog on disk: a folder, a card, and the AGENTS.md a person reviews."""
    root.mkdir(parents=True, exist_ok=True)
    for card, instructions in cards:
        folder = root / card["template"]
        folder.mkdir()
        (folder / "card.yaml").write_text(yaml.safe_dump(card))
        (folder / "AGENTS.md").write_text(instructions)
    return root


@pytest.fixture
def environment(tmp_path):
    """A signed-in, loopback environment seeded with just assistant and botops, plus a catalog."""
    clients = []

    def build(cards=None, seed=SEED, **overrides):
        index = len(clients)
        registry = tmp_path / ("registry-%d" % index)
        registry.mkdir()
        (registry / "hub-access.yaml").write_text(yaml.safe_dump({"bot_admins": ["riley@acme.example"]}))
        token = tmp_path / ("token-%d" % index)
        token.write_text(TOKEN)
        token.chmod(0o600)
        catalog = overrides.pop("catalog_dir", None)  # a test of the shipped catalog names it
        if catalog is None:
            catalog = tmp_path / ("catalog-%d" % index)
            if cards is None:
                cards = [
                    (ASSISTANT_CARD, ASSISTANT_AGENT),
                    (BOTOPS_CARD, ""),
                    (SUPPORT_CARD, SUPPORT_AGENT),
                    (SALES_CARD, ""),
                ]
            write_catalog(catalog, cards)
        settings = Settings(
            db_path=tmp_path / ("hub-%d.db" % index),
            registry_dir=registry,
            catalog_dir=catalog,
            local_owner_token_file=token,
            **{"owner_email": OWNER_EMAIL, "enabled_providers": ("openai",), **COMPANY, **overrides},
        )
        client = TestClient(create_app(settings))
        client.__enter__()
        clients.append(client)
        with client.app.state.store.transaction() as c:
            H.sync_registry(c, seed, PEOPLE)
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode(PEOPLE),))
            for slug, config in seed.items():
                c.execute('INSERT INTO bot_config(bot,owner_id,repo) VALUES(?,?,?)', (slug, 'morgan', 'emp-' + slug))
                BS.write(c, slug, config)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def signed_in(token=TOKEN):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}


def build_team(api):
    """Finish setup with the picked bots made as bots, the way the sample company does (`complete(seats=False)`).
    Setup itself lays every picked role out as a Recruiting seat (bot-lifecycle-spec.md); these tests are about the
    bots themselves once they exist."""
    with api.app.state.store.transaction() as c:
        return api.app.state.onboarding.complete(c, api.app.state.auth.owner_identity(c), seats=False)


def as_person(api, person):
    """A signed-in non-owner, without standing an identity proxy up for the test."""
    api.app.state.store.settings.test_identities[person] = Identity(
        "human:" + person, "human", email=person + "@acme.example"
    )
    return signed_in(person)


def machine(api, label="Owner Mac"):
    code = api.post("/api/v2/enrollments", json={"operator": "morgan"}, headers=signed_in()).json()["code"]
    return api.post(
        "/api/v2/runners/enroll",
        json={"code": code, "label": label, "platform": "test"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    ).json()


def draft(api, **overrides):
    body = {
        "names": {"company_name": "Acme", "app_name": "Atlas", "assistant_name": "Morgan"},
        "answers": {
            "what_we_do": "We clean apartments.",
            "customers": "businesses",
            "team_size": "6-10",
            "work_arrives": ["email", "tickets"],
            "repetitive_work": "Answering the same questions.",
            "never_without_person": ["send", "spend"],
        },
        "selected": {},
    }
    body.update(overrides)
    return api.put("/api/v2/onboarding", json=body, headers=signed_in())


LIBRARIAN_CARD = {
    "template": "librarian",
    "slug": "librarian",
    "name": "Librarian",
    "required": True,
    "bootstrap": True,
    "summary": "Answers from the docs.",
    "owns": ["the map"],
    "never": ["invents"],
    "reasoning_effort": "medium",
    "recommend_when": ["always"],
}
