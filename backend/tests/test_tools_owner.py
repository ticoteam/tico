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


def test_its_daily_learn_from_use_is_on_for_a_new_company_and_reaches_a_tools_owner_from_before(environment):
    """The routine is seeded on with the bot, and a Tools Owner set up before it existed gets it at the next start, once."""
    from backend import routines

    api = environment(seed={}, cards=BASE)
    with_templates(api, "general-manager", "tools-owner")
    manifest = Path(api.app.state.store.settings.catalog_dir) / "tools-owner" / "bot.yaml"
    shipped = manifest.read_text()
    assert "id: learn-from-use" in shipped
    manifest.write_text(shipped.replace("id: learn-from-use", "id: learn-from-use-gone"))  # as the older template
    machine(api)
    draft(api)
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    with api.app.state.store.read() as c:
        assert routines.row(c, "tools-owner:review-the-tools")["enabled"] == 1
        assert routines.row(c, "tools-owner:learn-from-use") is None
    manifest.write_text(shipped)
    with api.app.state.store.transaction() as c:
        assert ("tools-owner", "learn-from-use") in api.app.state.onboarding.ensure_builtin_routines(c)
    with api.app.state.store.transaction() as c:
        assert api.app.state.onboarding.ensure_builtin_routines(c) == []
        assert routines.row(c, "tools-owner:learn-from-use")["enabled"] == 1


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


def test_the_tools_owner_reads_every_callers_activity_never_a_value_and_others_only_what_they_may(api):
    """`tico tool activity` (tico_tool_activity) over GET /api-tools/{id}/activity: the Tools page's owner reads every
    caller's calls, filtered by window and result; a bot that is not the owner and holds no level is refused."""
    from backend.tests.helpers.mcp import call

    vault(api)
    tid = tool(api)["id"]
    key(api, tid, SECRET)
    level(api, tid, "bot:cpo", "read")
    with api.app.state.store.transaction() as c:
        for actor, method, path, code, result, hours in (
            ("bot:cpo", "GET", "/api/v1/lead/lead_8f2k91x/", 404, "sent", 1),
            ("bot:cpo", "GET", "/api/v1/lead/lead_77ab12y/", 404, "sent", 2),
            ("bot:cpo", "POST", "/api/v1/lead/", None, "refused", 3),
            ("human:ana", "GET", "/api/v1/me/", 200, "sent", 50),
        ):
            c.execute(
                "INSERT INTO tool_calls(id,tool_id,credential_id,actor,bot,access_level,method,path,status_code,result,"
                "detail,duration_ms,created_at) VALUES(?,?,NULL,?,?,'read',?,?,?,?,'',5,?)",
                (
                    H.new_id(),
                    tid,
                    actor,
                    actor.removeprefix("bot:") if actor.startswith("bot:") else "",
                    method,
                    path,
                    code,
                    result,
                    H.shift(H.now(), hours=-hours),
                ),
            )
    ops = ops_turn(api)["token"]
    get(api, f"api-tools/{tid}/activity", ops, expected=404)  # not the owner, no level: it may not know the tool
    put(api, "page-owners/tools", {"bot": "ops"})
    every = get(api, f"api-tools/{tid}/activity", ops)
    assert [r["actor"] for r in every["rows"]] == ["bot:cpo"] * 3 + ["human:ana"]
    assert SECRET not in str(every)
    assert len(get(api, f"api-tools/{tid}/activity?since=24h", ops)["rows"]) == 3
    assert [r["method"] for r in get(api, f"api-tools/{tid}/activity?result=refused", ops)["rows"]] == ["POST"]
    get(api, f"api-tools/{tid}/activity?since=yesterday", ops, expected=422)
    get(api, f"api-tools/{tid}/activity", "cara-test", expected=404)  # a member with no level
    level(api, tid, "human:cara", "read")
    assert "human:ana" not in [r["actor"] for r in get(api, f"api-tools/{tid}/activity", "cara-test")["rows"]]
    err, out = call(api, "tico_tool_activity", {"id": "api:" + tid, "since": "24h"}, ops)
    assert not err, out
    assert out["tool"]["id"] == tid and out["added_by"] == ["human:ana"] and SECRET not in str(out)
    top = out["summary"][0]
    assert (top["caller"], top["endpoint"], top["status_code"], top["count"]) == ("cpo", "/api/v1/lead/:id/", 404, 2)
    from clients import hubcli, remotecli

    args = hubcli.parser().parse_args(["tool", "activity", "close-crm", "--since", "24h", "--result", "refused"])
    assert remotecli.tool_name(args.fn) == "tico_tool_activity" and (args.id, args.since) == ("close-crm", "24h")
