"""Tools (`GET /api/v2/tool-index`, backend/api_tools.py): one list of every way the company reaches the outside world,
filtered per reader with connections.py's rules. The owner and admins see every row; a member sees their own and the
company's rows they may read; a bot the company's; incoming webhooks are not tools (they live in Automations).

Ana owns the company, Ben is an admin (a member after `as_member`), Cara is a member."""

from backend import api_tools
from backend.store import H
from backend.tests.test_api import api, as_member, get, put, setup_attempt  # noqa: F401  (fixture)
from backend.tests.test_inbound_audience import narrow


def seed(api):
    narrow(api)                                            # ibx_a, ibx_b, ibx_c; Cara is in East, under Sales
    put(api, "inbound/inboxes/ibx_b/audience", {"audience": "team", "group_id": "sales"})
    put(api, "inbound/inboxes/ibx_c/audience", {"audience": "team", "group_id": "ops-team"})
    now = H.now()
    with api.app.state.store.transaction() as c:
        for wid, who in (("wh-ben", "human:ben"), ("wh-cara", "human:cara")):
            c.execute("INSERT INTO webhooks(id,name,url,host,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (wid, "Hook " + wid, "https://hooks.acme.example/x", "hooks.acme.example", who, now, now))
        c.execute("INSERT INTO triggers(id,name,secret_hash,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                  ("tr-1", "Orders", "x", "human:ana", now, now))


def rows(api, token="ana-test", query=""):
    return {r["id"].replace("number:", "inbox:").replace("tico:", "inbox:"): r
            for r in get(api, "tool-index" + query, token)["rows"]}


def ids(api, token):
    return set(rows(api, token))


def test_admins_see_every_row_and_members_their_own_and_what_they_may_read(api):
    seed(api)
    everything = {"inbox:ibx_a", "inbox:ibx_b", "inbox:ibx_c", "webhook:wh-ben", "webhook:wh-cara"}
    assert everything <= ids(api, "ana-test")
    assert everything <= ids(api, "ben-test")              # an admin
    cara = ids(api, "cara-test")
    assert {"inbox:ibx_a", "inbox:ibx_b", "webhook:wh-cara"} <= cara
    assert not cara & {"inbox:ibx_c", "webhook:wh-ben"}    # another group's inbox, another person's webhook
    assert "slack" not in cara and "github" not in cara    # not connected: only admins see them, to connect
    assert {"slack", "github"} <= ids(api, "ana-test")
    assert not any(i.startswith("trigger") for i in ids(api, "ana-test"))
    as_member(api, "ben@acme.example")
    ben = ids(api, "ben-test")
    assert "webhook:wh-ben" in ben and not ben & {"inbox:ibx_c", "webhook:wh-cara"}


def test_a_row_says_what_it_is_its_status_and_where_it_opens(api):
    seed(api)
    got = get(api, "tool-index", "cara-test")
    assert got["counts"]["all"] == len(got["rows"]) and got["counts"]["websites"] == 0
    row = rows(api, "cara-test")["inbox:ibx_b"]
    assert row["kind"] in ("mailbox", "number") and row["href"] == "#/tools/number/ibx_b"
    assert set(row) >= {"id", "kind", "name", "detail", "way", "status", "who", "who_total", "needs_you", "href"}
    assert row["status"]["tone"] in ("ok", "wait", "fail", "run", "idle")


def test_bots_and_agents_see_only_company_rows_they_may_read(api):
    seed(api)
    attempt = setup_attempt(api)[2]
    seen = ids(api, attempt["token"])
    assert "inbox:ibx_a" in seen
    assert not seen & {"inbox:ibx_b", "inbox:ibx_c", "webhook:wh-ben", "webhook:wh-cara"}


def walk(api, token, limit):
    pages, cursor = [], ""
    while True:
        got = get(api, f"tool-index?limit={limit}" + (f"&cursor={cursor}" if cursor else ""), token)
        pages.append([r["id"] for r in got["rows"]])
        if not got["next"]:
            return pages
        cursor = got["next"]


def test_the_list_is_paged_and_byte_capped(api, monkeypatch):
    seed(api)
    now = H.now()
    with api.app.state.store.transaction() as c:          # more webhooks than one page, and Cara's among them
        for i in range(7):
            c.execute("INSERT INTO webhooks(id,name,url,host,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (f"wh-{i:02d}", f"Hook {i}", "https://hooks.acme.example/x", "hooks.acme.example",
                       "human:cara" if i % 3 == 0 else "human:ben", now, now))
    for token in ("ana-test", "cara-test"):
        whole = {r["id"] for r in get(api, "tool-index", token)["rows"]}
        for limit in (1, 3):
            pages = walk(api, token, limit)
            flat = [i for p in pages for i in p]
            assert len(flat) == len(set(flat)) and set(flat) == whole, (token, limit)
            assert all(len(p) == limit for p in pages[:-1]) and pages[-1], "no short or empty page while more follow"
    assert {"webhook:wh-00", "webhook:wh-03", "webhook:wh-06"} <= ids(api, "cara-test")
    assert not {"webhook:wh-01", "inbox:ibx_c"} & ids(api, "cara-test")
    every = {r["id"] for r in get(api, "tool-index")["rows"]}
    monkeypatch.setattr(api_tools, "INDEX_BYTES_MAX", 1)
    capped = walk(api, "ana-test", 100)
    assert all(len(p) == 1 for p in capped) and {p[0] for p in capped} == every
    for bad in ("abc", "-1"):
        get(api, "tool-index?cursor=" + bad, expected=422)


def test_a_narrowed_intake_inbox_names_nothing_outside_its_group(api):
    """Physical mail's email-in inbox follows the inbox's audience: Tools and GET /postal/intake."""
    seed(api)                                              # ibx_c is Operations'; Cara is not in it
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO mail_intake(singleton,inbox_id,set_by,set_at,senders) VALUES(1,?,?,?,?)",
                  ("ibx_c", "human:ana", H.now(), '["scans@provider.acme.example"]'))
    address = get(api, "inbound/inboxes")["inboxes"]
    address = next(i["address"] for i in address if i["inbox_id"] == "ibx_c")
    postal = rows(api, "cara-test")["address"]
    assert address not in str(postal) and postal["href"] == "#/tools/address/intake"
    assert rows(api)["address"]["detail"] == address
    intake = get(api, "postal/intake", "cara-test")["intake"]
    assert intake["hidden"] is True and intake["inbox_id"] is None and not intake["senders"]
    assert address not in str(intake) and "provider" not in str(intake)
    assert get(api, "postal/intake")["intake"]["inbox_id"] == "ibx_c"


def test_mcp_and_cli_read_the_same_list_and_set_an_inbox_audience(api):
    from backend.tests.test_mcp import call
    seed(api)
    err, result = call(api, "hub_tool_index", {"limit": 100}, "cara-test")
    assert not err and {r["id"] for r in result["rows"]} == {r["id"] for r in get(api, "tool-index", "cara-test")["rows"]}
    err, result = call(api, "hub_inbound_audience", {"inbox_id": "ibx_a", "audience": "team", "group": "sales"},
                       "cara-test")
    assert err                                              # a member may not
    err, result = call(api, "hub_inbound_audience", {"inbox_id": "ibx_a", "audience": "team", "group": "ops-team"})
    assert not err and result["group_id"] == "ops-team"
    assert "inbox:ibx_a" not in ids(api, "cara-test")
    from clients import hubcli, remotecli
    for words in (["tool", "index", "--limit", "5"], ["tools", "index"]):     # `hub tools` is the old spelling
        assert remotecli.tool_name(hubcli.parser().parse_args(hubcli.rename_argv(words)[0]).fn) == "hub_tool_index"
    args = hubcli.parser().parse_args(["tool", "check", "close-crm"])
    assert remotecli.tool_name(args.fn) == "hub_tool_check" and args.id == "close-crm"
    args = hubcli.parser().parse_args(["inbound", "audience", "ibx_a", "company"])
    assert remotecli.tool_name(args.fn) == "hub_inbound_audience" and args.audience == "company"
    get(api, "connections", expected=404)                  # the old Connections list is gone
