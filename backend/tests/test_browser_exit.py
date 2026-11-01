"""Browser exits: only a computer's owner shares its internet, and only with the bots they pick; a bot's managers point it
at such a computer; every tunnel is checked against both and fails closed, naming the exit; what went through is
added up per day for the owner. The relay itself is a stand-in here (relay-rs/src/exit.rs has its own tests)."""

import pytest

from backend import browser_exit as BE
from backend.store import Problem
from backend.tests.test_api import api, as_member, assign, get, post, runner  # noqa: F401  (the api fixture)


class Relay:
    """Stands in for screen_relay.RemoteRegistry's exit operations."""

    def __init__(self):
        self.answers, self.grants, self.pushed, self.closed = [], [], [], []

    def exit_generation_of(self, rid):
        return len([p for p in self.pushed if p == rid])

    def push_exit_policy(self, rid):
        self.pushed.append(rid)

    def exit_grant(self, grant):
        self.grants.append(grant)
        return self.answers.pop(0) if self.answers else {"ticket": "a" * 64}

    def exit_close(self, bot=None, exit=None, reason=""):
        self.closed.append((bot, exit, reason))


@pytest.fixture
def relay(api, monkeypatch):
    fake, sessions = Relay(), api.app.state.store.screen_sessions
    for name in ("exit_generation_of", "push_exit_policy", "exit_grant", "exit_close"):
        monkeypatch.setattr(sessions, name, getattr(fake, name))
    return fake


def laptop(api):
    """Cara's laptop, and the computer ops runs on (Ana's)."""
    caras = runner(api, operator="cara", label="Cara's MacBook")
    server = runner(api, operator="ana", label="Office server")
    assign(api, server, "ops")
    return caras, server


def share(api, rid, token="cara-test", expected=200, **body):
    return post(api, f"computers/{rid}/browser-exit", {"sharing": True, "bots": ["ops"], **body}, token=token,
                expected=expected)


def tunnel(api, r, expected=200, bot="ops", host="www.acme.example", port=443):
    return post(api, "runners/me/browser-exit/tunnels", {"bot": bot, "host": host, "port": port}, token=r["token"],
                expected=expected)


def test_only_the_computers_owner_shares_its_internet_and_an_admin_may_only_turn_it_off(api, relay):
    caras, _ = laptop(api)
    rid = caras["runner_id"]
    for token in ("ana-test", "ben-test"):                      # the owner of the company, an admin
        share(api, rid, token=token, expected=403)
    assert get(api, f"computers/{rid}/browser-exit", token="cara-test")["sharing"] is False      # off by default
    share(api, rid, token="cara-test", bots=["no-such-bot"], expected=404)
    assert share(api, rid) == {"sharing": True, "bots": ["ops"], "daily_cap_bytes": BE.DEFAULT_CAP}
    assert relay.pushed == [rid]
    seen = get(api, f"computers/{rid}/browser-exit", token="cara-test")
    assert (seen["may_change"], seen["used_today_bytes"]) == (True, 0)
    # An admin turns it off but changes nothing else; heartbeats say so to the runner.
    assert post(api, "runners/heartbeat", {"version": "t", "platform": "t"}, token=caras["token"])["browser_exit"]
    post(api, f"computers/{rid}/browser-exit", {"sharing": True, "bots": []}, token="ben-test", expected=403)
    assert post(api, f"computers/{rid}/browser-exit", {"sharing": False}, token="ben-test")["bots"] == ["ops"]
    assert not post(api, "runners/heartbeat", {"version": "t", "platform": "t"}, token=caras["token"])["browser_exit"]
    share(api, rid)
    as_member(api, "ben@acme.example")
    post(api, f"computers/{rid}/browser-exit", {"sharing": False}, token="ben-test", expected=403)
    row = next(m for m in get(api, "operations", token="ben-test")["computers"] if m["id"] == rid)
    assert row["browser_exit"]["sharing"] and "used_today_bytes" not in row["browser_exit"]


def test_a_bot_browses_only_through_a_computer_that_shares_with_it_and_a_change_closes_its_connections(api, relay):
    caras, server = laptop(api)
    rid = caras["runner_id"]
    assert post(api, "bots/ops/browser-exit", {"runner_id": rid}, expected=409)["error"]["code"] == \
        "browser_exit_not_shared"
    share(api, rid)
    post(api, "bots/ops/browser-exit", {"runner_id": rid}, token="cara-test", expected=403)   # not ops's manager
    choices = get(api, "bots/ops/browser-exit")
    assert [c["runner_id"] for c in choices["choices"]] == [rid] and choices["browser_exit"] is None
    assert post(api, "bots/ops/browser-exit", {"runner_id": rid})["browser_exit"] == {
        "runner_id": rid, "name": "Cara's MacBook"}
    ops = next(e for e in api.get("/api/employees", headers={"Authorization": "Bearer ana-test"}).json()
               if e["name"] == "ops")
    assert ops["browser_exit"] == rid
    assert relay.closed == []
    post(api, "bots/ops/browser-exit", {"runner_id": None})
    assert relay.closed == [("ops", None, "browser exit changed")]


def test_every_tunnel_fails_closed_with_words_that_name_the_exit(api, relay):
    caras, server = laptop(api)
    rid = caras["runner_id"]

    def refused(expected, code, words=None, **kw):
        error = tunnel(api, server, expected=expected, **kw)["error"]
        assert error["code"] == code, error
        if words:
            assert words in error["detail"], error

    refused(400, "browser_exit_refused", port=22)
    refused(400, "browser_exit_refused", host="www.acme.example/x")
    refused(403, "forbidden", bot="cpo")                             # not a bot on this computer
    refused(409, "browser_exit_none")
    share(api, rid)
    post(api, "bots/ops/browser-exit", {"runner_id": rid})
    tunnel(api, caras, expected=403)                                 # ops is not on Cara's laptop
    post(api, f"computers/{rid}/browser-exit", {"sharing": True, "bots": []}, token="cara-test")
    refused(403, "browser_exit_not_shared", "Cara's MacBook doesn't share its internet with this bot")
    share(api, rid)
    for answer, status, code, words in [
            ("exit_offline", 409, "browser_exit_offline", "Cara's MacBook is offline"),
            ("exit_over_cap", 429, "browser_exit_over_cap", "Cara's MacBook reached its daily limit"),
            ("exit_busy", 429, "browser_exit_busy", "Cara's MacBook"),
            ("unavailable", 503, "browser_exit_unavailable", "Cara's MacBook")]:
        relay.answers = [answer]
        refused(status, code, words)
    # A policy change between the read and the grant is read again once.
    relay.answers = ["exit_stale"]
    granted = tunnel(api, server)
    assert granted["ticket"] == "a" * 64 and granted["exit"] == {"runner_id": rid, "name": "Cara's MacBook"}
    grant = relay.grants[-1]
    assert (grant["exit"], grant["runner_id"], grant["bot"], grant["port"]) == (rid, server["runner_id"], "ops", 443)
    assert grant["policy"] == {"generation": 3, "sharing": True, "bots": ["ops"], "cap": BE.DEFAULT_CAP}
    # Over the day's cap by recorded bytes: refused here, the relay never asked.
    asked = len(relay.grants)
    with api.app.state.store.transaction() as c:
        BE.record(c, [{"exit": rid, "day": BE.today(), "bot": "ops", "host": "www.acme.example",
                       "bytes": BE.DEFAULT_CAP}])
    refused(429, "browser_exit_over_cap", "Cara's MacBook reached its daily limit")
    assert len(relay.grants) == asked
    # A removed exit says so, and its tunnels close.
    post(api, f"runners/{rid}/revoke", {}, token="cara-test")
    refused(409, "browser_exit_removed", "Cara's MacBook was removed")
    assert (None, rid, "computer removed") in relay.closed


def test_the_owner_sees_what_went_through_per_day_and_old_days_go(api, relay):
    caras, _ = laptop(api)
    rid = caras["runner_id"]
    old = "2000-01-01"
    with api.app.state.store.transaction() as c:
        BE.record(c, [{"exit": rid, "day": BE.today(), "bot": "ops", "host": "WWW.acme.example", "bytes": 700},
                      {"exit": rid, "day": BE.today(), "bot": "ops", "host": "www.acme.example", "bytes": 300},
                      {"exit": rid, "day": BE.today(), "bot": "ops", "host": "cdn.acme.example", "bytes": 5},
                      {"exit": rid, "day": old, "bot": "ops", "host": "www.acme.example", "bytes": 9},
                      {"exit": rid, "bot": "ops"}])                                          # malformed: skipped
    get(api, f"computers/{rid}/browser-exit/usage", token="ben-test")                       # an admin
    as_member(api, "ben@acme.example")
    get(api, f"computers/{rid}/browser-exit/usage", token="ben-test", expected=403)
    page = get(api, f"computers/{rid}/browser-exit/usage?limit=1", token="cara-test")
    assert page["rows"] == [{"day": BE.today(), "bot": "ops", "host": "www.acme.example", "bytes": 1000, "tunnels": 2}]
    assert page["next"] == 1 and page["days"] == [{"day": BE.today(), "bytes": 1005}]
    assert get(api, f"computers/{rid}/browser-exit/usage?offset=1", token="cara-test")["next"] is None
    with api.app.state.store.transaction() as c:
        BE.prune(c)
        assert not c.execute("SELECT 1 FROM browser_exit_usage WHERE day=?", (old,)).fetchone()


def test_the_relay_lets_an_exit_link_in_only_while_its_owner_shares(api, relay):
    caras, server = laptop(api)
    state = api.app.state

    def link(r):
        return BE.authorize_link(state.store, state.auth, state.execution, {"authorization": "Bearer " + r["token"]})

    with pytest.raises(Problem):
        link(caras)
    share(api, caras["runner_id"])
    assert link(caras) == {"allowed": True, "runner_id": caras["runner_id"]}
    with pytest.raises(Problem):
        link(server)
