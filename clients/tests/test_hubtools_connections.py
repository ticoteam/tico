"""The connection tools (clients/commands/connections.py) through the table and the `tico` CLI, with a stubbed API: the
GitHub App steps, repository grants that keep a bot's other repositories, and whether a company can work now."""

import json

import pytest

from clients import hubcli, hubtools, remotecli
from clients.commands import connections as hubtools_connections
from clients.tico import APIError

APP = {
    "connected": True,
    "checks_withheld": "Posting Tico's approval check needs Checks write.",
    "slug": "acme-tico",
    "org": "acme",
    "account_type": "Organization",
    "app_id": 7,
    "administration": True,
    "installed": True,
    "authorized": False,
    "callback_url": "",
    "install_url": "/api/v2/github/app/install",
    "settings_url": "https://github.com/organizations/acme/settings/apps/acme-tico",
    "permissions_url": "https://github.com/organizations/acme/settings/apps/acme-tico/permissions",
    "create_url": "",
    "uninstall_url": "",
}
REPOS = {
    "repositories": [
        {"full_name": "acme/bot-builder", "enabled": False, "bot_repo": True, "reachable": True},
        {"full_name": "acme/bot-reviewer", "enabled": False, "bot_repo": True, "reachable": False},
        {"full_name": "acme/web", "enabled": True, "bot_repo": False, "reachable": True, "setup_command": None},
        {"full_name": "acme/docs", "enabled": False, "bot_repo": False, "reachable": True},
    ],
    "new_bot_default": "own",
    "github_connected": True,
    "github_installed": True,
}


class Api:
    """A person's token against stubbed routes; `routes` maps (METHOD, path) to an answer or an APIError."""

    def __init__(self, routes=None, actor="human:ana"):
        self.routes, self.calls = dict(routes or {}), []
        self.actor = actor

    def answer(self, method, path, body=None):
        self.calls.append((method, path, body))
        found = self.routes.get((method, path))
        if isinstance(found, Exception):
            raise found
        if found is None:
            raise AssertionError(f"unexpected {method} {path}")
        return found(body) if callable(found) else json.loads(json.dumps(found))

    def get(self, path, **query):
        if path == "me":
            return {"actor": self.actor, "role": "owner", "kind": "owner"}
        return self.answer("GET", path, {k: v for k, v in query.items() if v is not None} or None)

    def post(self, path, body=None, key=None):
        return self.answer("POST", path, body)

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        return self.answer(method, path, body)


def tool(name, api, **args):
    return hubtools.BY_NAME[name]["fn"](api, args)


def cli(monkeypatch, api, *argv):
    """Run `tico <argv>` through the parser and remotecli against `api`; (exit code, printed JSON)."""
    printed = []
    monkeypatch.setattr(remotecli, "Client", lambda *a, **k: api)
    monkeypatch.setenv("HUB_API_URL", "https://acme.example")
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(a[0]) if not k.get("file") else None)
    code = remotecli.main(hubcli.parser().parse_args(list(argv)))
    return code, json.loads(printed[-1]) if printed else None


def test_every_connection_tool_is_registered_with_a_command_and_offered_to_people_not_the_assistant():
    names = {t["name"] for t in hubtools_connections.MINE}
    commands = {*hubtools_connections.COMMANDS, "health ready"}  # `tico health ready` is a spec (computers.HEALTH)
    assert names == {remotecli.tool_name(fn) for fn in commands}
    for name in names:
        entry = hubtools.BY_NAME[name]
        assert entry in hubtools.TOOLS
        kinds = hubtools.offered_to(entry)
        assert "owner" in kinds and "assistant" not in kinds and "bot" not in kinds, name
    assert "botops" not in hubtools.offered_to(hubtools.BY_NAME["tico_github_connect"])
    assert "botops" in hubtools.offered_to(hubtools.BY_NAME["tico_repo_grant"])


# ----------------------------------------------------------------------------- GitHub App
def test_github_status_says_what_is_connected_where_and_what_is_missing():
    api = Api({("GET", "github/app"): APP, ("GET", "repositories"): REPOS})
    got = tool("tico_github_status", api)
    assert got["connected"] and got["installed"] and got["org"] == "acme" and got["app"] == "acme-tico"
    assert got["repositories"]["ticked"] == ["acme/web"]
    assert [m["key"] for m in got["missing"]] == ["checks_withheld", "bot_repositories_unreachable"]
    assert "acme/bot-reviewer" in got["missing"][1]["detail"]
    assert got["summary"].startswith("Connected to acme (Organization) as the App acme-tico; installed; 1 repository")
    assert "Missing: checks_withheld" in got["summary"]

    bare = tool(
        "tico_github_status", Api({("GET", "github/app"): {"connected": False}, ("GET", "repositories"): REPOS})
    )
    assert bare["connected"] is False and bare["missing"][0]["key"] == "not_connected"
    created = tool(
        "tico_github_status",
        Api(
            {
                ("GET", "github/app"): {**APP, "installed": False, "checks_withheld": None},
                ("GET", "repositories"): REPOS,
            }
        ),
    )
    assert created["missing"][0]["key"] == "not_installed"


def test_github_connect_writes_the_manifest_page_and_finish_and_installed_take_the_next_steps(monkeypatch, tmp_path):
    manifest = {"name": "Acme Tico", "url": "https://acme.example", "note": "it's <ours> & \"quoted\""}
    action = "https://github.com/organizations/acme/settings/apps/new?state=s1"
    api = Api(
        {
            ("GET", "github/app/manifest"): {"action": action, "manifest": manifest, "state": "s1"},
            ("POST", "github/app/finish"): {
                "install_url": "https://github.com/apps/acme-tico/installations/new?state=i"
            },
            ("GET", "github/app/installed"): APIError("http_error", "HTTP 302", 302),
            ("GET", "github/app"): APP,
            ("GET", "repositories"): REPOS,
        }
    )
    made = tool("tico_github_connect", api, org="acme", administration=False)
    assert api.calls[-1] == ("GET", "github/app/manifest", {"org": "acme", "administration": "false"})
    assert made["state"] == "s1" and made["next"][1].startswith("tico github finish s1")
    page = made["page_html"]
    assert 'action="https://github.com/organizations/acme/settings/apps/new?state=s1"' in page
    # The manifest survives the HTML attribute exactly as GitHub must read it back.
    import html as html_lib
    import re

    value = re.search(r'name=manifest value="([^"]*)"', page).group(1)
    assert json.loads(html_lib.unescape(value)) == manifest

    out = tmp_path / "github.html"
    code, printed = cli(monkeypatch, api, "github", "connect", "acme", "--html", str(out))
    assert code == 0 and printed["page"] == str(out) and "page_html" not in printed
    assert out.read_text() == page and printed["next"][0].startswith(f"Open {out} in a browser signed in to GitHub")

    # Task #255: no account is a personal account. Nothing is named, and the page opens signed in as that account.
    made = tool("tico_github_connect", api)
    assert api.calls[-1] == ("GET", "github/app/manifest", None)
    assert made["next"][0].startswith("Open the page in a browser signed in to GitHub as the account the App is for")
    assert "tico_github_connect" in hubtools.BY_NAME and "org" not in hubtools.BY_NAME["tico_github_connect"].get(
        "required", ()
    )
    code, printed = cli(monkeypatch, api, "github", "connect", "--html", str(out))
    assert code == 0 and api.calls[-1] == ("GET", "github/app/manifest", None)
    assert (
        printed["next"][0]
        == f"Open {out} in a browser signed in to GitHub as the account the App is for, and click Create GitHub App"
    )

    code, printed = cli(monkeypatch, api, "github", "finish", "s1")
    assert code == 0 and printed["install_url"].startswith("https://github.com/apps/acme-tico")
    assert ("POST", "github/app/finish", {"state": "s1"}) in api.calls
    # The install route answers a browser with a redirect to Tools; the command records it and prints the status.
    code, printed = cli(monkeypatch, api, "github", "installed")
    assert code == 0 and printed["recorded"] and printed["installed"] and printed["org"] == "acme"


def test_github_installed_still_raises_a_real_refusal():
    api = Api({("GET", "github/app/installed"): APIError("forbidden", "Only the owner connects GitHub", 403)})
    with pytest.raises(APIError, match="Only the owner"):
        tool("tico_github_installed", api)


def test_github_disconnect_names_its_target_and_check_reads_permissions_again():
    api = Api(
        {
            ("GET", "github/app"): APP,
            ("POST", "github/app/disconnect"): {"ok": True},
            ("POST", "github/app/check-permissions"): {**APP, "checks_withheld": None},
            ("GET", "repositories"): REPOS,
        }
    )
    with pytest.raises(APIError, match="--confirm acme"):
        tool("tico_github_disconnect", api, confirm="acme-other")
    assert ("POST", "github/app/disconnect", {}) not in api.calls
    assert tool("tico_github_disconnect", api, confirm="acme") == {"ok": True}
    checked = tool("tico_github_check", api)
    assert "checks_withheld" not in [m["key"] for m in checked["missing"]]


# ----------------------------------------------------------------------------- repositories
def access(mode, chosen=(), effective=(), all_access="write"):
    return {"mode": mode, "all_access": all_access, "chosen": list(chosen), "effective": list(effective)}


def test_repo_grant_adds_one_repository_and_keeps_the_bots_other_chosen_ones(monkeypatch):
    builder = access(
        "chosen",
        chosen=[{"full_name": "acme/docs", "access": "read"}, {"full_name": "acme/web", "access": "read"}],
    )
    api = Api(
        {
            ("GET", "repositories"): REPOS,
            ("GET", "bots/builder/repositories"): builder,
            ("PUT", "bots/builder/repositories"): lambda body: {"effective": body["chosen"]},
        }
    )
    code, got = cli(monkeypatch, api, "repo", "grant", "ACME/web", "builder", "write", "--actions")
    assert code == 0 and got["changed"] and got["repository"] == "acme/web" and got["was"] == "chosen"
    assert api.calls[-1] == (
        "PUT",
        "bots/builder/repositories",
        {
            "mode": "chosen",
            "chosen": [
                {"full_name": "acme/docs", "access": "read"},
                {"full_name": "acme/web", "access": "write", "run_workflows": True},
            ],
        },
    )


def test_repo_grant_on_an_own_bot_switches_to_chosen_and_ticks_only_when_asked():
    api = Api(
        {
            ("GET", "repositories"): REPOS,
            ("GET", "bots/reviewer/repositories"): access("own", chosen=[{"full_name": "acme/web", "access": "write"}]),
            ("PUT", "repositories/acme/docs"): {"enabled": True},
            ("PUT", "bots/reviewer/repositories"): {"effective": []},
        }
    )
    got = tool("tico_repo_grant", api, repository="acme/docs", bot="reviewer", access="read")
    assert got["was"] == "own" and "not ticked" in got["warning"]
    assert api.calls[-1][2] == {"mode": "chosen", "chosen": [{"full_name": "acme/docs", "access": "read"}]}
    assert ("PUT", "repositories/acme/docs", {"enabled": True}) not in api.calls
    got = tool("tico_repo_grant", api, repository="acme/docs", bot="reviewer", access="read", tick=True)
    assert got["ticked_now"] and "warning" not in got
    assert ("PUT", "repositories/acme/docs", {"enabled": True}) in api.calls


def test_repo_grant_refuses_to_narrow_an_all_repos_bot_and_an_unknown_repository():
    api = Api(
        {
            ("GET", "repositories"): REPOS,
            ("GET", "bots/ops/repositories"): access("all", all_access="read"),
        }
    )
    assert tool("tico_repo_grant", api, repository="acme/web", bot="ops", access="read")["changed"] is False
    with pytest.raises(APIError, match="tico bot repos ops --chosen acme/web:write"):
        tool("tico_repo_grant", api, repository="acme/web", bot="ops")
    with pytest.raises(APIError, match="tico repo refresh"):
        tool("tico_repo_grant", api, repository="acme/nope", bot="ops")
    with pytest.raises(ValueError, match="need write"):
        tool("tico_repo_grant", api, repository="acme/web", bot="ops", access="read", edit_workflows=True)
    assert [c for c in api.calls if c[0] == "PUT"] == []


def test_repo_revoke_takes_one_away_and_says_when_there_is_nothing_to_take():
    api = Api(
        {
            ("GET", "bots/builder/repositories"): access(
                "chosen",
                chosen=[
                    {"full_name": "acme/web", "access": "write", "run_workflows": True},
                    {"full_name": "acme/docs", "access": "read"},
                ],
                effective=[{"full_name": "acme/bot-builder", "access": "write"}],
            ),
            ("PUT", "bots/builder/repositories"): {"effective": []},
            ("GET", "bots/ops/repositories"): access("all"),
        }
    )
    got = tool("tico_repo_revoke", api, repository="acme/web", bot="builder")
    assert got["changed"] and api.calls[-1][2] == {
        "mode": "chosen",
        "chosen": [{"full_name": "acme/docs", "access": "read"}],
    }
    own = tool("tico_repo_revoke", api, repository="acme/bot-builder", bot="builder")
    assert own["changed"] is False and "own repository" in own["detail"]
    with pytest.raises(APIError, match="All ticked repos"):
        tool("tico_repo_revoke", api, repository="acme/web", bot="ops")


def test_repo_show_lists_who_reads_and_writes_a_repository(monkeypatch):
    bots = [
        {"slug": "builder", "repo": "acme/bot-builder", "state": "active"},
        {"slug": "reviewer", "repo": "bot-reviewer", "state": "active"},
        {"slug": "ops", "repo": "acme/bot-ops", "state": "active"},
        {"slug": "gone", "repo": "acme/bot-gone", "state": "archived"},
        {"slug": "hidden", "repo": "", "state": "active"},
    ]
    api = Api(
        {
            ("GET", "repositories"): REPOS,
            ("GET", "bots"): bots,
            ("GET", "bots/builder/repositories"): access(
                "chosen",
                chosen=[{"full_name": "acme/web", "access": "write", "run_workflows": True}],
                effective=[{"full_name": "acme/web", "access": "write"}],
            ),
            ("GET", "bots/reviewer/repositories"): access(
                "chosen",
                chosen=[{"full_name": "acme/web", "access": "read"}],
                effective=[{"full_name": "acme/web", "access": "read"}],
            ),
            ("GET", "bots/ops/repositories"): access(
                "all", all_access="read", effective=[{"full_name": "acme/web", "access": "read"}]
            ),
            ("GET", "bots/hidden/repositories"): APIError("forbidden", "This bot is not visible to you", 403),
        }
    )
    code, got = cli(monkeypatch, api, "repo", "show", "acme/web")
    assert code == 0 and got["repository"]["full_name"] == "acme/web"
    assert got["bots"] == [
        {"bot": "builder", "access": "write", "via": "chosen", "run_workflows": True},
        {"bot": "reviewer", "access": "read", "via": "chosen"},
        {"bot": "ops", "access": "read", "via": "all"},
    ]
    assert got["writers"] == ["builder"] and got["readers"] == ["reviewer", "ops"]
    assert ("GET", "bots/gone/repositories", None) not in api.calls


def test_repo_refresh_default_and_setup_command_reach_their_routes():
    api = Api(
        {
            ("POST", "repositories/refresh"): REPOS,
            ("GET", "repositories"): REPOS,
            ("PUT", "repositories/settings"): lambda body: body,
            ("PUT", "repositories/acme/web"): lambda body: {"full_name": "acme/web", **body},
        }
    )
    assert tool("tico_repo_refresh", api)["ticked"] == ["acme/web"]
    assert tool("tico_repo_default", api) == {"new_bot_default": "own"}
    assert tool("tico_repo_default", api, mode="all") == {"new_bot_default": "all"}
    assert tool("tico_repo_setup_command", api, repository="acme/web", command="npm ci")["setup_command"] == "npm ci"


# ----------------------------------------------------------------------------- is the company able to work
HEALTH = {
    "checks": [
        {"id": "computers", "status": "ok", "summary": "1 computer online."},
        {"id": "github", "status": "ok", "summary": "Connected to acme."},
        {"id": "publish", "status": "warn", "summary": "Some bots' history is not on GitHub yet."},
        {"id": "queue", "status": "warn", "summary": "1 bot with work that is not starting."},
        {"id": "backups", "status": "ok", "summary": "Copied to s3."},
    ],
    "computers": [{"id": "r1", "label": "Shared cloud machine", "online": True}],
    "checked": "2026-11-11T08:00:00.000000Z",
}
BOTS = [
    {"slug": "builder", "display_name": "Builder", "state": "planned", "onboarding_state": "needs_setup"},
    {"slug": "reviewer-1", "display_name": "Reviewer 1", "state": "active", "onboarding_state": "onboarded"},
    {"slug": "botops", "display_name": "BotOps", "state": "active", "status": {"state": "quarantined"}},
    {"slug": "writer", "display_name": "Writer", "state": "paused"},
    {"slug": "old", "state": "archived"},
]


def ready_api(health=HEALTH, bots=BOTS, app=APP):
    return Api(
        {
            ("GET", "health"): health,
            ("GET", "bots"): bots,
            ("GET", "github/app"): app,
            ("GET", "repositories"): REPOS,
        }
    )


def test_health_ready_names_each_blocker_with_its_fix_and_exits_1(monkeypatch):
    code, got = cli(monkeypatch, ready_api(), "health", "ready")
    assert code == 1 and got["ready"] is False
    assert [(b["check"], b.get("bot")) for b in got["blockers"]] == [
        ("bot_planned", "builder"),
        ("bot_quarantined", "botops"),
    ]
    assert got["blockers"][0]["fix"] == "tico bot go-live builder"
    assert {w["check"] for w in got["warnings"]} == {"bot_paused", "publish", "queue"}
    assert got["bots"]["planned"] == ["builder"] and got["bots"]["total"] == 4
    assert got["computers"]["online"] == ["Shared cloud machine"] and got["github"]["installed"] is True


def test_health_ready_for_named_bots_only_and_ready_exits_0(monkeypatch):
    code, got = cli(monkeypatch, ready_api(), "health", "ready", "--bot", "reviewer-1")
    assert code == 0 and got["ready"] is True and got["summary"].startswith("Ready")
    assert {w["check"] for w in got["warnings"]} >= {"bot_planned", "bot_quarantined"}
    code, got = cli(monkeypatch, ready_api(), "health", "ready", "--bot", "nobody")
    assert code == 1 and got["blockers"][0]["check"] == "unknown_bots"


def test_health_ready_blocks_on_no_computer_and_on_github_unless_told_not_to():
    offline = {**HEALTH, "computers": [{"id": "r1", "label": "Mac", "online": False}]}
    got = tool("tico_health_ready", ready_api(health=offline, bots=[]))
    assert [b["check"] for b in got["blockers"]] == ["computers"] and got["computers"]["offline"] == ["Mac"]

    unplugged = ready_api(bots=[], app={"connected": False})
    assert [b["check"] for b in tool("tico_health_ready", unplugged)["blockers"]] == ["github"]
    assert tool("tico_health_ready", unplugged, github=False)["ready"] is True

    # Only the Owner reads the App itself: an admin's answer comes from Health's own GitHub check.
    admin = ready_api(bots=[])
    admin.routes[("GET", "github/app")] = APIError("forbidden", "Only the owner connects GitHub", 403)
    got = tool("tico_health_ready", admin)
    assert got["ready"] is True and got["github"] == {"connected": True, "summary": "Connected to acme."}

    bad = {
        **HEALTH,
        "checks": HEALTH["checks"] + [{"id": "backups_off", "status": "bad", "summary": "Backups are off."}],
    }
    assert [b["check"] for b in tool("tico_health_ready", ready_api(health=bad, bots=[]))["blockers"]] == [
        "backups_off"
    ]
