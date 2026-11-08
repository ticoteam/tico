"""Messaging joins bot coverage to real mail and Slack records without widening access."""

from pathlib import Path

from backend.store import H, encode
from backend.tests.test_api import api, as_member, assign, get, post, runner  # noqa: F401
from backend.tests.test_mail_api import put_mail, seed_org


def setup_sources(api, tmp_path):
    seed_org(api)
    registry = Path(tmp_path) / "registry"
    registry.mkdir()
    (registry / "slack-channels.yaml").write_text("""channels:
  - id: CCHANNEL1
    name: release_notes
    purpose: Product changes
    post: false
    readers: [doc-updater]
  - id: CCHANNEL2
    name: uncovered
    purpose: Nobody reads this
    post: false
""")
    api.app.state.store.settings.registry_dir = registry
    put_mail(api, "ana@acme.example", msg_id="mail1", thread_id="thread1",
             subject="Customer asks for help", snippet="Please help", body="Please help us set up.",
             labels=["INBOX", "hub/triaged/inbox", "hub/needs-owner"],
             rule_hits=[{"id": "needs-human", "actions": {"label": "hub/needs-owner"}}])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=? WHERE bot='cpo'",
                  (encode({"access": [{"service": "gmail", "identity": "ana@acme.example"}]}),))
        c.execute("INSERT INTO registry_metadata VALUES('slack_workspace_url',?)",
                  (encode("https://acme.slack.com/"),))
        c.execute("INSERT INTO mail_agent_instructions VALUES(?,?,?,?)",
                  ("inbox", "# Ana Inbox\n\nTriage incoming mail.", "runner-1", H.now()))
        c.execute("INSERT INTO slack_events(event_id,team_id,channel,channel_kind,thread_ts,reply_ts,ts,"
                  "user_id,event_type,text,received_at,state,author,author_name,updated_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("E1", "T1", "CCHANNEL1", "channel", "1789800000.000001", "",
                   "1789800000.000001", "U1", "message", "Feature shipped for customers",
                   H.now(), "stored", "human", "Ana", H.now()))
        c.execute("INSERT INTO slack_reads VALUES(?,?,?,?,?)",
                  ("CCHANNEL1", "doc-updater", "1789800000.000001", H.now(), 1))
        conv = H.open_conversation(c, H.KEEPER, [H.KEEPER, "bot:doc-updater"],
                                   kind="chat", subject="Slack channels", scope="direct")
        message = H.feed(c, "bot:doc-updater", "New Slack channel message", conv)
        c.execute("INSERT INTO slack_digests VALUES(?,?,?,?,?,?,?,?,?)",
                  ("digest-1", "doc-updater", conv["id"], message["id"],
                   encode(["#release_notes"]), encode(["E1"]), 1, 0, H.now()))


def test_source_and_message_access_does_not_follow_bot_visibility(api, tmp_path):
    as_member(api, "ben@acme.example")
    setup_sources(api, tmp_path)
    ben = get(api, "messaging/bots", "ben-test")
    assert all(s["name"] != "ana@acme.example" for bot in ben["bots"] for s in bot["sources"])
    assert all(s["kind"] != "slack" for bot in ben["bots"] for s in bot["sources"])
    get(api, "messaging/bots/doc-updater", "ben-test", expected=404)
    get(api, "messaging/bots/inbox?source=email:ana@acme.example", "ben-test", expected=404)
    get(api, "messaging/bots/doc-updater/slack-thread?channel=CCHANNEL1&thread_ts=1789800000.000001",
        "ben-test", expected=403)
    get(api, "messaging/bots/doc-updater/slack-digests/digest-1", "ben-test", expected=403)


def test_a_hidden_backlog_never_says_more_is_waiting():
    """The inbox's `more_*` flags count only items the caller could read: a tail it may not see stays unmentioned."""
    from backend.app import INBOX_LIMIT, _first_visible
    readable = [{"id": i, "ok": True} for i in range(INBOX_LIMIT)]
    hidden = [{"id": -1, "ok": False}]
    kept, more = _first_visible(readable + hidden, lambda m: m["ok"])
    assert kept == readable and more is False
    kept, more = _first_visible(readable + hidden + [{"id": -2, "ok": True}], lambda m: m["ok"])
    assert kept == readable and more is True
