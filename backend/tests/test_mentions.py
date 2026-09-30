"""A human's mentions (`backend/mentions.py`, the gateway's `receive_mention`, docs/mentions.md).

Slack is a fake per token; the store is real, because who may see a mention and what is posted as
a human are the point.
"""

from backend import mentions as M
from backend import slack_gateway as G
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401  (the api fixture)
from backend.tests.test_slack_gateway import MARKETING, TEAM, Clock, FakeSlack, hub  # noqa: F401

SHARED = "C0EXT"


class HumanSlack(FakeSlack):
    """Slack as one human's user token sees it (Ben is U2)."""

    def __init__(self, user="U2"):
        super().__init__()
        self.user, self.before_pages, self.thread_pages = user, {}, {}

    def auth_test(self):
        return {"ok": True, "team_id": TEAM, "user_id": self.user, "url": "https://acme.slack.com/"}

    def permalink(self, channel, ts):
        return f"https://acme.slack.com/archives/{channel}/p{ts.replace('.', '')}"

    def thread(self, channel, root, limit=200):
        return self.thread_pages.get((channel, root), [])

    def before(self, channel, ts, limit=8):
        return [m for m in self.before_pages.get(channel, []) if float(m["ts"]) <= float(ts)][-limit:]

    def post_message(self, channel, thread_ts, text, username=None, icon_emoji=None, icon_url=None):
        super().post_message(channel, thread_ts, text, username, icon_emoji, icon_url)
        return {"ok": True, "ts": f"17000001{len(self.posts):02d}.000100"}


def message(text, ts, user="U1", channel=MARKETING, thread_ts=None, kind="channel"):
    event = {"type": "message", "channel_type": kind, "channel": channel, "user": user, "ts": ts, "text": text}
    if thread_ts:
        event["thread_ts"] = thread_ts
    return {"type": "event_callback", "team_id": TEAM, "api_app_id": "A0MENTIONS", "event_id": "Ev" + ts, "event": event}


def gateway(hub, slack):  # noqa: F811
    tokens = {"app_token": "xapp-m", "revision": ("m", 1), "ids": [],
              "people": {"ben": {"user_token": "xoxp-b", "revision": ("b", 1), "ids": []}}}
    gw = G.Gateway(hub, FakeSlack(), clock=Clock(), mention_tokens=lambda: tokens,
                   slack_factory=lambda bot_token, app_token: slack)
    gw.verify_app()
    return gw


def mentions(hub):  # noqa: F811
    with hub.read() as c:
        return [M.view(c, r) for r in c.execute("SELECT * FROM mentions ORDER BY created")]


def test_a_thread_that_names_a_human_is_one_mention_with_its_context_and_their_reply_goes_out_as_them(hub):  # noqa: F811
    slack = HumanSlack()
    slack.before_pages[MARKETING] = [{"ts": "1700000000.000100", "user": "U4", "text": "pricing looks off for Denver"},
                                     {"ts": "1700000001.000100", "user": "U1", "text": "<@U2> can you check the Denver price?"}]
    gw = gateway(hub, slack)
    assert gw.connect_mentions() == ["ben"] and gw.mention_people["ben"].user_id == "U2"

    # Named: a pending mention until its context is read, then open, with the channel before it.
    assert gw.receive_mention(message("<@U2> can you check the Denver price?", "1700000001.000100")) == "mention"
    assert [m["status"] for m in mentions(hub)] == ["pending"]
    gw.tick()
    [m] = mentions(hub)
    assert m["status"] == "open" and m["where"] == "#marketing" and m["ready"]
    assert m["title"] == "Ana Rivera: @Ben can you check the Denver price?"
    assert [line["text"] for line in m["context"]] == ["pricing looks off for Denver", "@Ben can you check the Denver price?"]
    assert m["context"][1]["mention"] and m["permalink"].endswith("/p1700000001000100")

    # The same thread again, and a follow-up without the name: one mention. Elsewhere: nothing.
    root = "1700000001.000100"
    assert gw.receive_mention(message("<@U2> also Boston", "1700000002.000100", thread_ts=root)) == "mention"
    assert gw.receive_mention(message("nvm Boston is fine", "1700000003.000100", user="U4", thread_ts=root)) == "context"
    assert gw.receive_mention(message("lunch?", "1700000004.000100")) == "not_a_mention"
    assert gw.receive_mention(message("<@U2> also Boston", "1700000002.000100", thread_ts=root)) == "mention"  # a redelivery
    gw.tick()
    [m] = mentions(hub)
    assert [(i["kind"], i["text"]) for i in m["items"]] == [
        ("mention", "@Ben can you check the Denver price?"), ("mention", "@Ben also Boston"), ("message", "nvm Boston is fine")]

    # Done, then named again in the thread: open again.
    with hub.transaction() as c:
        c.execute("UPDATE mentions SET status='done', done_at=? WHERE id=?", (H.now(), m["id"]))
    gw.receive_mention(message("<@U2> ping", "1700000005.000100", thread_ts=root))
    gw.tick()
    assert mentions(hub)[0]["status"] == "open" and mentions(hub)[0]["done_at"] is None

    # The reply they approved is posted as them in the thread, escaped, once; its echo is not a second reply.
    with hub.transaction() as c:
        c.execute("INSERT INTO mention_items(id,mention_id,kind,source_id,author_id,text,at,state,created,updated) "
                  "VALUES('r1',?,'reply','queued:r1','human:ben','Checked: <!here> it is $10 & fine',?,'ready',?,?)",
                  (m["id"], H.now(), H.now(), H.now()))
    gw.tick()
    assert slack.posts == [{"channel": MARKETING, "thread_ts": root, "text": "Checked: &lt;!here&gt; it is $10 &amp; fine",
                            "username": None, "icon_emoji": None, "icon_url": None}]
    sent = [i for i in mentions(hub)[0]["items"] if i["kind"] == "reply"]
    assert [(i["state"], i["permalink"] != "") for i in sent] == [("sent", True)]
    assert gw.receive_mention(message("Checked", "1700000101.000100", user="U2", thread_ts=root)) == "context"
    assert gw.receive_mention(message("Also: Boston too", "1700000200.000100", user="U2", thread_ts=root)) == "context"
    gw.tick()
    replies = [(i["state"], i["text"]) for i in mentions(hub)[0]["items"] if i["kind"] == "reply"]
    assert replies == [("sent", "Checked: <!here> it is $10 & fine"), ("sent", "Also: Boston too")], \
        "the echo of the posted reply is the same reply; their own later message in Slack is another"
    assert len(slack.posts) == 1

    # Naming yourself is a mention too (a note to self).
    assert gw.receive_mention(message("<@U2> remember the Boston fee", "1700000300.000100", user="U2")) == "mention"

    # A channel shared outside the company is never a mention.
    gw.receive_mention(message("<@U2> hi from a customer", "1700000009.000100", channel=SHARED))
    gw.tick()
    with hub.read() as c:
        assert [r[0] for r in c.execute("SELECT status FROM mentions ORDER BY created")] == ["open", "open", "ignored"]


def test_a_dm_or_group_dm_is_never_read_even_when_it_names_them(hub):  # noqa: F811
    gw = gateway(hub, HumanSlack())
    gw.connect_mentions()
    assert gw.receive_mention(message("<@U2> quick q", "1700000007.000100", channel="D0AAAAAAA", kind="im")) == "type"
    assert gw.receive_mention(message("<@U2> quick q", "1700000008.000100", channel="G0MPIM", kind="mpim")) == "type"
    with hub.read() as c:
        assert c.execute("SELECT COUNT(*) FROM mention_items").fetchone()[0] == 0, "nothing of a DM is stored"


def test_a_token_is_read_only_for_the_human_whose_slack_it_is(hub):  # noqa: F811
    gw = gateway(hub, HumanSlack(user="U1"))         # Ana's account, stored under Ben's name
    assert gw.connect_mentions() == [] and gw.mention_people == {}
    assert gw.receive_mention(message("<@U1> hi", "1700000010.000100", user="U4")) == "not_a_mention"


def test_only_the_human_reads_and_answers_their_mentions(api):  # noqa: F811
    store = api.app.state.store
    store.settings.test_identities["ben-assistant"] = Identity("human:ben", "human", "ben@acme.example", via="assistant")
    with store.transaction() as c:
        c.execute("INSERT INTO mentions(id,person,source,place,thread,status,title,created,updated,last_at) "
                  "VALUES('m1','ben','slack','C1','1.1','open','Ana Rivera: check this',?,?,?)", (H.now(), H.now(), H.now()))
        c.execute("INSERT INTO mentions(id,person,source,place,thread,status,created,updated,last_at) "
                  "VALUES('m2','ben','slack','C1','2.2','pending',?,?,?)", (H.now(), H.now(), H.now()))
    assert [m["id"] for m in get(api, "mentions", "ben-test")["mentions"]] == ["m1"], "pending is not shown"
    assert get(api, "mentions", "ben-test")["connected"] is False
    with store.transaction() as c:
        for cid, env in (("c1", "SLACK_MENTIONS_APP_TOKEN"), ("c2", "BEN_SLACK_USER_TOKEN")):
            c.execute("INSERT INTO credentials(id,name,kind,env,ciphertext,nonce,created,updated,updated_by) "
                      "VALUES(?,?,'token',?,x'00',x'00',?,?,'human:ben')", (cid, env, env, H.now(), H.now()))
    assert get(api, "mentions", "ben-test")["connected"] is True, "his Slack is set up, so a frontend shows the section"
    assert get(api, "mentions", "cara-test")["connected"] is False, "and nobody else's is"
    assert get(api, "mentions", "ana-test")["mentions"] == [], "not even the owner sees someone else's"
    get(api, "mentions/m1", "cara-test", expected=404)
    post(api, "mentions/m1/reply", {"text": "on it"}, "cara-test", expected=404)
    post(api, "mentions/m1/reply", {"text": "on it"}, "ben-assistant", expected=403)
    done = post(api, "mentions/m1", {"status": "done", "bot": "cpo"}, "ben-test")
    assert done["status"] == "done" and done["bot"] == "cpo" and done["done_at"]
    assert post(api, "mentions/m1", {"bot": "coo"}, "ben-test")["bot"] == "cpo", "set once"
    assert post(api, "mentions/m1/reply", {"text": "on it"}, "ben-test")["state"] == "ready"
    with store.read() as c:
        assert [dict(r) for r in c.execute("SELECT kind,state,text,author_id FROM mention_items")] == [
            {"kind": "reply", "state": "ready", "text": "on it", "author_id": "human:ben"}]
