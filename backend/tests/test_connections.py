"""Connections (backend/connections.py): one list of every connection, filtered per reader. The owner and admins see
every row; a member sees their own and the company's rows they may read; the list is paged and byte-capped.

Ana owns the company, Ben is an admin (a member after `as_member`), Cara is a member."""

from backend import connections
from backend.store import H
from backend.tests.test_api import api, as_member, get, post, put, setup_attempt  # noqa: F401  (fixture)
from backend.tests.test_inbound_audience import narrow


def seed(api):
    narrow(api)                                            # ibx_a, ibx_b, ibx_c; Cara is in East, under Sales
    put(api, "inbound/inboxes/ibx_b/audience", {"audience": "team", "group_id": "sales"})
    put(api, "inbound/inboxes/ibx_c/audience", {"audience": "team", "group_id": "ops-team"})
    now = H.now()
    with api.app.state.store.transaction() as c:
        for wid, who in (("wh-ben", "human:ben"), ("wh-cara", "human:cara")):
            c.execute("INSERT INTO webhooks(id,name,url,host,created_by,created,updated) VALUES(?,?,?,?,?,?,?)",
                      (wid, "Hook " + wid, "https://hooks.acme.example/x", "hooks.acme.example", who, now, now))
        c.execute("INSERT INTO triggers(id,name,secret_hash,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                  ("tr-1", "Orders", "x", "human:ana", now, now))


def ids(api, token):
    return {r["id"] for r in get(api, "connections?limit=100", token)["connections"]}


def test_admins_see_every_row_and_members_their_own_and_what_they_may_read(api):
    seed(api)
    everything = {"inbox:ibx_a", "inbox:ibx_b", "inbox:ibx_c", "webhook:wh-ben", "webhook:wh-cara", "trigger:tr-1"}
    assert everything <= ids(api, "ana-test")
    assert everything <= ids(api, "ben-test")              # an admin
    cara = ids(api, "cara-test")
    assert {"inbox:ibx_a", "inbox:ibx_b", "webhook:wh-cara", "trigger:tr-1"} <= cara
    assert not cara & {"inbox:ibx_c", "webhook:wh-ben"}    # another group's inbox, another person's webhook
    assert "slack" not in cara and "github" not in cara    # not connected: only admins see them, to connect
    assert {"slack", "github"} <= ids(api, "ana-test")
    as_member(api, "ben@acme.example")
    ben = ids(api, "ben-test")
    assert "webhook:wh-ben" in ben and not ben & {"inbox:ibx_c", "webhook:wh-cara"}


def test_a_row_says_whose_it_is_who_reads_it_and_where_to_change_it(api):
    seed(api)
    rows = {r["id"]: r for r in get(api, "connections", "cara-test")["connections"]}
    team = rows["inbox:ibx_b"]
    assert team["scope"] == "team" and team["readers"] == "Sales" and team["configure"] == "#/settings/inbound"
    assert team["can_configure"] is False
    assert rows["inbox:ibx_a"]["scope"] == "company" and rows["inbox:ibx_a"]["readers"] == "Everyone"
    assert rows["webhook:wh-cara"]["scope"] == "person" and rows["webhook:wh-cara"]["can_configure"] is True
    assert set(rows["trigger:tr-1"]) == {"id", "kind", "name", "detail", "status", "scope", "readers", "bots",
                                        "more_bots", "configure", "can_configure"}


def test_bots_and_agents_see_only_company_rows_they_may_read(api):
    seed(api)
    attempt = setup_attempt(api)[2]
    rows = ids(api, attempt["token"])
    assert "trigger:tr-1" in rows and "inbox:ibx_a" in rows
    assert not rows & {"inbox:ibx_b", "inbox:ibx_c", "webhook:wh-ben", "webhook:wh-cara"}


def walk(api, token, limit):
    pages, cursor = [], ""
    while True:
        got = get(api, f"connections?limit={limit}" + (f"&cursor={cursor}" if cursor else ""), token)
        pages.append([r["id"] for r in got["connections"]])
        if not got["next"]:
            return pages
        cursor = got["next"]


def test_the_list_is_paged_and_byte_capped(api, monkeypatch):
    seed(api)
    now = H.now()
    with api.app.state.store.transaction() as c:          # more webhooks than one page, and Cara's among them
        for i in range(7):
            c.execute("INSERT INTO webhooks(id,name,url,host,created_by,created,updated) VALUES(?,?,?,?,?,?,?)",
                      (f"wh-{i:02d}", f"Hook {i}", "https://hooks.acme.example/x", "hooks.acme.example",
                       "human:cara" if i % 3 == 0 else "human:ben", now, now))
    for token in ("ana-test", "cara-test"):
        whole = ids(api, token)
        for limit in (1, 2, 3):
            pages = walk(api, token, limit)
            flat = [i for p in pages for i in p]
            assert len(flat) == len(set(flat)) and set(flat) == whole, (token, limit)
            assert all(len(p) == limit for p in pages[:-1]) and pages[-1], "no short or empty page while more follow"
    assert {"webhook:wh-00", "webhook:wh-03", "webhook:wh-06"} <= ids(api, "cara-test")
    assert not {"webhook:wh-01", "inbox:ibx_c"} & ids(api, "cara-test")
    every = ids(api, "ana-test")
    monkeypatch.setattr(connections, "BYTES_MAX", 1)
    capped = walk(api, "ana-test", 100)
    assert all(len(p) == 1 for p in capped) and {p[0] for p in capped} == every
    for bad in ("abc", "99", "x:1"):
        get(api, "connections?cursor=" + bad, expected=422)


def test_a_narrowed_intake_inbox_names_nothing_outside_its_group(api):
    """Physical mail's email-in inbox follows the inbox's audience: Connections and GET /postal/intake."""
    seed(api)                                              # ibx_c is Operations'; Cara is not in it
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO mail_intake(singleton,inbox_id,set_by,set_at,senders_json) VALUES(1,?,?,?,?)",
                  ("ibx_c", "human:ana", H.now(), '["scans@provider.acme.example"]'))
    address = get(api, "inbound/inboxes")["inboxes"]
    address = next(i["address"] for i in address if i["inbox_id"] == "ibx_c")
    postal = {r["id"]: r for r in get(api, "connections?limit=100", "cara-test")["connections"]}["postal"]
    assert address not in str(postal)
    assert {r["id"]: r for r in get(api, "connections?limit=100")["connections"]}["postal"]["detail"] == address
    intake = get(api, "postal/intake", "cara-test")["intake"]
    assert intake["hidden"] is True and intake["inbox_id"] is None and not intake["senders"]
    assert address not in str(intake) and "provider" not in str(intake)
    assert get(api, "postal/intake")["intake"]["inbox_id"] == "ibx_c"


def test_mcp_and_cli_read_the_same_list_and_set_an_inbox_audience(api, monkeypatch):
    from backend.tests.test_mcp import call
    seed(api)
    err, result = call(api, "hub_connections_list", {"limit": 100}, "cara-test")
    assert not err and {r["id"] for r in result["connections"]} == ids(api, "cara-test")
    err, result = call(api, "hub_inbound_audience", {"inbox_id": "ibx_a", "audience": "team", "group": "sales"},
                       "cara-test")
    assert err                                              # a member may not
    err, result = call(api, "hub_inbound_audience", {"inbox_id": "ibx_a", "audience": "team", "group": "ops-team"})
    assert not err and result["group_id"] == "ops-team"
    assert "inbox:ibx_a" not in ids(api, "cara-test")
    from clients import hubcli, remotecli
    args = hubcli.parser().parse_args(["connections", "list", "--limit", "5"])
    assert remotecli.tool_name(args.fn) == "hub_connections_list"
    args = hubcli.parser().parse_args(["inbound", "audience", "ibx_a", "company"])
    assert remotecli.tool_name(args.fn) == "hub_inbound_audience" and args.audience == "company"


def test_a_member_sees_the_slack_links_and_peers_of_the_threads_they_may_read_and_only_admins_configure_them(api):
    """Audit 41: a link to a thread and a peer that carries one reach each reader who may read that thread, read-only for
    a member. A thread a reader cannot read leaves its link and its peer out."""
    seed(api)
    open_thread = post(api, "threads", {"title": "Launch", "audience": "company", "body": "Launch notes"}, "ana-test")["id"]
    private = post(api, "threads", {"title": "Plans", "audience": "members", "members": ["human:ben"],
                                    "body": "Plans"}, "ben-test")["id"]
    now = H.now()
    with api.app.state.store.transaction() as c:
        for link, thread, audience, channel in (("sl-open", open_thread, "company", "marketing"),
                                                ("sl-private", private, "members", "secret")):
            c.execute("INSERT INTO slack_thread_links (id,thread_id,channel_ref,channel_id,channel_name,channel_private,"
                      "audience,state,linked_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'active','human:ana',?,?)",
                      (link, thread, "#" + channel, "C" + link[-6:].upper(), channel, int(audience == "members"),
                       audience, now, now))
        c.execute("INSERT INTO federation_peerings (id,hq_peering_id,peer_install,direction,status,peer_company,"
                  "created_at,updated_at) VALUES('fp-1','hq-1','gi_0123456789abcdef01234567','outgoing','active',"
                  "'Partner Co',?,?)", (now, now))
        c.execute("INSERT INTO federation_threads (id,thread_id,home_install,origin_id,created_at,updated_at) "
                  "VALUES('ft-open',?,'gi_0123456789abcdef01234567','o-open',?,?)", (open_thread, now, now))
        c.execute("INSERT INTO federation_threads (id,thread_id,home_install,origin_id,created_at,updated_at) "
                  "VALUES('ft-private',?,'gi_fedcba9876543210fedcba98','o-private',?,?)", (private, now, now))
    member = {r["id"]: r for r in get(api, "connections?limit=100", "cara-test")["connections"]}
    assert "slack_link:sl-open" in member and "slack_link:sl-private" not in member
    assert member["slack_link:sl-open"]["can_configure"] is False
    assert member["slack_link:sl-open"]["configure"] == f"#/thread/{open_thread}"
    assert "peer:gi_0123456789abcdef01234567" in member
    assert "peer:gi_fedcba9876543210fedcba98" not in member and "peer:gi_0123456789abcdef01234567" in member
    # A link's thread is shown to a reader only when they read the thread, admins included (as the links listing does).
    assert "slack_link:sl-private" not in ids(api, "ana-test")
    admin = {r["id"]: r for r in get(api, "connections?limit=100", "ben-test")["connections"]}
    assert admin["slack_link:sl-private"]["can_configure"] is True

