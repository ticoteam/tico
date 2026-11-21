"""Which Slack channels bots may read and post in is a list in the database (backend/slack_channels.py): who may change it,
Agent Ops and the MCP tools as the requester, and the Slack gateway following the list.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture).
"""

import importlib
import json

from backend import hubdb as H
from backend import slack_channels as SC
from backend import slack_threads as ST
from backend.auth import Identity
from backend.tests.helpers.api import get, post, setup_attempt  # noqa: F401  (fixture)
from backend.tests.helpers.agent_ops_parity import act
from backend.tests.helpers.mcp import call as mcp
from backend.tests.helpers.member_bots import agent_ops, finish, turn  # noqa: F401  (fixture)
from backend.tests.helpers.slack_gateway import (  # noqa: F401  (fixtures)
    CHANNELS,
    MARKETING,
    QUIET,
    RELEASE,
    channel_message,
    hub,
    readers,
    rows,
)


# ------------------------------------------------------------------ who may change the list, and what an entry holds
def test_an_owner_or_admin_lists_a_channel_and_a_member_may_read_the_list_but_not_change_it(api):
    made = post(
        api,
        "slack/channels",
        {"channel": "#Customer_Success", "readers": ["ops", "BOT:cpo"], "note": "Customer success"},
        "ben-test",
    )
    assert made["created"] and made["channel"] == "#customer_success" and made["id"] == ""
    assert made["readers"] == ["ops", "cpo"] and made["post"] is True, "bots may post unless someone turns it off"
    # Adding again adds readers and changes only what is sent.
    again = post(
        api, "slack/channels", {"channel": "customer_success", "readers": ["ops", "finance"], "post": False}, "ana-test"
    )
    assert not again["created"] and again["readers"] == ["ops", "cpo", "finance"] and again["post"] is False
    assert again["note"] == "Customer success", "a note that was not sent is kept"
    by_id = post(api, "slack/channels", {"channel": "C0000000010", "name": "#ideas"}, "ana-test")
    assert (by_id["id"], by_id["name"], by_id["channel"]) == ("C0000000010", "ideas", "#ideas")
    # An rc.1 client still sends `digest_hours` (`tico slack channel add --digest-hours`); it is taken and ignored.
    rc1 = {"channel": "C0000000010", "name": "#ideas", "readers": ["ops"], "post": True, "note": "", "digest_hours": 6}
    assert post(api, "slack/channels", rc1, "ana-test")["readers"] == ["ops"]

    seen = get(api, "slack/channels", "cara-test")
    assert (
        seen["can_manage"] is False
        and seen["bots"] == []
        and {r["channel"] for r in seen["channels"]} == {"#customer_success", "#ideas"}
    )
    assert get(api, "slack/channels", "ben-test")["can_manage"] is True
    for path, body in (
        ("slack/channels", {"channel": "#customer_success", "readers": ["ops"]}),
        ("slack/channels/remove", {"channel": "#customer_success"}),
    ):
        post(api, path, body, "cara-test", expected=403)
    post(api, "slack/channels", {"channel": "#customer_success", "readers": ["not-a-bot"]}, "ana-test", expected=422)
    post(api, "slack/channels", {"channel": "two words"}, "ana-test", expected=422)
    # A bot reads the list for its own checks but cannot change it.
    _, _, attempt = setup_attempt(api, "ops")
    assert len(get(api, "slack/channels", attempt["token"])["channels"]) == 2
    post(api, "slack/channels", {"channel": "#sales", "readers": ["ops"]}, attempt["token"], expected=403)
    post(api, "slack/channels/remove", {"channel": "#customer_success"}, attempt["token"], expected=403)


# ------------------------------------------------------------------ Agent Ops and the MCP tools, as the requester
def test_agent_ops_and_the_mcp_tools_change_the_list_as_the_person_who_asked(api, agent_ops):
    err, added = mcp(
        api,
        "tico_slack_channel_add",
        {"channel": "#customer_success", "readers": ["ops"], "note": "Onboarding"},
        token="ben-test",
    )
    assert not err and added["created"] and added["readers"] == ["ops"], added
    err, refused = mcp(api, "tico_slack_channel_add", {"channel": "#sales", "readers": ["ops"]}, token="cara-test")
    assert err and "owner or an admin" in json.dumps(refused)
    # Agent Ops is an admin's hands when an admin asks, and a member's when a member does.
    ben = turn(api, agent_ops, person="ben-test", text="Let the CPO read #customer_success")
    err, done = mcp(
        api,
        "tico_slack_channel_add",
        {"channel": "#customer_success", "readers": ["cpo"], "post": False},
        token=ben["token"],
    )
    assert not err and done["readers"] == ["ops", "cpo"] and done["post"] is False and "needs_confirm" not in done, done
    with api.app.state.store.read() as c:
        event = c.execute(
            "SELECT actor,detail FROM events WHERE action='slack.channel_changed' ORDER BY created_at DESC"
        ).fetchone()
    assert event["actor"] == "human:ben" and '"via": "botops"' in event["detail"]
    err, removed = mcp(
        api, "tico_slack_channel_remove", {"channel": "#customer_success", "reader": "cpo"}, token=ben["token"]
    )
    assert not err and removed["readers"] == ["ops"]
    err, listing = mcp(api, "tico_slack_channel_list", {}, token=ben["token"])
    assert not err and [r["channel"] for r in listing["channels"]] == ["#customer_success"]
    finish(api, agent_ops, ben)
    cara = turn(api, agent_ops, person="cara-test", text="Let ops read #sales")
    assert act(api, cara, "POST", "slack/channels", {"channel": "#sales", "readers": ["ops"]}).status_code == 403


# ------------------------------------------------------------------ the gateway and the connector read the list
def test_the_gateway_follows_the_list_in_the_database(readers, monkeypatch):
    from backend import thread_bot_digest

    gw, hub = readers, readers.store
    ana = Identity("human:ana", "owner", "ana@acme.example")
    monkeypatch.setitem(CHANNELS, "C0NEW", {"id": "C0NEW", "name": "newchan", "is_member": True})
    gw.slack.conversations_list = lambda: list(CHANNELS.values())
    with hub.transaction() as c:
        SC.remove(c, hub.settings, ana, "#marketing", reader="cmo")
        SC.add(c, hub.settings, ana, "#newchan", readers=["cto"])  # the name only: Slack gives the id
    gw.clock.advance(60)
    gw.tick()  # the id is looked up, and the new read-only link is checked
    with hub.read() as c:
        assert c.execute("SELECT channel_id FROM slack_channels WHERE name='newchan'").fetchone()[0] == "C0NEW"
        listed = {r["channel"]: r for r in SC.listing(c, hub.settings)}
    assert listed["#marketing"]["readers"] == ["seo", "finance"], "cmo was taken off #marketing"
    assert listed["#newchan"]["readers"] == ["cto"] and listed["#newchan"]["link"]["state"] == "active"
    assert listed["#quiet"]["readers"] == [] and listed["#quiet"]["link"] is None
    assert gw.receive(channel_message("Launch copy is ready")) == "stored"
    assert gw.receive(channel_message("Something new", channel="C0NEW")) == "stored"
    assert gw.receive(channel_message("Nobody reads this", channel=QUIET)) == "stored"
    assert thread_bot_digest.sweep(hub) == 2
    woken = {
        r["recipient_id"]: r["body"]
        for r in rows(hub, "SELECT recipient_id, body FROM messages WHERE sender_id='keeper' AND kind='notice'")
    }
    assert set(woken) == {"bot:cto", "bot:seo"}
    assert listed["#newchan"]["link"]["thread_id"] in woken["bot:cto"]
    assert listed["#marketing"]["link"]["thread_id"] in woken["bot:seo"]


# ------------------------------------------------------------------ the readers' pass moves to read-only links (D9-5)
MIGRATION = "backend.migrations.20261117104315_slack_readers_to_links"


def _old_shape(c):
    """The tables as the readers' pass left them: readers and digest_hours on each entry, a cursor table."""
    c.execute("DROP INDEX IF EXISTS slack_thread_links_ref")
    c.execute("DROP INDEX IF EXISTS slack_thread_links_read_only")
    c.execute("ALTER TABLE slack_thread_links DROP COLUMN read_only")
    c.execute("ALTER TABLE slack_thread_links DROP COLUMN read_ts")
    c.execute("ALTER TABLE slack_channels ADD COLUMN readers TEXT NOT NULL DEFAULT '[]'")
    c.execute("ALTER TABLE slack_channels ADD COLUMN digest_hours REAL NOT NULL DEFAULT 0")
    c.execute(
        "CREATE TABLE slack_reads (channel TEXT NOT NULL, reader TEXT NOT NULL, last_ts TEXT NOT NULL DEFAULT '', "
        "last_run_at TEXT, delivered INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (channel, reader))"
    )


def _listed(c, key, cid, name, readers, updated_by):
    now = H.now()
    c.execute(
        "INSERT INTO slack_channels(key,channel_id,name,readers,post,note,digest_hours,created_at,updated_at,updated_by) "
        "VALUES(?,?,?,?,1,'',1,?,?,?)",
        (key, cid, name, json.dumps(readers), now, now, updated_by),
    )


def test_the_migration_moves_each_channels_readers_to_a_read_only_link_and_runs_once(hub):
    """#marketing (read by the CMO and SEO, last changed by Ben) gets a members thread Ben owns and a read-only link;
    #release_notes already has a two-way link, so the CTO follows that thread; a channel named without an id, changed
    by a bot, is owned by the company owner; a channel nobody reads, and a slug that is no bot, get nothing."""
    migration = importlib.import_module(MIGRATION)
    with hub.transaction() as c:
        _old_shape(c)
        now = H.now()
        c.execute(
            "INSERT INTO conversations (id,kind,subject,created_at,audience,root_id,path,participants) "
            "VALUES ('T-two','thread','Releases',?,'company','T-two','','[]')",
            (now,),
        )
        c.execute(
            "INSERT INTO slack_thread_links (id,thread_id,channel_ref,channel_id,channel_name,audience,state,"
            "linked_by,created_at,updated_at) VALUES ('L-two','T-two','#release_notes',?,'release_notes','company',"
            "'active','human:ana',?,?)",
            (RELEASE, now, now),
        )
        _listed(c, MARKETING, MARKETING, "marketing", ["cmo", "bot:seo", "ghost"], "human:ben")
        _listed(c, RELEASE, RELEASE, "release_notes", ["cto"], "human:ana")
        _listed(c, "#ideas", "", "ideas", ["legal"], "bot:cmo")
        _listed(c, QUIET, QUIET, "quiet", [], "human:ana")
        for reader, last_ts in (("cmo", "1700000009.000100"), ("seo", "1700000003.000100"), ("cto", "1700000001.0")):
            c.execute("INSERT INTO slack_reads(channel,reader,last_ts) VALUES(?,?,?)", (MARKETING, reader, last_ts))
        c.execute(
            "INSERT OR REPLACE INTO registry_metadata VALUES('owner',?)", (json.dumps({"email": "cara@acme.example"}),)
        )
        migration.up(c)

    def state():
        with hub.read() as c:
            return (
                rows(
                    hub,
                    "SELECT id,thread_id,channel_ref,state,read_only,read_ts,linked_by FROM slack_thread_links "
                    "ORDER BY id",
                ),
                rows(hub, "SELECT thread_id,actor,following,notify FROM thread_followers ORDER BY thread_id,actor"),
                rows(hub, "SELECT thread_id,actor,role FROM thread_members ORDER BY thread_id,actor"),
                c.execute("SELECT participants FROM conversations WHERE id='T-two'").fetchone()[0],
            )

    links, follows, members, participants = state()
    by_ref = {link["channel_ref"]: link for link in links}
    assert set(by_ref) == {MARKETING, "#release_notes", "#ideas"}, "a channel nobody reads gets no link"
    marketing, ideas = by_ref[MARKETING], by_ref["#ideas"]
    assert (marketing["state"], marketing["read_only"], marketing["linked_by"]) == ("requested", 1, "human:ben")
    assert (ideas["read_only"], ideas["linked_by"]) == (1, "human:cara"), "a bot's change: the company owner"
    assert marketing["read_ts"] == "1700000003.000100", "it reads from its readers' oldest cursor (not the CTO's)"
    assert ideas["read_ts"] == "", "a channel with no id had no cursor: it reads from its activation"
    assert by_ref["#release_notes"]["read_only"] == 0
    readers = {(f["thread_id"], f["actor"]) for f in follows if f["following"] and f["notify"] == "digest"}
    assert readers == {
        (marketing["thread_id"], "bot:cmo"),
        (marketing["thread_id"], "bot:seo"),
        ("T-two", "bot:cto"),
        (ideas["thread_id"], "bot:legal"),
    }
    assert {(m["actor"], m["role"]) for m in members if m["thread_id"] == marketing["thread_id"]} == {
        ("human:ben", "owner"),
        ("bot:cmo", "member"),
        ("bot:seo", "member"),
    }
    assert "bot:cto" in json.loads(participants), "a company thread's reader is a participant"
    with hub.read() as c:
        assert {"readers", "digest_hours"}.isdisjoint(r[1] for r in c.execute("PRAGMA table_info(slack_channels)"))
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='slack_reads'").fetchone() is None
        listed = {r["key"]: r["readers"] for r in SC.listing(c, hub.settings)}
    assert listed == {MARKETING: ["cmo", "seo"], RELEASE: ["cto"], "#ideas": ["legal"], QUIET: []}
    with hub.transaction() as c:
        migration.up(c)
    assert state() == (links, follows, members, participants), "a second run changes nothing"


def test_the_list_reads_its_links_and_readers_by_index(hub):
    """Pins the plans of the list's two reads (slack_channels.LINKS_SQL, READERS_SQL): the links by their channel
    reference or id, the followers by thread, no scan of either table."""
    with hub.read() as c:
        plans = [
            " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, args))
            for sql, args in ((SC.LINKS_SQL, ()), (SC.READERS_SQL.format("?,?"), ("t1", "t2")))
        ]
    assert "SEARCH l USING INDEX slack_thread_links_ref (channel_ref=? AND state=?)" in plans[0], plans[0]
    assert "SEARCH l USING INDEX slack_thread_links_channel (channel_id=? AND state=?)" in plans[0], plans[0]
    assert "SCAN l" not in plans[0], plans[0]
    assert "SEARCH thread_followers USING INDEX sqlite_autoindex_thread_followers_2 (thread_id=?)" in plans[1], plans[1]
    assert "SCAN thread_followers" not in plans[1], plans[1]


def test_the_catch_up_pass_finds_its_read_only_links_by_index(hub):
    """Pins the plan of the gateway's catch-up read (slack_threads.CATCH_UP_SQL, once a minute): the partial index on
    read-only links, never a scan of every link."""
    with hub.read() as c:
        plan = " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + ST.CATCH_UP_SQL))
    assert plan == "SEARCH slack_thread_links USING INDEX slack_thread_links_read_only (state=?)", plan
