"""A tool's Settings (backend/tool_settings.py) and Tools' vendors: a setting reaches every run that holds a level on its
tool (a turn and a watcher alike) and no one else's, a key of the same variable wins, a value that looks like a key is
refused, a computer's file brings its non-secret variables in as settings, the one-time move turns stored keys that are
settings into settings only where every holder keeps the variable, how a key reaches runs never puts a tool under Needs
you, and tools of one vendor share it."""

import json

from backend import tool_settings
from backend.credentials import FILE_MIGRATION
from backend.tests.helpers.api import assign, claim, get, headers, post, ready, runner
from backend.tests.helpers.api_tools import key, level, tool
from backend.tests.helpers.credentials import explicit_only, setup

SECRET = "acme-close-synthetic-rw-0011223344"


def setting(api, tid, name, value, token="ana-test", expected=200):
    return post(api, f"api-tools/{tid}/settings", {"name": name, "value": value}, token, expected=expected)


def turn(api, bot="ops"):
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    post(api, "chat/" + bot, {"text": "Use only the synthetic fixture"})
    return machine, claim(api, machine)


def delivered(api, attempt):
    return {i["env"]: i for i in get(api, "credential-runtime", attempt["token"])["credentials"]}


def test_a_setting_reaches_every_run_holding_a_level_on_its_tool_and_never_counts_as_a_key(api):
    setup(api)
    tid = tool(api)["id"]  # Close CRM, on the proxy
    key(api, tid, SECRET)
    other = tool(api, "Acme internal")["id"]
    key(api, other, SECRET + "-other")
    explicit_only(api)  # what explicit grants alone give
    level(api, tid, "bot:ops", "read")
    setting(api, tid, "CLOSE_REGION", "eu")
    setting(api, other, "ACME_PROJECT_ID", "acme-7")  # ops holds nothing of Acme internal
    machine, attempt = turn(api)
    items = delivered(api, attempt)
    assert items["CLOSE_REGION"]["value"] == "eu" and items["CLOSE_REGION"]["kind"] == "setting"
    assert items["CLOSE_API_KEY"]["value"].startswith("tpx1.")  # the key itself stays on the proxy
    assert "ACME_PROJECT_ID" not in items
    watched = get(api, "runner-watcher-credentials?bot=ops", machine["token"])["credentials"]
    assert {i["env"]: i["value"] for i in watched if i["kind"] == "setting"} == {"CLOSE_REGION": "eu"}
    assert "CLOSE_REGION" in get(api, "runner-credential-grants", machine["token"])["bots"]["ops"]
    # In the clear on the tool, not a key; the tool's status is its keys' alone.
    page = get(api, f"api-tools/{tid}")
    assert [(s["name"], s["value"]) for s in page["settings"]] == [("CLOSE_REGION", "eu")]
    assert [k["env"] for k in page["keys"]] == ["CLOSE_API_KEY"]
    # A key the run holds under the same variable wins: a run never gets two values for one name.
    post(
        api,
        f"api-tools/{other}/keys",
        {"name": "Region", "value": "acme-synthetic-region-4455", "access_level": "read", "env": "CLOSE_REGION"},
    )
    level(api, other, "bot:ops", "read")
    items = get(api, "credential-runtime", attempt["token"])["credentials"]
    assert [i["kind"] for i in items if i["env"] == "CLOSE_REGION"] == ["api_key"]
    assert next(i for i in items if i["env"] == "ACME_PROJECT_ID")["value"] == "acme-7"  # a level on Acme now
    # Taken away with the level; gone with the tool.
    level(api, tid, "bot:ops", "none")
    level(api, other, "bot:ops", "none")
    assert "CLOSE_REGION" not in delivered(api, attempt)
    assert api.delete(f"/api/v2/api-tools/{tid}", headers=headers()).json()["deleted"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tool_settings WHERE tool_id=?", (tid,)).fetchone()[0] == 0


def test_only_a_plain_value_under_a_free_name_is_a_setting_and_only_a_manager_changes_one(api):
    setup(api)
    tid = tool(api)["id"]
    key(api, tid, SECRET)
    setting(api, tid, "CLOSE_URL", "https://user:synthetic-pass@close.acme.example", expected=422)
    setting(api, tid, "CLOSE_URL", "https://close.acme.example/v1?api_key=synthetic", expected=422)
    setting(api, tid, "CLOSE_HOST", "sk-synthetic0123456789abcdef", expected=422)
    setting(api, tid, "CLOSE_HOST", "line one\nline two", expected=422)
    setting(api, tid, "CLOSE_SESSION_TOKEN", "plain", expected=422)
    setting(api, tid, "TICO_REGION", "eu", expected=422)  # Tico's own names stay Tico's
    setting(api, tid, "CLOSE_API_KEY", "eu", expected=409)  # the tool's key variable
    made = setting(api, tid, "CLOSE_HOST", "api.close.acme.example")
    setting(api, tid, "CLOSE_HOST", "again.acme.example", expected=409)
    setting(api, tid, "CLOSE_REGION", "eu", "cara-test", expected=403)
    path = f"/api/v2/api-tools/{tid}/settings/{made['id']}"
    r = api.patch(path, json={"value": "eu.close.acme.example"}, headers=headers())
    assert r.status_code == 200 and r.json()["value"] == "eu.close.acme.example", r.text
    assert api.patch(path, json={"value": "x"}, headers=headers("cara-test")).status_code == 403
    assert api.delete(path, headers=headers("cara-test")).status_code == 403
    assert api.delete(path, headers=headers()).json()["deleted"]
    assert get(api, f"api-tools/{tid}")["settings"] == []


def test_a_computers_file_brings_its_non_secret_variables_in_as_settings_on_the_bots_vendor_tool(api):
    setup(api)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key=?", (json.dumps(["ops"]), FILE_MIGRATION))
    values = {
        "POSTHOG_HOST": "https://eu.posthog.example",
        "POSTHOG_PROJECT_ID": "4242",
        "POSTHOG_API_KEY": "phx-synthetic-file-key-5150",
        "AWS_PROFILE": "acme-ops",  # no AWS tool holds a key for ops: stays a key, as before
        "ACME_URL": "https://ops:synthetic-pass@acme.example",  # credentials in it: a key
    }
    body = {"bot": "ops", "credentials": [{"env": k, "value": v} for k, v in values.items()]}
    post(api, "runner-credential-migration", body, machine["token"])
    with api.app.state.store.read() as c:
        keys = {r["env"] for r in c.execute("SELECT env FROM credentials")}
        settings = {
            (r["tool_id"], r["name"]): r["value"] for r in c.execute("SELECT tool_id, name, value FROM tool_settings")
        }
        posthog = c.execute("SELECT tool_id FROM credentials WHERE env='POSTHOG_API_KEY'").fetchone()[0]
    assert keys == {"POSTHOG_API_KEY", "AWS_PROFILE", "ACME_URL"}
    assert settings == {
        (posthog, "POSTHOG_HOST"): "https://eu.posthog.example",
        (posthog, "POSTHOG_PROJECT_ID"): "4242",
    }
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = claim(api, machine, "ops")
    items = delivered(api, attempt)
    assert {k: items[k]["value"] for k in values} == values
    assert get(api, "runner-credential-grants", machine["token"])["bots"]["ops"].count("POSTHOG_HOST") == 1


def test_the_one_time_move_makes_settings_of_stored_keys_only_where_every_holder_still_gets_them(api):
    setup(api)
    store, vault = api.app.state.store, api.app.state.vault

    def stored(name, value):
        made = post(api, "credentials", {"name": name, "env": name, "secret": value})
        return made["id"]

    def grant(cid, bot):
        post(api, f"credentials/{cid}/grants", {"subject": "bot:" + bot})

    api_key = stored("POSTHOG_API_KEY", "phx-synthetic-move-key-8080")
    host = stored("POSTHOG_HOST", "https://eu.posthog.example")
    region = stored("POSTHOG_REGION", "eu")
    profile = stored("AWS_PROFILE", "acme-ops")
    for cid in (api_key, host, region, profile):
        grant(cid, "ops")
    grant(region, "finance")  # finance holds no PostHog key: moving it would take the variable away
    with store.read() as c:
        posthog = c.execute("SELECT tool_id FROM credentials WHERE id=?", (api_key,)).fetchone()[0]
        made_for_host = c.execute("SELECT tool_id FROM credentials WHERE id=?", (host,)).fetchone()[0]
    assert made_for_host and made_for_host != posthog
    with store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (tool_settings.MOVED,))
    assert tool_settings.move_once(store, vault) == 1
    assert tool_settings.move_once(store, vault) == 0  # once
    with store.read() as c:
        left = {r["env"] for r in c.execute("SELECT env FROM credentials")}
        settings = {(r["tool_id"], r["name"]): r["value"] for r in c.execute("SELECT * FROM tool_settings")}
        gone = c.execute("SELECT 1 FROM tools WHERE id=?", (made_for_host,)).fetchone()
    assert left == {"POSTHOG_API_KEY", "POSTHOG_REGION", "AWS_PROFILE"}
    assert settings == {(posthog, "POSTHOG_HOST"): "https://eu.posthog.example"}
    assert gone is None  # the tool made for the host alone went with it
    machine, attempt = turn(api)
    items = delivered(api, attempt)
    assert items["POSTHOG_HOST"]["value"] == "https://eu.posthog.example" and items["POSTHOG_HOST"]["kind"] == "setting"
    assert {"POSTHOG_REGION", "AWS_PROFILE", "POSTHOG_API_KEY"} <= set(items)


def test_how_a_key_reaches_runs_never_needs_you(api):
    setup(api)
    tid = tool(api, "Acme Billing")["id"]  # no address: its key goes to computers
    key(api, tid, SECRET)
    row = next(r for r in get(api, "tool-index")["rows"] if r["id"] == "api:" + tid)
    assert (row["delivery"], row["needs_you"], row["status"]["text"]) == ("computer", False, "Not checked")
    close = "api:" + tool(api)["id"]
    assert next(r for r in get(api, "tool-index")["rows"] if r["id"] == close)["delivery"] == "proxy"


def keyed(api, name, env):
    """An API tool as a computer's file left it: named for its variable or its vendor, with its one key."""
    tid = tool(api, name)["id"]
    post(api, f"api-tools/{tid}/keys", {"name": name, "value": "acme-synthetic-" + env.lower(), "env": env})
    return "api:" + tid


def test_tools_list_one_row_a_vendor_and_a_vendors_page_holds_its_website_mcp_token_and_keys_in_one_read(api):
    setup(api)
    site = post(api, "website-tools", {"sites": ["raygun.com"]})
    prod = keyed(api, "Raygun production", "RAYGUN_MCP_TOKEN")
    staging = keyed(api, "Raygun dev and staging", "RAYGUN_PAT")
    setting(api, staging[4:], "RAYGUN_REGION", "eu")
    aws = [
        keyed(api, n, e)
        for n, e in (
            ("AWS_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
            ("TIDY_AWS_READONLY", "TIDY_AWS_READONLY"),
            ("AWS tidy-security", "AWS_SECURITY_KEY"),
        )
    ]
    google = [keyed(api, "GSC_SERVICE_ACCOUNT", "GSC_SERVICE_ACCOUNT"), "api:" + tool(api, "Google Ads")["id"]]
    github = keyed(api, "GH_TOKEN", "GH_TOKEN")
    # An MCP server joins its vendor by its address, whatever it is named.
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,updated_at,"
            "mcp_url) VALUES('errors-mcp','Errors MCP','','','bearer','','proxy','human:ana',?,?,?)",
            ("2026-11-16T00:00:00Z", "2026-11-16T00:00:00Z", "https://api.raygun.com/v3/mcp"),
        )
    mcp = "api:errors-mcp"
    rows = {r["id"]: r for r in get(api, "tool-index")["rows"]}
    vendors = {i: rows[i]["vendor"] for i in [prod, staging, mcp, "website:" + site["id"], *aws, *google, github]}
    assert vendors == {
        prod: "raygun",
        mcp: "raygun",
        staging: "raygun",
        "website:" + site["id"]: "raygun",
        **dict.fromkeys(aws, "aws"),
        **dict.fromkeys(google, "google"),
        github: "github",
    }
    assert rows[prod]["vendor_name"] == "Raygun" and rows[aws[0]]["vendor_name"] == "AWS"
    # The vendor's page, in one read: the website, the MCP token and the key.
    page = get(api, "tool-vendors/raygun")
    assert page["name"] == "Raygun" and {r["id"] for r in page["rows"]} == {prod, staging, mcp, "website:" + site["id"]}
    by_id = {r["id"]: r for r in page["rows"]}
    # A summary: who holds each API tool and whether it is an MCP server; its keys and settings stay on its own page.
    assert by_id[mcp]["mcp"] is True and by_id[prod]["mcp"] is False and by_id[prod]["access_total"] == 0
    assert "keys" not in by_id[prod] and "settings" not in by_id[staging]
    assert [k["env"] for k in get(api, "api-tools/" + prod[4:])["keys"]] == ["RAYGUN_MCP_TOKEN"]
    assert "acme-synthetic" not in json.dumps(page)
    # A member who holds none of its keys sees only what everyone sees of it: the website. Its API tools are hers to
    # ask for by name alone, which says no vendor.
    assert [r["id"] for r in get(api, "tool-vendors/raygun", "cara-test")["rows"]] == ["website:" + site["id"]]
    get(api, "tool-vendors/nobody", expected=404)
    # A person moves a tool to another vendor.
    other = keyed(api, "Ledger export", "LEDGER_KEY")
    assert (
        rows.get(other) is None
        and next(r for r in get(api, "tool-index")["rows"] if r["id"] == other)["vendor"] == "ledger"
    )
    r = api.patch(f"/api/v2/api-tools/{other[4:]}", json={"service": "raygun"}, headers=headers())
    assert r.status_code == 200 and r.json()["vendor"] == "raygun", r.text
    # A bot at Read on a key nothing can hold to reads (no address to proxy through): the vendor's page marks its row.
    level(api, other[4:], "bot:ops", "read")
    ledger = next(r for r in get(api, "tool-vendors/raygun")["rows"] if r["id"] == other)
    assert [(a["subject"], a.get("read_not_enforced")) for a in ledger["access"]] == [("bot:ops", True)]


def test_a_vendor_is_read_from_a_service_a_name_a_variable_or_a_domain():
    from backend.tool_services import vendor_of

    cases = {
        ("aws-production", "Aws Production", ()): "aws",
        ("github-satellites", "Github Satellites", ()): "github",
        ("google-ads", "Google Ads", ()): "google",
        ("hub-intake", "Hub Intake", ()): "hub-intake",  # Tico's own: no other vendor
        ("", "SLACK_APP_TOKEN", ("SLACK_APP_TOKEN",)): "slack",
        ("", "POSTHOG_HOST", ("POSTHOG_HOST",)): "posthog",
        ("", "TWILIO_API_SECRET", ("TWILIO_API_SECRET",)): "twilio",
        ("", "GOOGLE_ADS_CLIENT_ID", ("GOOGLE_ADS_CLIENT_ID",)): "google",
        ("", "Prod key", ()): "",
    }
    assert {k: vendor_of(*k) for k in cases} == cases
    assert vendor_of(name="raygun.com", host="raygun.com") == vendor_of(host="app.raygun.com") == "raygun"
    assert vendor_of(host="app.close.com") == "close-crm"
