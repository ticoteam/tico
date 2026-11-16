"""The Tools Owner (templates/catalog/tools-owner) is built in: a required bootstrap card under the General Manager, the
Tools page's owner, nobody archives it, and a company from before it gets it on update. As the Tools page's owner it asks
for any other bot's key with a card (never its own) and reads every tool the company sees, and it still grants nothing."""

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from backend import page_owners
from backend.app import create_app
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, headers, post, put, ready, runner
from backend.tests.helpers.api_tools import key, level, tool
from backend.tests.helpers.credentials import setup as vault
from backend.tests.helpers.goal_manager import BASE
from backend.tests.helpers.onboarding import draft, environment, machine, signed_in  # noqa: F401

CATALOG = Path(__file__).resolve().parents[2] / "templates" / "catalog"
SECRET = "acme-crm-synthetic-0123456789"


def with_templates(api, *slugs):
    for slug in slugs:
        shutil.copytree(CATALOG / slug, Path(api.app.state.store.settings.catalog_dir) / slug)


def set_up(environment, tools_owner=True):
    api = environment(seed={}, cards=BASE)
    with_templates(api, "general-manager", *(["tools-owner"] if tools_owner else []))
    machine(api)
    draft(api)
    done = api.post("/api/v2/setup/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    return api


def line(api, slug):
    with api.app.state.store.read() as c:
        return c.execute("SELECT reports_to FROM bot_config WHERE bot=?", (slug,)).fetchone()["reports_to"]


def test_a_new_company_gets_the_tools_owner_under_the_general_manager_as_the_tools_pages_owner(environment):
    api = set_up(environment)
    with api.app.state.store.read() as c:
        assert H.bot(c, H.TOOLS_OWNER)["state"] == "active"
        assert page_owners.owner(c, "tools") == page_owners.owner(c, "tools", fallback=False) == "bot:tools-owner"
    assert line(api, H.TOOLS_OWNER) == "general-manager"
    pages = api.get("/api/v2/page-owners", headers=signed_in()).json()["pages"]
    assert pages["tools"]["bot"] == pages["tools"]["default"] == "bot:tools-owner"
    assert api.app.state.auth.system_bot(H.TOOLS_OWNER)
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot=?", (H.TOOLS_OWNER,)).fetchone()[0]
    gone = api.post(f"/api/v2/bots/{H.TOOLS_OWNER}/archive", json={"expected_revision": revision}, headers=signed_in())
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "system_bot"


def test_a_company_from_before_gets_it_on_update_under_the_general_manager_once(environment):
    api = set_up(environment, tools_owner=False)
    with api.app.state.store.read() as c:
        assert not H.resolve_bot(c, H.TOOLS_OWNER)
        assert page_owners.owner(c, "tools", fallback=False) is None  # no key guard without its own owner
    with_templates(api, "tools-owner")
    with TestClient(create_app(api.app.state.store.settings)):  # a restart is an update
        pass
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM bots WHERE slug=?", (H.TOOLS_OWNER,)).fetchone()[0] == 1
        assert page_owners.owner(c, "tools", fallback=False) == "bot:tools-owner"
    assert line(api, H.TOOLS_OWNER) == "general-manager"
    with api.app.state.store.transaction() as c:
        assert api.app.state.onboarding.ensure_tools_owner(c) is None  # twice is still one


def ops_turn(api):
    """A turn of the ops bot on a task Ana asked it for."""
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "tasks", {"owner": "ops", "title": "Connect the Acme CRM for cpo", "body": "Get it working."})
    return claim(api, machine)


def ask(api, attempt, **body):
    return api.post(
        "/api/v2/credential-requests",
        json={"env": "ACME_CRM_TOKEN", "label": "the Acme CRM token", **body},
        headers=headers(attempt["token"]),
    )


def test_the_tools_owner_asks_for_another_bots_key_never_its_own_and_the_person_saves_it(api):
    attempt = ops_turn(api)
    refused = ask(api, attempt, for_bot="cpo")
    assert refused.status_code == 403  # an ordinary bot asks only for its own
    put(api, "page-owners/tools", {"bot": "ops"})
    assert ask(api, attempt).status_code == 403  # the Tools owner holds none of the keys it connects
    opened = ask(api, attempt, for_bot="cpo")
    assert opened.status_code == 200, opened.text
    post(api, f"credential-requests/{opened.json()['id']}/save", {"value": SECRET})
    with api.app.state.store.read() as c:
        holders = {
            r["subject"]
            for r in c.execute(
                "SELECT g.subject FROM credential_grants g JOIN credentials k ON k.id=g.credential_id "
                "WHERE k.env='ACME_CRM_TOKEN' AND g.revoked_at IS NULL"
            )
        }
    assert holders == {"bot:cpo"}


def test_the_tools_owner_reads_every_tool_and_who_holds_it_but_changes_nothing(api):
    vault(api)
    tid = tool(api)["id"]
    key(api, tid, SECRET)
    level(api, tid, "bot:cpo", "read")
    ops = ops_turn(api)["token"]
    rows = lambda: {r["id"]: r for r in get(api, "tool-index", ops)["rows"]}  # noqa: E731
    assert "api:" + tid not in rows()  # a bot sees only the tools it is granted
    put(api, "page-owners/tools", {"bot": "ops"})
    assert [w["id"] for w in rows()["api:" + tid]["who"]] == ["cpo"]
    page = get(api, f"api-tools/{tid}", ops)
    assert page["can_manage"] is False and [a["subject"] for a in page["access"]] == ["bot:cpo"]
    assert all(k["preview"] == "" for k in page["keys"])
    level(api, tid, "bot:ops", "read", ops, expected=403)
