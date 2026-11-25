"""The Tools registry (backend/tool_guides.py, docs/connect-tools.md, "Tools registry"): shipped guides, the
company's guides and built tools, and every vendor in Tools, one list; how the caller reaches each tool, never a
value; who keeps what.

Ana owns the company, Ben is an admin, Cara a member who does not manage ops; ana and ben are credential admins."""

from backend.store import H
from backend.tool_proxy import url_env
from backend.tests.helpers.api import get, headers, put, setup_attempt
from backend.tests.helpers.api_tools import key, level, tool
from backend.tests.helpers.credentials import explicit_only, setup
from backend.tests.helpers.openapi_v2 import conforms

SECRET = "acme-stripe-synthetic-rw-0011223344"


def rows(api, token="ana-test", query=""):
    return {r["slug"]: r for r in get(api, "tool-guides" + query, token)["tool_guides"]}


def delete(api, path, token="ana-test", expected=200):
    r = api.delete("/api/v2/" + path, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def call(api, tid, bot="ops"):
    """One call through Tico's proxy by `bot`, as the proxy logs it."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO tool_calls(id,tool_id,actor,bot,access_level,method,path,status_code,result,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (H.new_id(), tid, "bot:" + bot, bot, "read", "GET", "/v1/charges", 200, "sent", H.now()),
        )


def test_the_registry_is_the_shipped_guides_the_companys_and_every_vendor_in_tools(api):
    setup(api)
    stripe = tool(api, "Stripe")["id"]
    key(api, stripe, SECRET)
    close = tool(api)["id"]  # Close CRM: a vendor Tico ships no guide for
    call(api, stripe)
    found = rows(api)
    assert found["stripe"]["has_guide"] and found["stripe"]["connections"] == 1
    assert (found["stripe"]["used_by"], found["stripe"]["origin"]) == (1, "vendor")
    assert found["stripe"]["last_used_at"]
    vendor = next(r["vendor"] for r in get(api, "tool-index")["rows"] if r["id"] == "api:" + close)
    assert (found[vendor]["has_guide"], found[vendor]["summary"], found[vendor]["connections"]) == (False, "", 1)
    assert found["jq"]["has_guide"] and found["jq"]["connections"] == 0  # shipped, nothing in Tools

    # A new slug no vendor has is a tool built here: words on how to use it come with it.
    built = {"summary": "Weekly refunds by reason", "source": "acme/scripts:refunds.py"}
    put(api, "tool-guides/refund-report", built, expected=422)
    saved = put(api, "tool-guides/refund-report", {**built, "instructions": "python3 refunds.py --week"})
    assert (saved["origin"], saved["owner"], saved["created_by"]) == ("built", "human:ana", "human:ana")
    assert set(rows(api, query="?origin=built")) == {"refund-report"}
    assert set(rows(api, query="?q=refunds+reason")) == {"refund-report"}

    # The company's words go over the shipped guide; an alias is the vendor's guide.
    put(api, "tool-guides/stripe", {"guidance": "Use the read key for reports."})
    page = get(api, "tool-guides/stripe")
    assert page["shipped"] and "smallest unit" in page["instructions"]
    assert (page["guidance"], page["updated_by"]) == ("Use the read key for reports.", "human:ana")
    assert put(api, "tool-guides/gh", {"category": "Code"})["slug"] == "github"
    assert rows(api)["github"]["category"] == "code"
    assert get(api, "tool-guides/psql")["slug"] == "postgres"  # a command names its guide
    put(api, "tool-guides/stripe", {"guidance": "STRIPE_API_KEY=" + SECRET}, expected=422)  # never a key

    # Archiving hides a guide, a shipped one too; a vendor in Tools stays, with no guide.
    delete(api, "tool-guides/jq")
    delete(api, "tool-guides/stripe")
    found = rows(api)
    assert "jq" not in found and not found["stripe"]["has_guide"] and found["stripe"]["connections"] == 1
    get(api, "tool-guides/jq", expected=404)
    put(api, "tool-guides/jq", {"guidance": "Back"})  # writing it brings it back
    assert rows(api)["jq"]["has_guide"]
    with api.app.state.store.read() as c:
        actions = [r[0] for r in c.execute("SELECT action FROM events WHERE action LIKE 'tool_guide.%'")]
    assert {"tool_guide.created", "tool_guide.updated", "tool_guide.archived"} <= set(actions)


def test_the_callers_access_names_variables_and_the_ask_never_a_value(api):
    setup(api)
    stripe = tool(api, "Stripe")["id"]
    key(api, stripe, SECRET)
    explicit_only(api)  # no Read by default: ops holds nothing yet
    bot = setup_attempt(api)[2]["token"]
    r = api.get("/api/v2/tool-guides/stripe", headers=headers(bot))
    assert r.status_code == 200 and SECRET not in r.text
    (row,) = [a for a in r.json()["access"] if a["id"] == "api:" + stripe]
    assert row["level"] == "none" and row["request"] == f"tico access request tool:{stripe} --level read"
    level(api, stripe, "bot:ops", "read")
    r = api.get("/api/v2/tool-guides/stripe", headers=headers(bot))
    assert r.status_code == 200 and SECRET not in r.text
    (row,) = [a for a in r.json()["access"] if a["id"] == "api:" + stripe]
    assert (row["level"], row["arrives"], row["request"]) == ("read", "proxy", None)
    assert row["url_env"] == url_env(stripe)
    assert all(SECRET not in name for name in row["env"])
    person = get(api, "tool-guides/stripe")
    assert [a["level"] for a in person["access"]] == ["none"] and person["access"][0]["request"] is None


def test_who_may_archive_a_guide_and_who_keeps_an_agents_list(api):
    setup(api)
    stripe = tool(api, "Stripe")["id"]
    key(api, stripe, SECRET)
    bot = setup_attempt(api)[2]["token"]
    put(api, "tool-guides/refund-report", {"summary": "Refunds", "instructions": "python3 refunds.py"})
    delete(api, "tool-guides/refund-report", "cara-test", expected=403)
    delete(api, "tool-guides/refund-report", bot, expected=403)  # an agent that is not the Tools page's owner

    # An agent keeps its own list; a member who does not manage it does not, and an agent keeps no other's.
    put(api, "bots/ops/tool-guides/duckdb", {"note": "Weekly revenue"}, "cara-test", expected=403)
    put(api, "bots/inbox/tool-guides/duckdb", {"note": "x"}, bot, expected=403)
    put(api, "bots/ops/tool-guides/duckdb", {"note": "Weekly revenue from the Stripe export"}, bot)
    put(api, "bots/ops/tool-guides/nothing-here", {"note": "x"}, bot, expected=404)
    put(api, "tool-guides/weekly-revenue", {"summary": "Revenue", "instructions": "duckdb -c ..."}, bot)
    put(api, "bots/ops/tool-guides/weekly-revenue", {"note": "Mine"}, bot)
    call(api, stripe)
    mine = {r["slug"]: r for r in get(api, "bots/ops/tool-guides")["tool_guides"]}
    assert (mine["duckdb"]["listed"], mine["duckdb"]["note"]) == (True, "Weekly revenue from the Stripe export")
    assert mine["weekly-revenue"]["built"] and not mine["duckdb"]["built"]
    assert (mine["stripe"]["listed"], mine["stripe"]["access"], mine["stripe"]["uses"]) == (False, "proxy", 1)
    users = {u["id"]: u for u in get(api, "tool-guides/duckdb")["used_by"]}
    assert users["ops"]["listed"] and users["ops"]["note"].startswith("Weekly revenue")
    assert set(rows(api, bot, "?bot=ops")) == {"duckdb", "weekly-revenue", "stripe"}
    document = get(api, "openapi.json")
    ops = {op["operationId"]: op for paths in document["paths"].values() for op in paths.values()}
    for operation, answer in (
        ("listToolGuides", get(api, "tool-guides")),
        ("getToolGuide", get(api, "tool-guides/duckdb")),
        ("listBotToolGuides", get(api, "bots/ops/tool-guides")),
    ):
        schema = ops[operation]["responses"]["200"]["content"]["application/json"]["schema"]
        assert conforms(answer, schema, document) is None, (operation, conforms(answer, schema, document))
    delete(api, "bots/ops/tool-guides/duckdb", bot)
    assert "duckdb" not in {r["slug"] for r in get(api, "bots/ops/tool-guides")["tool_guides"]}

    # The Tools page's owner archives; so do credential admins.
    put(api, "page-owners/tools", {"bot": "ops"})
    delete(api, "tool-guides/refund-report", bot)
    delete(api, "tool-guides/weekly-revenue", "ben-test")
    assert not {"refund-report", "weekly-revenue"} & set(rows(api))
